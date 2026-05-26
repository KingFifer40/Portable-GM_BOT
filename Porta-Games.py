# =============================================================================
# Porta-Games.py  —  Game Engine for Porta-GMBOT
# =============================================================================
# This module contains ALL game logic for the bot.  Porta-GMBOT.py imports
# this module and delegates every game-related command here.
#
# Supported games (as of this version):
#   • Connect Four  — classic 6×7 drop game; #start c4 [easy|medium|hard]
#   • Tic-Tac-Toe  — 3×3 grid; #start ttt [easy|medium|hard]
#
# Adding a new game:
#   1. Add its state initializer to _fresh_game_session().
#   2. Write its logic (board render, move, win-check, AI).
#   3. Add its command handlers inside handle_game_command().
#   4. Add its enable flag to Porta-GMBOT.py (CONNECT4_ENABLED, etc.)
#      and wire it into the group-record / dispatch / snapshot machinery.
#   5. Document it in the #help game handler (also in Porta-GMBOT.py).
#
# Design rules:
#   • No game may start while another is active in the same group.
#   • All inter-module calls go through the injected helper functions
#     (send_fn, add_pts_fn, get_pts_fn, known_names_fn) so this file never
#     imports Porta-GMBOT directly (avoids circular imports).
#   • Points for games (win rewards, bets) are handled here but deducted /
#     credited through the injected helpers from the main file.
# =============================================================================

import os
import random
import time
import threading

# ---------------------------------------------------------------------------
# Injected helpers — set by Porta-GMBOT.py at startup via register_helpers()
# ---------------------------------------------------------------------------
_send_fn        = None   # send_fn(group_id, text, reply_to_id=None)
_send_typing_fn = None   # send_typing_fn(group_id)
_add_pts_fn     = None   # add_pts_fn(group_id, uid, name, delta) → new_bal
_get_pts_fn     = None   # get_pts_fn(group_id, uid, name) → balance
_transfer_pts_fn = None  # transfer_pts_fn(group_id, from_id, from_name, to_id, to_name, amount) → (taken, from_new, to_new)
_known_names_fn  = None  # known_names_fn() → dict {uid: display_name}


def register_helpers(send_fn, send_typing_fn, add_pts_fn, get_pts_fn,
                     transfer_pts_fn, known_names_fn):
    """
    Called once by Porta-GMBOT.py to wire up the inter-module helpers.
    Must be called before any game command is processed.
    """
    global _send_fn, _send_typing_fn, _add_pts_fn, _get_pts_fn
    global _transfer_pts_fn, _known_names_fn
    _send_fn         = send_fn
    _send_typing_fn  = send_typing_fn
    _add_pts_fn      = add_pts_fn
    _get_pts_fn      = get_pts_fn
    _transfer_pts_fn = transfer_pts_fn
    _known_names_fn  = known_names_fn


def _send(gid, text, reply_to_id=None):
    _send_fn(gid, text, reply_to_id=reply_to_id)

def _typing(gid):
    _send_typing_fn(gid)

def _add_pts(gid, uid, name, delta):
    return _add_pts_fn(gid, uid, name, delta)

def _get_pts(gid, uid, name):
    return _get_pts_fn(gid, uid, name)

def _transfer(gid, from_id, from_name, to_id, to_name, amount):
    return _transfer_pts_fn(gid, from_id, from_name, to_id, to_name, amount)

def _name(uid):
    """Look up a display name from the known-names registry."""
    return _known_names_fn().get(str(uid), str(uid))


# =============================================================================
# GAME SESSION STATE
# =============================================================================
# Each group has one game_session dict.  Only one game may be active at a time.
# Porta-GMBOT stores this inside the per-group record as "game_session".

def fresh_game_session():
    """
    Return a brand-new, inactive game session dict.
    All game sub-states live as top-level keys here.
    """
    return {
        # ── Active game marker ─────────────────────────────────────────────
        "active_game": None,      # None | "connect4" | "tictactoe" | "chess"
        "last_move_time": None,   # float — for timeout tracking
        "timeout_seconds": 300,

        # ── Connect Four state ─────────────────────────────────────────────
        "c4": _fresh_c4(),

        # ── Tic-Tac-Toe state ─────────────────────────────────────────────
        "ttt": _fresh_ttt(),

        # ── Chess state ────────────────────────────────────────────────────
        "chess": _fresh_chess(),
    }


def any_game_active(session):
    """Return True if any game is currently running in this session."""
    return session.get("active_game") is not None


# =============================================================================
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  CONNECT FOUR                                                           │
# └─────────────────────────────────────────────────────────────────────────┘
# =============================================================================

# Piece symbols
C4_EMPTY    = "⚫"
C4_P1       = "🔴"
C4_P2       = "🟡"
C4_AI_PIECE = "🟢"

# Column header emojis (replaces A–G letters so the board aligns in GroupMe)
_C4_COL_EMOJIS = ["🔵", "🟠", "🟤", "🟣", "🔶", "🔷", "🟥"]
_C4_EMOJI_TO_COL = {e: i for i, e in enumerate(_C4_COL_EMOJIS)}
_C4_COL_KEY = "  ".join(f"{e}={chr(65+i)}" for i, e in enumerate(_C4_COL_EMOJIS))

# Point rewards for beating the Connect Four AI (imported from main at runtime)
# These are read from the injected _c4_rewards dict set by Porta-GMBOT.
_c4_rewards = {"easy": 50, "medium": 125, "hard": 200}

def set_c4_rewards(easy, medium, hard):
    """Called by Porta-GMBOT.py to sync point reward constants."""
    _c4_rewards["easy"]   = easy
    _c4_rewards["medium"] = medium
    _c4_rewards["hard"]   = hard


def _fresh_c4():
    return {
        "board": None,
        "players": {},          # {uid: {"name": str, "symbol": str}}
        "turn_order": [],
        "current_turn": 0,
        "ai_difficulty": "medium",
        "pvp_bets": {},
        "pvp_bet_locked": False,
        "spectator_bets": {},
    }


def _c4_init_board():
    return [[C4_EMPTY for _ in range(7)] for _ in range(6)]


def _c4_board_text(board):
    header = "".join(_C4_COL_EMOJIS)
    rows = [header] + ["".join(board[r]) for r in range(6)] + [_C4_COL_KEY]
    return "\n".join(rows)


def _c4_col_from_cmd(raw):
    """Parse a column letter A-G or emoji; returns 0-6 index or None."""
    if raw in _C4_EMOJI_TO_COL:
        return _C4_EMOJI_TO_COL[raw]
    mapping = {"A":0,"B":1,"C":2,"D":3,"E":4,"F":5,"G":6}
    return mapping.get(raw.upper())


def _c4_drop(board, col_idx, symbol):
    for row in range(5, -1, -1):
        if board[row][col_idx] == C4_EMPTY:
            board[row][col_idx] = symbol
            return row, col_idx
    return None, None


def _c4_check_winner(board, symbol):
    rows, cols = 6, 7
    for r in range(rows):
        for c in range(cols-3):
            if all(board[r][c+i] == symbol for i in range(4)): return True
    for c in range(cols):
        for r in range(rows-3):
            if all(board[r+i][c] == symbol for i in range(4)): return True
    for r in range(rows-3):
        for c in range(cols-3):
            if all(board[r+i][c+i] == symbol for i in range(4)): return True
    for r in range(3, rows):
        for c in range(cols-3):
            if all(board[r-i][c+i] == symbol for i in range(4)): return True
    return False


def _c4_board_full(board):
    return all(board[0][c] != C4_EMPTY for c in range(7))


# ── Connect Four Minimax AI ────────────────────────────────────────────────

def _c4_valid_moves(board):
    return [c for c in range(7) if board[0][c] == C4_EMPTY]

def _c4_temp_move(board, col, piece):
    temp = [row[:] for row in board]
    for r in range(5, -1, -1):
        if temp[r][col] == C4_EMPTY:
            temp[r][col] = piece
            return temp
    return None

def _c4_count_window(window, piece, opp):
    score = 0
    if window.count(piece) == 4:              score += 100000
    elif window.count(piece) == 3 and window.count(C4_EMPTY) == 1: score += 1000
    elif window.count(piece) == 2 and window.count(C4_EMPTY) == 2: score += 50
    if window.count(opp) == 3 and window.count(C4_EMPTY) == 1:     score -= 1200
    return score

def _c4_score_pos(board, piece):
    opp = C4_P1 if piece != C4_P1 else C4_AI_PIECE
    score = 0
    center = [board[r][3] for r in range(6)]
    score += center.count(piece) * 6
    for r in range(6):
        for c in range(4):
            score += _c4_count_window(board[r][c:c+4], piece, opp)
    for c in range(7):
        col_arr = [board[r][c] for r in range(6)]
        for r in range(3):
            score += _c4_count_window(col_arr[r:r+4], piece, opp)
    for r in range(3):
        for c in range(4):
            score += _c4_count_window([board[r+i][c+i] for i in range(4)], piece, opp)
    for r in range(3, 6):
        for c in range(4):
            score += _c4_count_window([board[r-i][c+i] for i in range(4)], piece, opp)
    return score

def _c4_is_terminal(board, ai_p, human_p):
    return _c4_check_winner(board, ai_p) or _c4_check_winner(board, human_p) or _c4_board_full(board)

def _c4_minimax(board, depth, alpha, beta, maximizing, ai_p, human_p):
    if depth == 0 or _c4_is_terminal(board, ai_p, human_p):
        if _c4_check_winner(board, ai_p):   return None, 100_000_000
        if _c4_check_winner(board, human_p): return None, -100_000_000
        if _c4_board_full(board):            return None, 0
        return None, _c4_score_pos(board, ai_p)
    valid = sorted(_c4_valid_moves(board), key=lambda c: abs(3-c))
    if maximizing:
        best, best_col = -10**12, random.choice(valid)
        for col in valid:
            tmp = _c4_temp_move(board, col, ai_p)
            _, s = _c4_minimax(tmp, depth-1, alpha, beta, False, ai_p, human_p)
            if s > best: best, best_col = s, col
            alpha = max(alpha, s)
            if alpha >= beta: break
        return best_col, best
    else:
        best, best_col = 10**12, random.choice(valid)
        for col in valid:
            tmp = _c4_temp_move(board, col, human_p)
            _, s = _c4_minimax(tmp, depth-1, alpha, beta, True, ai_p, human_p)
            if s < best: best, best_col = s, col
            beta = min(beta, s)
            if alpha >= beta: break
        return best_col, best

def _c4_ai_choose(board, ai_p, human_p, difficulty):
    depth_map = {"easy":2, "medium":5, "hard":9}
    depth = depth_map.get(difficulty, 5)
    if difficulty == "easy" and random.random() < 0.40:
        valid = _c4_valid_moves(board)
        return random.choice(valid) if valid else 0
    col, _ = _c4_minimax(board, depth, -10**12, 10**12, True, ai_p, human_p)
    return col


