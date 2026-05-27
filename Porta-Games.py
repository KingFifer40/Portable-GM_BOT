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

# This comment here is just to add more data so I can update it, nothing essential and should be removed as soon as possible, i am simply making a commit.

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
                    "Example: #start c4 hard  |  #start ttt  |  #start chess easy",
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

    # ─── #board — resend the current chess board ─────────────────────────────
    if cmd == "#board":
        if session.get("active_game") == "chess":
            chess_board(gid, session, msg_id)
        else:
            _send(gid, "No chess game is currently running.", reply_to_id=msg_id)
        return True

    # ─── Chess moves: #e2e4, #O-O, #O-O-O, #e7e8Q ────────────────────────────
    if session.get("active_game") == "chess":
        move_str = cmd[1:] if cmd.startswith("#") else ""
        if _is_chess_move(move_str):
            chess_move(gid, session, sender_id, sender_name, move_str, msg_id, chess_enabled)
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
# │  UNO  —  DM-based multiplayer card game                                 │
# └─────────────────────────────────────────────────────────────────────────┘
# State dict keys used by the main bot:
#   state["state"]    : "lobby" | "playing" | "done"
#   state["players"]  : list of uid strings (turn order)
#   state["current"]  : index into players for whose turn it is
#   state["names"]    : {uid: display_name}
#   state["hands"]    : {uid: [card, ...]}
#   state["deck"]     : [card, ...]  remaining draw pile
#   state["discard"]  : [card, ...]  top is [-1]
#   state["host"]     : uid of the player who opened the lobby
#   state["group"]    : group name string
#   state["drawn"]    : bool — current player drew this turn (may pass)
#   state["direction"]: 1 (clockwise) or -1 (counter-clockwise)
#   state["last_move"]: float timestamp of last action (for idle kick)
#   state["skip_next"]: bool — next player's turn is skipped
#   state["color"]    : active color (after wild is played)
# =============================================================================

# ── Card definitions ──────────────────────────────────────────────────────────

_UNO_COLORS  = ["Red", "Yellow", "Green", "Blue"]
_UNO_NUMBERS = ["0","1","2","3","4","5","6","7","8","9"]
_UNO_SPECIALS = ["Skip", "Reverse", "Draw Two"]
_UNO_WILDS   = ["Wild", "Wild Draw Four"]

_COLOR_EMOJI = {
    "Red":    "🔴",
    "Yellow": "🟡",
    "Green":  "🟢",
    "Blue":   "🔵",
    "Wild":   "🃏",
}

UNO_IDLE_SECONDS = 120   # kick player after 2 minutes of inactivity
UNO_MAX_PLAYERS  = 10
UNO_MIN_PLAYERS  = 2
UNO_HAND_SIZE    = 7


def _uno_make_deck():
    deck = []
    for color in _UNO_COLORS:
        deck.append(f"{color} 0")
        for num in _UNO_NUMBERS[1:]:
            deck.append(f"{color} {num}")
            deck.append(f"{color} {num}")
        for sp in _UNO_SPECIALS:
            deck.append(f"{color} {sp}")
            deck.append(f"{color} {sp}")
    for _ in range(4):
        deck.append("Wild")
        deck.append("Wild Draw Four")
    random.shuffle(deck)
    return deck


def _uno_card_emoji(card):
    """Return a short emoji+text representation of a card."""
    if card in ("Wild", "Wild Draw Four"):
        return f"🃏 {card}"
    parts = card.split(" ", 1)
    color = parts[0]
    face  = parts[1] if len(parts) > 1 else ""
    return f"{_COLOR_EMOJI.get(color, '')} {card}"


def _uno_hand_text(hand):
    """Format a player's hand as a numbered list."""
    lines = ["Your hand:"]
    for i, card in enumerate(hand, 1):
        lines.append(f"  {i}. {_uno_card_emoji(card)}")
    return "\n".join(lines)


def _uno_top_card_text(state):
    top   = state["discard"][-1]
    color = state["color"]
    base  = f"Top card: {_uno_card_emoji(top)}"
    if top in ("Wild", "Wild Draw Four"):
        base += f"  (active color: {_COLOR_EMOJI.get(color,'')} {color})"
    return base


def _uno_can_play(card, top, active_color):
    """Return True if card can legally be played on top/active_color."""
    if card in ("Wild", "Wild Draw Four"):
        return True
    parts = card.split(" ", 1)
    card_color = parts[0]
    card_face  = parts[1] if len(parts) > 1 else ""
    if card_color == active_color:
        return True
    top_parts = top.split(" ", 1) if top not in ("Wild","Wild Draw Four") else [None, None]
    top_face = top_parts[1] if top not in ("Wild","Wild Draw Four") else None
    if top_face and card_face == top_face:
        return True
    return False


def _uno_active_color(state):
    top = state["discard"][-1]
    if top not in ("Wild", "Wild Draw Four"):
        return top.split(" ", 1)[0]
    return state.get("color", "Red")


def _uno_draw_cards(state, n):
    """Draw n cards from deck, reshuffling discard if needed. Returns list."""
    drawn = []
    for _ in range(n):
        if not state["deck"]:
            # reshuffle all but top discard card
            if len(state["discard"]) > 1:
                top = state["discard"].pop()
                state["deck"] = state["discard"]
                random.shuffle(state["deck"])
                state["discard"] = [top]
            else:
                break  # truly out of cards
        if state["deck"]:
            drawn.append(state["deck"].pop())
    return drawn


def _uno_next_turn(state, skip=False):
    """Advance state["current"] by direction, optionally skipping one."""
    n = len(state["players"])
    steps = 2 if skip else 1
    state["current"] = (state["current"] + state["direction"] * steps) % n
    state["drawn"]   = False


