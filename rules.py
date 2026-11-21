"""Chess rules and the JSON representation used by the browser."""

import chess


PROMOTION_ORDER = "qrbn"
DRAW_REASONS = {
    "stalemate": "stalemate",
    "insufficient_material": "insufficient material",
    "fivefold_repetition": "fivefold repetition",
    "seventyfive_moves": "the seventy-five-move rule",
    "threefold_repetition": "threefold repetition",
    "fifty_moves": "the fifty-move rule",
}


def color_name(color):
    return "white" if color == chess.WHITE else "black"


def automatic_result(board):
    # Claimable draws are optional. Only automatic endings stop a move here.
    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return None
    winner = color_name(outcome.winner) if outcome.winner is not None else None
    return {"winner": winner, "reason": outcome.termination.name.lower()}


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


def game_state(game):
    board = game.board
    result = game.result
    turn = color_name(board.turn)
    checked_king = chess.square_name(board.king(board.turn)) if board.is_check() else None
    status = "playing"
    if result:
        reason = result["reason"]
        if reason == "resignation":
            loser = "black" if result["winner"] == "white" else "white"
            status = f"{game.names[result['winner']]} wins. {game.names[loser]} resigned."
        elif result["winner"]:
            status = f"{game.names[result['winner']]} wins by checkmate."
        else:
            status = f"Draw by {DRAW_REASONS[reason]}."
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
        "status": status,
        "draw": {"now": [], "intended": []} if result else draw_options(board),
    }