# ── Connect Four bet helpers ───────────────────────────────────────────────

def _c4_refund_all_bets(gid, session):
    c4 = session["c4"]
    lines = []
    for uid, amt in c4["pvp_bets"].items():
        if amt > 0:
            name = c4["players"].get(uid, {}).get("name", uid)
            bal = _add_pts(gid, uid, name, amt)
            lines.append(f"  {name}: +{amt} pts refunded ({bal} pts)")
    for uid, bdata in c4["spectator_bets"].items():
        amt = bdata["amount"]
        name = bdata["bettor_name"]
        bal = _add_pts(gid, uid, name, amt)
        lines.append(f"  {name}: +{amt} pts refunded ({bal} pts)")
    return lines

def _c4_settle_spectator_bets(gid, session, winner_id):
    c4 = session["c4"]
    lines = []
    if not c4["spectator_bets"]:
        return lines
    winning, losing = {}, {}
    for uid, bdata in c4["spectator_bets"].items():
        if str(bdata["on"]) == str(winner_id):
            winning[uid] = bdata
        else:
            losing[uid] = bdata
    lines.append("👥 Spectator Results:")
    w_stake = sum(b["amount"] for b in winning.values())
    l_stake = sum(b["amount"] for b in losing.values())
    if not winning:
        for uid, b in losing.items():
            bal = _add_pts(gid, uid, b["bettor_name"], b["amount"])
            lines.append(f"  🔄 {b['bettor_name']} refunded {b['amount']} pts (no one bet on winner). ({bal} pts)")
        return lines
    if not losing:
        for uid, b in winning.items():
            bal = _add_pts(gid, uid, b["bettor_name"], b["amount"])
            lines.append(f"  🔄 {b['bettor_name']} refunded {b['amount']} pts (no opposing bets). ({bal} pts)")
        return lines
    for uid, b in winning.items():
        share = round(b["amount"] / w_stake * l_stake)
        payout = b["amount"] + share
        bal = _add_pts(gid, uid, b["bettor_name"], payout)
        lines.append(f"  🎉 {b['bettor_name']} wins {share} pts profit! (Payout: {payout} pts, balance: {bal} pts)")
    for uid, b in losing.items():
        lines.append(f"  😔 {b['bettor_name']} loses {b['amount']} pts.")
    return lines

def _c4_reset(session):
    session["c4"] = _fresh_c4()
    session["active_game"] = None
    session["last_move_time"] = None


# ── Connect Four command handlers ──────────────────────────────────────────

def c4_start(gid, session, sender_id, sender_name, difficulty, msg_id, enabled):
    """#start c4 [easy|medium|hard]"""
    if not enabled:
        _send(gid, "🎮 Connect Four is currently disabled.", reply_to_id=msg_id)
        return
    if any_game_active(session):
        active = session["active_game"]
        _send(gid, f"A game of {active} is already running. Use #quit to end it first.", reply_to_id=msg_id)
        return
    diff = difficulty if difficulty in ("easy","medium","hard") else "medium"
    session["active_game"] = "connect4"
    session["last_move_time"] = time.time()
    session["timeout_seconds"] = session.get("timeout_seconds", 300)
    c4 = _fresh_c4()
    c4["board"] = _c4_init_board()
    c4["players"][sender_id] = {"name": sender_name, "symbol": C4_P1}
    c4["turn_order"] = [sender_id]
    c4["current_turn"] = 0
    c4["ai_difficulty"] = diff
    session["c4"] = c4
    _send(
        gid,
        f"🎮 {sender_name} started Connect Four! (AI difficulty: {diff})\n"
        f"Use #join to play PvP, or #addai to play vs the AI.\n\n"
        + _c4_board_text(c4["board"]),
        reply_to_id=msg_id,
    )


def c4_join(gid, session, sender_id, sender_name, msg_id, enabled):
    """#join — join as Player 2"""
    if not enabled:
        _send(gid, "🎮 Connect Four is currently disabled.", reply_to_id=msg_id); return
    c4 = session["c4"]
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game. Use #start c4 to begin.", reply_to_id=msg_id); return
    if sender_id in c4["players"]:
        _send(gid, "You are already in this game.", reply_to_id=msg_id); return
    if len(c4["players"]) >= 2:
        _send(gid, "The game already has two players.", reply_to_id=msg_id); return
    c4["players"][sender_id] = {"name": sender_name, "symbol": C4_P2}
    c4["turn_order"].append(sender_id)
    session["last_move_time"] = time.time()
    p1_id = c4["turn_order"][0]
    p1_name = c4["players"][p1_id]["name"]
    _send(
        gid,
        f"⚔️ {sender_name} joined as Player 2!\n"
        f"{p1_name} 🔴 vs {sender_name} 🟡\n\n"
        f"💰 *PvP Betting:* Use #pvpbet <amount> before the game starts, or #pvpbet 0 to skip.\n"
        f"Both players must bet (or skip) before play begins.\n"
        f"Spectators: use #bet <amount> @player to wager!\n\n"
        + _c4_board_text(c4["board"]),
        reply_to_id=msg_id,
    )


def c4_addai(gid, session, sender_id, sender_name, difficulty, msg_id, enabled):
    """#addai [easy|medium|hard]"""
    if not enabled:
        _send(gid, "🎮 Connect Four is currently disabled.", reply_to_id=msg_id); return
    c4 = session["c4"]
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game. Use #start c4 first.", reply_to_id=msg_id); return
    if len(c4["players"]) >= 2:
        _send(gid, "A second player already joined.", reply_to_id=msg_id); return
    if difficulty in ("easy","medium","hard"):
        c4["ai_difficulty"] = difficulty
    diff = c4["ai_difficulty"]
    c4["players"]["AI"] = {"name": "AI", "symbol": C4_AI_PIECE}
    c4["turn_order"].append("AI")
    c4["pvp_bet_locked"] = True
    session["last_move_time"] = time.time()
    p1_name = c4["players"][c4["turn_order"][0]]["name"]
    reward = _c4_rewards.get(diff, 125)
    _send(
        gid,
        f"🟢 AI joined as Player 2 ({diff.capitalize()} difficulty).\n"
        f"{p1_name} 🔴 vs AI 🟢\n"
        f"Beat the AI on {diff} to earn {reward} pts!\n\n"
        + _c4_board_text(c4["board"]),
        reply_to_id=msg_id,
    )


def c4_quit(gid, session, sender_name, msg_id):
    """#quit — end the Connect Four game"""
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game.", reply_to_id=msg_id); return
    refunds = _c4_refund_all_bets(gid, session)
    _c4_reset(session)
    parts = [f"🚫 Connect Four ended by {sender_name}."]
    if refunds:
        parts.append("💰 Bets refunded:\n" + "\n".join(refunds))
    _send(gid, "\n".join(parts), reply_to_id=msg_id)


def c4_column_move(gid, session, sender_id, sender_name, col_raw, msg_id, enabled):
    """Handle a column drop move (letter A-G or column emoji)."""
    col_idx = _c4_col_from_cmd(col_raw)
    if col_idx is None:
        return False   # not a C4 move

    if not enabled:
        _send(gid, "🎮 Connect Four is currently disabled.", reply_to_id=msg_id); return True
    c4 = session["c4"]
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game. Use #start c4 to begin.", reply_to_id=msg_id); return True
    if len(c4["players"]) < 2:
        _send(gid, "Waiting for a second player — use #join or #addai.", reply_to_id=msg_id); return True
    is_pvp = "AI" not in c4["players"]
    if is_pvp and not c4["pvp_bet_locked"]:
        _send(gid, "⏳ Waiting for both players to set their bet.\nUse #pvpbet <amount> or #pvpbet 0 to skip.", reply_to_id=msg_id); return True

    cur_uid = c4["turn_order"][c4["current_turn"]]
    if sender_id != cur_uid:
        cur_name = c4["players"][cur_uid]["name"]
        _send(gid, f"It is {cur_name}'s turn.", reply_to_id=msg_id); return True

    sym = c4["players"][sender_id]["symbol"]
    row, _ = _c4_drop(c4["board"], col_idx, sym)
    if row is None:
        _send(gid, "That column is full. Choose another.", reply_to_id=msg_id); return True
    session["last_move_time"] = time.time()

    # Check win
    if _c4_check_winner(c4["board"], sym):
        board_text = _c4_board_text(c4["board"])
        opp_id = next((p for p in c4["turn_order"] if p != sender_id and p != "AI"), None)
        if opp_id:
            # PvP win
            opp_name = c4["players"][opp_id]["name"]
            lines = [f"🏆 {sender_name} wins Connect Four!"]
            w_bet = c4["pvp_bets"].get(str(sender_id), 0)
            l_bet = c4["pvp_bets"].get(str(opp_id), 0)
            pot = w_bet + l_bet
            if pot > 0:
                bal = _add_pts(gid, sender_id, sender_name, pot)
                if w_bet > 0 and l_bet > 0:
                    lines.append(f"💰 {sender_name} wins {l_bet} pts from {opp_name} + gets {w_bet} pts back! ({bal} pts)")
                elif w_bet > 0:
                    lines.append(f"💰 {sender_name} gets their {w_bet} pts back. ({bal} pts)")
                else:
                    lines.append(f"💰 {sender_name} wins {l_bet} pts from {opp_name}! ({bal} pts)")
            lines.extend(_c4_settle_spectator_bets(gid, session, str(sender_id)))
            _send(gid, "\n".join(lines) + f"\n\n{board_text}", reply_to_id=msg_id)
        else:
            # vs AI win
            diff = c4["ai_difficulty"]
            reward = _c4_rewards.get(diff, 125)
            bal = _add_pts(gid, sender_id, sender_name, reward)
            _send(gid, f"🏆 {sender_name} beats the AI ({diff.capitalize()})!\nEarned {reward} pts! ({bal} pts)\n\n{board_text}", reply_to_id=msg_id)
        _c4_reset(session)
        return True

    # Draw
    if _c4_board_full(c4["board"]):
        board_text = _c4_board_text(c4["board"])
        refunds = _c4_refund_all_bets(gid, session)
        msg = f"🤝 Connect Four draw!\n\n{board_text}"
        if refunds:
            msg += "\n💰 Bets refunded:\n" + "\n".join(refunds)
        _send(gid, msg, reply_to_id=msg_id)
        _c4_reset(session)
        return True

    # Next turn
    c4["current_turn"] = (c4["current_turn"] + 1) % len(c4["turn_order"])
    next_uid = c4["turn_order"][c4["current_turn"]]

    if next_uid == "AI":
        _send(gid, "🤖 AI is thinking...")
        stop = threading.Event()
        def _typing_loop():
            while not stop.is_set():
                _typing(gid); time.sleep(2)
        threading.Thread(target=_typing_loop, daemon=True).start()

        diff = c4["ai_difficulty"]
        ai_col = _c4_ai_choose(c4["board"], C4_AI_PIECE, C4_P1, diff)
        stop.set()
        _c4_drop(c4["board"], ai_col, C4_AI_PIECE)
        session["last_move_time"] = time.time()
        board_text = _c4_board_text(c4["board"])
        col_label = chr(ai_col + 65)

        if _c4_check_winner(c4["board"], C4_AI_PIECE):
            _send(gid, f"🟢 AI plays column {col_label}. AI wins!\nBetter luck next time — no points lost.\n\n{board_text}", reply_to_id=msg_id)
            _c4_reset(session); return True
        if _c4_board_full(c4["board"]):
            _send(gid, f"🟢 AI plays column {col_label}. Draw!\n\n{board_text}", reply_to_id=msg_id)
            _c4_reset(session); return True
        c4["current_turn"] = 0
        _send(gid, f"🟢 AI plays column {col_label}.\nYour turn, {sender_name}!\n\n{board_text}", reply_to_id=msg_id)
        return True

    next_name = c4["players"][next_uid]["name"]
    board_text = _c4_board_text(c4["board"])
    _send(gid, f"{sender_name} played column {col_raw.upper()}.\nIt is now {next_name}'s turn.\n\n{board_text}", reply_to_id=msg_id)
    return True