def _uno_announce_turn(gid, state, sdm, sg):
    """DM the current player their hand + top card, and post whose turn it is in group."""
    uid  = state["players"][state["current"]]
    name = state["names"].get(uid, uid)
    top_txt  = _uno_top_card_text(state)
    hand_txt = _uno_hand_text(state["hands"][uid])
    card_count_line = "  ".join(
        f"{state['names'].get(u, u)}: {len(state['hands'][u])} card{'s' if len(state['hands'][u])!=1 else ''}"
        for u in state["players"] if u != uid
    )
    dm_lines = [
        f"━━━ Your turn! ━━━",
        top_txt,
        hand_txt,
        "",
        f"Other players: {card_count_line}" if card_count_line else "",
        "",
        "Commands: #play <card>  |  #draw  |  #hand  |  #pass (after drawing)  |  #quit",
        "Example: #play Red 7   or   #play Wild (then you'll be asked for a color)",
    ]
    sdm(uid, "\n".join(l for l in dm_lines if l != "" or True))
    sg(gid, f"🃏 It's {name}'s turn! ({len(state['hands'][uid])} cards)")
    state["last_move"] = time.time()


# ── Public API called by Porta-GMBOT ─────────────────────────────────────────

def uno_help_text():
    return (
        "🃏 UNO — How to play:\n"
        "#start uno   — open a lobby\n"
        "#join        — join the lobby\n"
        "#start uno go — host starts the game (deals cards)\n"
        "\nIn-game (via DM from the bot):\n"
        "#hand        — show your cards\n"
        "#play <card> — play a card  e.g. #play Red 7  or  #play Wild\n"
        "#draw        — draw a card\n"
        "#pass        — pass after drawing\n"
        "#status      — show group game status\n"
        "#quit        — leave the game"
    )


def uno_start(gid, group_name, sender_id, sender_name, enabled, sg, sdm):
    """Open a lobby. Returns the new state dict, or None on failure."""
    if not enabled:
        sg(gid, "🃏 UNO is currently disabled. An admin must enable it first.")
        return None
    state = {
        "state":     "lobby",
        "players":   [str(sender_id)],
        "names":     {str(sender_id): sender_name},
        "hands":     {},
        "deck":      [],
        "discard":   [],
        "host":      str(sender_id),
        "group":     group_name,
        "current":   0,
        "direction": 1,
        "drawn":     False,
        "color":     "Red",
        "last_move": time.time(),
        "skip_next": False,
    }
    sg(gid,
       f"🃏 {sender_name} opened a UNO lobby!\n"
       f"Type #join to join. Host: say #start uno go when everyone is in "
       f"(need {UNO_MIN_PLAYERS}–{UNO_MAX_PLAYERS} players).")
    return state


def uno_join(gid, state, sender_id, sender_name, sg, sdm):
    """Add a player to the lobby."""
    uid = str(sender_id)
    if uid in state["players"]:
        sg(gid, f"{sender_name}, you're already in the lobby!")
        return
    if len(state["players"]) >= UNO_MAX_PLAYERS:
        sg(gid, f"Sorry {sender_name}, the lobby is full ({UNO_MAX_PLAYERS} players max).")
        return
    state["players"].append(uid)
    state["names"][uid] = sender_name
    names_list = ", ".join(state["names"][u] for u in state["players"])
    sg(gid, f"🃏 {sender_name} joined the UNO lobby! Players: {names_list}")


def uno_begin(gid, state, sender_id, sg, sdm):
    """Host starts the game — deal cards and flip first card."""
    uid = str(sender_id)
    if uid != state["host"]:
        sg(gid, "Only the host can start the game.")
        return
    if len(state["players"]) < UNO_MIN_PLAYERS:
        sg(gid, f"Need at least {UNO_MIN_PLAYERS} players to start. Currently: {len(state['players'])}.")
        return

    state["deck"]    = _uno_make_deck()
    state["discard"] = []
    state["hands"]   = {u: [] for u in state["players"]}

    # Deal hands
    for u in state["players"]:
        state["hands"][u] = _uno_draw_cards(state, UNO_HAND_SIZE)

    # Flip first card — skip wilds as starting card
    first = None
    while True:
        card = state["deck"].pop()
        if card not in ("Wild", "Wild Draw Four"):
            first = card
            break
        state["deck"].insert(0, card)  # put wild back at bottom

    state["discard"] = [first]
    state["color"]   = first.split(" ", 1)[0]
    state["state"]   = "playing"
    state["current"] = 0
    state["direction"] = 1
    state["drawn"]   = False
    state["last_move"] = time.time()

    # Apply first-card special effects
    skip_first = False
    if " Skip" in first:
        skip_first = True
    elif " Reverse" in first:
        state["direction"] = -1
    elif " Draw Two" in first:
        victim_uid = state["players"][1 % len(state["players"])]
        drawn = _uno_draw_cards(state, 2)
        state["hands"][victim_uid].extend(drawn)
        skip_first = True

    player_list = " → ".join(state["names"][u] for u in state["players"])
    sg(gid,
       f"🃏 UNO starts! Turn order: {player_list}\n"
       f"First card: {_uno_card_emoji(first)}")

    # DM each player their hand
    for u in state["players"]:
        hand_txt = _uno_hand_text(state["hands"][u])
        sdm(u, f"🃏 UNO has started in {state['group']}!\n{hand_txt}\n\nWait for your turn.")

    if skip_first:
        first_name = state["names"][state["players"][0]]
        sg(gid, f"⏭ {first_name} is skipped by the first card!")
        _uno_next_turn(state)

    _uno_announce_turn(gid, state, sdm, sg)


def uno_show_hand(state, uid, sdm):
    """DM the player their current hand."""
    uid = str(uid)
    hand = state["hands"].get(uid, [])
    sdm(uid, _uno_hand_text(hand))


