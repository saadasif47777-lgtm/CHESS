"use strict";

const byId = (id) => document.getElementById(id);
const board = byId("board");
const names = {k: "king", q: "queen", r: "rook", b: "bishop", n: "knight", p: "pawn"};
const drawReasons = {
    threefold_repetition: "threefold repetition",
    fifty_moves: "fifty-move rule",
};
const files = "abcdefgh";
const ranks = "87654321";

let game = null;
let flipped = false;
let selected = null;
let destinations = [];
let selectionRequest = 0;
let retrySquare = null;
let focusSquare = "e2";
let busy = false;
let synchronized = false;
let presentedResultId = null;

class RequestError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

async function request(path, data) {
    const options = {signal: AbortSignal.timeout(10000), cache: "no-store"};
    if (data !== undefined) {
        options.method = "POST";
        options.headers = {"Content-Type": "application/json"};
        options.body = JSON.stringify(data);
    }
    const response = await fetch(path, options);
    const body = await response.json().catch(() => null);
    if (!response.ok) {
        throw new RequestError(body?.error || "The request failed.", response.status);
    }
    if (!body) throw new Error("The server response was not JSON.");
    return body;
}

function showNotice(message = "") {
    byId("notice").textContent = message;
}

function setLegalRetry(square = null) {
    retrySquare = square;
    byId("retry-legal").hidden = !square;
}

function colorOf(symbol) {
    return symbol === symbol.toUpperCase() ? "white" : "black";
}

function pieceImage(symbol) {
    const image = document.createElement("img");
    image.src = `/static/pieces/${colorOf(symbol)[0]}${symbol.toUpperCase()}.svg`;
    image.alt = "";
    image.draggable = false;
    return image;
}

function coordinate(text, className) {
    const label = document.createElement("span");
    label.className = `coordinate ${className}`;
    label.setAttribute("aria-hidden", "true");
    label.textContent = text;
    return label;
}

function axis(value) {
    const characters = [...value];
    return flipped ? characters.reverse() : characters;
}

function squareButton(square) {
    return board.querySelector(`[data-square="${square}"]`);
}

function renderBoard() {
    const restoreFocus = board.contains(document.activeElement);
    const columns = axis(files);
    const rows = axis(ranks);
    const grid = document.createDocumentFragment();
    board.setAttribute("aria-label", `Chessboard, ${flipped ? "Black" : "White"} at bottom`);
    for (const [rowIndex, rank] of rows.entries()) {
        const row = document.createElement("div");
        row.className = "board-row";
        row.setAttribute("role", "row");
        for (const [columnIndex, file] of columns.entries()) {
            const square = file + rank;
            const symbol = game?.pieces[square];
            const legal = destinations.find((entry) => entry.square === square);
            const cell = document.createElement("div");
            cell.setAttribute("role", "gridcell");
            cell.setAttribute("aria-selected", String(square === selected));
            const button = document.createElement("button");
            button.type = "button";
            button.dataset.square = square;
            button.tabIndex = square === focusSquare ? 0 : -1;
            const light = (files.indexOf(file) + Number(rank)) % 2 === 0;
            button.className = `square ${light ? "light" : "dark"}`;
            button.classList.toggle("selected", square === selected);
            button.classList.toggle("last-move", game?.last_move.includes(square) ?? false);
            button.classList.toggle("in-check", square === game?.checked_king);
            button.classList.toggle("legal", Boolean(legal));
            button.classList.toggle("capture", Boolean(legal?.capture));
            const description = symbol ? `${colorOf(symbol)} ${names[symbol.toLowerCase()]}` : "empty";
            const action = legal ? (legal.capture ? ", legal capture" : ", legal move") : "";
            const check = square === game?.checked_king ? ", in check" : "";
            button.setAttribute("aria-label", `${square}, ${description}${action}${check}`);
            if (columnIndex === 0) button.append(coordinate(rank, "rank"));
            if (rowIndex === 7) button.append(coordinate(file, "file"));
            if (symbol) button.append(pieceImage(symbol));
            cell.append(button);
            row.append(cell);
        }
        grid.append(row);
    }
    board.replaceChildren(grid);
    if (restoreFocus) squareButton(focusSquare)?.focus({preventScroll: true});
}

function renderPlayer(element, color) {
    const label = document.createElement("span");
    label.className = `player-color ${color}`;
    label.setAttribute("aria-hidden", "true");
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = game?.names[color] || (color === "white" ? "Player 1" : "Player 2");
    const active = game && !game.result && game.turn === color;
    const side = document.createElement("span");
    side.className = "player-side";
    side.textContent = color;
    info.append(name);
    if (name.textContent.toLowerCase() !== color) info.append(side);
    element.replaceChildren(label, info);
    element.classList.toggle("active", Boolean(active));
    if (active) {
        const indicator = document.createElement("span");
        indicator.className = "player-turn";
        indicator.textContent = "To move";
        element.append(indicator);
    }
}