def c4_pvpbet(gid, session, sender_id, sender_name, parts, msg_id):
    """#pvpbet <amount>"""
    c4 = session["c4"]
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game.", reply_to_id=msg_id); return
    if sender_id not in c4["players"] or "AI" in c4["players"]:
        _send(gid, "💡 #pvpbet is for PvP players only.", reply_to_id=msg_id); return
    if c4["pvp_bet_locked"]:
        _send(gid, "Betting is locked — the game has already started!", reply_to_id=msg_id); return
    if len(c4["players"]) < 2:
        _send(gid, "Wait for a second player to #join before betting.", reply_to_id=msg_id); return
    if str(sender_id) in c4["pvp_bets"]:
        _send(gid, "You already set your bet.", reply_to_id=msg_id); return
    if len(parts) < 2:
        _send(gid, "Usage: #pvpbet <amount>  (use 0 to skip)", reply_to_id=msg_id); return
    try:
        bet_amt = int(parts[1])
        if bet_amt < 0: raise ValueError
    except ValueError:
        _send(gid, "Bet must be a whole number (0 or more).", reply_to_id=msg_id); return

    bal = _get_pts(gid, sender_id, sender_name)
    allin = False
    if bet_amt == 0:
        c4["pvp_bets"][str(sender_id)] = 0
        conf = f"✅ {sender_name} skipped betting."
    else:
        if bet_amt >= bal:
            bet_amt = bal; allin = True
        if bet_amt == 0:
            _send(gid, f"💸 {sender_name}, you have 0 pts — can't bet.", reply_to_id=msg_id); return
        _add_pts(gid, sender_id, sender_name, -bet_amt)
        c4["pvp_bets"][str(sender_id)] = bet_amt
        conf = f"{'🎰 ALL IN! ' if allin else '✅ '}{sender_name} wagered {bet_amt} pts!"
    _send(gid, conf, reply_to_id=msg_id)

    player_ids = [p for p in c4["turn_order"] if p != "AI"]
    if all(str(p) in c4["pvp_bets"] for p in player_ids):
        c4["pvp_bet_locked"] = True
        pot = sum(c4["pvp_bets"].values())
        pot_str = f"Total pot: {pot} pts. " if pot > 0 else ""
        _send(gid, f"🔒 Both players have bet. {pot_str}Game begins! 🎮\n\n" + _c4_board_text(c4["board"]))
    else:
        other = next((c4["players"][p]["name"] for p in player_ids if str(p) not in c4["pvp_bets"]), "other player")
        _send(gid, f"⏳ Waiting for {other} to #pvpbet.")


def c4_spectator_bet(gid, session, sender_id, sender_name, parts, message, msg_id):
    """#bet <amount> @player"""
    c4 = session["c4"]
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game to bet on.", reply_to_id=msg_id); return
    if sender_id in c4["players"]:
        _send(gid, "💡 As a player, use #pvpbet to bet on yourself.", reply_to_id=msg_id); return
    if len(c4["players"]) < 2:
        _send(gid, "Wait for both players to join before betting.", reply_to_id=msg_id); return
    if str(sender_id) in c4["spectator_bets"]:
        _send(gid, "You already have a bet. Use #quit to cancel.", reply_to_id=msg_id); return
    if len(parts) < 3:
        _send(gid, "Usage: #bet <amount> @player", reply_to_id=msg_id); return
    try:
        bet_amt = int(parts[1])
        if bet_amt <= 0: raise ValueError
    except ValueError:
        _send(gid, "Bet must be a positive whole number.", reply_to_id=msg_id); return

    mention_text = " ".join(parts[2:]).lstrip("@").strip().lower()
    target_id, target_name = None, None
    for pid, pdata in c4["players"].items():
        if pid == "AI": continue
        if pdata["name"].lower() == mention_text or mention_text in pdata["name"].lower():
            target_id, target_name = pid, pdata["name"]; break
    if target_id is None:
        for att in message.get("attachments", []):
            if att.get("type") == "mentions":
                for uid in att.get("user_ids", []):
                    if uid in c4["players"] and uid != "AI":
                        target_id, target_name = uid, c4["players"][uid]["name"]; break
    if target_id is None:
        _send(gid, "❌ Could not find that player. Try using @PlayerName.", reply_to_id=msg_id); return

    bal = _get_pts(gid, sender_id, sender_name)
    allin = False
    if bet_amt >= bal:
        bet_amt = bal; allin = True
    if bet_amt == 0:
        _send(gid, f"💸 {sender_name}, you have 0 pts — can't bet.", reply_to_id=msg_id); return
    _add_pts(gid, sender_id, sender_name, -bet_amt)
    c4["spectator_bets"][str(sender_id)] = {
        "amount": bet_amt, "on": target_id, "on_name": target_name, "bettor_name": sender_name
    }
    _send(gid, f"{'🎰 ALL IN! ' if allin else '🎲 '}{sender_name} bet {bet_amt} pts on {target_name}!", reply_to_id=msg_id)


def c4_stats(gid, session, msg_id):
    """#stats — show current game bets"""
    c4 = session["c4"]
    if session["active_game"] != "connect4":
        _send(gid, "No active Connect Four game.", reply_to_id=msg_id); return
    lines = ["📊 *Connect Four Game Stats*"]
    is_ai = "AI" in c4["players"]
    if is_ai:
        diff = c4["ai_difficulty"]
        reward = _c4_rewards.get(diff, 125)
        p1 = c4["players"][c4["turn_order"][0]]["name"]
        lines.append(f"🔴 {p1} vs 🟢 AI ({diff.capitalize()})")
        lines.append(f"Win reward: {reward} pts")
    else:
        for pid in c4["turn_order"]:
            pdata = c4["players"][pid]
            bet = c4["pvp_bets"].get(str(pid))
            bet_str = "⏳ betting..." if bet is None else ("no bet" if bet == 0 else f"{bet} pts wagered")
            lines.append(f"{pdata['symbol']} {pdata['name']}: {bet_str}")
    if c4["spectator_bets"]:
        lines.append("")
        lines.append("👥 Spectator Bets:")
        tally = {}
        for b in c4["spectator_bets"].values():
            tally[b["on_name"]] = tally.get(b["on_name"], 0) + b["amount"]
        for pname, total in tally.items():
            lines.append(f"  {pname}: {total} pts wagered by spectators")
    else:
        lines.append("No spectator bets yet.")
    _send(gid, "\n".join(lines), reply_to_id=msg_id)


# =============================================================================
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  TIC-TAC-TOE                                                            │
# └─────────────────────────────────────────────────────────────────────────┘
# Grid layout — coordinate system: column letter (A-C) + row number (1-3)
#
#      A   B   C
#   1  .   .   .
#   2  .   .   .
#   3  .   .   .
#
# Players type e.g. #B2 to play centre.
# Internally the board is a 3×3 list: board[row][col]  (both 0-based)
# =============================================================================

TTT_EMPTY = "⬜"
TTT_X     = "❌"
TTT_O     = "⭕"

_TTT_COL_LABELS = ["A", "B", "C"]
_TTT_ROW_LABELS = ["1", "2", "3"]


def _fresh_ttt():
    return {
        "board": None,            # 3×3 or None when not active
        "players": {},            # {uid: symbol}
        "turn_order": [],
        "current_turn": 0,
        "ai_difficulty": "impossible",
    }


def _ttt_coord_to_rc(col_letter, row_number):
    """
    Convert coordinate like ('B', '2') to (row_idx, col_idx).
    Returns (None, None) if invalid.
    """
    col_map = {"A": 0, "B": 1, "C": 2}
    row_map = {"1": 0, "2": 1, "3": 2}
    r = row_map.get(row_number)
    c = col_map.get(col_letter.upper())
    if r is None or c is None:
        return None, None
    return r, c


def _ttt_parse_cmd(cmd_raw):
    """
    Parse a command like '#B2' or '#b2' into (col_letter, row_number).
    Returns (None, None) if it doesn't match the pattern.
    """
    # cmd_raw should be like "#B2" (already lowered by caller)
    # We check original case before lower was applied by using the raw cmd
    inner = cmd_raw.lstrip("#").upper()
    if len(inner) == 2 and inner[0] in "ABC" and inner[1] in "123":
        return inner[0], inner[1]
    return None, None


def _ttt_init_board():
    return [[TTT_EMPTY]*3 for _ in range(3)]


def _ttt_board_text(board):
    """
    Render the 3×3 board with column headers A B C and row numbers 1 2 3.

       🇦  🇧  🇨
    1️⃣ ⬜  ⬜  ⬜
    2️⃣ ⬜  ⬜  ⬜
    3️⃣ ⬜  ⬜  ⬜
    """
    col_header = "    🇦  🇧  🇨"
    row_emoji  = ["1️⃣", "2️⃣", "3️⃣"]
    rows = [col_header]
    for r in range(3):
        cells = "  ".join(board[r])
        rows.append(f"{row_emoji[r]}  {cells}")
    return "\n".join(rows)


_TTT_LINES = [
    # rows
    [(0,0),(0,1),(0,2)], [(1,0),(1,1),(1,2)], [(2,0),(2,1),(2,2)],
    # cols
    [(0,0),(1,0),(2,0)], [(0,1),(1,1),(2,1)], [(0,2),(1,2),(2,2)],
    # diagonals
    [(0,0),(1,1),(2,2)], [(0,2),(1,1),(2,0)],
]


def _ttt_check_winner(board):
    for line in _TTT_LINES:
        syms = [board[r][c] for r, c in line]
        if syms[0] != TTT_EMPTY and syms[0] == syms[1] == syms[2]:
            return syms[0]
    return None