def uno_draw(gid, state, uid, sg, sdm):
    """Current player draws a card."""
    uid = str(uid)
    cur_uid = state["players"][state["current"]]
    if uid != cur_uid:
        sdm(uid, "It's not your turn.")
        return
    if state["drawn"]:
        sdm(uid, "You already drew this turn. Use #pass to end your turn, or #play <card>.")
        return

    drawn = _uno_draw_cards(state, 1)
    if not drawn:
        sdm(uid, "The deck is empty — no card to draw!")
        return

    state["hands"][uid].extend(drawn)
    state["drawn"] = True
    state["last_move"] = time.time()
    card = drawn[0]
    top  = state["discard"][-1]
    active_color = _uno_active_color(state)

    if _uno_can_play(card, top, active_color):
        sdm(uid,
            f"You drew: {_uno_card_emoji(card)}\n"
            f"You can play it! Use #play {card}  or  #pass to skip.")
    else:
        sdm(uid,
            f"You drew: {_uno_card_emoji(card)}\n"
            f"Can't play it. Use #pass to end your turn.")


def uno_pass(gid, state, uid, sg, sdm):
    """Pass after drawing."""
    uid = str(uid)
    cur_uid = state["players"][state["current"]]
    if uid != cur_uid:
        sdm(uid, "It's not your turn.")
        return
    if not state["drawn"]:
        sdm(uid, "You must draw a card first (#draw) before you can pass.")
        return
    name = state["names"].get(uid, uid)
    sg(gid, f"➡️ {name} passes.")
    _uno_next_turn(state)
    _uno_announce_turn(gid, state, sdm, sg)


def uno_play_card(gid, state, uid, card_text, sg, sdm):
    """Play a card, with wild color-pick handling."""
    uid = str(uid)
    cur_uid = state["players"][state["current"]]
    if uid != cur_uid:
        sdm(uid, "It's not your turn.")
        return

    hand = state["hands"][uid]
    name = state["names"].get(uid, uid)

    # ── Wild color pick in progress ───────────────────────────────────────────
    if state.get("color_pending") and state.get("color_pending_uid") == uid:
        chosen = card_text.strip().title()
        if chosen not in _UNO_COLORS:
            sdm(uid,
                f"'{card_text}' isn't a valid color. Choose one:\n"
                f"  #play Red\n  #play Yellow\n  #play Green\n  #play Blue")
            return
        state["color"] = chosen
        state.pop("color_pending", None)
        matched = state.pop("color_pending_card", "Wild")
        sg(gid, f"🎨 {name} chose {_COLOR_EMOJI.get(chosen,'')} {chosen}!")

        # Apply Wild Draw Four effect
        if matched == "Wild Draw Four":
            next_idx = (state["current"] + state["direction"]) % len(state["players"])
            victim_uid = state["players"][next_idx]
            drawn_cards = _uno_draw_cards(state, 4)
            state["hands"][victim_uid].extend(drawn_cards)
            victim_name = state["names"][victim_uid]
            sg(gid, f"➕4️⃣ {victim_name} draws 4 cards and is skipped!")
            sdm(victim_uid,
                f"You were hit with Wild Draw Four! You drew 4 cards.\n"
                f"{_uno_hand_text(state['hands'][victim_uid])}")
            _uno_next_turn(state, skip=True)
        else:
            _uno_next_turn(state)

        # Check win (hand was already removed before color pick prompt)
        if len(hand) == 0:
            _uno_handle_win(gid, state, uid, sg, sdm)
            return
        if len(hand) == 1:
            sg(gid, f"🔔 UNO! {name} has one card left!")

        _uno_announce_turn(gid, state, sdm, sg)
        return

    # ── Normal play ──────────────────────────────────────────────────────────
    top          = state["discard"][-1]
    active_color = _uno_active_color(state)

    card_text_norm = card_text.strip().title()
    matched = None
    for c in hand:
        if c.lower() == card_text_norm.lower():
            matched = c
            break
    if matched is None:
        for c in hand:
            if card_text_norm.lower() in c.lower():
                matched = c
                break

    if matched is None:
        sdm(uid,
            f"Card '{card_text}' not found in your hand.\n"
            f"{_uno_hand_text(hand)}\n"
            "Tip: type the card name exactly as shown, e.g.  #play Red 7")
        return

    if not _uno_can_play(matched, top, active_color):
        sdm(uid,
            f"Can't play {_uno_card_emoji(matched)} on {_uno_top_card_text(state)}.\n"
            f"Must match color ({active_color}) or face value.")
        return

    # Remove from hand and place on discard
    hand.remove(matched)
    state["discard"].append(matched)
    state["drawn"]     = False
    state["last_move"] = time.time()

    sg(gid, f"🃏 {name} played {_uno_card_emoji(matched)}!")

    if len(hand) == 1:
        sg(gid, f"🔔 UNO! {name} has one card left!")

    if len(hand) == 0:
        _uno_handle_win(gid, state, uid, sg, sdm)
        return

    # Wild — prompt for color, don't advance turn yet
    if matched in ("Wild", "Wild Draw Four"):
        state["color_pending"]      = True
        state["color_pending_card"] = matched
        state["color_pending_uid"]  = uid
        sdm(uid,
            f"You played {_uno_card_emoji(matched)}!\n"
            f"Choose a color:\n"
            f"  #play Red\n  #play Yellow\n  #play Green\n  #play Blue")
        sg(gid, f"🎨 {name} is choosing a color...")
        return

    # Non-wild: set color and apply effects
    state["color"] = matched.split(" ", 1)[0]
    face  = matched.split(" ", 1)[1] if " " in matched else ""
    skip  = False

    if face == "Reverse":
        state["direction"] *= -1
        if len(state["players"]) == 2:
            skip = True
        else:
            sg(gid, "🔄 Direction reversed!")

    elif face == "Skip":
        skip = True
        next_idx = (state["current"] + state["direction"]) % len(state["players"])
        skipped_name = state["names"][state["players"][next_idx]]
        sg(gid, f"⏭ {skipped_name} is skipped!")

    elif face == "Draw Two":
        next_idx = (state["current"] + state["direction"]) % len(state["players"])
        victim_uid = state["players"][next_idx]
        drawn_cards = _uno_draw_cards(state, 2)
        state["hands"][victim_uid].extend(drawn_cards)
        victim_name = state["names"][victim_uid]
        sg(gid, f"➕2️⃣ {victim_name} draws 2 cards and is skipped!")
        sdm(victim_uid,
            f"You were hit with Draw Two! Drew 2 cards.\n"
            f"{_uno_hand_text(state['hands'][victim_uid])}")
        skip = True

    _uno_next_turn(state, skip=skip)
    _uno_announce_turn(gid, state, sdm, sg)


