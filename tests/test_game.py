"""Exercise the routes against a temporary SQLite database."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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
        self.assertEqual(self.game["moves"], [{"number": 1, "color": "white", "san": "e4"}])

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

    def test_promotion_must_have_a_piece_choice(self):
        self.start_game("7k/P7/8/8/8/8/8/R3K3 w - - 0 1")
        self.assertEqual(self.legal("a7")["a8"]["promotions"], ["q", "r", "b", "n"])
        self.action("move", 400, uci="a7a8")
        self.moves("a7a8n")
        self.assertEqual(self.game["pieces"]["a8"], "N")
        self.assertEqual(self.game["moves"][-1]["san"], "a8=N")

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
        self.assertEqual(new_game["names"], {"white": "White", "black": "Black"})
        self.assertEqual(new_game["moves"], [])
        self.assertEqual(new_game["turn"], "white")


if __name__ == "__main__":
    unittest.main()