def _ttt_is_draw(board):
    return all(board[r][c] != TTT_EMPTY for r in range(3) for c in range(3))


def _ttt_ai_move(board, ai_sym, human_sym):
    """
    Perfect minimax AI — impossible to beat.
    Returns (row, col) of the best move.
    """
    empty = [(r, c) for r in range(3) for c in range(3) if board[r][c] == TTT_EMPTY]
    if not empty:
        return None, None

    def minimax(b, is_max, depth):
        w = _ttt_check_winner(b)
        if w == ai_sym:    return 10 - depth
        if w == human_sym: return depth - 10
        if _ttt_is_draw(b): return 0
        moves = [(r2, c2) for r2 in range(3) for c2 in range(3) if b[r2][c2] == TTT_EMPTY]
        if is_max:
            best = -99
            for r2, c2 in moves:
                b[r2][c2] = ai_sym
                best = max(best, minimax(b, False, depth+1))
                b[r2][c2] = TTT_EMPTY
            return best
        else:
            best = 99
            for r2, c2 in moves:
                b[r2][c2] = human_sym
                best = min(best, minimax(b, True, depth+1))
                b[r2][c2] = TTT_EMPTY
            return best

    best_score, best_r, best_c = -99, empty[0][0], empty[0][1]
    for r, c in empty:
        board[r][c] = ai_sym
        s = minimax(board, False, 0)
        board[r][c] = TTT_EMPTY
        if s > best_score:
            best_score, best_r, best_c = s, r, c
    return best_r, best_c


def _ttt_reset(session):
    session["ttt"] = _fresh_ttt()
    session["active_game"] = None
    session["last_move_time"] = None


# ── Tic-Tac-Toe command handlers ───────────────────────────────────────────

def ttt_start(gid, session, sender_id, sender_name, difficulty, msg_id, enabled):
    """#start ttt [easy|medium|hard|impossible]"""
    if not enabled:
        _send(gid, "⭕ Tic-Tac-Toe is currently disabled.", reply_to_id=msg_id); return
    if any_game_active(session):
        active = session["active_game"]
        _send(gid, f"A game of {active} is already running. Use #quit to end it first.", reply_to_id=msg_id); return

    session["active_game"] = "tictactoe"
    session["last_move_time"] = time.time()
    ttt = _fresh_ttt()
    ttt["board"] = _ttt_init_board()
    ttt["players"][sender_id] = TTT_X
    ttt["turn_order"] = [sender_id]
    ttt["current_turn"] = 0
    # TTT AI is always "impossible" (perfect minimax), but accept the arg for UX
    ttt["ai_difficulty"] = "impossible"
    session["ttt"] = ttt

    _send(
        gid,
        f"⭕ {sender_name} started Tic-Tac-Toe! (You are ❌)\n"
        f"Use #join to play PvP, or #addai to play vs AI.\n\n"
        + _ttt_board_text(ttt["board"])
        + "\n\nPlay by typing a coordinate, e.g. #B2 for the center.\n"
          "Columns: A B C    Rows: 1 2 3",
        reply_to_id=msg_id,
    )


def ttt_join(gid, session, sender_id, sender_name, msg_id, enabled):
    """#join — join as Player 2 (⭕)"""
    if not enabled:
        _send(gid, "⭕ Tic-Tac-Toe is currently disabled.", reply_to_id=msg_id); return
    ttt = session["ttt"]
    if session["active_game"] != "tictactoe":
        _send(gid, "No active Tic-Tac-Toe game. Use #start ttt to begin.", reply_to_id=msg_id); return
    if sender_id in ttt["players"]:
        _send(gid, "You are already in this game.", reply_to_id=msg_id); return
    if len(ttt["players"]) >= 2:
        _send(gid, "The game is already full.", reply_to_id=msg_id); return
    ttt["players"][sender_id] = TTT_O
    ttt["turn_order"].append(sender_id)
    session["last_move_time"] = time.time()
    p1_id = ttt["turn_order"][0]
    p1_name = _name(p1_id)
    _send(
        gid,
        f"⭕ {sender_name} joined as ⭕!\n"
        f"{p1_name} ❌ vs {sender_name} ⭕\n"
        f"{p1_name}'s turn first.\n\n"
        + _ttt_board_text(ttt["board"]),
        reply_to_id=msg_id,
    )


def ttt_addai(gid, session, sender_id, sender_name, msg_id, enabled):
    """#addai — add the perfect AI as opponent"""
    if not enabled:
        _send(gid, "⭕ Tic-Tac-Toe is currently disabled.", reply_to_id=msg_id); return
    ttt = session["ttt"]
    if session["active_game"] != "tictactoe":
        _send(gid, "No active Tic-Tac-Toe game. Use #start ttt first.", reply_to_id=msg_id); return
    if len(ttt["players"]) >= 2:
        _send(gid, "The game already has a second player.", reply_to_id=msg_id); return
    ttt["players"]["AI"] = TTT_O
    ttt["turn_order"].append("AI")
    session["last_move_time"] = time.time()
    p1_id = ttt["turn_order"][0]
    p1_name = _name(p1_id)
    _send(
        gid,
        f"🤖 AI joined as ⭕ (Perfect AI — impossible to beat).\n"
        f"{p1_name} ❌ vs AI ⭕\n"
        f"{p1_name}'s turn first.\n\n"
        + _ttt_board_text(ttt["board"]),
        reply_to_id=msg_id,
    )


def ttt_quit(gid, session, sender_name, msg_id):
    """#quit — forfeit the Tic-Tac-Toe game"""
    if session["active_game"] != "tictactoe":
        _send(gid, "No active Tic-Tac-Toe game.", reply_to_id=msg_id); return
    _ttt_reset(session)
    _send(gid, f"🚫 Tic-Tac-Toe ended by {sender_name}.", reply_to_id=msg_id)


def ttt_move(gid, session, sender_id, sender_name, col_letter, row_number, msg_id, enabled):
    """
    Handle a Tic-Tac-Toe coordinate move like #B2.
    col_letter: 'A'|'B'|'C'   row_number: '1'|'2'|'3'
    """
    if not enabled:
        _send(gid, "⭕ Tic-Tac-Toe is currently disabled.", reply_to_id=msg_id); return
    ttt = session["ttt"]
    if session["active_game"] != "tictactoe":
        _send(gid, "No active Tic-Tac-Toe game. Use #start ttt to begin.", reply_to_id=msg_id); return
    if len(ttt["players"]) < 2:
        _send(gid, "Waiting for a second player — use #join or #addai.", reply_to_id=msg_id); return

    cur_uid = ttt["turn_order"][ttt["current_turn"]]
    if sender_id != cur_uid:
        cur_name = _name(cur_uid) if cur_uid != "AI" else "AI"
        _send(gid, f"It is {cur_name}'s turn.", reply_to_id=msg_id); return

    r, c = _ttt_coord_to_rc(col_letter, row_number)
    if r is None:
        _send(gid, "Invalid coordinate. Use a column letter (A-C) and row number (1-3), e.g. #B2.", reply_to_id=msg_id); return
    if ttt["board"][r][c] != TTT_EMPTY:
        _send(gid, f"Cell {col_letter}{row_number} is already taken. Choose another.", reply_to_id=msg_id); return

    sym = ttt["players"][sender_id]
    ttt["board"][r][c] = sym
    session["last_move_time"] = time.time()

    winner = _ttt_check_winner(ttt["board"])
    if winner:
        board_text = _ttt_board_text(ttt["board"])
        _send(gid, f"🎉 {sender_name} wins with {winner}!\n\n{board_text}", reply_to_id=msg_id)
        _ttt_reset(session); return
    if _ttt_is_draw(ttt["board"]):
        board_text = _ttt_board_text(ttt["board"])
        _send(gid, f"🤝 It's a draw!\n\n{board_text}", reply_to_id=msg_id)
        _ttt_reset(session); return

    # Switch turn
    ttt["current_turn"] = (ttt["current_turn"] + 1) % 2
    next_uid = ttt["turn_order"][ttt["current_turn"]]

    if next_uid == "AI":
        human_sym = ttt["players"][ttt["turn_order"][0]]
        ai_sym    = TTT_O
        ai_r, ai_c = _ttt_ai_move(ttt["board"], ai_sym, human_sym)
        ttt["board"][ai_r][ai_c] = ai_sym
        session["last_move_time"] = time.time()
        ai_coord = f"{_TTT_COL_LABELS[ai_c]}{_TTT_ROW_LABELS[ai_r]}"
        board_text = _ttt_board_text(ttt["board"])

        winner = _ttt_check_winner(ttt["board"])
        if winner:
            _send(gid, f"🤖 AI plays {ai_coord}. AI wins!\nBetter luck next time.\n\n{board_text}", reply_to_id=msg_id)
            _ttt_reset(session); return
        if _ttt_is_draw(ttt["board"]):
            _send(gid, f"🤖 AI plays {ai_coord}. It's a draw!\n\n{board_text}", reply_to_id=msg_id)
            _ttt_reset(session); return
        ttt["current_turn"] = 0
        p1_id = ttt["turn_order"][0]
        p1_name = _name(p1_id)
        _send(gid, f"🤖 AI plays {ai_coord}.\nYour turn, {p1_name}!\n\n{board_text}", reply_to_id=msg_id)
        return

    next_name = _name(next_uid)
    board_text = _ttt_board_text(ttt["board"])
    _send(
        gid,
        f"{sender_name} played {col_letter}{row_number}.\n"
        f"It is now {next_name}'s turn ({ttt['players'][next_uid]}).\n\n{board_text}",
        reply_to_id=msg_id,
    )


# =============================================================================
# UNIFIED GAME COMMAND ROUTER
# =============================================================================
# Porta-GMBOT.py calls handle_game_command() for every # command in a group.
# Returns True if the command was consumed, False to let the main file handle it.

