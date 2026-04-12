# AI-FSY — GroupMe Connect Four Bot with AI Chat

A GroupMe bot that lets your group play **Connect Four**, look up **scriptures**, chat with a **local AI** (via Ollama), and more. Built in Python, runs on Windows, Mac, or Linux.

---

## Features

- 🎮 **Connect Four** — two-player or vs AI (minimax engine, depth 8)
- 🤖 **AI Chat** — powered by a local Ollama model; remembers your last 10 exchanges per person
- 🎱 **Magic 8-Ball** — `?` + any question
- 📖 **Scripture lookup** — Bible (KJV) and Book of Mormon verse search (files included)
- 🔒 **Safe by default** — hardened AI safety rules, English-only responses, spam cooldowns
- 🛠️ **Admin controls** — enable/disable individual features from inside the group
- 🧙 **First-run setup wizard** — GUI on desktop, terminal fallback on servers

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.9+ | [python.org](https://python.org) |
| Ollama | [ollama.com](https://ollama.com) — install it, the bot handles the rest |
| GroupMe account + access token | Free — see setup below |

The only Python dependency (`requests`) is installed automatically on first run.

---

## Setup — just three steps

### 1. Clone the repo

```
git clone https://github.com/YOUR_USERNAME/AI-FSY.git
cd AI-FSY
```

### 2. Install Ollama

Download and install from [ollama.com](https://ollama.com). You don't need to do anything else — the bot starts Ollama automatically and downloads the AI model on first run.

### 3. Run the bot

```
python AI-FSY.py
```

On first run, a **setup wizard** opens automatically:

- On **Windows / Mac / Linux desktop** → a GUI window appears
- On a **headless server** → clean terminal prompts instead

The wizard asks for:

| Field | Where to find it |
|---|---|
| **GroupMe Access Token** | [dev.groupme.com](https://dev.groupme.com) → log in → click your avatar → *Access Token* |
| **Dev Group ID** | Open your private dev group at [web.groupme.com](https://web.groupme.com) — the ID is in the URL: `.../groups/XXXXXXXX` |
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

If you don't know your game group's ID, no worries! Just type `!listgroups` in the dev group and it will list all your groups with their IDs.

To find it manually: open the group at [web.groupme.com](https://web.groupme.com) and copy the number from the URL.

---

## Commands

### Game group — everyone

| Command | Description |
|---|---|
| `#help` | Show help categories |
| `#help game` | Connect Four commands |
| `#help 8ball` | Magic 8-Ball info |
| `#help scripture` | Scripture commands |
| `#help ai` | AI chat commands |
| `#help admin` | Admin feature controls |
| `#start` | Start a new Connect Four game |
| `#join` | Join as Player 2 |
| `#addai` | Add AI engine as Player 2 |
| `#quit` | End the current game |
| `#A` – `#G` | Drop a piece in that column |
| `#timeout N` | Set inactivity timeout (seconds) |
| `#randverse` | Random scripture verse |
| `#randverse bible` | Random Bible verse |
| `#randverse bom` | Random Book of Mormon verse |
| `#findverse ...` | Look up or search a verse |
| `?<question>` | Magic 8-Ball |
| `!ai <message>` | Chat with the AI (15s per-user cooldown) |
| `!aiset <text>` | Set AI personality — anyone (60s cooldown) |
| `!aiforget` | Clear your own AI conversation history |
| `#state` | Show current state of all features |
| `#state <feature>` | Check one feature's state |

### Game group — admins only

| Command | Description |
|---|---|
| `#state all true/false` | Master on/off switch for the whole bot |
| `#state ai true/false` | Enable or disable AI chat |
| `#state 8ball true/false` | Enable or disable Magic 8-Ball |
| `#state scripture true/false` | Enable or disable scripture commands |
| `#state connect4 true/false` | Enable or disable Connect Four |
| `!aiforgetall` | Clear all users' AI conversation history |

### Dev group — developer only

| Command | Description |
|---|---|
| `!help` | Show dev commands |
| `!listgroups` | List all groups your token is in (with IDs) |
| `!add GROUPID` | Set the active game group |
| `!reload` | Restart the bot script |
| `!state true/false` | Enable or disable game responses |
| `!aiswitch true/false` | Enable or disable AI |

---

## AI Personality

Anyone in the game group can set the AI's personality with `!aiset`:

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

---

## Tuning

Near the top of `AI-FSY.py`:

```python
AI_COOLDOWN_SECONDS    = 15   # seconds between !ai uses per user
AISET_COOLDOWN_SECONDS = 60   # seconds between !aiset uses per user
AI_MEMORY_MAX_TURNS    = 10   # exchanges the AI remembers per user
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
| `config.json` | Your saved settings — token, group IDs, model choice |
| `AI-BOT/Modelfile` | Auto-generated Ollama Modelfile (safe to delete to reset) |
| `AI-BOT/resources/` | Scripture text files — included in the repo |

---

## .gitignore

`config.json` is already ignored so your token is never committed. The `AI-BOT/` folder is **not** ignored because the scripture files live there and are part of the repo.

---

## License

No License...