def _uno_handle_win(gid, state, uid, sg, sdm):
    """Player played their last card — they win."""
    name = state["names"].get(uid, uid)
    sg(gid,
       f"🎉 {name} played their last card and wins UNO! 🏆\n"
       f"Thanks for playing everyone!")
    state["state"] = "done"


def uno_quit_player(gid, state, uid, name, sg, sdm):
    """Remove a player from the game. Returns True if game ended."""
    uid = str(uid)
    if uid not in state["players"]:
        return False

    idx = state["players"].index(uid)
    state["players"].remove(uid)
    state["names"].pop(uid, None)

    # Return hand to deck
    hand = state["hands"].pop(uid, [])
    state["deck"].extend(hand)
    random.shuffle(state["deck"])

    sg(gid, f"🚪 {name} left the UNO game.")

    if len(state["players"]) < UNO_MIN_PLAYERS:
        if state["players"]:
            winner_uid  = state["players"][0]
            winner_name = state["names"].get(winner_uid, winner_uid)
            sg(gid, f"Not enough players to continue. {winner_name} wins by default!")
        else:
            sg(gid, "Everyone left — UNO game over!")
        state["state"] = "done"
        return True

    # Fix current index if needed
    if state["state"] == "playing":
        n = len(state["players"])
        if idx <= state["current"]:
            state["current"] = max(0, state["current"] - 1)
        state["current"] = state["current"] % n
        _uno_announce_turn(gid, state, sdm, sg)

    return False


def uno_check_idle(gid, state, sg, sdm):
    """Kick the current player if they've been idle too long. Returns True if game ended."""
    if state["state"] != "playing":
        return False
    last = state.get("last_move", time.time())
    if time.time() - last < UNO_IDLE_SECONDS:
        return False

    uid  = state["players"][state["current"]]
    name = state["names"].get(uid, uid)
    sg(gid, f"⏰ {name} took too long and is kicked from UNO!")
    sdm(uid, "You were removed from the UNO game for inactivity.")
    return uno_quit_player(gid, state, uid, name, sg, sdm)


def uno_status(gid, state, sg):
    """Post a summary of the current game state to the group."""
    if state["state"] == "lobby":
        players_list = ", ".join(state["names"][u] for u in state["players"])
        sg(gid, f"🃏 UNO Lobby — Players: {players_list}\nHost: say #start uno go to begin.")
        return
    if state["state"] != "playing":
        sg(gid, "No active UNO game.")
        return
    cur_uid  = state["players"][state["current"]]
    cur_name = state["names"].get(cur_uid, cur_uid)
    top_txt  = _uno_top_card_text(state)
    counts   = "\n".join(
        f"  {state['names'].get(u,u)}: {len(state['hands'][u])} card{'s' if len(state['hands'][u])!=1 else ''}"
        for u in state["players"]
    )
    direction = "➡️ clockwise" if state["direction"] == 1 else "⬅️ counter-clockwise"
    sg(gid,
       f"🃏 UNO Status\n"
       f"{top_txt}\n"
       f"Turn: {cur_name}  |  Direction: {direction}\n"
       f"Card counts:\n{counts}")

# =============================================================================
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  CHESS                                                                  │
# └─────────────────────────────────────────────────────────────────────────┘
# =============================================================================
# Full chess engine: legal move generation (including castling, en passant,
# promotion), check/checkmate/stalemate detection, threefold-repetition,
# 50-move rule, and a minimax AI with alpha-beta pruning.
#
# Board representation: 8x8 list of lists.
#   - Uppercase = White pieces  (K Q R B N P)
#   - Lowercase = Black pieces  (k q r b n p)
#   - ''        = empty square
#
# Coordinate notation: files a-h (0-7), ranks 1-8 (row 0 = rank 8, row 7 = rank 1)
# Move input: "e2e4", "O-O", "O-O-O", "e7e8Q" (promotion)
# =============================================================================

# ── Emoji board display ───────────────────────────────────────────────────────
_CHESS_PIECES = {
    "K": "🔵", "Q": "🟦", "R": "🟪", "B": "🔷", "N": "💠", "P": "🔹",
    "k": "🔴", "q": "🟥", "r": "🟣",  "b": "🔶", "n": "🔸", "p": "❤️",
    "":  None,
}
_DARK  = "🟫"
_LIGHT = "🟨"
_FILE_LABELS = "🅰️🅱️🇨🇩🇪🇫🇬🇭"

_CHESS_AI_REWARDS = {"easy": 75, "medium": 175, "hard": 300}

def _fresh_chess():
    return {
        "board": None,
        "players": {},          # {"white": uid, "black": uid}  uid=None means AI
        "names":   {},          # {uid: name}
        "turn": "white",        # whose turn
        "status": "waiting",    # "waiting" | "playing" | "done"
        "castling": {"white": {"K": True, "Q": True}, "black": {"k": True, "q": True}},
        "en_passant": None,     # target square (row, col) or None
        "halfmove_clock": 0,    # for 50-move rule
        "fullmove": 1,
        "position_history": [], # list of board FEN-like strings for repetition
        "ai_difficulty": "medium",
        "ai_color": None,
    }