def handle_game_command(message, gid, session, connect4_enabled, tictactoe_enabled,
                         game_timeout_seconds, chess_enabled=False):
    """
    Route game-related # commands to the correct game handler.

    Parameters
    ----------
    message            : GroupMe message dict
    gid                : str group ID
    session            : the per-group game_session dict (mutated in place)
    connect4_enabled   : bool
    tictactoe_enabled  : bool
    game_timeout_seconds : int — inactive game timeout (kept in sync here)
    chess_enabled      : bool

    Returns True if command handled, False otherwise.
    """
    text = (message.get("text") or "").strip()
    if not text.startswith("#"):
        return False

    parts     = text.split()
    cmd       = parts[0].lower()
    sender_id = message.get("user_id")
    raw_name  = message.get("name", "Unknown")
    # Use main bot's known_names registry for the canonical display name
    known     = _known_names_fn()
    sender_name = known.get(str(sender_id), raw_name)
    msg_id    = message.get("id")

    # Keep timeout in sync
    session["timeout_seconds"] = game_timeout_seconds

    # ─── #start <game> [difficulty] ──────────────────────────────────────────
    if cmd == "#start":
        if len(parts) < 2:
            # Show what games are available
            games = []
            if connect4_enabled:   games.append("c4 (Connect Four)")
            if tictactoe_enabled:  games.append("ttt (Tic-Tac-Toe)")
            if chess_enabled:      games.append("chess (Chess)")
            if games:
                _send(gid,
                    "Usage: #start <game> [difficulty]\n"
                    "Games: " + ", ".join(games) + "\n"
                    "Example: #start c4 hard  |  #start ttt  |  #start chess medium",
                    reply_to_id=msg_id)
            else:
                _send(gid, "No games are enabled. Ask an admin to enable them with #state.", reply_to_id=msg_id)
            return True

        game_arg = parts[1].lower()
        diff_arg = parts[2].lower() if len(parts) >= 3 else "medium"

        if game_arg in ("c4", "connect4", "connectfour"):
            c4_start(gid, session, sender_id, sender_name, diff_arg, msg_id, connect4_enabled)
            return True
        if game_arg in ("ttt", "tictactoe"):
            ttt_start(gid, session, sender_id, sender_name, diff_arg, msg_id, tictactoe_enabled)
            return True
        if game_arg in ("chess",):
            chess_start(gid, session, sender_id, sender_name, diff_arg, msg_id, chess_enabled)
            return True

        _send(gid, f"Unknown game '{game_arg}'.\nKnown games: c4, ttt, chess", reply_to_id=msg_id)
        return True

    # ─── #join — joins whatever game is waiting for a second player ───────────
    if cmd == "#join":
        ag = session.get("active_game")
        if ag == "connect4":
            c4_join(gid, session, sender_id, sender_name, msg_id, connect4_enabled)
        elif ag == "tictactoe":
            ttt_join(gid, session, sender_id, sender_name, msg_id, tictactoe_enabled)
        elif ag == "chess":
            chess_join(gid, session, sender_id, sender_name, msg_id, chess_enabled)
        else:
            _send(gid, "No game is waiting for players. Use #start <game> to begin.", reply_to_id=msg_id)
        return True

    # ─── #addai — add AI to whatever game is waiting ─────────────────────────
    if cmd == "#addai":
        ag = session.get("active_game")
        diff_arg = parts[1].lower() if len(parts) >= 2 and parts[1].lower() in ("easy","medium","hard") else "medium"
        if ag == "connect4":
            c4_addai(gid, session, sender_id, sender_name, diff_arg, msg_id, connect4_enabled)
        elif ag == "tictactoe":
            ttt_addai(gid, session, sender_id, sender_name, msg_id, tictactoe_enabled)
        elif ag == "chess":
            chess_addai(gid, session, sender_id, sender_name, diff_arg, msg_id, chess_enabled)
        else:
            _send(gid, "No game is waiting for a second player. Use #start <game> first.", reply_to_id=msg_id)
        return True

    # ─── #quit — quit whatever game is running ────────────────────────────────
    if cmd == "#quit":
        ag = session.get("active_game")
        if ag == "connect4":
            c4_quit(gid, session, sender_name, msg_id)
        elif ag == "tictactoe":
            ttt_quit(gid, session, sender_name, msg_id)
        elif ag == "chess":
            chess_quit(gid, session, sender_name, msg_id)
        else:
            _send(gid, "No game is currently running.", reply_to_id=msg_id)
        return True

    # ─── #timeout <seconds> ───────────────────────────────────────────────────
    if cmd == "#timeout":
        if len(parts) < 2:
            _send(gid, f"Current game timeout: {session['timeout_seconds']}s.", reply_to_id=msg_id)
            return True
        try:
            val = int(parts[1])
            if val <= 0: raise ValueError
        except ValueError:
            _send(gid, "Usage: #timeout <seconds>  (must be positive)", reply_to_id=msg_id)
            return True
        session["timeout_seconds"] = val
        _send(gid, f"Game timeout set to {val} seconds.", reply_to_id=msg_id)
        return True

    # ─── #board — resend the chess board ─────────────────────────────────────
    if cmd == "#board":
        if session.get("active_game") == "chess":
            chess_board(gid, session, sender_id, msg_id)
            return True

    # ─── Chess moves: #e2e4 / #O-O / #O-O-O ─────────────────────────────────
    if session.get("active_game") == "chess":
        raw = cmd  # e.g. "#e2e4" or "#O-O"
        from_sq, to_sq, promo = _parse_chess_move(raw)
        if from_sq is not None:
            is_castle = (from_sq in ("O-O", "O-O-O"))
            chess_move(gid, session, sender_id, sender_name,
                       from_sq, to_sq, promo, is_castle, msg_id, chess_enabled)
            return True

    # ─── Connect Four column moves: #A through #G or emoji ───────────────────
    if session.get("active_game") == "connect4":
        raw_col = cmd[1:] if cmd.startswith("#") else ""
        if raw_col and _c4_col_from_cmd(raw_col) is not None:
            c4_column_move(gid, session, sender_id, sender_name, raw_col, msg_id, connect4_enabled)
            return True

    # ─── Tic-Tac-Toe coordinate moves: #A1 / #B2 / #C3 etc. ─────────────────
    if session.get("active_game") == "tictactoe":
        col_l, row_n = _ttt_parse_cmd(cmd)
        if col_l is not None:
            ttt_move(gid, session, sender_id, sender_name, col_l, row_n, msg_id, tictactoe_enabled)
            return True

    # ─── Connect Four betting commands ────────────────────────────────────────
    if cmd == "#pvpbet":
        c4_pvpbet(gid, session, sender_id, sender_name, parts, msg_id)
        return True

    if cmd == "#bet":
        c4_spectator_bet(gid, session, sender_id, sender_name, parts, message, msg_id)
        return True

    if cmd == "#stats":
        c4_stats(gid, session, msg_id)
        return True

    return False   # command not handled here — let main file process it


# =============================================================================
# TIMEOUT CHECK (called from the poll loop in Porta-GMBOT.py)
# =============================================================================

def check_timeout(gid, session):
    """
    If a game has been inactive past its timeout, end it and refund bets.
    Returns True if a timeout was triggered (poll loop should skip message fetch).
    """
    if not any_game_active(session):
        return False
    last = session.get("last_move_time")
    if last is None:
        return False
    elapsed = time.time() - last
    if elapsed <= session.get("timeout_seconds", 300):
        return False

    ag = session["active_game"]
    refunds = []
    if ag == "connect4":
        refunds = _c4_refund_all_bets(gid, session)
        _c4_reset(session)
    elif ag == "tictactoe":
        _ttt_reset(session)
    elif ag == "chess":
        _chess_reset(session)

    msg = f"⏰ {ag.title() if ag else 'Game'} timed out due to inactivity."
    if refunds:
        msg += "\n💰 Bets refunded:\n" + "\n".join(refunds)
    _send_fn(gid, msg)
    return True


# =============================================================================
# LEADERBOARD (pass-through — data lives in main file's points system)
# =============================================================================

def leaderboard_text(entries, top_n):
    """Format a leaderboard from a list of point-entry dicts."""
    medals = ["🥇","🥈","🥉"] + ["   "] * max(0, top_n - 3)
    lines = ["🏆 Points Leaderboard:"]
    for i, e in enumerate(entries):
        lines.append(f"{medals[i]} {e.get('name','?')}: {e.get('points',0):,} pts")
    return "\n".join(lines)


# =============================================================================
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  CHESS  —  PvP / vs-AI two-player board game                            │
# └─────────────────────────────────────────────────────────────────────────┘
#
# Board representation
# ---------------------
#   board[row][col]  — row 0 = rank 8 (Black's back row), row 7 = rank 1 (White)
#   col 0 = file a … col 7 = file h
#   Each cell is "" (empty) or a piece string like "wK", "bP", etc.
#     Prefix:  w = White,  b = Black
#     Suffix:  K King  Q Queen  R Rook  B Bishop  N Knight  P Pawn
#
# Move input
# ----------
#   Players type  #e2e4  (from-square to-square, no spaces).
#   Castling shortcuts: #O-O  (kingside)  #O-O-O  (queenside)
#   Pawn promotion: #e7e8Q  (appended piece letter)
#
# Board display
# -------------
#   Squares alternate 🟫🟨 (dark/light).  Pieces are colour-coded emoji:
#     White pieces → 🔵 blue square  |  Black pieces → 🔴 red square
#   A key is printed below every board so players always know which is which.
#
# AI
# --
#   Minimax with alpha-beta pruning, iterative deepening.
#   easy=2 ply, medium=4 ply, hard=6 ply.
#   Rewards for winning AI: easy=75, medium=175, hard=300 pts.
#
# =============================================================================

# ── Piece-to-emoji mapping ────────────────────────────────────────────────────
# We use coloured-square emojis as the "piece cell" so every square is exactly
# one emoji wide regardless of the Unicode chess glyph renderer.
#
#   White pieces: blue background  🔵-family
#   Black pieces: red background   🔴-family
#   Empty dark square:  🟫
#   Empty light square: 🟨

_CHESS_W_EMOJI = {
    "wK": "🔵",   # King
    "wQ": "🟦",   # Queen
    "wR": "🟪",   # Rook  (reusing purple — key explains)
    "wB": "🔷",   # Bishop
    "wN": "💠",   # Knight
    "wP": "🔹",   # Pawn
}
_CHESS_B_EMOJI = {
    "bK": "🔴",   # King
    "bQ": "🟥",   # Queen
    "bR": "🟣",   # Rook
    "bB": "🔶",   # Bishop
    "bN": "🔸",   # Knight
    "bP": "❤️",   # Pawn — single emoji, renders same width
}
# Piece full names for the key
_CHESS_PIECE_NAMES = {
    "K": "King", "Q": "Queen", "R": "Rook",
    "B": "Bishop", "N": "Knight", "P": "Pawn",
}

_CHESS_DARK  = "🟫"
_CHESS_LIGHT = "🟨"

# Key printed below every board
_CHESS_KEY = (
    "♟ KEY — White(🔵): 🔵K 🟦Q 🟪R 🔷B 💠N 🔹P\n"
    "♟ KEY — Black(🔴): 🔴K 🟥Q 🟣R 🔶B 🔸N ❤️P"
)

# File/rank labels for board edges
_CHESS_FILES = "abcdefgh"
_CHESS_RANKS = "87654321"   # row 0 → rank 8, row 7 → rank 1

# Point rewards
_chess_rewards = {"easy": 75, "medium": 175, "hard": 300}

def set_chess_rewards(easy, medium, hard):
    _chess_rewards["easy"]   = easy
    _chess_rewards["medium"] = medium
    _chess_rewards["hard"]   = hard


# ── Board init ────────────────────────────────────────────────────────────────

