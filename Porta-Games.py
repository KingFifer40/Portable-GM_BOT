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
        "active_game": None,      # None | "connect4" | "tictactoe"
        "last_move_time": None,   # float — for timeout tracking
        "timeout_seconds": 300,

        # ── Connect Four state ─────────────────────────────────────────────
        "c4": _fresh_c4(),

        # ── Tic-Tac-Toe state ─────────────────────────────────────────────
        "ttt": _fresh_ttt(),
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
                         game_timeout_seconds):
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
            if games:
                _send(gid,
                    "Usage: #start <game> [difficulty]\n"
                    "Games: " + ", ".join(games) + "\n"
                    "Example: #start c4 hard  |  #start ttt",
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

        _send(gid, f"Unknown game '{game_arg}'.\nKnown games: c4, ttt", reply_to_id=msg_id)
        return True

    # ─── #join — joins whatever game is waiting for a second player ───────────
    if cmd == "#join":
        ag = session.get("active_game")
        if ag == "connect4":
            c4_join(gid, session, sender_id, sender_name, msg_id, connect4_enabled)
        elif ag == "tictactoe":
            ttt_join(gid, session, sender_id, sender_name, msg_id, tictactoe_enabled)
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