def _chess_reset(session):
    session["chess"]       = _fresh_chess()
    session["active_game"] = None
    session["last_move_time"] = None

def _init_chess_board():
    b = [[""] * 8 for _ in range(8)]
    order = ["r","n","b","q","k","b","n","r"]
    for c, p in enumerate(order):
        b[0][c] = p
        b[7][c] = p.upper()
    for c in range(8):
        b[1][c] = "p"
        b[6][c] = "P"
    return b

def _render_chess_board(board, last_move=None):
    """Return a GroupMe-friendly text board."""
    lines = []
    lm_squares = set()
    if last_move:
        lm_squares = {last_move[0], last_move[1]}
    rank_nums = ["8","7","6","5","4","3","2","1"]
    for row in range(8):
        row_str = rank_nums[row] + " "
        for col in range(8):
            sq = (row, col)
            piece = board[row][col]
            if piece:
                row_str += _CHESS_PIECES[piece]
            else:
                row_str += _DARK if (row + col) % 2 == 1 else _LIGHT
        lines.append(row_str)
    lines.append("  " + "".join(_FILE_LABELS[i] for i in range(8)))
    lines.append("⬜=light  🟫=dark")
    lines.append("White: 🔵K 🟦Q 🟪R 🔷B 💠N 🔹P")
    lines.append("Black: 🔴k 🟥q 🟣r 🔶b 🔸n ❤️p")
    return "\n".join(lines)

def _sq_to_rc(sq_str):
    """'e2' → (row=6, col=4)"""
    f = ord(sq_str[0].lower()) - ord('a')
    r = 8 - int(sq_str[1])
    return (r, f)

def _rc_to_sq(row, col):
    return chr(ord('a') + col) + str(8 - row)

def _parse_move(move_str):
    """
    Parse 'e2e4', 'e7e8Q' etc.
    Returns (from_rc, to_rc, promotion) or None on failure.
    """
    s = move_str.strip()
    if len(s) < 4:
        return None
    try:
        fr = _sq_to_rc(s[0:2])
        to = _sq_to_rc(s[2:4])
    except Exception:
        return None
    promo = s[4].upper() if len(s) >= 5 and s[4] in "QRBNqrbn" else None
    return (fr, to, promo)

def _is_chess_move(move_str):
    """Return True if the string looks like a chess move command."""
    s = move_str.strip()
    if s in ("O-O", "O-O-O", "0-0", "0-0-0"):
        return True
    if len(s) >= 4:
        try:
            _sq_to_rc(s[0:2])
            _sq_to_rc(s[2:4])
            return True
        except Exception:
            return False
    return False

# ── Piece movement helpers ────────────────────────────────────────────────────

def _on_board(r, c):
    return 0 <= r < 8 and 0 <= c < 8

def _is_white(p): return p and p.isupper()
def _is_black(p): return p and p.islower()
def _color(p):    return "white" if _is_white(p) else ("black" if _is_black(p) else None)
def _enemy(color): return "black" if color == "white" else "white"

def _piece_moves_raw(board, row, col, color, en_passant=None):
    """
    Return all pseudo-legal destination squares for the piece at (row,col).
    Does NOT filter moves that leave the king in check.
    """
    p = board[row][col]
    if not p or _color(p) != color:
        return []
    P = p.upper()
    moves = []
    opp = _is_white if color == "black" else _is_black
    mine = _is_black if color == "black" else _is_white
    pawn_dir = 1 if color == "black" else -1
    start_row = 1 if color == "black" else 6

    if P == "P":
        nr = row + pawn_dir
        if _on_board(nr, col) and not board[nr][col]:
            moves.append((nr, col, None))
            if row == start_row and not board[row + 2*pawn_dir][col]:
                moves.append((row + 2*pawn_dir, col, None))
        for dc in (-1, 1):
            nc = col + dc
            if _on_board(nr, nc):
                if opp(board[nr][nc]):
                    moves.append((nr, nc, None))
                elif en_passant == (nr, nc):
                    moves.append((nr, nc, "ep"))
        # Auto-promote — caller must handle promotion choice
        return moves

    if P == "N":
        for dr, dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
            nr, nc = row+dr, col+dc
            if _on_board(nr,nc) and not mine(board[nr][nc]):
                moves.append((nr,nc,None))
        return moves

    if P == "K":
        for dr in (-1,0,1):
            for dc in (-1,0,1):
                if dr==0 and dc==0: continue
                nr,nc = row+dr,col+dc
                if _on_board(nr,nc) and not mine(board[nr][nc]):
                    moves.append((nr,nc,None))
        return moves

    # Sliding pieces
    dirs = []
    if P in ("R","Q"): dirs += [(0,1),(0,-1),(1,0),(-1,0)]
    if P in ("B","Q"): dirs += [(1,1),(1,-1),(-1,1),(-1,-1)]
    for dr,dc in dirs:
        nr,nc = row+dr,col+dc
        while _on_board(nr,nc):
            if mine(board[nr][nc]): break
            moves.append((nr,nc,None))
            if board[nr][nc]: break  # hit enemy, stop
            nr,nc = nr+dr,nc+dc
    return moves

def _find_king(board, color):
    k = "K" if color == "white" else "k"
    for r in range(8):
        for c in range(8):
            if board[r][c] == k:
                return (r,c)
    return None

def _is_attacked(board, row, col, by_color):
    """Return True if (row,col) is attacked by any piece of by_color."""
    for r in range(8):
        for c in range(8):
            if _color(board[r][c]) == by_color:
                raw = _piece_moves_raw(board, r, c, by_color)
                if any(m[0]==row and m[1]==col for m in raw):
                    return True
    return False

