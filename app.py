"""Run Alderwick Chess Club locally and expose its JSON API."""

import os
from pathlib import Path
from tempfile import gettempdir

import chess
from flask import Flask, jsonify, render_template, request

import database
import rules


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Send a JSON object.")
    return data


def player_name(data, color):
    name = data.get(color, "")
    if not isinstance(name, str) or len(name.strip()) > 32:
        raise ApiError("Player names must be text with at most 32 characters.")
    return name.strip() or ("Player 1" if color == "white" else "Player 2")


def require_version(game, version):
    if type(version) is not int or version != game.version:
        raise ApiError("The match changed. Reload the position and try again.", 409)


def configure_app(app, database_path=None):
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    # Vercel's deployed application directory is read-only.
    data_dir = Path(gettempdir()) if os.environ.get("VERCEL") == "1" else Path(app.instance_path)
    # Keep the original filename and accept the old override for saved installs.
    path = Path(database_path or os.environ.get("ALDERWICK_DB") or os.environ.get("KNIGHTFALL_DB")
                or (data_dir / "knightfall.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    database.initialize(path)

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        return jsonify(error=str(error)), error.status

    @app.after_request
    def prevent_stale_api_cache(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def find_game(connection, game_id):
        game = database.load_game(connection, game_id)
        if game is None:
            raise ApiError("This match could not be found.", 404)
        return game

    def active_game(connection, game_id, data):
        game = find_game(connection, game_id)
        if database.current_game_id(connection) != game_id:
            raise ApiError("A new match has started on this device.", 409)
        require_version(game, data.get("version"))
        if game.result:
            raise ApiError("This game has ended.")
        return game

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/games/current")
    def current_game():
        with database.transaction(path) as connection:
            game = database.load_game(connection, database.current_game_id(connection))
        return jsonify(game=rules.game_state(game) if game else None)

    @app.get("/api/games/<int:game_id>")
    def get_game(game_id):
        with database.transaction(path) as connection:
            game = find_game(connection, game_id)
        return jsonify(game=rules.game_state(game))

    @app.post("/api/games")
    def new_game():
        data = json_body()
        white, black = player_name(data, "white"), player_name(data, "black")
        with database.transaction(path, write=True) as connection:
            current_id = database.current_game_id(connection)
            if data.get("previous_id") != current_id:
                raise ApiError("The current match changed. Reload and try again.", 409)
            if current_id is not None:
                require_version(find_game(connection, current_id), data.get("version"))
            game = database.create_game(connection, white, black)
        return jsonify(game=rules.game_state(game)), 201

    @app.get("/api/games/<int:game_id>/legal")
    def legal_moves(game_id):
        try:
            square = chess.parse_square(request.args.get("square", ""))
        except ValueError as error:
            raise ApiError("Choose a square from a1 to h8.") from error
        with database.transaction(path) as connection:
            game = find_game(connection, game_id)
            require_version(game, request.args.get("version", type=int))
            if database.current_game_id(connection) != game_id:
                raise ApiError("A new match has started on this device.", 409)
        # An empty list is valid for a blocked piece. An ended game is reported
        # explicitly, and errors use HTTP status codes so the UI can retry.
        if game.result:
            return jsonify(selection="finished", destinations=[], game=rules.game_state(game))
        piece = game.board.piece_at(square)
        if piece is None:
            return jsonify(selection="empty", destinations=[])
        if piece.color != game.board.turn:
            return jsonify(selection="wrong_color", destinations=[], turn=rules.color_name(game.board.turn))
        destinations = rules.legal_destinations(game.board, square)
        return jsonify(selection="legal" if destinations else "blocked", destinations=destinations)

    @app.post("/api/games/<int:game_id>/move")
    def play_move(game_id):
        data = json_body()
        uci = data.get("uci")
        if not isinstance(uci, str):
            raise ApiError("Supply a move such as e2e4.")
        with database.transaction(path, write=True) as connection:
            game = active_game(connection, game_id, data)
            try:
                move = game.board.parse_uci(uci)
            except ValueError as error:
                raise ApiError("That move is illegal in this position.") from error
            # parse_uci also recognizes a null move. Passing is illegal in chess.
            if not move:
                raise ApiError("Passing is illegal in chess.")
            if game.board.piece_type_at(move.to_square) == chess.KING:
                raise ApiError("The king cannot be captured.")
            # SAN depends on the position before the move. Save it alongside
            # UCI, then advance the board and check whether the match ended.
            san = game.board.san(move)
            database.record_move(connection, game, move, san)
            game.board.push(move)
            # A legal reply declines a pending draw offer in the same write.
            database.save_action(connection, game, rules.automatic_result(game.board))
        return jsonify(game=rules.game_state(game))

    @app.post("/api/games/<int:game_id>/draw")
    def claim_draw(game_id):
        data = json_body()
        intended = data.get("uci")
        with database.transaction(path, write=True) as connection:
            game = active_game(connection, game_id, data)
            options = rules.draw_options(game.board)
            reasons = options["now"]
            if intended is not None:
                reasons = next((option["reasons"] for option in options["intended"]
                                if option["uci"] == intended), [])
            if not reasons:
                raise ApiError("A draw cannot be claimed for that position or move.")
            # The declared move establishes eligibility without being played.
            database.save_action(connection, game, {"winner": None, "reason": reasons[0]})
        return jsonify(game=rules.game_state(game))

    @app.post("/api/games/<int:game_id>/resign")
    def resign(game_id):
        data = json_body()
        with database.transaction(path, write=True) as connection:
            game = active_game(connection, game_id, data)
            database.save_action(connection, game, rules.resignation_result(game.board))
        return jsonify(game=rules.game_state(game))

    @app.post("/api/games/<int:game_id>/draw/offer")
    def offer_draw(game_id):
        data = json_body()
        with database.transaction(path, write=True) as connection:
            game = active_game(connection, game_id, data)
            if not rules.can_offer_draw(game):
                raise ApiError("Both players must have moved and there must be no pending offer.")
            database.save_action(connection, game, draw_offer=rules.color_name(not game.board.turn))
        return jsonify(game=rules.game_state(game))

    @app.post("/api/games/<int:game_id>/draw/respond")
    def respond_to_draw(game_id):
        data = json_body()
        accepted = data.get("accept")
        if type(accepted) is not bool:
            raise ApiError("Choose whether to accept the offer.")
        with database.transaction(path, write=True) as connection:
            game = active_game(connection, game_id, data)
            if game.draw_offer != rules.color_name(not game.board.turn):
                raise ApiError("There is no draw offer for the current player.")
            result = {"winner": None, "reason": "agreement"} if accepted else None
            database.save_action(connection, game, result)
        return jsonify(game=rules.game_state(game))

    return app


def create_app(database_path=None):
    """Create an isolated Flask app when tests supply their own SQLite path."""
    return configure_app(Flask(__name__), database_path)


# WSGI hosts import this instance as app:app.
app = Flask(__name__)
configure_app(app)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
