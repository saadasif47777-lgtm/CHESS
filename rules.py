"""Chess rules and the JSON representation used by the browser."""

import chess


PROMOTION_ORDER = "qrbn"
DRAW_REASONS = {
    "stalemate": "stalemate",
    "insufficient_material": "insufficient material",
    "agreement": "agreement",
    "fivefold_repetition": "fivefold repetition",
    "seventyfive_moves": "the seventy-five-move rule",
    "threefold_repetition": "threefold repetition",
    "fifty_moves": "the fifty-move rule",
}


def color_name(color):
    return "white" if color == chess.WHITE else "black"


def other_color(color):
    return "black" if color == "white" else "white"


def automatic_result(board):
    # Claimable draws are optional. Only automatic endings stop a move here.
    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return None
    winner = color_name(outcome.winner) if outcome.winner is not None else None
    return {"winner": winner, "reason": outcome.termination.name.lower()}


def resignation_result(board):
    opponent = not board.turn
    # This asks whether the opponent could mate with cooperation. It does
    # not ask whether the opponent can force mate against good play.
    if board.has_insufficient_material(opponent):
        return {"winner": None, "reason": "resignation_no_mate"}
    return {"winner": color_name(opponent), "reason": "resignation"}


def move_details(board, move, san):
    """Describe a move and its visual effects from the position before it."""
    origin = chess.square_name(move.from_square)
    target = chess.square_name(move.to_square)
    piece = board.piece_at(move.from_square)
    description = f"{chess.piece_name(piece.piece_type).capitalize()} from {origin} to {target}"
    slides = [{"from": origin, "to": target}]
    capture = None
    if board.is_capture(move):
        en_passant = board.is_en_passant(move)
        captured_square = move.to_square
        if en_passant:
            # The pawn that disappears is behind the destination square.
            captured_square += -8 if board.turn == chess.WHITE else 8
        captured_piece = chess.piece_name(board.piece_type_at(captured_square))
        capture = chess.square_name(captured_square)
        description += f", capturing {captured_piece}"
        if en_passant:
            description += " en passant"
    if board.is_castling(move):
        kingside = board.is_kingside_castling(move)
        rank = origin[1]
        rook_from, rook_to = ("h", "f") if kingside else ("a", "d")
        slides.append({"from": rook_from + rank, "to": rook_to + rank})
        description += ", castling kingside" if kingside else ", castling queenside"
    if move.promotion:
        description += f", promoting to {chess.piece_name(move.promotion)}"
    return {
        "number": board.fullmove_number,
        "color": color_name(board.turn),
        "description": description,
        "san": san,
        "effects": {"slides": slides, "capture": capture},
    }


def claim_reasons(board):
    reasons = []
    if board.is_repetition(3):
        reasons.append("threefold_repetition")
    if board.is_fifty_moves():
        reasons.append("fifty_moves")
    return reasons


def draw_options(board):
    """Return claims for this position and claims tied to an intended move."""
    now = claim_reasons(board)
    may_repeat = board.can_claim_threefold_repetition()
    may_reach_fifty = board.halfmove_clock >= 99
    intended = []
    if may_repeat or may_reach_fifty:
        # Test on a copy with its move stack so repetition uses the real history.
        trial = board.copy()
        for move in list(trial.legal_moves):
            trial.push(move)
            reasons = claim_reasons(trial)
            trial.pop()
            if reasons:
                intended.append({"uci": move.uci(), "san": trial.san(move), "reasons": reasons})
    return {"now": now, "intended": intended}


def legal_destinations(board, square):
    destinations = {}
    for move in board.legal_moves:
        if move.from_square != square:
            continue
        target = chess.square_name(move.to_square)
        entry = destinations.setdefault(target, {
            "square": target,
            "capture": board.is_capture(move),
            "promotions": [],
        })
        if move.promotion:
            entry["promotions"].append(chess.piece_symbol(move.promotion))
    for entry in destinations.values():
        entry["promotions"].sort(key=PROMOTION_ORDER.index)
    return list(destinations.values())


def result_details(result, names, turn):
    """Keep the result panel and dialog tied to the same recorded outcome."""
    reason, winner = result["reason"], result["winner"]
    if winner:
        loser = other_color(winner)
        if reason == "resignation":
            explanation = f"{names[loser]} resigned."
            status = f"{names[winner]} wins. {explanation}"
        else:
            explanation = f"{winner.capitalize()} delivered checkmate."
            status = f"{names[winner]} wins by checkmate."
        return {
            "winner": {"name": names[winner], "color": winner},
            "loser": {"name": names[loser], "color": loser},
            "score": "1–0" if winner == "white" else "0–1",
            "explanation": explanation,
            "status": status,
        }

    if reason == "resignation_no_mate":
        opponent = names[other_color(turn)]
        explanation = f"{names[turn]} resigned. {opponent} could not possibly deliver mate."
        status = f"Draw. {explanation}"
    else:
        explanation = f"{DRAW_REASONS[reason].capitalize()}."
        status = f"Draw by {DRAW_REASONS[reason]}."
    return {"winner": None, "loser": None, "score": "½–½",
            "explanation": explanation, "status": status}


def can_offer_draw(game):
    # An offer belongs to the player who just moved. Both players need a
    # move in the history before an agreement can end the match.
    return not game.result and len(game.moves) >= 2 and game.draw_offer is None


def game_state(game):
    board = game.board
    result = game.result
    turn = color_name(board.turn)
    checked_king = chess.square_name(board.king(board.turn)) if board.is_check() else None
    summary = result_details(result, game.names, turn) if result else None
    if summary:
        status = summary["status"]
    elif checked_king:
        status = f"{game.names[turn]} is in check."
    else:
        status = "Game in progress."

    last_move = board.peek() if board.move_stack else None
    return {
        "id": game.id,
        "version": game.version,
        "names": game.names,
        "turn": turn,
        "pieces": {chess.square_name(square): piece.symbol()
                   for square, piece in board.piece_map().items()},
        "moves": game.moves,
        "last_move": [chess.square_name(last_move.from_square),
                      chess.square_name(last_move.to_square)] if last_move else [],
        "checked_king": checked_king,
        "result": result,
        "result_details": summary,
        "status": status,
        "draw": {"now": [], "intended": []} if result else draw_options(board),
        "draw_offer": game.draw_offer,
        "can_offer_draw": can_offer_draw(game),
    }
