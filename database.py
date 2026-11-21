"""SQLite storage for match metadata and the ordered, replayable move log."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

import chess

import rules


SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    white_name TEXT NOT NULL,
    black_name TEXT NOT NULL,
    initial_fen TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    winner TEXT,
    reason TEXT,
    draw_offer TEXT
);

CREATE TABLE IF NOT EXISTS moves (
    game_id INTEGER NOT NULL REFERENCES games(id),
    ply INTEGER NOT NULL,
    uci TEXT NOT NULL,
    san TEXT NOT NULL,
    PRIMARY KEY (game_id, ply)
);
"""


@dataclass
class Game:
    id: int
    names: dict
    version: int
    board: chess.Board
    moves: list
    result: dict | None
    draw_offer: str | None


@contextmanager
def transaction(path, *, write=False):
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        # Reserve the write lock before loading the version. Two tabs cannot
        # both read the same version and then make conflicting updates.
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(path):
    with transaction(path) as connection:
        connection.executescript(SCHEMA)
    # Keep the existing database in place. Serialize the schema check and
    # upgrade so two workers can safely open the same older file.
    with transaction(path, write=True) as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(games)")}
        if "draw_offer" not in columns:
            connection.execute("ALTER TABLE games ADD COLUMN draw_offer TEXT")


def current_game_id(connection):
    row = connection.execute("SELECT MAX(id) AS id FROM games").fetchone()
    return row["id"]


def load_game(connection, game_id):
    row = connection.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        return None

    moves = connection.execute(
        "SELECT uci, san FROM moves WHERE game_id = ? ORDER BY ply", (game_id,)
    )
    board = chess.Board(row["initial_fen"])
    notation = []
    # A final FEN alone loses repetition history. Replay the stored UCI moves
    # to rebuild both the current position and python-chess's move stack.
    for row_move in moves:
        move = board.parse_uci(row_move["uci"])
        notation.append(rules.move_details(board, move, row_move["san"]))
        board.push(move)
    result = {"winner": row["winner"], "reason": row["reason"]} if row["reason"] else None
    return Game(row["id"], {"white": row["white_name"], "black": row["black_name"]},
                row["version"], board, notation, result, row["draw_offer"])


def create_game(connection, white, black, initial_fen=chess.STARTING_FEN):
    result = rules.automatic_result(chess.Board(initial_fen))
    cursor = connection.execute(
        "INSERT INTO games (white_name, black_name, initial_fen, winner, reason) VALUES (?, ?, ?, ?, ?)",
        (white, black, initial_fen, result["winner"] if result else None, result["reason"] if result else None),
    )
    return load_game(connection, cursor.lastrowid)


def record_move(connection, game, move, san):
    connection.execute(
        "INSERT INTO moves (game_id, ply, uci, san) VALUES (?, ?, ?, ?)",
        (game.id, len(game.moves) + 1, move.uci(), san),
    )
    game.moves.append(rules.move_details(game.board, move, san))


def save_action(connection, game, result=None, draw_offer=None):
    """Save result, offer and version in the same transaction as any new move."""
    connection.execute(
        "UPDATE games SET version = version + 1, winner = ?, reason = ?, draw_offer = ? WHERE id = ?",
        (result["winner"] if result else None, result["reason"] if result else None, draw_offer, game.id),
    )
    game.version += 1
    game.result = result
    game.draw_offer = draw_offer