function renderMoves() {
    const fragment = document.createDocumentFragment();
    for (const [index, move] of game.moves.entries()) {
        const row = document.createElement("tr");
        row.setAttribute("role", "row");
        const number = document.createElement("th");
        number.scope = "row";
        number.setAttribute("role", "rowheader");
        number.textContent = move.number;
        row.append(number);
        for (const [column, text] of Object.entries({
            "move-player": `${game.names[move.color]} · ${capitalize(move.color)}`,
            description: move.description,
            san: move.san,
        })) {
            const cell = document.createElement("td");
            cell.className = column;
            cell.setAttribute("role", "cell");
            cell.textContent = text;
            row.append(cell);
        }
        if (index === game.moves.length - 1) row.className = "latest";
        fragment.append(row);
    }
    byId("moves").replaceChildren(fragment);
    byId("empty-history").hidden = game.moves.length > 0;
    const scroll = byId("notation-scroll");
    scroll.scrollTop = scroll.scrollHeight;
}

function renderControls() {
    const unavailable = !game || !synchronized || busy;
    byId("new-game").disabled = unavailable;
    byId("flip").disabled = !game || busy;
    byId("flip").setAttribute("aria-pressed", String(flipped));
    byId("resign").disabled = unavailable || Boolean(game?.result);
    byId("view-result").hidden = !game?.result;
    byId("view-result").disabled = busy;
    byId("offer-draw").disabled = unavailable || !game?.can_offer_draw;
    byId("offer-draw").hidden = Boolean(game?.result || game?.draw_offer);
    byId("draw-offer").hidden = !game?.draw_offer;
    byId("accept-draw").disabled = unavailable;
    byId("decline-draw").disabled = unavailable;
    const offerMessage = game?.draw_offer
        ? `${game.names[game.draw_offer]} offers a draw. ${game.names[game.turn]} can accept, decline, or play on.`
        : "";
    if (byId("draw-offer-message").textContent !== offerMessage) {
        byId("draw-offer-message").textContent = offerMessage;
    }
    const canDraw = Boolean(game && (game.draw.now.length || game.draw.intended.length));
    byId("draw").hidden = !canDraw;
    byId("draw").disabled = unavailable || !canDraw;
    byId("retry").hidden = synchronized || busy;
    board.setAttribute("aria-busy", String(busy));
}

function render() {
    renderBoard();
    renderPlayer(byId("far-player"), flipped ? "white" : "black");
    renderPlayer(byId("near-player"), flipped ? "black" : "white");
    if (game) {
        const turnText = game.result ? "Game over" : `${game.names[game.turn]} to move`;
        const turn = byId("turn");
        const status = byId("status");
        if (turn.textContent !== turnText) turn.textContent = turnText;
        if (status.textContent !== game.status) status.textContent = game.status;
        status.classList.toggle("emphasis", Boolean(game.checked_king || game.result));
        byId("turn-swatch").className = `turn-swatch ${game.result ? "finished" : game.turn}`;
        renderMoves();
    }
    renderControls();
}

function clearSelection() {
    selectionRequest++;
    selected = null;
    destinations = [];
    setLegalRetry();
}

async function loadCurrent(createIfEmpty = false) {
    try {
        let response = await request("/api/games/current");
        if (!response.game && createIfEmpty) {
            try {
                response = await request("/api/games", {previous_id: null});
            } catch (error) {
                if (error instanceof RequestError && error.status !== 409 && error.status < 500) throw error;
                // Another tab may have opened the first match, or this tab
                // may have lost the response after the new match was saved.
                response = await request("/api/games/current");
            }
        }
        if (!response.game) throw new Error("No match was loaded.");
        game = response.game;
        synchronized = true;
        clearSelection();
        render();
        return true;
    } catch {
        synchronized = false;
        if (!game) byId("turn").textContent = "Match unavailable";
        showNotice("Could not load the saved match. Use Reload position to try again.");
        renderControls();
        return false;
    }
}