def _chess_start_board():
    """Return the standard starting position as an 8×8 list of strings."""
    back = ["R","N","B","Q","K","B","N","R"]
    board = [[""]*8 for _ in range(8)]
    for c, p in enumerate(back):
        board[0][c] = "b" + p   # Black back row
        board[7][c] = "w" + p   # White back row
    for c in range(8):
        board[1][c] = "bP"
        board[6][c] = "wP"
    return board


def _fresh_chess():
    return {
        "board": None,
        "players": {},          # {uid: {"name": str, "color": "w"|"b"}}
        "turn_order": [],       # [uid_white, uid_black]  (or "AI")
        "current_turn": 0,      # index into turn_order
        "ai_difficulty": "medium",
        # Castling rights: True = still possible (hasn't moved king/rook)
        "castle_rights": {
            "w": {"K": True, "Q": True},   # w kingside / queenside
            "b": {"K": True, "Q": True},
        },
        "en_passant": None,     # (row, col) square that can be captured en-passant, or None
        "last_move": None,      # string like "e2e4" for display
        "move_count": 0,
        "check_flag": None,     # "w" | "b" | None  — whose king is in check
        "halfmove_clock": 0,    # for 50-move draw rule
        "position_history": [], # list of board hashes for threefold repetition
    }


# ── Board rendering ───────────────────────────────────────────────────────────

def _chess_cell_emoji(piece, row, col):
    """Return the display emoji for a board cell."""
    if piece == "":
        return _CHESS_DARK if (row + col) % 2 == 0 else _CHESS_LIGHT
    if piece.startswith("w"):
        return _CHESS_W_EMOJI.get(piece, "❓")
    return _CHESS_B_EMOJI.get(piece, "❓")


def _chess_board_text(board, last_move=None, perspective="w"):
    """
    Render the 8×8 board as text.
    perspective="w" → White's view (rank 8 on top, a-file on left).
    perspective="b" → Black's view (rank 1 on top, h-file on left).
    """
    rows_iter  = range(8)        if perspective == "w" else range(7,-1,-1)
    cols_iter  = range(8)        if perspective == "w" else range(7,-1,-1)
    rank_labels = _CHESS_RANKS   if perspective == "w" else _CHESS_RANKS[::-1]

    # Column header (file letters)
    if perspective == "w":
        file_header = "  " + " ".join(_CHESS_FILES)
    else:
        file_header = "  " + " ".join(reversed(_CHESS_FILES))

    lines = [file_header]
    for ri, row in enumerate(rows_iter):
        rank_lbl = rank_labels[ri]
        row_cells = "".join(
            _chess_cell_emoji(board[row][col], row, col)
            for col in cols_iter
        )
        lines.append(f"{rank_lbl} {row_cells}")
    lines.append(file_header)

    board_str = "\n".join(lines)
    if last_move:
        board_str += f"\n📌 Last move: {last_move}"
    board_str += "\n" + _CHESS_KEY
    return board_str


# ── Square helpers ────────────────────────────────────────────────────────────

def _sq_to_rc(sq):
    """'e2' → (6, 4)  (row, col).  Returns (None,None) on error."""
    sq = sq.lower().strip()
    if len(sq) < 2 or sq[0] not in _CHESS_FILES or sq[1] not in "12345678":
        return None, None
    col = _CHESS_FILES.index(sq[0])
    row = 8 - int(sq[1])
    return row, col

def _rc_to_sq(row, col):
    return _CHESS_FILES[col] + _CHESS_RANKS[row]

def _parse_chess_move(cmd):
    """
    Parse '#e2e4', '#e2e4Q', '#O-O', '#O-O-O'.
    Returns (from_sq, to_sq, promo) or (None,None,None).
    """
    inner = cmd.lstrip("#").upper()
    if inner in ("O-O", "0-0"):
        return "O-O", None, None
    if inner in ("O-O-O", "0-0-0"):
        return "O-O-O", None, None
    inner = inner.lower()
    if len(inner) == 4:
        return inner[:2], inner[2:], None
    if len(inner) == 5 and inner[4] in "qrbn":
        return inner[:2], inner[2:4], inner[4]
    return None, None, None


# ── Move generation ───────────────────────────────────────────────────────────

def _chess_color(piece):
    return piece[0] if piece else None

def _chess_opponent(color):
    return "b" if color == "w" else "w"

def _in_bounds(r, c):
    return 0 <= r < 8 and 0 <= c < 8

def _chess_raw_moves(board, row, col, en_passant=None):
    """
    Generate all pseudo-legal (row,col) destination squares for piece at (row,col).
    Does NOT check for leaving own king in check.
    Returns list of (to_row, to_col, flags) where flags is a dict.
    """
    piece = board[row][col]
    if not piece:
        return []
    color = piece[0]
    ptype = piece[1]
    opp   = _chess_opponent(color)
    moves = []

    def add(r, c, flags=None):
        if _in_bounds(r, c) and _chess_color(board[r][c]) != color:
            moves.append((r, c, flags or {}))

    def slide(dirs):
        for dr, dc in dirs:
            r, c = row + dr, col + dc
            while _in_bounds(r, c):
                if board[r][c]:
                    if _chess_color(board[r][c]) == opp:
                        moves.append((r, c, {}))
                    break
                moves.append((r, c, {}))
                r += dr; c += dc

    if ptype == "P":
        dir_ = -1 if color == "w" else 1
        start_row = 6 if color == "w" else 1
        # Forward
        r1 = row + dir_
        if _in_bounds(r1, col) and not board[r1][col]:
            promo = (r1 == 0 or r1 == 7)
            moves.append((r1, col, {"promo": promo}))
            # Double push
            r2 = row + 2*dir_
            if row == start_row and not board[r2][col]:
                moves.append((r2, col, {"ep_set": (row + dir_, col)}))
        # Captures
        for dc in (-1, 1):
            rc = (row + dir_, col + dc)
            if _in_bounds(*rc):
                if board[rc[0]][rc[1]] and _chess_color(board[rc[0]][rc[1]]) == opp:
                    moves.append((rc[0], rc[1], {"promo": (rc[0] == 0 or rc[0] == 7)}))
                # En passant
                if en_passant and rc == en_passant:
                    moves.append((rc[0], rc[1], {"ep_capture": (row, col + dc)}))

    elif ptype == "N":
        for dr, dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
            add(row+dr, col+dc)

    elif ptype == "B":
        slide([(-1,-1),(-1,1),(1,-1),(1,1)])

    elif ptype == "R":
        slide([(-1,0),(1,0),(0,-1),(0,1)])

    elif ptype == "Q":
        slide([(-1,-1),(-1,1),(1,-1),(1,1),(-1,0),(1,0),(0,-1),(0,1)])

    elif ptype == "K":
        for dr, dc in [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]:
            add(row+dr, col+dc)

    return moves


def _chess_find_king(board, color):
    for r in range(8):
        for c in range(8):
            if board[r][c] == color + "K":
                return r, c
    return None, None


def _chess_is_attacked(board, row, col, by_color):
    """Return True if (row,col) is attacked by any piece of by_color."""
    for r in range(8):
        for c in range(8):
            if board[r][c] and board[r][c][0] == by_color:
                for tr, tc, _ in _chess_raw_moves(board, r, c):
                    if tr == row and tc == col:
                        return True
    return False


def _chess_in_check(board, color):
    kr, kc = _chess_find_king(board, color)
    if kr is None:
        return True   # no king = in check (shouldn't happen in normal play)
    return _chess_is_attacked(board, kr, kc, _chess_opponent(color))


def _chess_apply_move(board, fr, fc, tr, tc, flags, promo_piece=None, castle_rights=None, en_passant=None):
    """
    Apply a move to a copy of the board.  Returns (new_board, new_castle_rights, new_en_passant).
    """
    import copy
    nb = copy.deepcopy(board)
    piece = nb[fr][fc]
    color = piece[0]
    ptype = piece[1]
    new_ep = None

    # En passant capture
    if "ep_capture" in flags:
        er, ec = flags["ep_capture"]
        nb[er][ec] = ""

    # Set EP square for double pawn push
    if "ep_set" in flags:
        new_ep = flags["ep_set"]

    # Move piece
    nb[tr][tc] = piece
    nb[fr][fc] = ""

    # Pawn promotion
    if ptype == "P" and (tr == 0 or tr == 7):
        pp = (promo_piece or "q").upper()
        nb[tr][tc] = color + pp

    # Update castle rights
    new_cr = copy.deepcopy(castle_rights) if castle_rights else {
        "w": {"K": True, "Q": True}, "b": {"K": True, "Q": True}
    }
    if ptype == "K":
        new_cr[color]["K"] = False
        new_cr[color]["Q"] = False
    if ptype == "R":
        back_row = 7 if color == "w" else 0
        if fr == back_row and fc == 7: new_cr[color]["K"] = False
        if fr == back_row and fc == 0: new_cr[color]["Q"] = False

    return nb, new_cr, new_ep


def _chess_apply_castle(board, color, side, castle_rights):
    """Apply castling. side='K' or 'Q'. Returns new board or None if illegal."""
    import copy
    row = 7 if color == "w" else 0
    opp = _chess_opponent(color)

    if side == "K":
        # King e→g, Rook h→f
        king_path = [(row, 4), (row, 5), (row, 6)]
        rook_from, rook_to = (row, 7), (row, 5)
    else:
        # King e→c, Rook a→d
        king_path = [(row, 4), (row, 3), (row, 2)]
        rook_from, rook_to = (row, 0), (row, 3)

    # Check rights and pieces present
    if not castle_rights[color][side]:
        return None
    if board[row][4] != color + "K":
        return None
    if board[rook_from[0]][rook_from[1]] != color + "R":
        return None
    # Squares between must be empty
    between = range(5, 7) if side == "K" else range(1, 4)
    for c in between:
        if board[row][c]:
            return None
    # King must not pass through check
    for r, c in king_path:
        if _chess_is_attacked(board, r, c, opp):
            return None

    nb = copy.deepcopy(board)
    king_to_col = 6 if side == "K" else 2
    nb[row][4]          = ""
    nb[row][king_to_col] = color + "K"
    nb[rook_from[0]][rook_from[1]] = ""
    nb[rook_to[0]][rook_to[1]]     = color + "R"
    return nb


def _chess_legal_moves(board, color, castle_rights, en_passant):
    """Return list of all legal (fr,fc,tr,tc,flags) moves for color."""
    legal = []
    for r in range(8):
        for c in range(8):
            if board[r][c] and board[r][c][0] == color:
                for tr, tc, flags in _chess_raw_moves(board, r, c, en_passant):
                    nb, ncr, nep = _chess_apply_move(board, r, c, tr, tc, flags,
                                                      castle_rights=castle_rights,
                                                      en_passant=en_passant)
                    if not _chess_in_check(nb, color):
                        legal.append((r, c, tr, tc, flags))
    # Castling
    for side in ("K", "Q"):
        nb_castle = _chess_apply_castle(board, color, side, castle_rights)
        if nb_castle and not _chess_in_check(nb_castle, color):
            row = 7 if color == "w" else 0
            to_col = 6 if side == "K" else 2
            legal.append((row, 4, row, to_col, {"castle": side}))
    return legal


