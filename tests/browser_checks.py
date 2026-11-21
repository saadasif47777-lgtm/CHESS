"""Run Chromium against a real Flask process and an isolated SQLite file."""

import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parent.parent
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SCREENSHOT_FEN = "r7/1Q6/8/p4P2/P2k4/R7/1pP5/1N2K3 b - - 0 27"
CYCLE = ("g1f3", "g8f6", "f3g1", "f6g8")
LEGAL = re.compile(r"\blegal\b")
ANIMATION_PROBE = """
    const startAnimation = Element.prototype.animate;
    window.pieceAnimations = [];
    window.resultOpenedDuringSlide = false;
    const showModal = HTMLDialogElement.prototype.showModal;
    HTMLDialogElement.prototype.showModal = function () {
        if (this.id === "result-dialog") {
            window.resultOpenedDuringSlide = Boolean(document.querySelector(".sliding-piece"));
        }
        return showModal.call(this);
    };
    Element.prototype.animate = function (frames, options) {
        const animation = startAnimation.call(this, frames, options);
        if (this.closest("#board")) {
            const record = {piece: this.getAttribute("src"), frames, duration: options.duration,
                            square: this.closest("button")?.dataset.square, finished: false};
            window.pieceAnimations.push(record);
            animation.finished.then(() => { record.finished = true; });
        }
        return animation;
    };
"""


