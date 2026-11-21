# Knightfall

Knightfall is a chess web app for two people sharing one device. White moves
first. Flask serves the interface, python-chess validates moves and decides
results, and SQLite saves each match locally. The page uses plain HTML, CSS and
JavaScript, with locally bundled piece artwork.

## Run in VS Code on Windows

Install Python 3.10 or newer and Git, then open PowerShell in VS Code.

```powershell
git clone https://github.com/saadasif47777-lgtm/CHESS.git
cd CHESS
py -m pip install -r requirements.txt
py app.py
```

If you have already cloned the repository, open its folder in VS Code and start
with the pip command. Open [Knightfall](http://127.0.0.1:5000/) in your browser.
Press Ctrl+C in the terminal to stop the server. Run `py app.py` again to
resume the match. SQLite creates `instance/knightfall.sqlite3` on the first
run. Keep that file to retain saved games.

## Deploy

The Flask entry point is `app:app`. For hosting with durable match storage,
set `KNIGHTFALL_DB` to a database file on a writable, persistent volume.
On Vercel, Knightfall defaults to temporary SQLite storage. Games can be
lost when a function restarts or requests reach a different instance.

## Play

The first visit starts a standard match with the names White and Black. Use
**New game** to enter names. Select a piece to see its legal destinations, then
select one of the highlighted squares. Use **Flip board** to switch the view
or **Resign** to concede on behalf of the player whose turn it is. When a draw
is claimable, **Claim draw** lets you use the current position or select a
qualifying intended move. A claim ends the game without playing that move.

With a keyboard, Tab to the board, move focus with the arrow keys, and press
Enter or Space to select or move. Escape clears the selection.

## Source layout

| Path | Purpose |
| --- | --- |
| `app.py` | Flask pages, JSON routes and validation of incoming requests |
| `rules.py` | Chess rules, draw eligibility, and the state sent to the browser |
| `database.py` | SQLite tables, transactions and reconstruction of saved positions |
| `templates/index.html` | Match page and dialogs |
| `static/style.css` | Board, responsive layout and interaction styles |
| `static/game.js` | Board rendering, input and API requests |
| `static/pieces/` | Bundled SVGs, attribution and license |
| `tests/test_game.py` | API and persistence tests against temporary SQLite files |

## What happens when you play e2–e4?

1. Clicking e2 asks Flask for the legal destinations at the current game
   version. Python uses python-chess to return e3 and e4.
2. Clicking e4 sends `e2e4` and the same version to Flask. SQLite locks the
   match for writing while Python checks the version and the move. Python
   records `e2e4` and its algebraic notation, `e4`, with the new version and any
   result in one transaction.
3. Flask sends the new position, move history and Black's turn back to the
   browser. After refresh, Python replays the saved moves from the initial
   position so repetition claims still work. If the move response is lost,
   the browser fetches the saved position before accepting another move.

## Run the tests

```powershell
py -m unittest discover -s tests -v
```

The tests use the installed Python dependencies and run without a separate
server or an existing match.

Chessnut artwork by Alexis Luengas, distributed under Apache License 2.0.
[Artwork attribution and license](static/pieces/ATTRIBUTION.md)