def _chess_is_checkmate(board, color, castle_rights, en_passant):
    return (_chess_in_check(board, color) and
            len(_chess_legal_moves(board, color, castle_rights, en_passant)) == 0)

def _chess_is_stalemate(board, color, castle_rights, en_passant):
    return (not _chess_in_check(board, color) and
            len(_chess_legal_moves(board, color, castle_rights, en_passant)) == 0)


# ── Evaluation for AI ─────────────────────────────────────────────────────────

_PIECE_VALUE = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 20000}

# Piece-square tables (white's perspective, row 0 = rank 8)
_PST = {
    "P": [
        [ 0,  0,  0,  0,  0,  0,  0,  0],
        [50, 50, 50, 50, 50, 50, 50, 50],
        [10, 10, 20, 30, 30, 20, 10, 10],
        [ 5,  5, 10, 25, 25, 10,  5,  5],
        [ 0,  0,  0, 20, 20,  0,  0,  0],
        [ 5, -5,-10,  0,  0,-10, -5,  5],
        [ 5, 10, 10,-20,-20, 10, 10,  5],
        [ 0,  0,  0,  0,  0,  0,  0,  0],
    ],
    "N": [
        [-50,-40,-30,-30,-30,-30,-40,-50],
        [-40,-20,  0,  0,  0,  0,-20,-40],
        [-30,  0, 10, 15, 15, 10,  0,-30],
        [-30,  5, 15, 20, 20, 15,  5,-30],
        [-30,  0, 15, 20, 20, 15,  0,-30],
        [-30,  5, 10, 15, 15, 10,  5,-30],
        [-40,-20,  0,  5,  5,  0,-20,-40],
        [-50,-40,-30,-30,-30,-30,-40,-50],
    ],
    "B": [
        [-20,-10,-10,-10,-10,-10,-10,-20],
        [-10,  0,  0,  0,  0,  0,  0,-10],
        [-10,  0,  5, 10, 10,  5,  0,-10],
        [-10,  5,  5, 10, 10,  5,  5,-10],
        [-10,  0, 10, 10, 10, 10,  0,-10],
        [-10, 10, 10, 10, 10, 10, 10,-10],
        [-10,  5,  0,  0,  0,  0,  5,-10],
        [-20,-10,-10,-10,-10,-10,-10,-20],
    ],
    "R": [
        [ 0,  0,  0,  0,  0,  0,  0,  0],
        [ 5, 10, 10, 10, 10, 10, 10,  5],
        [-5,  0,  0,  0,  0,  0,  0, -5],
        [-5,  0,  0,  0,  0,  0,  0, -5],
        [-5,  0,  0,  0,  0,  0,  0, -5],
        [-5,  0,  0,  0,  0,  0,  0, -5],
        [-5,  0,  0,  0,  0,  0,  0, -5],
        [ 0,  0,  0,  5,  5,  0,  0,  0],
    ],
    "Q": [
        [-20,-10,-10, -5, -5,-10,-10,-20],
        [-10,  0,  0,  0,  0,  0,  0,-10],
        [-10,  0,  5,  5,  5,  5,  0,-10],
        [ -5,  0,  5,  5,  5,  5,  0, -5],
        [  0,  0,  5,  5,  5,  5,  0, -5],
        [-10,  5,  5,  5,  5,  5,  0,-10],
        [-10,  0,  5,  0,  0,  0,  0,-10],
        [-20,-10,-10, -5, -5,-10,-10,-20],
    ],
    "K": [
        [-30,-40,-40,-50,-50,-40,-40,-30],
        [-30,-40,-40,-50,-50,-40,-40,-30],
        [-30,-40,-40,-50,-50,-40,-40,-30],
        [-30,-40,-40,-50,-50,-40,-40,-30],
        [-20,-30,-30,-40,-40,-30,-30,-20],
        [-10,-20,-20,-20,-20,-20,-20,-10],
        [ 20, 20,  0,  0,  0,  0, 20, 20],
        [ 20, 30, 10,  0,  0, 10, 30, 20],
    ],
}

def _chess_evaluate(board):
    """Static evaluation relative to White (positive = White advantage)."""
    score = 0
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if not p:
                continue
            color, ptype = p[0], p[1]
            val = _PIECE_VALUE.get(ptype, 0)
            pst_row = r if color == "w" else 7 - r
            pst_val = _PST.get(ptype, [[0]*8]*8)[pst_row][c]
            if color == "w":
                score += val + pst_val
            else:
                score -= val + pst_val
    return score


def _chess_minimax(board, depth, alpha, beta, maximizing, color, castle_rights, en_passant):
    """Alpha-beta minimax. Returns (score, move) where move=(fr,fc,tr,tc,flags)."""
    opp = _chess_opponent(color)
    cur_color = "w" if maximizing else "b"

    if depth == 0:
        return _chess_evaluate(board), None

    moves = _chess_legal_moves(board, cur_color, castle_rights, en_passant)
    if not moves:
        if _chess_in_check(board, cur_color):
            return (100_000 if not maximizing else -100_000), None
        return 0, None  # stalemate

    # Move ordering: captures first
    def move_priority(m):
        fr, fc, tr, tc, flags = m
        return 0 if board[tr][tc] else 1

    moves.sort(key=move_priority)
    best_move = moves[0]

    if maximizing:
        best_val = -10**9
        for mv in moves:
            fr, fc, tr, tc, flags = mv
            if "castle" in flags:
                nb = _chess_apply_castle(board, cur_color, flags["castle"], castle_rights)
                ncr, nep = castle_rights, None
            else:
                nb, ncr, nep = _chess_apply_move(board, fr, fc, tr, tc, flags,
                                                   castle_rights=castle_rights,
                                                   en_passant=en_passant)
            if nb is None:
                continue
            val, _ = _chess_minimax(nb, depth-1, alpha, beta, False, color, ncr, nep)
            if val > best_val:
                best_val, best_move = val, mv
            alpha = max(alpha, val)
            if alpha >= beta:
                break
        return best_val, best_move
    else:
        best_val = 10**9
        for mv in moves:
            fr, fc, tr, tc, flags = mv
            if "castle" in flags:
                nb = _chess_apply_castle(board, cur_color, flags["castle"], castle_rights)
                ncr, nep = castle_rights, None
            else:
                nb, ncr, nep = _chess_apply_move(board, fr, fc, tr, tc, flags,
                                                   castle_rights=castle_rights,
                                                   en_passant=en_passant)
            if nb is None:
                continue
            val, _ = _chess_minimax(nb, depth-1, alpha, beta, True, color, ncr, nep)
            if val < best_val:
                best_val, best_move = val, mv
            beta = min(beta, val)
            if alpha >= beta:
                break
        return best_val, best_move


def _chess_ai_choose(board, ai_color, castle_rights, en_passant, difficulty):
    depth_map = {"easy": 2, "medium": 4, "hard": 6}
    depth = depth_map.get(difficulty, 4)
    maximizing = (ai_color == "w")
    _, move = _chess_minimax(board, depth, -10**9, 10**9, maximizing, ai_color, castle_rights, en_passant)
    return move


# ── Chess state helpers ───────────────────────────────────────────────────────

def _chess_reset(session):
    session["chess"] = _fresh_chess()
    session["active_game"] = None
    session["last_move_time"] = None


def _chess_board_hash(board):
    return tuple(tuple(row) for row in board)


# ── Chess command handlers ────────────────────────────────────────────────────

def chess_start(gid, session, sender_id, sender_name, difficulty, msg_id, enabled):
    """#start chess [easy|medium|hard]"""
    if not enabled:
        _send(gid, "♟ Chess is currently disabled.", reply_to_id=msg_id); return
    if any_game_active(session):
        _send(gid, f"A game of {session['active_game']} is already running. Use #quit to end it first.", reply_to_id=msg_id); return

    diff = difficulty if difficulty in ("easy","medium","hard") else "medium"
    session["active_game"] = "chess"
    session["last_move_time"] = time.time()

    ch = _fresh_chess()
    ch["board"] = _chess_start_board()
    ch["players"][sender_id] = {"name": sender_name, "color": "w"}
    ch["turn_order"] = [sender_id]
    ch["current_turn"] = 0
    ch["ai_difficulty"] = diff
    session["chess"] = ch

    board_txt = _chess_board_text(ch["board"], perspective="w")
    _send(
        gid,
        f"♟ {sender_name} started Chess! (You are White ⬜)\n"
        f"Use #join to play PvP, or #addai to play vs the AI ({diff}).\n\n"
        + board_txt
        + "\n\nMake moves like: #e2e4  •  Castle: #O-O or #O-O-O  •  Promote: #e7e8Q",
        reply_to_id=msg_id,
    )


def chess_join(gid, session, sender_id, sender_name, msg_id, enabled):
    """#join — join chess as Black"""
    if not enabled:
        _send(gid, "♟ Chess is currently disabled.", reply_to_id=msg_id); return
    ch = session["chess"]
    if session["active_game"] != "chess":
        _send(gid, "No active Chess game. Use #start chess to begin.", reply_to_id=msg_id); return
    if sender_id in ch["players"]:
        _send(gid, "You are already in this game.", reply_to_id=msg_id); return
    if len(ch["turn_order"]) >= 2:
        _send(gid, "The game is already full.", reply_to_id=msg_id); return

    ch["players"][sender_id] = {"name": sender_name, "color": "b"}
    ch["turn_order"].append(sender_id)
    session["last_move_time"] = time.time()

    p1_id   = ch["turn_order"][0]
    p1_name = ch["players"][p1_id]["name"]
    board_txt = _chess_board_text(ch["board"], perspective="w")
    _send(
        gid,
        f"♟ {sender_name} joined as Black! ⬛\n"
        f"{p1_name} (White) vs {sender_name} (Black)\n"
        f"{p1_name}'s turn first.\n\n"
        + board_txt,
        reply_to_id=msg_id,
    )