async function animateMove(effects) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const surface = board.getBoundingClientRect();
    const movingPieces = [];
    const animations = [];
    const settings = {duration: 215, easing: "ease-in-out", fill: "forwards"};

    // Animate the pieces from the old position. A promoting pawn stays a
    // pawn while it slides, and en passant fades the actual captured square.
    for (const {from, to} of effects.slides) {
        const original = squareButton(from).querySelector("img");
        if (!original) continue;
        const ghost = original.cloneNode();
        const start = original.getBoundingClientRect();
        const origin = squareButton(from).getBoundingClientRect();
        const target = squareButton(to).getBoundingClientRect();
        ghost.className = "sliding-piece";
        Object.assign(ghost.style, {
            left: `${start.x - surface.x}px`,
            top: `${start.y - surface.y}px`,
            width: `${start.width}px`,
            height: `${start.height}px`,
        });
        original.style.visibility = "hidden";
        board.append(ghost);
        movingPieces.push({ghost, original});
        animations.push(ghost.animate([
            {transform: "translate(0, 0)"},
            {transform: `translate(${target.x - origin.x}px, ${target.y - origin.y}px)`},
        ], settings));
    }
    const captured = effects.capture && squareButton(effects.capture).querySelector("img");
    if (captured) animations.push(captured.animate([{opacity: 1}, {opacity: 0}], settings));
    await Promise.allSettled(animations.map((animation) => animation.finished));
    for (const {ghost, original} of movingPieces) {
        ghost.remove();
        original.style.visibility = "";
    }
    for (const animation of animations) animation.cancel();
}

async function mutate(path, data, animate = false) {
    if (busy || !synchronized) return;
    busy = true;
    showNotice();
    clearSelection();
    renderBoard();
    renderControls();
    try {
        const response = await request(path, {...data, version: game.version});
        if (animate) await animateMove(response.game.moves.at(-1).effects);
        game = response.game;
        render();
    } catch (error) {
        if (error instanceof RequestError && error.status < 500 && error.status !== 409) {
            showNotice(error.message);
        } else {
            // A timeout can follow a successful commit. Read the saved version
            // before unlocking input and never automatically retry a mutation.
            synchronized = false;
            if (await loadCurrent()) {
                showNotice(error.status === 409 ? error.message
                    : "The response was interrupted. The board shows the saved position.");
            }
        }
    } finally {
        busy = false;
        renderControls();
    }
    maybeShowResult();
}

function gamePath(action) {
    return `/api/games/${game.id}/${action}`;
}

async function chooseSquare(square) {
    if (busy || !synchronized || !game || game.result) return;
    const move = destinations.find((entry) => entry.square === square);
    if (move) {
        const uci = selected + square;
        if (move.promotions.length) openPromotion(uci, move.promotions);
        else await mutate(gamePath("move"), {uci}, true);
        return;
    }
    const toggleOff = square === selected;
    clearSelection();
    showNotice();
    const symbol = game.pieces[square];
    if (toggleOff || !symbol) {
        renderBoard();
        return;
    }
    if (colorOf(symbol) !== game.turn) {
        showNotice(`It is ${game.names[game.turn]}’s turn (${capitalize(game.turn)}).`);
        renderBoard();
        return;
    }
    await loadLegalMoves(square);
}

async function loadLegalMoves(square) {
    clearSelection();
    selected = square;
    const thisRequest = selectionRequest;
    showNotice();
    renderBoard();
    try {
        const response = await request(`${gamePath("legal")}?square=${square}&version=${game.version}`);
        if (thisRequest !== selectionRequest) return;
        if (response.selection === "finished") {
            game = response.game;
            clearSelection();
            render();
            maybeShowResult();
            return;
        }
        if (!Array.isArray(response.destinations)) {
            throw new Error("The server did not return a list of legal moves.");
        }
        destinations = response.destinations;
        if (response.selection === "blocked") showNotice("This piece has no legal moves. Select another piece.");
        renderBoard();
    } catch (error) {
        if (thisRequest !== selectionRequest) return;
        if (error.status === 409) {
            synchronized = false;
            renderControls();
            if (await loadCurrent()) {
                showNotice(error.message);
                maybeShowResult();
            }
        } else {
            // Keep the match and selected square visible. Failure tells us
            // nothing about the move list or whether the game has ended.
            showNotice("Could not load legal moves. Retry to check this piece.");
            setLegalRetry(square);
        }
    }
}

function capitalize(word) {
    return word[0].toUpperCase() + word.slice(1);
}

function showResult() {
    const result = game.result_details;
    const dialog = byId("result-dialog");
    if (!result || dialog.open) return;
    const player = (entry) => `${entry.name} (${capitalize(entry.color)})`;
    byId("result-title").textContent = result.winner ? "Game over" : "Game drawn";
    byId("result-players").textContent = result.winner
        ? `${player(result.winner)} wins — ${player(result.loser)} loses.`
        : "Neither player wins.";
    byId("result-score").textContent = result.score;
    byId("result-score").setAttribute("aria-label", `Score ${result.score}`);
    byId("result-reason").textContent = result.explanation;
    presentedResultId = game.id;
    dialog.showModal();
}

