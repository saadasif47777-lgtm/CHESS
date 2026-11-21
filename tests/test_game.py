"""Exercise the routes against a temporary SQLite database."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import chess

from app import create_app
import database


KNIGHT_CYCLE = ("g1f3", "g8f6", "f3g1", "f6g8")


class GameTests(unittest.TestCase):
    def setUp(self):
        temporary_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_dir.cleanup)
        self.path = Path(temporary_dir.name) / "match.sqlite3"
        self.app = create_app(self.path)
        self.client = self.app.test_client()

    def start_game(self, fen=None):
        if fen:
            with database.transaction(self.path, write=True) as connection:
                database.create_game(connection, "Ada", "Grace", fen)
            response = self.client.get("/api/games/current")
            self.assertEqual(response.status_code, 200)
        else:
            response = self.client.post("/api/games", json={"white": "Ada", "black": "Grace"})
            self.assertEqual(response.status_code, 201)
        self.game = response.json["game"]

    def action(self, action, expected=200, **data):
        response = self.client.post(
            f"/api/games/{self.game['id']}/{action}",
            json={"version": self.game["version"], **data},
        )
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))
        if expected == 200:
            self.game = response.json["game"]

    def moves(self, *moves):
        for move in moves:
            self.action("move", uci=move)

    def legal(self, square):
        response = self.client.get(
            f"/api/games/{self.game['id']}/legal",
            query_string={"square": square, "version": self.game["version"]},
        )
        self.assertEqual(response.status_code, 200)
        return {entry["square"]: entry for entry in response.json["destinations"]}

    def restart(self):
        self.client = create_app(self.path).test_client()
        self.game = self.client.get("/api/games/current").json["game"]

    def test_opening_and_persistence(self):
        self.assertIsNone(self.client.get("/api/games/current").json["game"])
        self.start_game()
        self.assertEqual(set(self.legal("e2")), {"e3", "e4"})
        self.moves("e2e4")
        saved = self.game
        self.restart()
        self.assertEqual(self.game, saved)
        self.assertEqual(self.game["pieces"]["e4"], "P")
        self.assertEqual(self.game["turn"], "black")
        self.assertEqual(self.game["moves"], [{
            "number": 1, "color": "white", "san": "e4", "description": "Pawn from e2 to e4",
            "effects": {"capture": None, "slides": [{"from": "e2", "to": "e4"}]},
        }])

    def test_checkmate_and_rejection(self):
        self.start_game()
        self.moves("f2f3", "e7e5", "g2g4", "d8h4")
        self.assertEqual(self.game["result"], {"winner": "black", "reason": "checkmate"})
        self.assertEqual(self.game["status"], "Grace wins by checkmate.")
        self.assertEqual(self.game["moves"][-1]["san"], "Qh4#")
        self.assertEqual(self.game["checked_king"], "e1")
        self.action("move", 400, uci="e1f2")
        self.assertEqual(self.legal("e1"), {})
        saved = self.game
        self.restart()
        self.assertEqual(self.game, saved)

    def test_castling_through_check_and_queenside_castle(self):
        self.start_game("r3k2r/8/8/8/2b5/8/8/R3K2R w KQkq - 0 1")
        self.assertNotIn("g1", self.legal("e1"))
        self.action("move", 400, uci="e1g1")
        self.assertIn("c1", self.legal("e1"))
        self.moves("e1c1")
        self.assertEqual(self.game["pieces"]["c1"], "K")
        self.assertEqual(self.game["pieces"]["d1"], "R")
        self.assertEqual(self.game["moves"][-1]["san"], "O-O-O")

    def test_kingside_castle(self):
        self.start_game("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
        self.moves("e1g1")
        self.assertEqual(self.game["pieces"]["g1"], "K")
        self.assertEqual(self.game["pieces"]["f1"], "R")
        self.assertEqual(self.game["moves"][-1]["san"], "O-O")

    def test_pinned_wrong_turn_and_null_moves_leave_game_unchanged(self):
        self.start_game("k3r3/8/8/8/8/8/4R3/4K3 w - - 0 1")
        saved = self.game
        self.assertNotIn("d2", self.legal("e2"))
        for move in ("e2d2", "a8b8", "0000"):
            self.action("move", 400, uci=move)
        self.restart()
        self.assertEqual(self.game, saved)

    def test_en_passant(self):
        self.start_game()
        self.moves("e2e4", "a7a6", "e4e5", "d7d5")
        self.assertTrue(self.legal("e5")["d6"]["capture"])
        self.moves("e5d6")
        self.assertNotIn("d5", self.game["pieces"])
        self.assertEqual(self.game["pieces"]["d6"], "P")
        self.assertEqual(self.game["moves"][-1]["san"], "exd6")
        self.assertEqual(self.game["moves"][-1]["effects"]["capture"], "d5")
        self.assertEqual(self.game["moves"][-1]["description"], "Pawn from e5 to d6, capturing pawn en passant")

    def test_en_passant_cannot_expose_the_king(self):
        self.start_game("k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
        self.assertNotIn("d6", self.legal("e5"))
        self.action("move", 400, uci="e5d6")

    def test_double_check_all_legal_moves_are_king_moves(self):
        self.start_game("4k3/p7/8/1B6/8/8/8/4R1K1 b - - 0 1")
        self.assertEqual(self.game["checked_king"], "e8")
        self.assertEqual(self.legal("a7"), {})
        self.assertTrue(self.legal("e8"))
        self.action("move", 400, uci="a7a6")

    def test_promotion_must_have_a_piece_choice(self):
        self.start_game("7k/P7/8/8/8/8/8/R3K3 w - - 0 1")
        self.assertEqual(self.legal("a7")["a8"]["promotions"], ["q", "r", "b", "n"])
        self.action("move", 400, uci="a7a8")
        self.moves("a7a8n")
        self.assertEqual(self.game["pieces"]["a8"], "N")
        self.assertEqual(self.game["moves"][-1]["san"], "a8=N")
        self.assertIn("promoting to knight", self.game["moves"][-1]["description"])

    def test_screenshot_rook_may_move_to_attacked_square(self):
        self.start_game("r7/1Q6/8/p4P2/P2k4/R7/1pP5/1N2K3 b - - 0 27")
        self.assertIn("b8", self.legal("a8"))
        self.moves("a8b8")
        self.assertEqual(self.game["moves"][-1]["description"], "Rook from a8 to b8")
        self.assertEqual(self.game["moves"][-1]["san"], "Rb8")
        self.assertEqual(self.game["turn"], "white")

    def test_san_keeps_knight_disambiguation(self):
        self.start_game("7k/8/1N1N4/8/8/8/8/R3K3 w - - 0 1")
        self.moves("b6c8")
        self.assertEqual(self.game["moves"][-1]["description"], "Knight from b6 to c8")
        self.assertEqual(self.game["moves"][-1]["san"], "Nbc8")

    def test_claimable_repetition_survives_restart(self):
        self.start_game()
        self.action("draw", 400)
        self.moves(*(KNIGHT_CYCLE * 2))
        self.assertIsNone(self.game["result"])
        self.restart()
        self.assertIn("threefold_repetition", self.game["draw"]["now"])
        self.action("draw")
        self.assertEqual(self.game["status"], "Draw by threefold repetition.")
        self.assertEqual(len(self.game["moves"]), 8)

    def test_intended_move_claim_leaves_move_unplayed(self):
        self.start_game()
        self.moves(*KNIGHT_CYCLE, *KNIGHT_CYCLE[:3])
        self.assertEqual(self.game["draw"]["now"], [])
        self.assertIn("f6g8", [move["uci"] for move in self.game["draw"]["intended"]])
        self.action("draw", 400, uci="a7a6")
        self.action("draw", uci="f6g8")
        self.assertEqual(self.game["result"]["reason"], "threefold_repetition")
        self.assertEqual(self.game["pieces"]["f6"], "n")
        self.assertEqual(len(self.game["moves"]), 7)

    def test_fifty_move_claim(self):
        self.start_game("7k/8/8/8/8/8/8/R3K3 w - - 99 51")
        self.assertEqual(self.game["draw"]["now"], [])
        self.assertIn("a1a2", [move["uci"] for move in self.game["draw"]["intended"]])
        self.action("draw", uci="a1a2")
        self.assertEqual(self.game["result"]["reason"], "fifty_moves")

    def test_automatic_fivefold_repetition(self):
        self.start_game()
        self.moves(*(KNIGHT_CYCLE * 4))
        self.assertEqual(self.game["result"]["reason"], "fivefold_repetition")

    def test_seventy_five_move_ending(self):
        self.start_game("7k/8/8/8/8/8/8/R3K3 w - - 149 76")
        self.moves("a1a2")
        self.assertEqual(self.game["result"]["reason"], "seventyfive_moves")

    def test_mate_takes_precedence_over_seventy_five_move_rule(self):
        self.start_game("7k/5Q2/6K1/8/8/8/8/8 w - - 149 76")
        self.moves("f7g7")
        self.assertEqual(self.game["result"], {"winner": "white", "reason": "checkmate"})

    def test_repetition_counts_only_legally_available_en_passant(self):
        for fen, eligible in (
            ("7k/6n1/8/3pP3/8/8/6N1/K7 w - d6 0 1", False),
            ("k3r3/6n1/8/3pP3/8/8/6N1/4K3 w - d6 0 1", True),
        ):
            with self.subTest(eligible=eligible):
                self.start_game(fen)
                self.moves(*(("g2f4", "g7f5", "f4g2", "f5g7") * 2))
                self.assertEqual("threefold_repetition" in self.game["draw"]["now"], eligible)

    def test_repetition_accounts_for_castling_rights(self):
        self.start_game("1n2k3/8/8/8/8/8/8/R3K3 w Q - 0 1")
        cycle = ("a1a2", "b8c6", "a2a1", "c6b8")
        self.moves(*(cycle * 2))
        self.assertNotIn("threefold_repetition", self.game["draw"]["now"])
        self.moves(*cycle)
        self.assertIn("threefold_repetition", self.game["draw"]["now"])

    def test_stalemate(self):
        self.start_game("7k/8/5QK1/8/8/8/8/8 w - - 0 1")
        self.moves("f6f7")
        self.assertEqual(self.game["result"]["reason"], "stalemate")

    def test_insufficient_material_on_capture(self):
        self.start_game("7k/6r1/5B2/8/8/8/8/K7 w - - 0 1")
        self.moves("f6g7")
        self.assertEqual(self.game["result"]["reason"], "insufficient_material")

    def test_two_tabs_cannot_commit_moves_from_the_same_version(self):
        self.start_game()
        path = f"/api/games/{self.game['id']}/move"
        ready = Barrier(2)

        def submit(move):
            client = self.app.test_client()
            ready.wait(timeout=5)
            return client.post(path, json={"version": 0, "uci": move}).status_code

        with ThreadPoolExecutor(max_workers=2) as tabs:
            statuses = list(tabs.map(submit, ["e2e4", "d2d4"]))
        self.assertEqual(sorted(statuses), [200, 409])
        with database.transaction(self.path) as connection:
            rows = connection.execute("SELECT uci FROM moves").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0]["uci"], {"e2e4", "d2d4"})

    def test_resignation_and_play_again(self):
        self.start_game()
        self.action("resign")
        game_id = self.game["id"]
        self.restart()
        self.assertEqual(self.game["status"], "Grace wins. Ada resigned.")
        stale = self.client.post("/api/games", json={"previous_id": game_id, "version": 0})
        self.assertEqual(stale.status_code, 409)
        response = self.client.post(
            "/api/games", json={"previous_id": game_id, "version": self.game["version"]},
        )
        self.assertEqual(response.status_code, 201)
        new_game = response.json["game"]
        self.assertNotEqual(new_game["id"], game_id)
        self.assertEqual(new_game["names"], {"white": "Player 1", "black": "Player 2"})
        self.assertEqual(new_game["moves"], [])
        self.assertEqual(new_game["turn"], "white")
        self.assertEqual(self.client.get(f"/api/games/{game_id}").json["game"]["result"]["reason"], "resignation")

    def test_resignation_is_draw_when_opponent_has_only_king(self):
        self.start_game("7k/8/8/8/8/8/8/R3K3 w - - 0 1")
        self.action("resign")
        self.assertEqual(self.game["result"], {"winner": None, "reason": "resignation_no_mate"})
        self.assertEqual(self.game["result_details"]["score"], "½–½")

    def test_standard_dead_positions(self):
        for fen in (
            "7k/8/8/8/8/8/8/K7 w - - 0 1",
            "7k/8/8/8/8/8/6N1/K7 w - - 0 1",
            "7k/8/8/8/8/8/5B2/K7 w - - 0 1",
            "7k/8/5b2/8/8/2B5/8/K7 w - - 0 1",
        ):
            with self.subTest(fen=fen):
                self.start_game(fen)
                self.assertEqual(self.game["result"]["reason"], "insufficient_material")

    def test_draw_offer_persists_and_can_be_accepted(self):
        self.start_game()
        self.action("draw/offer", 400)
        self.moves("e2e4")
        self.action("draw/offer", 400)
        self.moves("e7e5")
        self.action("draw/offer")
        self.restart()
        self.assertEqual(self.game["draw_offer"], "black")
        self.action("draw/respond", accept=True)
        self.assertIsNone(self.game["draw_offer"])
        self.assertEqual(self.game["result"], {"winner": None, "reason": "agreement"})

    def test_draw_offer_can_be_declined_by_button_or_move(self):
        self.start_game()
        self.moves("d2d4", "d7d5")
        self.action("draw/offer")
        self.action("draw/respond", accept=False)
        self.assertIsNone(self.game["draw_offer"])
        self.action("draw/offer")
        self.action("move", 400, uci="d4e5")
        self.restart()
        self.assertEqual(self.game["draw_offer"], "black")
        self.moves("g1f3")
        self.assertIsNone(self.game["draw_offer"])
        self.assertIsNone(self.game["result"])

    def test_legacy_database_is_upgraded_without_losing_moves(self):
        legacy_path = self.path.with_name("knightfall.sqlite3")
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(database.SCHEMA.replace(",\n    draw_offer TEXT", ""))
            connection.execute("INSERT INTO games VALUES (1, 'White', 'Black', ?, 1, NULL, NULL)",
                               (chess.STARTING_FEN,))
            connection.execute("INSERT INTO moves VALUES (1, 1, 'e2e4', 'e4')")
        client = create_app(legacy_path).test_client()
        restored = client.get("/api/games/current").json["game"]
        self.assertEqual(restored["names"], {"white": "White", "black": "Black"})
        self.assertEqual(restored["moves"][0]["description"], "Pawn from e2 to e4")
        self.assertEqual(restored["turn"], "black")
        self.assertIsNone(restored["draw_offer"])


if __name__ == "__main__":
    unittest.main()