def _in_check(board, color):
    kr, kc = _find_king(board, color)
    return _is_attacked(board, kr, kc, _enemy(color))

def _apply_move(board, fr, to, promo=None, en_passant=None, castling=None):
    """
    Apply move to a COPY of board and return (new_board, new_ep, new_castling, captured).
    Handles en passant, castling, and promotion.
    """
    import copy
    b = copy.deepcopy(board)
    cast = copy.deepcopy(castling) if castling else {"white":{"K":True,"Q":True},"black":{"k":True,"q":True}}
    r1,c1 = fr; r2,c2 = to
    piece = b[r1][c1]
    captured = b[r2][c2]
    new_ep = None

    # En passant capture
    if piece.upper() == "P" and en_passant == to:
        cap_row = r1  # the captured pawn is on the same rank as the moving pawn
        b[cap_row][c2] = ""
        captured = "ep"

    # Castling
    if piece in ("K","k") and abs(c2-c1) == 2:
        if c2 > c1:  # kingside
            b[r1][5] = b[r1][7]; b[r1][7] = ""
        else:        # queenside
            b[r1][3] = b[r1][0]; b[r1][0] = ""

    b[r2][c2] = piece
    b[r1][c1] = ""

    # Promotion
    if piece == "P" and r2 == 0:
        b[r2][c2] = promo if promo else "Q"
    if piece == "p" and r2 == 7:
        b[r2][c2] = promo.lower() if promo else "q"

    # Update en passant
    if piece.upper() == "P" and abs(r2-r1) == 2:
        new_ep = ((r1+r2)//2, c1)

    # Update castling rights
    color = _color(piece)
    if piece == "K": cast["white"]["K"] = cast["white"]["Q"] = False
    if piece == "k": cast["black"]["k"] = cast["black"]["q"] = False
    if (r1,c1)==(7,7) or (r2,c2)==(7,7): cast["white"]["K"] = False
    if (r1,c1)==(7,0) or (r2,c2)==(7,0): cast["white"]["Q"] = False
    if (r1,c1)==(0,7) or (r2,c2)==(0,7): cast["black"]["k"] = False
    if (r1,c1)==(0,0) or (r2,c2)==(0,0): cast["black"]["q"] = False

    return b, new_ep, cast, captured

def _legal_moves(board, color, castling, en_passant):
    """Return list of (from_rc, to_rc, promo_or_flag) legal moves for color."""
    legal = []
    for r in range(8):
        for c in range(8):
            if _color(board[r][c]) != color:
                continue
            for (tr,tc,flag) in _piece_moves_raw(board, r, c, color, en_passant):
                nb, _, _, _ = _apply_move(board, (r,c), (tr,tc), None, en_passant, castling)
                if not _in_check(nb, color):
                    # Handle promotions: generate all 4 options
                    p = board[r][c].upper()
                    if p == "P" and ((color=="white" and tr==0) or (color=="black" and tr==7)):
                        for pr in ("Q","R","B","N"):
                            legal.append(((r,c),(tr,tc),pr))
                    else:
                        legal.append(((r,c),(tr,tc),flag))

    # Castling
    king_row = 7 if color == "white" else 0
    king_col = 4
    if board[king_row][king_col] in ("K","k") and not _in_check(board, color):
        # Kingside
        ks_key = "K" if color=="white" else "k"
        qs_key = "Q" if color=="white" else "q"
        if castling[color].get(ks_key) and not board[king_row][5] and not board[king_row][6]:
            if not _is_attacked(board,king_row,5,_enemy(color)) and not _is_attacked(board,king_row,6,_enemy(color)):
                nb,_,_,_ = _apply_move(board,(king_row,4),(king_row,6),None,en_passant,castling)
                if not _in_check(nb,color):
                    legal.append(((king_row,4),(king_row,6),"castle_k"))
        # Queenside
        if castling[color].get(qs_key) and not board[king_row][3] and not board[king_row][2] and not board[king_row][1]:
            if not _is_attacked(board,king_row,3,_enemy(color)) and not _is_attacked(board,king_row,2,_enemy(color)):
                nb,_,_,_ = _apply_move(board,(king_row,4),(king_row,2),None,en_passant,castling)
                if not _in_check(nb,color):
                    legal.append(((king_row,4),(king_row,2),"castle_q"))
    return legal

def _board_key(board, color, castling, en_passant):
    return (str(board), color, str(castling), str(en_passant))

# ── Simple material evaluation for AI ────────────────────────────────────────
_PIECE_VALUES = {"P":100,"N":320,"B":330,"R":500,"Q":900,"K":20000}

def _evaluate(board):
    score = 0
    for row in board:
        for p in row:
            if p:
                v = _PIECE_VALUES.get(p.upper(), 0)
                score += v if p.isupper() else -v
    return score

def _minimax(board, depth, alpha, beta, maximizing, castling, en_passant):
    color = "white" if maximizing else "black"
    moves = _legal_moves(board, color, castling, en_passant)
    if not moves:
        if _in_check(board, color):
            return (-99999 if maximizing else 99999)
        return 0  # stalemate
    if depth == 0:
        return _evaluate(board)
    if maximizing:
        val = -100000
        for (fr,to,flag) in moves:
            promo = flag if flag in ("Q","R","B","N") else None
            nb,nep,ncast,_ = _apply_move(board,fr,to,promo,en_passant,castling)
            val = max(val, _minimax(nb,depth-1,alpha,beta,False,ncast,nep))
            alpha = max(alpha, val)
            if alpha >= beta: break
        return val
    else:
        val = 100000
        for (fr,to,flag) in moves:
            promo = flag if flag in ("Q","R","B","N") else None
            nb,nep,ncast,_ = _apply_move(board,fr,to,promo,en_passant,castling)
            val = min(val, _minimax(nb,depth-1,alpha,beta,True,ncast,nep))
            beta = min(beta, val)
            if alpha >= beta: break
        return val

def _ai_move(board, ai_color, difficulty, castling, en_passant):
    """Return best (fr,to,promo) for the AI."""
    depth = {"easy":1,"medium":2,"hard":3}.get(difficulty,2)
    moves = _legal_moves(board, ai_color, castling, en_passant)
    if not moves: return None
    maximizing_ai = (ai_color == "white")
    best, best_val = None, (-100000 if maximizing_ai else 100000)
    random.shuffle(moves)
    for (fr,to,flag) in moves:
        promo = flag if flag in ("Q","R","B","N") else None
        nb,nep,ncast,_ = _apply_move(board,fr,to,promo,en_passant,castling)
        val = _minimax(nb,depth-1,-100000,100000, not maximizing_ai, ncast, nep)
        if maximizing_ai and val > best_val:
            best_val, best = val, (fr,to,promo)
        elif not maximizing_ai and val < best_val:
            best_val, best = val, (fr,to,promo)
    return best

# ── Chess game commands ───────────────────────────────────────────────────────

def chess_start(gid, session, sender_id, sender_name, difficulty, msg_id, chess_enabled):
    if not chess_enabled:
        _send(gid, "♟ Chess is currently disabled.\nUse #state chess true as an admin to enable it.", reply_to_id=msg_id)
        return
    if any_game_active(session):
        _send(gid, "A game is already running! Use #quit to end it first.", reply_to_id=msg_id)
        return
    if difficulty not in ("easy","medium","hard"):
        difficulty = "medium"
    ch = session["chess"]
    ch.update(_fresh_chess())
    ch["board"]  = _init_chess_board()
    ch["status"] = "waiting"
    ch["players"]["white"] = str(sender_id)
    ch["names"][str(sender_id)] = sender_name
    ch["ai_difficulty"] = difficulty
    session["active_game"]   = "chess"
    session["last_move_time"] = time.time()
    _send(gid,
        f"♟ {sender_name} started Chess (plays White)!\n"
        f"Use #join to play as Black, or #addai [{difficulty}] for AI.\n"
        f"#quit to cancel.",
        reply_to_id=msg_id)

def chess_join(gid, session, sender_id, sender_name, msg_id, chess_enabled):
    if not chess_enabled:
        _send(gid, "♟ Chess is disabled.", reply_to_id=msg_id)
        return
    ch = session.get("chess", {})
    if ch.get("status") != "waiting":
        _send(gid, "No chess game is waiting for a player.", reply_to_id=msg_id)
        return
    if str(sender_id) == ch["players"].get("white"):
        _send(gid, "You already started this game as White!", reply_to_id=msg_id)
        return
    ch["players"]["black"] = str(sender_id)
    ch["names"][str(sender_id)] = sender_name
    ch["status"] = "playing"
    session["last_move_time"] = time.time()
    board_txt = _render_chess_board(ch["board"])
    white_name = ch["names"].get(ch["players"]["white"], "White")
    _send(gid,
        f"♟ {sender_name} joined as Black!\n"
        f"{white_name} (⬜ White) vs {sender_name} (⬛ Black)\n"
        f"\n{board_txt}\n"
        f"White moves first. Use #e2e4 style moves.",
        reply_to_id=msg_id)

def chess_addai(gid, session, sender_id, sender_name, difficulty, msg_id, chess_enabled):
    if not chess_enabled:
        _send(gid, "♟ Chess is disabled.", reply_to_id=msg_id)
        return
    ch = session.get("chess", {})
    if ch.get("status") != "waiting":
        _send(gid, "No chess game is waiting for a player.", reply_to_id=msg_id)
        return
    if difficulty not in ("easy","medium","hard"):
        difficulty = ch.get("ai_difficulty","medium")
    ch["players"]["black"] = None   # None = AI
    ch["ai_color"]    = "black"
    ch["ai_difficulty"] = difficulty
    ch["status"] = "playing"
    session["last_move_time"] = time.time()
    white_name = ch["names"].get(ch["players"]["white"], "White")
    board_txt = _render_chess_board(ch["board"])
    _send(gid,
        f"🤖 AI ({difficulty}) added as Black!\n"
        f"{white_name} (⬜ White) vs 🤖 AI (⬛ Black)\n"
        f"\n{board_txt}\n"
        f"Your move, {white_name}! Use #e2e4 style moves.",
        reply_to_id=msg_id)

def chess_board(gid, session, msg_id):
    ch = session.get("chess", {})
    if ch.get("status") not in ("waiting","playing"):
        _send(gid, "No active chess game.", reply_to_id=msg_id)
        return
    board_txt = _render_chess_board(ch["board"])
    turn = ch.get("turn","white").capitalize()
    _send(gid, f"♟ Current Board ({turn} to move):\n{board_txt}", reply_to_id=msg_id)

def chess_quit(gid, session, quitter_name, msg_id):
    _chess_reset(session)
    _send(gid, f"♟ Chess game ended by {quitter_name}.", reply_to_id=msg_id)

def chess_move(gid, session, sender_id, sender_name, move_str, msg_id, chess_enabled):
    if not chess_enabled:
        _send(gid, "♟ Chess is disabled.", reply_to_id=msg_id)
        return
    ch = session.get("chess", {})
    if ch.get("status") != "playing":
        _send(gid, "No chess game is in progress.", reply_to_id=msg_id)
        return

    color = ch["turn"]
    expected_uid = ch["players"].get(color)
    if expected_uid is not None and str(sender_id) != expected_uid:
        _send(gid, f"It's {color.capitalize()}'s turn!", reply_to_id=msg_id)
        return

    board    = ch["board"]
    castling = ch["castling"]
    ep       = ch["en_passant"]

    # Parse castling shorthand
    castle_side = None
    if move_str.upper() in ("O-O","0-0"):
        castle_side = "K" if color=="white" else "k"
    elif move_str.upper() in ("O-O-O","0-0-0"):
        castle_side = "Q" if color=="white" else "q"

    if castle_side:
        kr = 7 if color=="white" else 0
        legal = _legal_moves(board, color, castling, ep)
        kc_to = 6 if castle_side in ("K","k") else 2
        found = next((m for m in legal if m[0]==(kr,4) and m[1]==(kr,kc_to)), None)
        if not found:
            _send(gid, "Castling is not legal right now.", reply_to_id=msg_id)
            return
        fr, to, flag = found
        promo = None
    else:
        parsed = _parse_move(move_str)
        if not parsed:
            _send(gid, f"Can't parse move '{move_str}'. Use format: e2e4 or e7e8Q", reply_to_id=msg_id)
            return
        (fr, to, promo) = parsed
        legal = _legal_moves(board, color, castling, ep)
        # Find matching legal move
        match = None
        for (lf,lt,lp) in legal:
            if lf==fr and lt==to:
                # promotion: must match if specified
                if promo:
                    if lp == promo:
                        match = (lf,lt,lp); break
                else:
                    # default to queen if promotion required
                    if lp in ("Q","R","B","N"):
                        match = (lf,lt,"Q"); break
                    else:
                        match = (lf,lt,lp); break
        if not match:
            _send(gid, f"Illegal move: {move_str}. Make sure it's your piece and the move is legal.", reply_to_id=msg_id)
            return
        fr, to, promo_used = match
        promo = promo_used if promo_used in ("Q","R","B","N") else None

    # Apply
    nb, new_ep, new_cast, captured = _apply_move(board, fr, to, promo, ep, castling)
    ch["board"]     = nb
    ch["en_passant"] = new_ep
    ch["castling"]  = new_cast
    ch["halfmove_clock"] = 0 if (captured or board[fr[0]][fr[1]].upper()=="P") else ch["halfmove_clock"]+1
    if color == "black":
        ch["fullmove"] += 1

    # Position history for repetition
    bkey = _board_key(nb, _enemy(color), new_cast, new_ep)
    ch["position_history"].append(bkey)
    repetition = ch["position_history"].count(bkey) >= 3

    next_color = _enemy(color)
    ch["turn"] = next_color

    # Check game-end conditions
    next_legal = _legal_moves(nb, next_color, new_cast, new_ep)
    in_check   = _in_check(nb, next_color)
    move_label = _rc_to_sq(*fr) + _rc_to_sq(*to) + (promo or "")

    if not next_legal:
        if in_check:
            # Checkmate
            winner_name = sender_name
            winner_color = color
            loser_color  = next_color
            reward = _CHESS_AI_REWARDS.get(ch["ai_difficulty"], 175)
            board_txt = _render_chess_board(nb)
            _send(gid,
                f"♟ {move_label}\n{board_txt}\n\n"
                f"♟ Checkmate! {winner_name} wins! 🏆\n" +
                (f"🏅 +{reward} pts!" if ch["ai_color"] == loser_color else ""),
                reply_to_id=msg_id)
            if ch["ai_color"] == loser_color:
                _add_pts(gid, str(sender_id), sender_name, reward)
            _chess_reset(session)
            return
        else:
            board_txt = _render_chess_board(nb)
            _send(gid,
                f"♟ {move_label}\n{board_txt}\n\n♟ Stalemate — it's a draw!",
                reply_to_id=msg_id)
            _chess_reset(session)
            return

    if ch["halfmove_clock"] >= 100:
        board_txt = _render_chess_board(nb)
        _send(gid, f"♟ {move_label}\n{board_txt}\n\n♟ Draw by 50-move rule.", reply_to_id=msg_id)
        _chess_reset(session)
        return

    if repetition:
        board_txt = _render_chess_board(nb)
        _send(gid, f"♟ {move_label}\n{board_txt}\n\n♟ Draw by threefold repetition.", reply_to_id=msg_id)
        _chess_reset(session)
        return

    session["last_move_time"] = time.time()
    check_note = " (Check!)" if in_check else ""
    board_txt = _render_chess_board(nb, last_move=(fr,to))

    next_name = ch["names"].get(ch["players"].get(next_color,""), next_color.capitalize())
    if ch["players"].get(next_color) is None:
        next_name = f"🤖 AI ({ch['ai_difficulty']})"

    _send(gid,
        f"♟ {move_label}{check_note}\n{board_txt}\n\n"
        f"{next_color.capitalize()}'s turn: {next_name}",
        reply_to_id=msg_id)

    # AI response
    if ch["players"].get(next_color) is None:
        _chess_ai_respond(gid, session, next_color, chess_enabled)


def _chess_ai_respond(gid, session, ai_color, chess_enabled):
    """Run the AI move in a background thread."""
    import threading as _t
    def _do():
        ch = session.get("chess",{})
        if ch.get("status") != "playing": return
        board    = ch["board"]
        castling = ch["castling"]
        ep       = ch["en_passant"]
        diff     = ch.get("ai_difficulty","medium")
        best = _ai_move(board, ai_color, diff, castling, ep)
        if best is None:
            _send(gid, "🤖 AI has no legal moves.")
            return
        fr, to, promo = best
        move_str = _rc_to_sq(*fr) + _rc_to_sq(*to) + (promo or "")
        # Re-use chess_move to apply and announce
        import types
        fake_msg = {"user_id": None, "name": f"AI ({diff})", "id": None, "text": "#"+move_str}
        ch["players"][ai_color] = None  # keep as AI
        chess_move(gid, session, None, f"🤖 AI ({diff})", move_str, None, chess_enabled)
        ch["players"][ai_color] = None  # restore
    _t.Thread(target=_do, daemon=True).start()
