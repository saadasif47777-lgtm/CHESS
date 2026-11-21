# Alderwick Chess Club

A local chess table for two people sharing one device. Player 1 plays White
and moves first. Player 2 plays Black. Use **New game** to set custom names.
The interface uses HTML, CSS and JavaScript. Flask, python-chess and SQLite
handle the entire game on the Python side.

## Run in VS Code on Windows

Install Python 3.10 or newer and Git. In the VS Code PowerShell terminal, use
the current GitHub URL and clone into the new local folder name.

```powershell
git clone https://github.com/saadasif47777-lgtm/CHESS.git alderwick-chess-club
cd alderwick-chess-club
py -m pip install -r requirements.txt
py app.py
```

In an existing checkout, open its folder in VS Code and start with the pip
command. Open [the local chess table](http://127.0.0.1:5000/) in your browser.
Press Ctrl+C in PowerShell to stop Flask.

Keep the `instance/` folder when updating or moving the project. Alderwick
opens the existing `instance/knightfall.sqlite3` and adds the draw-offer column
on startup. It keeps saved player names, positions, notation, and results. The
older `KNIGHTFALL_DB` environment variable still works. For new configurations,
use `ALDERWICK_DB` to specify a different SQLite file.

## Play

- Select a piece and choose a highlighted square. With a keyboard, Tab to the
  board, use the arrow keys to move focus, and press Enter or Space to select
  or move. Press Escape to clear your selection.
- **Flip board** changes the view. The pieces remain upright and keep their
  positions. **Resign** asks the player to move to confirm.
- Once both players have moved, the player who just moved can offer a draw.
  The other player can accept, decline, or continue by making a move. When
  **Claim draw** is available, choose the current position or an eligible
  intended move. Claiming with an intended move leaves that move unplayed.
- At the end of a match, **Review Board** leaves the last position and history
  visible. **Start New Game** creates a separate match with the same names.
  **New game** in the match panel lets you edit the names.

## Source layout

| Path | Purpose |
| --- | --- |
| `app.py` | Flask entry point, JSON routes, request and version validation |
| `rules.py` | Move descriptions, visual effects, draw eligibility, and results |
| `database.py` | SQLite schema, transactions, and reconstruction of saved matches |
| `templates/index.html` | Chess table, controls, and dialogs |
| `static/style.css`, `static/game.js` | Layout, board rendering, animation, and input |
| `static/emblem.svg`, `static/fonts/`, `static/pieces/` | Club emblem, fonts, and piece artwork |
| `tests/test_game.py` | Python API and persistence tests |
| `tests/browser_checks.py` | Chromium interactions against a real Flask process |

## From e2 to e4

1. Clicking e2 sends its square and the match version to Flask. Python checks
   whose turn it is and uses python-chess to return e3 and e4.
2. Clicking e4 submits `e2e4` with that version. SQLite reserves a write lock
   while Python validates the move, calculates `e4` and “Pawn from e2 to e4,”
   advances the board, and evaluates the result. The move and updated match
   are saved in one transaction.
3. The browser animates the move, then renders the saved board, move history,
   and new turn. If a response is lost after a save, the browser reloads the
   current match before accepting another move. On restart, Python replays
   the moves from the initial position so repetition eligibility survives.

## Rules and scope

Standard play follows the [FIDE Laws of Chess](https://handbook.fide.com/chapter/E012023).
The game is untimed. python-chess recognizes ordinary dead positions based on
insufficient mating material, including bare kings, king and a lone bishop
or knight against a king, and bishops on one square color with no other
material. It uses each side's mating material for the resignation exception.
Its material check does not search every legal continuation. Unusual dead
positions caused by permanently blocked pieces or forced lines can go
undetected, including when evaluating whether a resignation is a draw.

## Test

```powershell
py -m unittest discover -s tests -v
```

For the optional Chromium checks, install Playwright and its browser once.

```powershell
py -m pip install playwright
py -m playwright install chromium
py tests/browser_checks.py
```

The tests use temporary databases and leave your saved matches alone. You can
point `ALDERWICK_CHROMIUM` to an existing Chromium executable for browser tests.

## Host

The WSGI entry point is `app:app`. To retain matches on a host, use a single
deployment with a shared writable, persistent volume and set `ALDERWICK_DB`
to the SQLite file on that volume. Vercel defaults to temporary SQLite.
Function restarts or requests routed to different instances can lose access
to the current match.

## Assets

Chessnut pieces by Alexis Luengas, under Apache License 2.0.
[Artwork attribution and license](static/pieces/ATTRIBUTION.md)

Cormorant Garamond and Instrument Sans are bundled under the SIL Open Font
License 1.1. [Cormorant Garamond license](static/fonts/cormorant-garamond-OFL.txt)
· [Instrument Sans license](static/fonts/instrument-sans-OFL.txt)