def chess_addai(gid, session, sender_id, sender_name, difficulty, msg_id, enabled):
    """#addai — add AI as Black opponent"""
    if not enabled:
        _send(gid, "♟ Chess is currently disabled.", reply_to_id=msg_id); return
    ch = session["chess"]
    if session["active_game"] != "chess":
        _send(gid, "No active Chess game. Use #start chess first.", reply_to_id=msg_id); return
    if len(ch["turn_order"]) >= 2:
        _send(gid, "The game already has a second player.", reply_to_id=msg_id); return

    diff = difficulty if difficulty in ("easy","medium","hard") else ch["ai_difficulty"]
    ch["players"]["AI"] = {"name": "AI", "color": "b"}
    ch["turn_order"].append("AI")
    ch["ai_difficulty"] = diff
    session["last_move_time"] = time.time()

    p1_name = ch["players"][ch["turn_order"][0]]["name"]
    board_txt = _chess_board_text(ch["board"], perspective="w")
    _send(
        gid,
        f"🤖 AI joined as Black ({diff.capitalize()})!\n"
        f"{p1_name} (White) vs AI (Black)\n"
        f"{p1_name}'s turn first.\n\n"
        + board_txt,
        reply_to_id=msg_id,
    )


def chess_quit(gid, session, sender_name, msg_id):
    """#quit — end the chess game"""
    if session["active_game"] != "chess":
        _send(gid, "No active Chess game.", reply_to_id=msg_id); return
    _chess_reset(session)
    _send(gid, f"♟ Chess game ended by {sender_name}.", reply_to_id=msg_id)


def chess_board(gid, session, sender_id, msg_id):
    """#board — resend the current board"""
    if session["active_game"] != "chess":
        _send(gid, "No active Chess game.", reply_to_id=msg_id); return
    ch = session["chess"]
    pdata = ch["players"].get(sender_id, {})
    persp = pdata.get("color", "w")
    board_txt = _chess_board_text(ch["board"], last_move=ch.get("last_move"), perspective=persp)
    _send(gid, board_txt, reply_to_id=msg_id)


def chess_move(gid, session, sender_id, sender_name, from_sq, to_sq, promo, is_castle, msg_id, enabled):
    """Process a chess move from a player."""
    if not enabled:
        _send(gid, "♟ Chess is currently disabled.", reply_to_id=msg_id); return

    ch = session["chess"]
    if session["active_game"] != "chess":
        _send(gid, "No active Chess game. Use #start chess to begin.", reply_to_id=msg_id); return
    if len(ch["turn_order"]) < 2:
        _send(gid, "Waiting for a second player — use #join or #addai.", reply_to_id=msg_id); return

    cur_uid = ch["turn_order"][ch["current_turn"]]
    if sender_id != cur_uid:
        cur_name = ch["players"][cur_uid]["name"]
        _send(gid, f"It's {cur_name}'s turn.", reply_to_id=msg_id); return

    color = ch["players"][sender_id]["color"]
    board = ch["board"]
    cr    = ch["castle_rights"]
    ep    = ch["en_passant"]

    # --- Castling ---
    if is_castle:
        side = "K" if from_sq == "O-O" else "Q"
        nb = _chess_apply_castle(board, color, side, cr)
        if nb is None:
            _send(gid, "❌ Castling is not legal right now.", reply_to_id=msg_id); return
        # Update castle rights
        cr[color]["K"] = False
        cr[color]["Q"] = False
        ch["board"] = nb
        ch["en_passant"] = None
        move_label = "O-O" if side == "K" else "O-O-O"
        ch["last_move"] = move_label
        ch["halfmove_clock"] += 1
    else:
        # --- Normal move ---
        fr, fc = _sq_to_rc(from_sq)
        tr, tc = _sq_to_rc(to_sq)
        if fr is None or tr is None:
            _send(gid, "❌ Invalid square. Use format like #e2e4.", reply_to_id=msg_id); return

        piece = board[fr][fc]
        if not piece or piece[0] != color:
            _send(gid, f"❌ No {('White' if color=='w' else 'Black')} piece on {from_sq}.", reply_to_id=msg_id); return

        # Check move is in legal moves
        legal = _chess_legal_moves(board, color, cr, ep)
        matching = [(r2,c2,f2) for (r1,c1,r2,c2,f2) in legal if r1==fr and c1==fc and r2==tr and c2==tc]
        if not matching:
            _send(gid, f"❌ {from_sq}{to_sq} is not a legal move.", reply_to_id=msg_id); return

        flags = matching[0][2]

        # Promotion check
        if flags.get("promo") and not promo:
            _send(gid, "♟ Pawn promotion! Append piece letter: #" + from_sq + to_sq + "Q  (Q/R/B/N)", reply_to_id=msg_id); return

        was_capture = bool(board[tr][tc]) or "ep_capture" in flags
        nb, new_cr, new_ep = _chess_apply_move(board, fr, fc, tr, tc, flags,
                                                promo_piece=promo,
                                                castle_rights=cr,
                                                en_passant=ep)

        ch["board"] = nb
        ch["castle_rights"] = new_cr
        ch["en_passant"] = new_ep
        move_label = from_sq + to_sq + (promo.upper() if promo else "")
        ch["last_move"] = move_label

        # Halfmove clock (reset on pawn move or capture)
        if piece[1] == "P" or was_capture:
            ch["halfmove_clock"] = 0
        else:
            ch["halfmove_clock"] += 1

    # Track position for threefold repetition
    h = _chess_board_hash(ch["board"])
    ch["position_history"].append(h)
    ch["move_count"] += 1
    session["last_move_time"] = time.time()

    opp_color = _chess_opponent(color)

    # ── Win / draw detection ──────────────────────────────────────────────────
    opp_legal = _chess_legal_moves(ch["board"], opp_color, ch["castle_rights"], ch["en_passant"])

    if len(opp_legal) == 0 and _chess_in_check(ch["board"], opp_color):
        # Checkmate
        board_txt = _chess_board_text(ch["board"], last_move=ch["last_move"], perspective="w")
        opp_uid = ch["turn_order"][(ch["current_turn"] + 1) % 2]
        is_ai_game = "AI" in ch["turn_order"]
        if is_ai_game:
            reward = _chess_rewards.get(ch["ai_difficulty"], 175)
            bal = _add_pts(gid, sender_id, sender_name, reward)
            _send(gid, f"♟ Checkmate! {sender_name} wins!\n🏆 Earned {reward} pts! ({bal} pts)\n\n{board_txt}", reply_to_id=msg_id)
        else:
            opp_name = ch["players"][opp_uid]["name"] if opp_uid != "AI" else "AI"
            _send(gid, f"♟ Checkmate! {sender_name} wins against {opp_name}! 🏆\n\n{board_txt}", reply_to_id=msg_id)
        _chess_reset(session)
        return

    if len(opp_legal) == 0:
        board_txt = _chess_board_text(ch["board"], last_move=ch["last_move"], perspective="w")
        _send(gid, f"♟ Stalemate! It's a draw.\n\n{board_txt}", reply_to_id=msg_id)
        _chess_reset(session); return

    # 50-move rule
    if ch["halfmove_clock"] >= 100:
        board_txt = _chess_board_text(ch["board"], last_move=ch["last_move"], perspective="w")
        _send(gid, f"♟ Draw by 50-move rule.\n\n{board_txt}", reply_to_id=msg_id)
        _chess_reset(session); return

    # Threefold repetition
    if ch["position_history"].count(h) >= 3:
        board_txt = _chess_board_text(ch["board"], last_move=ch["last_move"], perspective="w")
        _send(gid, f"♟ Draw by threefold repetition.\n\n{board_txt}", reply_to_id=msg_id)
        _chess_reset(session); return

    # ── Advance turn ──────────────────────────────────────────────────────────
    ch["current_turn"] = (ch["current_turn"] + 1) % 2
    next_uid = ch["turn_order"][ch["current_turn"]]
    check_notice = ""
    if _chess_in_check(ch["board"], opp_color):
        check_notice = "⚠️ CHECK!\n"

    persp_next = "w" if opp_color == "w" else "b"
    board_txt = _chess_board_text(ch["board"], last_move=ch["last_move"], perspective=persp_next)

    if next_uid == "AI":
        next_name = "AI"
        _send(gid, f"{sender_name} played {ch['last_move']}. {check_notice}🤖 AI is thinking...\n\n{board_txt}")
        _typing(gid)

        ai_color = ch["players"]["AI"]["color"]
        ai_move  = _chess_ai_choose(ch["board"], ai_color, ch["castle_rights"], ch["en_passant"], ch["ai_difficulty"])
        if ai_move is None:
            # No moves — already handled above as stalemate; safety fallback
            _send(gid, "♟ AI has no moves. Game over.", reply_to_id=msg_id)
            _chess_reset(session); return

        a_fr, a_fc, a_tr, a_tc, a_flags = ai_move
        if "castle" in a_flags:
            side = a_flags["castle"]
            nb = _chess_apply_castle(ch["board"], ai_color, side, ch["castle_rights"])
            ch["castle_rights"][ai_color]["K"] = False
            ch["castle_rights"][ai_color]["Q"] = False
            ch["board"] = nb
            ch["en_passant"] = None
            ai_label = "O-O" if side == "K" else "O-O-O"
        else:
            nb, new_cr, new_ep = _chess_apply_move(
                ch["board"], a_fr, a_fc, a_tr, a_tc, a_flags,
                castle_rights=ch["castle_rights"], en_passant=ch["en_passant"]
            )
            ch["board"] = nb
            ch["castle_rights"] = new_cr
            ch["en_passant"] = new_ep
            ai_label = _rc_to_sq(a_fr, a_fc) + _rc_to_sq(a_tr, a_tc)
            if a_flags.get("promo"):
                ai_label += "Q"   # AI always promotes to queen
                ch["board"][a_tr][a_tc] = ai_color + "Q"

        ch["last_move"] = ai_label
        ch["move_count"] += 1
        session["last_move_time"] = time.time()

        h2 = _chess_board_hash(ch["board"])
        ch["position_history"].append(h2)

        p1_color = ch["players"][ch["turn_order"][0]]["color"]
        p1_legal = _chess_legal_moves(ch["board"], p1_color, ch["castle_rights"], ch["en_passant"])
        p1_name  = ch["players"][ch["turn_order"][0]]["name"]
        board_txt2 = _chess_board_text(ch["board"], last_move=ai_label, perspective=p1_color)

        if len(p1_legal) == 0 and _chess_in_check(ch["board"], p1_color):
            _send(gid, f"🤖 AI plays {ai_label}.\n♟ Checkmate! AI wins! Better luck next time.\n\n{board_txt2}")
            _chess_reset(session); return

        if len(p1_legal) == 0:
            _send(gid, f"🤖 AI plays {ai_label}.\n♟ Stalemate! It's a draw.\n\n{board_txt2}")
            _chess_reset(session); return

        check2 = "⚠️ CHECK! " if _chess_in_check(ch["board"], p1_color) else ""
        ch["current_turn"] = 0
        _send(gid, f"🤖 AI plays {ai_label}. {check2}Your turn, {p1_name}!\n\n{board_txt2}")
        return

    # PvP — show board to both
    next_name = ch["players"][next_uid]["name"]
    _send(
        gid,
        f"{sender_name} played {ch['last_move']}. {check_notice}It's now {next_name}'s turn.\n\n{board_txt}",
        reply_to_id=msg_id,
    )