function maybeShowResult() {
    if (game?.result && game.id !== presentedResultId) showResult();
}

function openPromotion(uci, options) {
    const dialog = byId("promotion-dialog");
    const buttons = options.map((piece) => {
        const button = document.createElement("button");
        button.type = "button";
        const label = document.createElement("span");
        label.textContent = names[piece];
        button.append(pieceImage(game.turn === "white" ? piece.toUpperCase() : piece), label);
        button.addEventListener("click", () => {
            dialog.close();
            void mutate(gamePath("move"), {uci: uci + piece}, true);
        });
        return button;
    });
    byId("promotion-options").replaceChildren(...buttons);
    dialog.showModal();
}

board.addEventListener("click", (event) => {
    const button = event.target.closest("[data-square]");
    if (!button) return;
    focusSquare = button.dataset.square;
    void chooseSquare(focusSquare);
});

board.addEventListener("keydown", (event) => {
    if (busy && event.key === "Escape") return;
    const deltas = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
    const delta = deltas[event.key];
    const from = event.target.dataset.square;
    if (event.key === "Escape") {
        clearSelection();
        renderBoard();
    }
    if (!delta || !from) return;
    event.preventDefault();
    const columns = axis(files);
    const rows = axis(ranks);
    const file = columns[columns.indexOf(from[0]) + delta[0]];
    const rank = rows[rows.indexOf(from[1]) + delta[1]];
    if (!file || !rank) return;
    event.target.tabIndex = -1;
    focusSquare = file + rank;
    const next = squareButton(focusSquare);
    next.tabIndex = 0;
    next.focus();
});

byId("flip").addEventListener("click", () => {
    flipped = !flipped;
    render();
});

byId("new-game").addEventListener("click", () => {
    const form = byId("game-form");
    form.elements.white.value = game.names.white;
    form.elements.black.value = game.names.black;
    byId("replacement-warning").hidden = Boolean(game.result);
    byId("game-dialog").showModal();
});

byId("retry-legal").addEventListener("click", () => {
    if (retrySquare && synchronized && !busy) {
        squareButton(retrySquare)?.focus({preventScroll: true});
        void loadLegalMoves(retrySquare);
    }
});

byId("offer-draw").addEventListener("click", () => {
    const offerer = game.moves.at(-1).color;
    byId("offer-message").textContent = `${game.names[offerer]} (${capitalize(offerer)}), offer your opponent a draw?`;
    byId("offer-dialog").showModal();
});

byId("offer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    byId("offer-dialog").close();
    void mutate(gamePath("draw/offer"), {});
});

byId("accept-draw").addEventListener("click", () => void mutate(gamePath("draw/respond"), {accept: true}));
byId("decline-draw").addEventListener("click", () => void mutate(gamePath("draw/respond"), {accept: false}));

byId("view-result").addEventListener("click", showResult);
byId("review-board").addEventListener("click", () => {
    byId("result-dialog").close();
    squareButton(game.last_move.at(-1) || focusSquare)?.focus({preventScroll: true});
});
byId("start-again").addEventListener("click", () => {
    byId("result-dialog").close();
    void mutate("/api/games", {...game.names, previous_id: game.id});
});

byId("game-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    byId("game-dialog").close();
    void mutate("/api/games", {white: form.get("white"), black: form.get("black"), previous_id: game.id});
});

byId("resign").addEventListener("click", () => {
    byId("resign-message").textContent = `${game.names[game.turn]}, are you sure you want to resign?`;
    byId("resign-dialog").showModal();
});

byId("resign-form").addEventListener("submit", (event) => {
    event.preventDefault();
    byId("resign-dialog").close();
    void mutate(gamePath("resign"), {});
});

byId("draw").addEventListener("click", () => {
    const options = [];
    const describe = (reasons) => reasons.map((reason) => drawReasons[reason]).join(" or ");
    if (game.draw.now.length) options.push(new Option(`Current position · ${describe(game.draw.now)}`, ""));
    for (const move of game.draw.intended) {
        options.push(new Option(`${move.san} · ${describe(move.reasons)}`, move.uci));
    }
    byId("draw-options").replaceChildren(...options);
    byId("draw-dialog").showModal();
});

byId("draw-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const uci = byId("draw-options").value || null;
    byId("draw-dialog").close();
    void mutate(gamePath("draw"), {uci});
});

document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
});

async function reload() {
    busy = true;
    showNotice();
    renderControls();
    await loadCurrent(true);
    busy = false;
    renderControls();
    maybeShowResult();
}

byId("retry").addEventListener("click", reload);
render();
void reload();