class LiveApp:
    def __init__(self, path):
        self.path = path
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        python = os.environ.get("ALDERWICK_SERVER_PYTHON", sys.executable)
        self.process = subprocess.Popen(
            [python, "-m", "flask", "--app", "app:app", "run", "--port", str(self.port)],
            cwd=ROOT, env={**os.environ, "ALDERWICK_DB": str(self.path)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            if self.process.poll() is not None:
                raise RuntimeError("Flask exited during startup.")
            try:
                with urlopen(self.url + "/api/games/current", timeout=1):
                    return
            except URLError:
                time.sleep(.1)
        raise RuntimeError("Flask did not respond.")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    def restart(self):
        self.stop()
        self.start()

    def fixture(self, fen):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO games (white_name, black_name, initial_fen) VALUES ('Ada', 'Grace', ?)", (fen,))


def square(page, name):
    return page.locator(f'[data-square="{name}"]')


def ready(page):
    expect(page.locator("#board button")).to_have_count(64)
    expect(page.locator("#new-game")).to_be_enabled()


def play(page, uci):
    square(page, uci[:2]).click()
    expect(square(page, uci[2:4])).to_have_class(LEGAL)
    square(page, uci[2:4]).click()


def piece(page, location, asset):
    expect(square(page, location).locator("img")).to_have_attribute("src", f"/static/pieces/{asset}.svg")


def last_move(page, description, san):
    expect(page.locator("tr.latest .description")).to_have_text(description)
    expect(page.locator("tr.latest .san")).to_have_text(san)


def game(page, url):
    return page.request.get(url + "/api/games/current").json()["game"]


def new_game(page, white="", black=""):
    page.locator("#new-game").click()
    dialog = page.locator("#game-dialog")
    dialog.get_by_label("White player").fill(white)
    dialog.get_by_label("Black player").fill(black)
    dialog.get_by_role("button", name="Start game").click()
    ready(page)
    expect(page.locator("#moves tr")).to_have_count(0)


def review(page):
    page.get_by_role("button", name="Review Board").click()
    expect(page.locator("#result-dialog")).not_to_be_visible()


def offer(page):
    page.locator("#offer-draw").click()
    page.locator("#offer-dialog").get_by_role("button", name="Offer draw").click()
    expect(page.locator("#draw-offer")).to_be_visible()


def recorded_animations(page):
    animations = page.evaluate("window.pieceAnimations")
    assert all(animation["duration"] == 215 and animation["finished"] for animation in animations)
    return animations


def check_saved_game(page, live):
    page.goto(live.url)
    ready(page)
    expect(page).to_have_title("Alderwick Chess Club")
    expect(page.locator("#turn")).to_have_text("Grace to move")
    last_move(page, "Pawn from e2 to e4", "e4")
    piece(page, "e4", "wP")
    assert not recorded_animations(page)
    assert page.evaluate("""async () => {
        await document.fonts.ready;
        return [...document.fonts].filter(font => font.status === "loaded").length === 2;
    }""")
    assert page.locator(".panel").bounding_box()["x"] > square(page, "h8").bounding_box()["x"]


def check_rook_and_retry(page, live):
    for flipped in (False, True):
        live.fixture(SCREENSHOT_FEN)
        page.reload()
        expect(page.locator("#turn")).to_have_text("Grace to move")
        ready(page)
        if flipped:
            page.locator("#flip").click()
            expect(page.locator("#board button").first).to_have_attribute("data-square", "h1")
            expect(page.locator("#near-player strong")).to_have_text("Grace")
        square(page, "b7").click()
        expect(page.locator("#notice")).to_have_text("It is Grace’s turn (Black).")
        page.route("**/legal?*", lambda route: route.abort(), times=1)
        square(page, "a8").click()
        expect(page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
        expect(page.locator("#turn")).to_have_text("Grace to move")
        piece(page, "a8", "bR")
        page.get_by_role("button", name="Retry", exact=True).click()
        expect(square(page, "b8")).to_have_class(LEGAL)
        square(page, "b8").click()
        last_move(page, "Rook from a8 to b8", "Rb8")
        expect(page.locator("#turn")).to_have_text("Ada to move")
        piece(page, "b8", "bR")
        animations = recorded_animations(page)
        assert len(animations) == 1
        assert animations[0]["piece"].endswith("/bR.svg")
        assert animations[0]["frames"][1]["transform"].startswith("translate(-") == flipped
        play(page, "b7b8")
        last_move(page, "Queen from b7 to b8, capturing rook", "Qxb8")
        piece(page, "b8", "wQ")
        slide, fade = recorded_animations(page)[-2:]
        assert slide["piece"].endswith("/wQ.svg")
        assert fade["square"] == "b8" and fade["piece"].endswith("/bR.svg")


def check_checkmate(page, live):
    new_game(page)
    expect(page.locator("#turn")).to_have_text("Player 1 to move")
    square(page, "a1").click()
    expect(page.locator("#notice")).to_have_text("This piece has no legal moves. Select another piece.")
    expect(page.locator("#status")).to_have_text("Game in progress.")
    for uci, san in (("f2f3", "f3"), ("e7e5", "e5"), ("g2g4", "g4"), ("d8h4", "Qh4#")):
        play(page, uci)
        expect(page.locator("tr.latest .san")).to_have_text(san)
    expect(page.locator("#result-players")).to_have_text("Player 2 (Black) wins — Player 1 (White) loses.")
    expect(page.locator("#result-score")).to_have_text("0–1")
    expect(page.locator("#result-reason")).to_have_text("Black delivered checkmate.")
    expect(square(page, "e1")).to_have_class(re.compile(r"\bin-check\b"))
    assert not page.evaluate("window.resultOpenedDuringSlide")
    finished = game(page, live.url)
    response = page.request.post(f"{live.url}/api/games/{finished['id']}/move",
                                 data={"version": finished["version"], "uci": "e1f2"})
    assert response.status == 400
    review(page)
    last_move(page, "Queen from d8 to h4", "Qh4#")
    page.reload()
    expect(page.locator("#result-dialog")).to_be_visible()
    page.get_by_role("button", name="Start New Game").click()
    ready(page)
    expect(page.locator("#board img")).to_have_count(32)
    expect(page.locator("#moves tr")).to_have_count(0)
    expect(page.locator("#turn")).to_have_text("Player 1 to move")
    assert page.request.get(f"{live.url}/api/games/{finished['id']}").json()["game"]["result"]["reason"] == "checkmate"


def check_special_moves(page, live):
    live.fixture("r3k2r/8/8/8/2b5/8/8/R3K2R w KQkq - 0 1")
    page.reload()
    ready(page)
    square(page, "e1").click()
    expect(square(page, "c1")).to_have_class(LEGAL)
    assert "legal" not in square(page, "g1").get_attribute("class").split()
    square(page, "c1").click()
    last_move(page, "King from e1 to c1, castling queenside", "O-O-O")
    piece(page, "c1", "wK")
    piece(page, "d1", "wR")
    assert [Path(item["piece"]).stem for item in recorded_animations(page)] == ["wK", "wR"]

    live.fixture("k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    page.reload()
    ready(page)
    square(page, "e5").click()
    expect(square(page, "e6")).to_have_class(LEGAL)
    assert "legal" not in square(page, "d6").get_attribute("class").split()

    live.fixture("7k/8/8/3pP3/8/8/8/R3K3 w - d6 0 1")
    page.reload()
    ready(page)
    play(page, "e5d6")
    last_move(page, "Pawn from e5 to d6, capturing pawn en passant", "exd6")
    piece(page, "d6", "wP")
    expect(square(page, "d5").locator("img")).to_have_count(0)
    animations = recorded_animations(page)
    assert animations[1]["square"] == "d5" and animations[1]["frames"][-1] == {"opacity": 0}

    live.fixture("7k/P7/8/8/8/8/8/R3K3 w - - 0 1")
    page.reload()
    ready(page)
    play(page, "a7a8")
    chooser = page.locator("#promotion-dialog")
    expect(chooser.locator("#promotion-options button")).to_have_count(4)
    expect(page.locator("#turn")).to_have_text("Ada to move")
    expect(page.locator("#moves tr")).to_have_count(0)
    chooser.get_by_role("button", name="knight").click()
    last_move(page, "Pawn from a7 to a8, promoting to knight", "a8=N")
    piece(page, "a8", "wN")
    assert recorded_animations(page)[0]["piece"].endswith("/wP.svg")

    live.fixture("8/8/1N6/7k/8/8/8/R3K3 w - - 0 1")
    page.reload()
    ready(page)
    play(page, "b6a8")
    last_move(page, "Knight from b6 to a8", "Na8")


def check_draws(page, live):
    live.fixture("7k/8/5QK1/8/8/8/8/8 w - - 0 1")
    page.reload()
    ready(page)
    play(page, "f6f7")
    expect(page.locator("#result-title")).to_have_text("Game drawn")
    expect(page.locator("#result-score")).to_have_text("½–½")
    expect(page.locator("#result-reason")).to_have_text("Stalemate.")
    review(page)
    expect(page.locator("#status")).to_have_text("Draw by stalemate.")
    page.locator("#view-result").click()
    page.get_by_role("button", name="Start New Game").click()
    ready(page)
    expect(page.locator("#board img")).to_have_count(32)
    expect(page.locator("#offer-draw")).to_be_disabled()

    play(page, "e2e4")
    expect(page.locator("#turn")).to_have_text("Grace to move")
    play(page, "e7e5")
    expect(page.locator("#turn")).to_have_text("Ada to move")
    offer(page)
    page.reload()
    expect(page.locator("#draw-offer-message")).to_contain_text("Grace offers a draw")
    page.locator("#decline-draw").click()
    expect(page.locator("#draw-offer")).not_to_be_visible()
    offer(page)
    play(page, "g1f3")
    expect(page.locator("#draw-offer")).not_to_be_visible()
    offer(page)
    page.locator("#accept-draw").click()
    expect(page.locator("#result-reason")).to_have_text("Agreement.")
    review(page)

    new_game(page, "Ada", "Grace")
    page.locator("#resign").click()
    expect(page.locator("#resign-message")).to_contain_text("Ada")
    page.locator("#resign-dialog").get_by_role("button", name="Resign", exact=True).click()
    expect(page.locator("#result-players")).to_have_text("Grace (Black) wins — Ada (White) loses.")
    review(page)

    for repeats in (2, 4):
        new_game(page, "Ada", "Grace")
        current = game(page, live.url)
        for uci in CYCLE * repeats:
            response = page.request.post(f"{live.url}/api/games/{current['id']}/move",
                                         data={"version": current["version"], "uci": uci})
            assert response.ok
            current = response.json()["game"]
        if repeats == 2:
            live.restart()
            page.reload()
            page.locator("#draw").click()
            expect(page.locator("#draw-options option").first).to_contain_text("Current position")
            page.locator("#draw-dialog").get_by_role("button", name="Claim draw").click()
            expect(page.locator("#result-reason")).to_have_text("Threefold repetition.")
        else:
            page.reload()
            expect(page.locator("#result-reason")).to_have_text("Fivefold repetition.")
        review(page)


def check_requests(page, live):
    new_game(page)
    page.evaluate("""() => {
        const realFetch = window.fetch;
        window.fetch = async (path, options) => {
            const response = await realFetch(path, options);
            if (path.includes('square=e2')) {
                await new Promise(resolve => { window.finishOldSelection = resolve; });
            }
            return response;
        };
    }""")
    square(page, "e2").click()
    page.wait_for_function("window.finishOldSelection !== undefined")
    square(page, "d2").click()
    expect(square(page, "d4")).to_have_class(LEGAL)
    page.evaluate("window.finishOldSelection()")
    expect(square(page, "e4")).not_to_have_class(LEGAL)

    other = page.context.new_page()
    other.goto(live.url)
    ready(other)
    square(other, "e2").click()
    expect(square(other, "e4")).to_have_class(LEGAL)
    square(page, "d4").evaluate("(button) => { button.click(); button.click(); }")
    last_move(page, "Pawn from d2 to d4", "d4")
    square(other, "e4").click()
    expect(other.locator("#notice")).to_contain_text("match changed")
    piece(other, "d4", "wP")
    with sqlite3.connect(live.path) as db:
        saved = db.execute("SELECT uci FROM moves WHERE game_id = ?", (game(page, live.url)["id"],)).fetchall()
    assert saved == [("d2d4",)]
    other.close()

    def lose_response(route):
        assert route.fetch().ok
        route.abort()

    page.route("**/move", lose_response, times=1)
    play(page, "e7e5")
    expect(page.locator("#notice")).to_contain_text("saved position")
    piece(page, "e5", "bP")
    assert len(game(page, live.url)["moves"]) == 2


def check_mobile(browser, live, desktop):
    new_game(desktop)
    phone = browser.new_page(viewport={"width": 360, "height": 800}, is_mobile=True, has_touch=True,
                             reduced_motion="reduce")
    phone.add_init_script(ANIMATION_PROBE)
    phone.goto(live.url)
    ready(phone)
    assert phone.evaluate("document.documentElement.scrollWidth") == 360
    assert phone.locator("#board button").evaluate_all("""squares => squares.every(square => {
        const rect = square.getBoundingClientRect();
        return rect.x >= 0 && rect.right <= 360 && rect.width > 0 && rect.height > 0;
    })""")
    assert phone.locator(".panel").bounding_box()["y"] > square(phone, "a1").bounding_box()["y"]
    square(phone, "e2").tap()
    expect(square(phone, "e4")).to_have_class(LEGAL)
    square(phone, "e4").tap()
    last_move(phone, "Pawn from e2 to e4", "e4")
    description = phone.locator("tr.latest .description").bounding_box()
    notation = phone.locator("tr.latest .san").bounding_box()
    assert description["width"] > 100 and notation["width"] > 100
    assert notation["y"] >= description["y"] + description["height"]
    assert not recorded_animations(phone)
    phone.close()

    desktop.reload()
    ready(desktop)
    new_game(desktop)
    desktop.reload()
    ready(desktop)
    desktop.keyboard.press("Tab")
    expect(square(desktop, "e2")).to_be_focused()
    assert square(desktop, "e2").evaluate("(button) => getComputedStyle(button).outlineStyle") == "dashed"
    for key in ("Enter", "ArrowUp", "ArrowUp", "Space"):
        desktop.keyboard.press(key)
        if key == "Enter":
            expect(square(desktop, "e4")).to_have_class(LEGAL)
    last_move(desktop, "Pawn from e2 to e4", "e4")
    desktop.reload()
    last_move(desktop, "Pawn from e2 to e4", "e4")
    assert not recorded_animations(desktop)


def seed_legacy_database(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE games (id INTEGER PRIMARY KEY, white_name TEXT NOT NULL,
                                black_name TEXT NOT NULL, initial_fen TEXT NOT NULL,
                                version INTEGER NOT NULL DEFAULT 0, winner TEXT, reason TEXT);
            CREATE TABLE moves (game_id INTEGER, ply INTEGER, uci TEXT, san TEXT,
                                PRIMARY KEY (game_id, ply));
            INSERT INTO moves VALUES (1, 1, 'e2e4', 'e4');
        """)
        db.execute("INSERT INTO games VALUES (1, 'Ada', 'Grace', ?, 1, NULL, NULL)", (START_FEN,))


def main():
    with tempfile.TemporaryDirectory() as folder, sync_playwright() as playwright:
        live = LiveApp(Path(folder) / "knightfall.sqlite3")
        seed_legacy_database(live.path)
        try:
            live.start()
            browser = playwright.chromium.launch(executable_path=os.environ.get("ALDERWICK_CHROMIUM"))
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(ANIMATION_PROBE)
            for check in (check_saved_game, check_rook_and_retry, check_checkmate, check_special_moves,
                          check_draws, check_requests):
                check(page, live)
                print(f"PASS {check.__name__}", flush=True)
            check_mobile(browser, live, page)
            print("PASS mobile, reduced motion and keyboard", flush=True)
            assert not errors, errors
            browser.close()
        finally:
            live.stop()


if __name__ == "__main__":
    main()
