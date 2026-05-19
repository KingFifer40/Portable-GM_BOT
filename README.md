# Porta-GMBOT — GroupMe Bot with AI Chat

A portable GroupMe bot that lets your group play **Connect Four** and **Tic-Tac-Toe**, look up **scriptures**, chat with a **local AI** (via Ollama), earn **points**, and more. Built in Python, runs on Windows, Mac, or Linux.

---

## Features

- 🎮 **Connect Four** — two-player PvP or vs AI (minimax engine, easy/medium/hard)
- ✏️ **Tic-Tac-Toe** — classic 3×3 grid, PvP or vs AI (impossible/easy/medium/hard)
- 💰 **Points system** — earn points by fishing (`!fih`), stealing (`!steal`), and coin flipping (`!coin`); give points to others (`!give`); wager them on Connect Four games
- 🤖 **AI Chat** — powered by a local Ollama model with a shared group memory
- 🌐 **Web search** — the AI automatically searches DuckDuckGo when asked about current events, recent news, live scores, or anything beyond its training data
- 🎱 **Magic 8-Ball** — `?` + any question
- 📖 **Scripture lookup** — Bible (KJV) and Book of Mormon verse search
- 🖼️ **Profile picture swap** — the bot stamps "BOT" on its GroupMe avatar while active, then reverts automatically
- 🔒 **Safe by default** — hardened AI safety rules, English-only responses, spam cooldowns
- 🛠️ **Admin controls** — enable/disable individual features from inside the group
- 🖥️ **Control panel** — desktop GUI for managing groups, AI settings, points tuning, and auto-updates
- 🧙 **First-run setup wizard** — GUI on desktop (with live group picker), terminal fallback on servers

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.9+ | [python.org](https://python.org) |
| Ollama | [ollama.com](https://ollama.com) — install it, the bot handles the rest |
| GroupMe account + access token | Free — see setup below |

The Python dependencies (`requests`, `ddgs`, `Pillow`) are installed automatically on first run.

---

## Setup — just three steps

### 1. Clone the repo

```
git clone https://github.com/KingFifer40/Portable-GM_BOT.git
cd Portable-GM_BOT
```

### 2. Install Ollama

Download and install from [ollama.com](https://ollama.com). You don't need to do anything else — the bot starts Ollama automatically and downloads the AI model on first run.

### 3. Run the bot

```
python Porta-GMBOT.py
```

On first run, a **setup wizard** opens automatically:

- On **Windows / Mac / Linux desktop** → a GUI window appears
- On a **headless server** → clean terminal prompts instead

The wizard asks for:

| Field | Where to find it |
|---|---|
| **GroupMe Access Token** | [dev.groupme.com](https://dev.groupme.com) → log in → click your avatar → *Access Token* |
| **Dev Group** | Enter your token and click **Fetch My Groups** — pick your dev group from the list |
| **Ollama Model** | Pick from the scrollable list, or type any model name from [ollama.com/library](https://ollama.com/library) |

Settings are saved to `config.json` — the wizard won't run again unless that file is missing or incomplete.

After setup the bot will automatically:
1. Install any missing Python packages
2. Start Ollama if it is not already running
3. Download the AI model if not present *(may take a few minutes on first run)*
4. Build the custom bot model from the Modelfile
5. Connect to your GroupMe groups and go live

---

## First-time group setup

Once the bot is running, go to your **dev group** and send:

```
!add YOUR_GAME_GROUP_ID
```

The bot joins the game group and announces itself.

If you don't know your game group's ID, type `!listgroups` in the dev group and it will list all your groups with their IDs.

### Subgroup / Topic mode

If your group uses GroupMe's Topics feature and you want the bot to operate inside a specific topic while still reading admin roles from the main group, use the comma syntax:

```
!add MAIN_GROUP_ID,TOPIC_GROUP_ID
```

You can also browse and set topics from the **Groups tab** of the control panel GUI.

---

## Commands

### Game group — everyone

#### General

| Command | Description |
|---|---|
| `#help` | Show help categories |
| `#help game` | Game commands (Connect Four, Tic-Tac-Toe) |
| `#help 8ball` | Magic 8-Ball info |
| `#help scripture` | Scripture commands |
| `#help ai` | AI chat commands |
| `#help points` | Points commands |
| `#help gamepoints` | Connect Four betting & AI rewards |
| `#help admin` | Admin feature controls |
| `?<question>` | Magic 8-Ball |

#### Connect Four

| Command | Description |
|---|---|
| `#start c4 [easy\|medium\|hard]` | Start a new Connect Four game (default: medium) |
| `#join` | Join as Player 2 (triggers PvP betting phase) |
| `#addai [easy\|medium\|hard]` | Add AI engine as Player 2 |
| `#A` – `#G` | Drop your piece in that column |
| `#quit` | End the current game (bets are fully refunded) |
| `#timeout N` | Set inactivity timeout in seconds |
| `#stats` | Show current game bets and player info |

#### Tic-Tac-Toe

| Command | Description |
|---|---|
| `#start ttt [easy\|medium\|hard]` | Start a new Tic-Tac-Toe game (default: medium) |
| `#join` | Join as Player 2 |
| `#addai` | Add AI as Player 2 |
| `#A1` – `#C3` | Play your piece at that coordinate (column A–C, row 1–3) |
| `#quit` | End the current game |

#### PvP Betting

After both players join, a betting phase starts before play begins:

| Command | Description |
|---|---|
| `#pvpbet <amount>` | Wager points on yourself |
| `#pvpbet 0` | Skip betting |

Both players must bet (or skip) before moves are accepted. The **winner gets their own stake back plus the loser's stake**. If the game ends early or draws, all bets are fully refunded.

#### Spectator Betting

Anyone not playing can bet on a player using a **pari-mutuel (pool) system**:

| Command | Description |
|---|---|
| `#bet <amount> @player` | Bet on a player to win |

All bets form a single shared pool. Winners receive their stake back **plus a proportional share of the losers' pot**. Losers forfeit their stake. Use `#quit` to cancel and get your bet back.

#### Points

| Command | Description |
|---|---|
| `!points` | Check your point balance |
| `!fih` | Fish for points — win or lose! (5 min cooldown) |
| `!steal` | Steal points from a random person (5 min cooldown) |
| `!coin <h/t> <amount>` | Flip a coin to double or lose your bet (1 min cooldown) |
| `!give @username <amount>` | Give points to another player |
| `#leaderboard` | Show the top points rankings |

#### Inventory

| Command | Description |
|---|---|
| `!create "Name" <worth>` | Create a named item (you pay the worth in points) |
| `!items` | View your inventory |
| `!items @user` | View someone else's inventory |
| `!sellitem i<slot>` | Sell a creation back to the bot for its worth |
| `!give @user i<slot>` | Gift one of your creations to another user |

#### AI Chat

| Command | Description |
|---|---|
| `!ai <message>` | Chat with the AI (15 s cooldown) |
| `!aiset <text>` | Set the AI personality (60 s cooldown; clears memory) |
| `!aiforget` | Clear the group's shared AI conversation history (admins only) |

The AI can **search the web automatically** — just ask about current events, scores, or anything recent and it will run a DuckDuckGo search before responding. It can also look up scriptures using the same engine as `#findverse`.

#### Scripture

| Command | Description |
|---|---|
| `#randverse` | Random verse (Bible or Book of Mormon) |
| `#randverse bible` | Random Bible verse |
| `#randverse bom` | Random Book of Mormon verse |
| `#findverse <Book> <Ch:V>` | Direct verse lookup — e.g. `#findverse Alma 32:21` |
| `#findverse "keyword"` | Keyword search across both books |
| `#findverse bible "keyword"` | Keyword search — Bible only |
| `#findverse bom "keyword"` | Keyword search — Book of Mormon only |

#### Feature status

| Command | Description |
|---|---|
| `#state` | Show current state of all features |
| `#state <feature>` | Check one feature's state |

---

### Game group — admins only

| Command | Description |
|---|---|
| `#state all true/false` | Master on/off switch for the whole bot |
| `#state ai true/false` | Enable or disable AI chat |
| `#state 8ball true/false` | Enable or disable Magic 8-Ball |
| `#state scripture true/false` | Enable or disable scripture commands |
| `#state connect4 true/false` | Enable or disable Connect Four |
| `#state tictactoe true/false` | Enable or disable Tic-Tac-Toe |
| `!aiswitch true/false` | Enable or disable AI (same as `#state ai`) |
| `!aiforget` | Clear the shared AI conversation history |

---

### Dev group — developer only

| Command | Description |
|---|---|
| `!help` | Show dev commands |
| `!listgroups` | List all groups your token is in (with IDs) |
| `!listgroups MAIN_GROUP_ID` | List topics/subgroups for a specific group |
| `!add GROUPID` | Set the active game group |
| `!add MAIN_ID,SUB_ID` | Set bot to a topic/subgroup |
| `!reload` | Restart the bot script |
| `!state true/false` | Enable or disable game responses |
| `!aiswitch true/false` | Enable or disable AI responses |

---

## Points & Rewards

### Earning points

| Activity | Points |
|---|---|
| `!fih` (lucky cast) | +5 to +40 pts (random) |
| `!fih` (unlucky) | −5 to −40 pts (25% chance) |
| `!fih` (Golden Fih! — 1-in-1000 jackpot) | +2000 pts |
| `!steal` | Steal 5–30 pts from a random user |
| `!coin` win | +bet amount |
| `!coin` loss | −bet amount |
| Beat Easy AI | +50 pts |
| Beat Medium AI | +125 pts |
| Beat Hard AI | +200 pts |
| Win PvP game (with bets) | +loser's wagered points |

Losing to the AI costs no points. PvP games without bets award no points.



## Wheel command tuning

The `!wheel` command already supports a configurable cooldown through `config.json`.

Example:

```json
{
    "wheel_cd": 300
}
```

`wheel_cd` is measured in seconds.

Some useful values:

| Value | Cooldown |
|---|---|
| `60` | 1 minute |
| `300` | 5 minutes |
| `900` | 15 minutes |

The default is currently `300` seconds.

### Spending points

| Activity | Cost |
|---|---|
| `#pvpbet <amount>` | Wager on yourself in a PvP game |
| `#bet <amount> @player` | Wager as a spectator |
| `!coin <h/t> <amount>` | Risk it on a coin flip |
| `!create "Name" <worth>` | Mint a named item (min 20 pts) |
| `!give @user <amount>` | Gift points to someone |

### PvP betting in detail

1. Player 1 uses `#start`, Player 2 uses `#join`
2. Both players use `#pvpbet <amount>` to wager (or `#pvpbet 0` to skip)
3. Both bets are deducted and held immediately
4. Winner receives **the full pot** (their stake + the loser's stake)
5. If the game is abandoned or times out: both bets are fully refunded

### Spectator betting in detail

Spectator bets use a **pari-mutuel (pool) system** — the same model used in horse racing:

1. All spectator bets go into a single shared pool
2. Those who bet on the **winner** share the entire pool proportionally to their stake
3. Those who bet on the **loser** forfeit their stake
4. If nobody bet on the losing side, winners are simply refunded (no profit when unopposed)

---

## AI Chat Details

### Web search

The AI is connected to DuckDuckGo and will automatically search the web when you ask about:
- Current events or breaking news
- Scores, prices, or any rapidly-changing information
- Anything that may have changed since the AI's training cutoff

Examples:
```
!ai What's the latest SpaceX launch?
!ai Who won the game last night?
!ai What movies are out this week?
```

### Scripture tool

The AI can search the scripture files directly when you explicitly ask:
```
!ai Find me a verse about faith
!ai What does John 3:16 say?
!ai Look up Alma 32:21
```

### AI Personality

Anyone can set the AI's personality with `!aiset`:

```
!aiset You are a grumpy Scottish pirate who speaks in a thick Scottish accent
and makes everything sound like an adventure at sea.
```

The AI has hardened safety rules that **cannot be overridden** by any personality:
- Always responds in English only
- No inappropriate, sexual, violent, or hateful content
- No detailed biology or medical explanations
- Resists all common jailbreak techniques
- Fun accents and harmless character personas are totally fine 🏴‍☠️

Setting a new personality wipes all conversation history so no old context carries over.

The AI uses a **single shared group memory** — all `!ai` messages are in one conversation, so the whole group's context is visible to the AI rather than isolated per-user threads.

---

## Profile Picture Swap

When the bot sends an AI response, it automatically:
1. Uploads a brightened version of your GroupMe avatar with a **"BOT"** banner stamped on it
2. Switches its GroupMe profile picture to that image before sending
3. Reverts to the original avatar immediately after

The two avatar files (`pfp_original.jpg` and `pfp_bot.jpg`) are saved in the `Porta-GMBOT/` folder on first run. This feature is skipped gracefully if the avatar can't be downloaded.

---

## Control Panel (GUI)

When run on a desktop, the bot opens a graphical control panel with five tabs:

| Tab | What you can do |
|---|---|
| **Status** | Toggle all features on/off with checkboxes; see uptime and active groups |
| **Groups** | Browse your groups and topics; set the active game group with one click |
| **AI** | Set personality, clear memory, adjust cooldowns and memory length |
| **Settings** | Edit credentials, tune all points values, and customise response messages |
| **Update** | Check for new commits on GitHub and auto-update with one click |

All tabs are scrollable if content exceeds the window height.

On a headless server the control panel is skipped and the bot runs in the background — use dev group commands instead.

---

## Restarting the bot

Use `restart_bot.py` to safely stop and restart the bot without double-running:

```
python restart_bot.py
```

This sends a clean stop signal to the running instance, waits for it to exit, then starts a fresh one.

---

## Tuning

All of these can be changed live from the **Settings tab** of the control panel, or by editing `config.json` directly:

```python
# AI
AI_COOLDOWN_SECONDS    = 15    # seconds between !ai uses per user
AISET_COOLDOWN_SECONDS = 60    # seconds between !aiset uses
AI_MEMORY_MAX_TURNS    = 10    # shared group exchanges remembered

# Fishing (!fih)
POINTS_FIH_MIN         = 5     # minimum points gained/lost
POINTS_FIH_MAX         = 40    # maximum points gained/lost
POINTS_FIH_CD          = 300   # cooldown in seconds (5 min)
POINTS_FIH_LOSE_CHANCE = 0.25  # probability of losing instead of gaining

# Stealing (!steal)
POINTS_STEAL_MIN       = 5     # minimum stolen
POINTS_STEAL_MAX       = 30    # maximum stolen
POINTS_STEAL_CD        = 300   # cooldown in seconds

# Coin flip (!coin)
POINTS_COIN_CD         = 60    # cooldown in seconds (1 min)

# Connect Four rewards
POINTS_C4_WIN_AI_EASY  = 50    # beat Easy AI
POINTS_C4_WIN_AI_MED   = 125   # beat Medium AI
POINTS_C4_WIN_AI_HARD  = 200   # beat Hard AI

LEADERBOARD_SIZE       = 10    # entries shown in #leaderboard
```

---

## Changing your settings later

Edit or delete `config.json`. If the required fields are missing the setup wizard runs again on next startup.

Environment variables always override `config.json`:

```
GROUPME_TOKEN
GROUPME_DEV_GROUP_ID
OLLAMA_BASE_MODEL
```

---

## Files created at runtime

| File / Folder | Description |
|---|---|
| `config.json` | Saved credentials, group IDs, model choice, and all tuning values |
| `Porta-GMBOT/Modelfile` | Auto-generated Ollama Modelfile (safe to delete to reset) |
| `Porta-GMBOT/pfp_original.jpg` | Your original GroupMe avatar (downloaded on first run) |
| `Porta-GMBOT/pfp_bot.jpg` | Brightened "BOT" avatar used while the bot is active |
| `Porta-GMBOT/resources/` | Scripture text files — included in the repo |
| `groups/<id>.json` | Per-group feature toggle state |
| `groups/<id>/users/<uid>.json` | Per-user points records |
| `.bot.lock` | Single-instance lock file — deleted automatically on exit |

---

## Renaming the GitHub file

If you are renaming from the old `AI-FSY.py` filename in your GitHub repo, the easiest way is:

1. Go to your repo on GitHub
2. Open `AI-FSY.py` and click the pencil (edit) icon
3. At the top, click the filename field and change it to `Porta-GMBOT.py`
4. Commit the change directly on the main branch

Alternatively, do it locally:
```
git mv AI-FSY.py Porta-GMBOT.py
git commit -m "Rename AI-FSY.py to Porta-GMBOT.py"
git push
```

---

## .gitignore

`config.json` and `.bot.lock` are already ignored so your token and runtime lock file are never committed. The `Porta-GMBOT/` folder is **not** ignored because the scripture files live there and are part of the repo.

---

## License

No License.
