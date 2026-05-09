"""
game_engine.py  —  Fantasy RPG Engine for AI-FSY GroupMe Bot
=============================================================
Phase 1: Foundation
  - Player registration (!beginpoints)
  - Stats: HP, ATK, DEF, SPD, Mana, Luck, Weight, Size
  - Gem currency system
  - Auto-clicker passive income (1 per player, +gems every 30s)
  - Inventory system (weapons, items, fish, chests)
  - Chest system (buyable, tiered storage, gem protection)
  - Global shop (panel-editable via game_data/shop.json)
  - Fishing (location-gated) and hunting stubs
  - Pillow map generation with player dot overlay
  - Help system (!help points, sections)
  - Full command dispatcher (called by AI-FSY.py)

This script is imported by AI-FSY.py.  It does NOT run standalone.
All file I/O uses paths relative to SCRIPT_DIR (passed in at init).
All GroupMe sending is done via a send_fn callback provided by AI-FSY.py.
"""

import os
import json
import math
import random
import time
import threading
import io
import base64
import traceback
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# GAME-WIDE CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GAME_TIME_MULTIPLIER = 2        # 2 in-game days per 1 real day
CLICKER_INTERVAL     = 30       # seconds between passive gem ticks
CLICKER_GEMS_PER_TICK = 1       # gems per tick per player (1 clicker max)
STARTING_GEMS        = 0        # gems on registration
STARTING_HP          = 50
STARTING_MAX_HP      = 50
STARTING_MANA        = 30
STARTING_MAX_MANA    = 30
STARTING_ATK         = 5
STARTING_DEF         = 3
STARTING_SPD         = 5
STARTING_LUCK        = 5
STARTING_WEIGHT      = 10       # affects carry capacity
STARTING_SIZE        = 5        # affects dodge/hit chance in future phases

XP_PER_LEVEL_BASE    = 100      # XP required for level 2; scales per level
WEAPON_SLOTS         = 10       # max weapon slots
LEADERBOARD_SIZE     = 10

# Map settings
MAP_WIDTH    = 2400
MAP_HEIGHT   = 1600
MAP_FILENAME = "world_map.png"

# ─────────────────────────────────────────────────────────────────────────────
# DATA DIRECTORY STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────
# <SCRIPT_DIR>/
#   game_data/
#     players.json        — all player records keyed by "group_id:user_id"
#     shop.json           — global shop items list
#     world.json          — map locations database
#     items.json          — item master list
#     enemies.json        — enemy/monster database
#     npcs.json           — NPC database
#     world_map.png       — generated map image
#     world_map_base.png  — clean map without player dots

# ─────────────────────────────────────────────────────────────────────────────
# MODULE STATE
# ─────────────────────────────────────────────────────────────────────────────

_script_dir  = None   # set by init()
_send_fn     = None   # set by init() — fn(group_id, text)
_upload_fn   = None   # set by init() — fn(group_id, image_bytes, caption) -> bool
_data_dir    = None

_clicker_thread = None
_game_lock   = threading.Lock()   # coarse lock for player data writes

# ─────────────────────────────────────────────────────────────────────────────
# INITIALISATION  (called once from AI-FSY.py at startup)
# ─────────────────────────────────────────────────────────────────────────────

def init(script_dir: str, send_fn, upload_fn=None):
    """
    Must be called before any other function.
    send_fn(group_id, text)  — sends a text message to a GroupMe group.
    upload_fn(group_id, image_bytes, caption)  — optional image uploader.
    """
    global _script_dir, _send_fn, _upload_fn, _data_dir
    _script_dir = script_dir
    _send_fn    = send_fn
    _upload_fn  = upload_fn
    _data_dir   = os.path.join(script_dir, "game_data")

    _ensure_directories()
    _ensure_default_data()
    _ensure_map()
    _start_clicker_loop()
    print("[game_engine] Initialised successfully.")


def _ensure_directories():
    os.makedirs(_data_dir, exist_ok=True)


def _ensure_default_data():
    """Create default JSON files if they don't exist yet."""
    _ensure_json("players.json",  {})
    _ensure_json("shop.json",     _default_shop())
    _ensure_json("world.json",    _default_world())
    _ensure_json("items.json",    _default_items())
    _ensure_json("enemies.json",  _default_enemies())
    _ensure_json("npcs.json",     _default_npcs())


def _ensure_json(filename, default):
    path = os.path.join(_data_dir, filename)
    if not os.path.exists(path):
        _write_json(path, default)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[game_engine] Write error {path}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PLAYER DATA
# ─────────────────────────────────────────────────────────────────────────────

def _player_key(group_id, user_id):
    return f"{group_id}:{user_id}"


def _load_players():
    return _read_json(os.path.join(_data_dir, "players.json")) or {}


def _save_players(players):
    _write_json(os.path.join(_data_dir, "players.json"), players)


def _get_player(group_id, user_id):
    players = _load_players()
    return players.get(_player_key(group_id, user_id))


def _save_player(group_id, user_id, record):
    with _game_lock:
        players = _load_players()
        players[_player_key(group_id, user_id)] = record
        _save_players(players)


def _new_player(name):
    """Return a fresh player record with default stats."""
    return {
        "name":         name,
        "registered":   time.time(),
        "gems":         STARTING_GEMS,

        # Primary stats
        "level":        1,
        "xp":           0,
        "hp":           STARTING_HP,
        "max_hp":       STARTING_MAX_HP,
        "mana":         STARTING_MANA,
        "max_mana":     STARTING_MAX_MANA,
        "atk":          STARTING_ATK,
        "def":          STARTING_DEF,
        "spd":          STARTING_SPD,

        # Secondary stats
        "luck":         STARTING_LUCK,
        "weight":       STARTING_WEIGHT,
        "size":         STARTING_SIZE,

        # Clicker
        "has_clicker":  True,   # every player gets one passive clicker
        "last_clicker_tick": time.time(),

        # Location
        "location":     "Hearthstone Village",
        "travelling_to": None,
        "travel_arrive": None,

        # Inventory
        # weapons: list of {item_id, name, qty, equipped}  — max WEAPON_SLOTS unique types
        # items:   list of {item_id, name, qty, category}
        # chests:  list of {chest_id, tier, stored_gems, stored_items, capacity}
        "weapons":      [],
        "items":        [],
        "chests":       [],

        # Cooldowns (unix timestamps)
        "cd_fish":      0,
        "cd_hunt":      0,
        "cd_coin":      0,

        # Battle state (None when not in combat)
        "in_combat":    False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PASSIVE INCOME — CLICKER LOOP
# ─────────────────────────────────────────────────────────────────────────────

def _start_clicker_loop():
    global _clicker_thread
    _clicker_thread = threading.Thread(target=_clicker_loop_fn, daemon=True)
    _clicker_thread.start()


def _clicker_loop_fn():
    while True:
        time.sleep(CLICKER_INTERVAL)
        try:
            _run_clicker_tick()
        except Exception as e:
            print(f"[game_engine][clicker] Error: {e}")


def _run_clicker_tick():
    """Award CLICKER_GEMS_PER_TICK gems to every registered player who has a clicker."""
    with _game_lock:
        players = _load_players()
        changed = False
        for key, p in players.items():
            if p.get("has_clicker", False):
                p["gems"] = p.get("gems", 0) + CLICKER_GEMS_PER_TICK
                p["last_clicker_tick"] = time.time()
                changed = True
        if changed:
            _save_players(players)


# ─────────────────────────────────────────────────────────────────────────────
# SHOP DATA
# ─────────────────────────────────────────────────────────────────────────────

def _default_shop():
    """
    The shop starts with a set of chests only.
    Admins can add/remove items from the game control panel.
    Chest tiers: capacity (gems), cost = capacity // 3
    """
    return {
        "items": [
            {
                "id":        "chest_50",
                "name":      "Small Chest",
                "category":  "chest",
                "description": "A small lockbox. Holds up to 50 gems or equivalent items.",
                "cost":      17,
                "capacity":  50,
                "tier":      "small",
            },
            {
                "id":        "chest_100",
                "name":      "Standard Chest",
                "category":  "chest",
                "description": "A sturdy wooden chest. Holds up to 100 gems or equivalent items.",
                "cost":      33,
                "capacity":  100,
                "tier":      "standard",
            },
            {
                "id":        "chest_250",
                "name":      "Iron Chest",
                "category":  "chest",
                "description": "An iron-bound chest. Holds up to 250 gems or equivalent items.",
                "cost":      83,
                "capacity":  250,
                "tier":      "iron",
            },
            {
                "id":        "chest_500",
                "name":      "Reinforced Chest",
                "category":  "chest",
                "description": "A heavily reinforced chest. Holds up to 500 gems or equivalent items.",
                "cost":      167,
                "capacity":  500,
                "tier":      "reinforced",
            },
            {
                "id":        "chest_1000",
                "name":      "Vault Chest",
                "category":  "chest",
                "description": "A vault-grade chest. Holds up to 1000 gems or equivalent items.",
                "cost":      333,
                "capacity":  1000,
                "tier":      "vault",
            },
        ]
    }


def load_shop():
    return _read_json(os.path.join(_data_dir, "shop.json")) or _default_shop()


def save_shop(shop):
    _write_json(os.path.join(_data_dir, "shop.json"), shop)


# ─────────────────────────────────────────────────────────────────────────────
# WORLD / MAP DATA
# ─────────────────────────────────────────────────────────────────────────────

def _default_world():
    """
    43 named locations across the fantasy map.
    Each location has:
      - display_name, region, description
      - danger: 0-10 (0=safe, 10=death zone)
      - has_water: can fish here
      - has_forest: can hunt here
      - is_city / is_village: NPC shops, safety
      - coords: (x, y) pixel on the 2400×1600 map
      - connections: list of location names you can travel to directly
      - weather_modifier: affects daily forecast rolls
    """
    return {
        "locations": [
            # ── HEARTLAND (central safe zone) ─────────────────────────────────
            {
                "name": "Hearthstone Village",
                "region": "Heartland",
                "description": "A cosy starting village surrounded by golden fields. The scent of fresh bread fills the air.",
                "danger": 0, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [1200, 800],
                "connections": ["Millford", "Thornwood Path", "Goldenfield Plains", "Crestlake"],
                "weather_modifier": 0,
            },
            {
                "name": "Millford",
                "region": "Heartland",
                "description": "A busy mill town straddling the River Crest. Merchants trade grain and fish.",
                "danger": 1, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [1050, 720],
                "connections": ["Hearthstone Village", "Crestlake", "Irongate City", "Ashfen Marsh"],
                "weather_modifier": 0,
            },
            {
                "name": "Goldenfield Plains",
                "region": "Heartland",
                "description": "Vast open grasslands stretching to the horizon. Peaceful but exposed.",
                "danger": 1, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1350, 900],
                "connections": ["Hearthstone Village", "Crestlake", "Dustwind Crossing", "Ridgeback Hills"],
                "weather_modifier": 1,
            },
            {
                "name": "Crestlake",
                "region": "Heartland",
                "description": "A calm lake ringed by reeds. Famous for silver trout and quiet evenings.",
                "danger": 1, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1100, 880],
                "connections": ["Hearthstone Village", "Millford", "Goldenfield Plains", "Thornwood Path"],
                "weather_modifier": -1,
            },

            # ── IRONGATE (northern industrial city) ──────────────────────────
            {
                "name": "Irongate City",
                "region": "Iron North",
                "description": "A great walled city of forges and smiths. The skies are perpetually grey with soot.",
                "danger": 2, "has_water": False, "has_forest": False,
                "is_city": True, "is_village": False,
                "coords": [900, 500],
                "connections": ["Millford", "Ashfen Marsh", "Stoneback Ridge", "Forge Road"],
                "weather_modifier": 1,
            },
            {
                "name": "Forge Road",
                "region": "Iron North",
                "description": "A wide trade road hammered flat by countless ore carts.",
                "danger": 2, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [850, 620],
                "connections": ["Irongate City", "Stoneback Ridge", "Millford"],
                "weather_modifier": 0,
            },
            {
                "name": "Stoneback Ridge",
                "region": "Iron North",
                "description": "A jagged spine of granite. Mountain goats and bandits share these narrow paths.",
                "danger": 4, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [750, 400],
                "connections": ["Irongate City", "Forge Road", "Frostpeak Summit", "Glacier Pass"],
                "weather_modifier": 2,
            },
            {
                "name": "Frostpeak Summit",
                "region": "Iron North",
                "description": "The highest point in the north. Snow falls year-round. Only the hardiest survive.",
                "danger": 7, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [680, 260],
                "connections": ["Stoneback Ridge", "Glacier Pass"],
                "weather_modifier": 4,
            },
            {
                "name": "Glacier Pass",
                "region": "Iron North",
                "description": "A treacherous ice corridor through the mountains. Avalanches are common.",
                "danger": 6, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [820, 280],
                "connections": ["Stoneback Ridge", "Frostpeak Summit", "Tundra Flats"],
                "weather_modifier": 3,
            },

            # ── ASHFEN (western swamp) ────────────────────────────────────────
            {
                "name": "Ashfen Marsh",
                "region": "Ashfen",
                "description": "A fog-choked swamp of dead trees and black water. Things move beneath the surface.",
                "danger": 5, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [700, 750],
                "connections": ["Millford", "Irongate City", "Bogmire", "Thornwood Path"],
                "weather_modifier": 2,
            },
            {
                "name": "Bogmire",
                "region": "Ashfen",
                "description": "A sunken village half-swallowed by the marsh. Its few residents are peculiar.",
                "danger": 4, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [580, 820],
                "connections": ["Ashfen Marsh", "Witchwood", "Saltmere Coast"],
                "weather_modifier": 1,
            },
            {
                "name": "Witchwood",
                "region": "Ashfen",
                "description": "An ancient, twisted forest at the swamp's edge. The trees seem to watch you.",
                "danger": 6, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [480, 900],
                "connections": ["Bogmire", "Deepwood Heart"],
                "weather_modifier": 2,
            },

            # ── THORNWOOD (central-west forest) ──────────────────────────────
            {
                "name": "Thornwood Path",
                "region": "Thornwood",
                "description": "A well-worn trail threading through dense forest. Relatively safe, but stay on the path.",
                "danger": 2, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [950, 900],
                "connections": ["Hearthstone Village", "Crestlake", "Ashfen Marsh", "Thornwood Village", "Deepwood Heart"],
                "weather_modifier": -1,
            },
            {
                "name": "Thornwood Village",
                "region": "Thornwood",
                "description": "A cheerful forest village where rangers and woodcutters make their home.",
                "danger": 1, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": True,
                "coords": [870, 1000],
                "connections": ["Thornwood Path", "Deepwood Heart", "Saltmere Coast"],
                "weather_modifier": -1,
            },
            {
                "name": "Deepwood Heart",
                "region": "Thornwood",
                "description": "The dense, ancient core of the Thornwood. No light reaches the floor. Very dangerous.",
                "danger": 7, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [650, 1050],
                "connections": ["Thornwood Path", "Thornwood Village", "Witchwood", "Ruinsgate"],
                "weather_modifier": 0,
            },

            # ── SALTMERE (southern coast) ──────────────────────────────────
            {
                "name": "Saltmere Coast",
                "region": "Saltmere",
                "description": "A windswept coastline of white cliffs and crashing waves. Excellent fishing.",
                "danger": 2, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [800, 1200],
                "connections": ["Bogmire", "Thornwood Village", "Saltmere Port", "Shipwreck Cove"],
                "weather_modifier": 1,
            },
            {
                "name": "Saltmere Port",
                "region": "Saltmere",
                "description": "A bustling port city smelling of brine and adventure. The largest market on the coast.",
                "danger": 3, "has_water": True, "has_forest": False,
                "is_city": True, "is_village": False,
                "coords": [950, 1350],
                "connections": ["Saltmere Coast", "Shipwreck Cove", "Dustwind Crossing", "Sunken Reef"],
                "weather_modifier": 1,
            },
            {
                "name": "Shipwreck Cove",
                "region": "Saltmere",
                "description": "A hidden cove littered with the bones of ships. Pirates and sea creatures lurk here.",
                "danger": 6, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [700, 1380],
                "connections": ["Saltmere Coast", "Saltmere Port", "Sunken Reef"],
                "weather_modifier": 2,
            },
            {
                "name": "Sunken Reef",
                "region": "Saltmere",
                "description": "A partially submerged reef. Rare fish abound, but so do sea serpents.",
                "danger": 8, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1050, 1450],
                "connections": ["Saltmere Port", "Shipwreck Cove"],
                "weather_modifier": 3,
            },

            # ── RIDGEBACK (eastern hills) ──────────────────────────────────
            {
                "name": "Ridgeback Hills",
                "region": "Ridgeback",
                "description": "Rolling amber hills dotted with ruins. Good hunting, moderate danger.",
                "danger": 3, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [1550, 850],
                "connections": ["Goldenfield Plains", "Dustwind Crossing", "Ridgeback Keep", "Ember Plateau"],
                "weather_modifier": 1,
            },
            {
                "name": "Ridgeback Keep",
                "region": "Ridgeback",
                "description": "A fortified keep atop the highest hill. Once a military stronghold, now a trading post.",
                "danger": 2, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [1700, 750],
                "connections": ["Ridgeback Hills", "Ember Plateau", "Dustwind Crossing"],
                "weather_modifier": 2,
            },
            {
                "name": "Ember Plateau",
                "region": "Ridgeback",
                "description": "A plateau of dark volcanic rock still warm to the touch. Fire lizards nest here.",
                "danger": 5, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1850, 680],
                "connections": ["Ridgeback Hills", "Ridgeback Keep", "Ashcrag Caldera"],
                "weather_modifier": 3,
            },
            {
                "name": "Ashcrag Caldera",
                "region": "Ridgeback",
                "description": "The mouth of a dormant (mostly) volcano. Magma elementals and fire drakes roam freely.",
                "danger": 9, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1980, 580],
                "connections": ["Ember Plateau"],
                "weather_modifier": 5,
            },

            # ── DUSTWIND (southern desert) ──────────────────────────────────
            {
                "name": "Dustwind Crossing",
                "region": "Dustwind",
                "description": "A crossroads carved into the desert. Caravans stop here, as do bandits.",
                "danger": 3, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [1400, 1100],
                "connections": ["Goldenfield Plains", "Ridgeback Hills", "Ridgeback Keep", "Saltmere Port", "Dunes of Kor", "Mirestone Oasis"],
                "weather_modifier": 2,
            },
            {
                "name": "Mirestone Oasis",
                "region": "Dustwind",
                "description": "A miraculous oasis shimmering with gem-clear water. Rumours say it heals wounds.",
                "danger": 2, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [1550, 1250],
                "connections": ["Dustwind Crossing", "Dunes of Kor"],
                "weather_modifier": -1,
            },
            {
                "name": "Dunes of Kor",
                "region": "Dustwind",
                "description": "Endless, scorching dunes hiding ancient tombs and sand wraiths.",
                "danger": 7, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1750, 1300],
                "connections": ["Dustwind Crossing", "Mirestone Oasis", "Tomb of Kor"],
                "weather_modifier": 4,
            },
            {
                "name": "Tomb of Kor",
                "region": "Dustwind",
                "description": "An ancient buried citadel. The air smells of old magic and something far worse.",
                "danger": 10, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1900, 1400],
                "connections": ["Dunes of Kor"],
                "weather_modifier": 5,
            },

            # ── TUNDRA (far north) ──────────────────────────────────────────
            {
                "name": "Tundra Flats",
                "region": "Tundra",
                "description": "Frozen, featureless plains stretching north forever. The cold can kill you in hours.",
                "danger": 6, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1000, 180],
                "connections": ["Glacier Pass", "Frostveil Settlement", "Ice Cavern"],
                "weather_modifier": 4,
            },
            {
                "name": "Frostveil Settlement",
                "region": "Tundra",
                "description": "A hardy community of fur-traders and ice-fishers. Warm fires, colder hearts.",
                "danger": 4, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": True,
                "coords": [1200, 130],
                "connections": ["Tundra Flats", "Ice Cavern", "Permafrost Depths"],
                "weather_modifier": 3,
            },
            {
                "name": "Ice Cavern",
                "region": "Tundra",
                "description": "A labyrinthine cave system carved by ancient glaciers. Ice beasts prowl inside.",
                "danger": 7, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1100, 80],
                "connections": ["Tundra Flats", "Frostveil Settlement", "Permafrost Depths"],
                "weather_modifier": 4,
            },
            {
                "name": "Permafrost Depths",
                "region": "Tundra",
                "description": "Below the ice cavern: a frozen underworld where ancient creatures are locked in ice — some still alive.",
                "danger": 10, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1050, 40],
                "connections": ["Ice Cavern", "Frostveil Settlement"],
                "weather_modifier": 5,
            },

            # ── RUINS / DUNGEONS ────────────────────────────────────────────
            {
                "name": "Ruinsgate",
                "region": "Ruinlands",
                "description": "A crumbling archway that once marked the entrance to a great city. Now only rubble and danger remain.",
                "danger": 6, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [550, 1150],
                "connections": ["Deepwood Heart", "Hollow City Ruins"],
                "weather_modifier": 1,
            },
            {
                "name": "Hollow City Ruins",
                "region": "Ruinlands",
                "description": "The gutted remains of a once-great city. Undead walk its empty streets at night.",
                "danger": 8, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [430, 1300],
                "connections": ["Ruinsgate", "The Abyss Gate"],
                "weather_modifier": 2,
            },
            {
                "name": "The Abyss Gate",
                "region": "Ruinlands",
                "description": "A swirling portal of dark energy at the map's edge. Only the reckless go here. Nothing is confirmed to come back.",
                "danger": 10, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [310, 1450],
                "connections": ["Hollow City Ruins"],
                "weather_modifier": 5,
            },

            # ── RIVERS / WATER CROSSINGS ──────────────────────────────────
            {
                "name": "River Crest Ford",
                "region": "Heartland",
                "description": "A wide, shallow crossing of the River Crest. Popular with travellers — and the creatures that hunt them.",
                "danger": 2, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1150, 650],
                "connections": ["Millford", "Hearthstone Village", "Irongate City"],
                "weather_modifier": 0,
            },
            {
                "name": "Mirepool Lake",
                "region": "Ashfen",
                "description": "A dark, still lake at the heart of the marsh. Locals say things live in its depths that haven't been named yet.",
                "danger": 5, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [600, 700],
                "connections": ["Ashfen Marsh", "Bogmire"],
                "weather_modifier": 2,
            },
            {
                "name": "Crystal River Delta",
                "region": "Saltmere",
                "description": "Where the Crystal River fans into the sea. Best fishing on the continent.",
                "danger": 2, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [1200, 1400],
                "connections": ["Saltmere Port", "Dustwind Crossing"],
                "weather_modifier": 0,
            },

            # ── MOUNTAIN RANGE ────────────────────────────────────────────
            {
                "name": "Stormcap Mountains",
                "region": "Iron North",
                "description": "Towering peaks permanently wreathed in storm clouds. Wyverns and trolls make this their home.",
                "danger": 8, "has_water": False, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [520, 350],
                "connections": ["Stoneback Ridge", "Frostpeak Summit"],
                "weather_modifier": 5,
            },

            # ── EASTERN WILDS ────────────────────────────────────────────
            {
                "name": "Verdant Wilds",
                "region": "Eastern Wilds",
                "description": "Lush, untamed jungle in the far east. Exotic creatures, rare plants, and hidden treasures.",
                "danger": 5, "has_water": True, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [2100, 900],
                "connections": ["Ember Plateau", "Ridgeback Keep", "Ancient Shrine"],
                "weather_modifier": -1,
            },
            {
                "name": "Ancient Shrine",
                "region": "Eastern Wilds",
                "description": "A moss-covered shrine to a forgotten god. Players who pray here report strange visions — and stranger luck.",
                "danger": 4, "has_water": False, "has_forest": True,
                "is_city": False, "is_village": False,
                "coords": [2250, 1050],
                "connections": ["Verdant Wilds"],
                "weather_modifier": -2,
            },

            # ── FAR WEST CLIFF ────────────────────────────────────────────
            {
                "name": "Cliffside Watch",
                "region": "Western Cliffs",
                "description": "A crumbling watchtower on a cliff overlooking the western sea. Windswept and lonely.",
                "danger": 3, "has_water": True, "has_forest": False,
                "is_city": False, "is_village": False,
                "coords": [200, 700],
                "connections": ["Witchwood", "Bogmire"],
                "weather_modifier": 2,
            },
        ]
    }


def load_world():
    return _read_json(os.path.join(_data_dir, "world.json")) or _default_world()


def get_location(name):
    world = load_world()
    for loc in world["locations"]:
        if loc["name"].lower() == name.lower():
            return loc
    return None


def find_location_fuzzy(query):
    """Return best matching location or None."""
    world = load_world()
    q = query.lower().strip()
    # Exact match first
    for loc in world["locations"]:
        if loc["name"].lower() == q:
            return loc
    # Partial match
    matches = [loc for loc in world["locations"] if q in loc["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ITEM DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _default_items():
    return {
        "fish": [
            {"id": "fish_minnow",    "name": "Minnow",         "rarity": "common",    "sell_value": 2,   "locations": ["any_water"]},
            {"id": "fish_perch",     "name": "River Perch",    "rarity": "common",    "sell_value": 5,   "locations": ["any_water"]},
            {"id": "fish_trout",     "name": "Silver Trout",   "rarity": "uncommon",  "sell_value": 12,  "locations": ["Crestlake", "River Crest Ford", "Millford"]},
            {"id": "fish_catfish",   "name": "Mudcatfish",     "rarity": "common",    "sell_value": 7,   "locations": ["Ashfen Marsh", "Mirepool Lake", "Bogmire"]},
            {"id": "fish_eel",       "name": "Dark Eel",       "rarity": "uncommon",  "sell_value": 18,  "locations": ["Mirepool Lake", "Ashfen Marsh"]},
            {"id": "fish_bass",      "name": "Saltwater Bass", "rarity": "uncommon",  "sell_value": 15,  "locations": ["Saltmere Coast", "Saltmere Port", "Crystal River Delta"]},
            {"id": "fish_shark",     "name": "Reef Shark",     "rarity": "rare",      "sell_value": 45,  "locations": ["Sunken Reef", "Shipwreck Cove"]},
            {"id": "fish_icefish",   "name": "Glacier Fish",   "rarity": "rare",      "sell_value": 40,  "locations": ["Glacier Pass", "Ice Cavern", "Frostveil Settlement"]},
            {"id": "fish_deepfish",  "name": "Abyssal Angler", "rarity": "epic",      "sell_value": 120, "locations": ["Permafrost Depths", "Sunken Reef"]},
            {"id": "fish_golden",    "name": "Golden Carp",    "rarity": "legendary", "sell_value": 500, "locations": ["Mirestone Oasis", "Crestlake"]},
        ],
        "hunt_drops": [
            {"id": "meat_rabbit",    "name": "Rabbit Meat",    "rarity": "common",    "sell_value": 8,   "edible": True},
            {"id": "meat_deer",      "name": "Venison",        "rarity": "uncommon",  "sell_value": 20,  "edible": True},
            {"id": "pelt_rabbit",    "name": "Rabbit Pelt",    "rarity": "common",    "sell_value": 6},
            {"id": "pelt_wolf",      "name": "Wolf Pelt",      "rarity": "uncommon",  "sell_value": 25},
            {"id": "pelt_bear",      "name": "Bear Hide",      "rarity": "rare",      "sell_value": 70},
            {"id": "fang_wolf",      "name": "Wolf Fang",      "rarity": "uncommon",  "sell_value": 15},
            {"id": "claw_bear",      "name": "Bear Claw",      "rarity": "rare",      "sell_value": 50},
            {"id": "scale_drake",    "name": "Drake Scale",    "rarity": "epic",      "sell_value": 200},
            {"id": "fang_wyvern",    "name": "Wyvern Fang",    "rarity": "epic",      "sell_value": 280},
        ],
        "food": [
            {"id": "food_apple",     "name": "Apple",          "sell_value": 1,  "hp_restore": 5,  "throwable": True, "break_on_throw": True},
            {"id": "food_bread",     "name": "Loaf of Bread",  "sell_value": 3,  "hp_restore": 10, "throwable": True, "break_on_throw": True},
            {"id": "food_stew",      "name": "Hearty Stew",    "sell_value": 10, "hp_restore": 25, "throwable": True, "break_on_throw": True},
            {"id": "food_elixir",    "name": "Healing Elixir", "sell_value": 50, "hp_restore": 50, "throwable": True, "break_on_throw": True},
        ],
        "weapons": [
            # id, name, atk_bonus, attacks, stackable, max_stack
            {"id": "wpn_stick",      "name": "Stick",          "atk_bonus": 2,  "attacks": ["stab", "bonk"],    "stackable": True,  "max_stack": 5},
            {"id": "wpn_club",       "name": "Club",           "atk_bonus": 5,  "attacks": ["bonk", "smash"],   "stackable": False},
            {"id": "wpn_dagger",     "name": "Dagger",         "atk_bonus": 6,  "attacks": ["stab", "slash"],   "stackable": True,  "max_stack": 2},
            {"id": "wpn_sword",      "name": "Iron Sword",     "atk_bonus": 12, "attacks": ["slash", "thrust"], "stackable": False},
            {"id": "wpn_greatsword", "name": "Greatsword",     "atk_bonus": 22, "attacks": ["cleave", "bash"],  "stackable": False},
            {"id": "wpn_bow",        "name": "Short Bow",      "atk_bonus": 10, "attacks": ["shoot"],           "stackable": False},
            {"id": "wpn_staff",      "name": "Mage Staff",     "atk_bonus": 8,  "attacks": ["cast", "bonk"],    "stackable": False},
            {"id": "wpn_axe",        "name": "Hand Axe",       "atk_bonus": 14, "attacks": ["chop", "throw"],   "stackable": True,  "max_stack": 3},
            {"id": "wpn_spear",      "name": "Spear",          "atk_bonus": 16, "attacks": ["stab", "throw"],   "stackable": False},
            {"id": "wpn_wand",       "name": "Wand",           "atk_bonus": 6,  "attacks": ["cast", "flick"],   "stackable": True,  "max_stack": 2},
        ],
        "materials": [
            {"id": "mat_stone",      "name": "Stone",          "sell_value": 1,  "throwable": True, "break_on_throw": False},
            {"id": "mat_iron_ore",   "name": "Iron Ore",       "sell_value": 8,  "throwable": True, "break_on_throw": False},
            {"id": "mat_wood",       "name": "Lumber",         "sell_value": 4,  "throwable": True, "break_on_throw": False},
            {"id": "mat_gem_shard",  "name": "Gem Shard",      "sell_value": 30, "throwable": True, "break_on_throw": False},
            {"id": "mat_bone",       "name": "Bone",           "sell_value": 5,  "throwable": True, "break_on_throw": False},
        ],
    }


def load_items():
    return _read_json(os.path.join(_data_dir, "items.json")) or _default_items()


# ─────────────────────────────────────────────────────────────────────────────
# ENEMY DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _default_enemies():
    return {
        "enemies": [
            # name, danger_min, danger_max, hp, atk, def, xp, gem_drop_min, gem_drop_max, item_drops
            {"id": "goblin",       "name": "Goblin",           "danger_min": 1, "danger_max": 3,  "hp": 20,  "atk": 4,  "def": 1,  "xp": 15,  "gem_min": 1,  "gem_max": 5,  "drops": ["mat_stone", "wpn_stick"]},
            {"id": "wolf",         "name": "Grey Wolf",        "danger_min": 2, "danger_max": 4,  "hp": 35,  "atk": 8,  "def": 2,  "xp": 30,  "gem_min": 2,  "gem_max": 8,  "drops": ["pelt_wolf", "fang_wolf", "meat_rabbit"]},
            {"id": "bandit",       "name": "Bandit",           "danger_min": 2, "danger_max": 5,  "hp": 40,  "atk": 10, "def": 3,  "xp": 35,  "gem_min": 5,  "gem_max": 20, "drops": ["wpn_dagger", "mat_iron_ore"]},
            {"id": "troll",        "name": "Mountain Troll",   "danger_min": 4, "danger_max": 6,  "hp": 80,  "atk": 15, "def": 8,  "xp": 70,  "gem_min": 10, "gem_max": 35, "drops": ["mat_bone", "mat_stone", "pelt_bear"]},
            {"id": "swamp_beast",  "name": "Swamp Beast",      "danger_min": 4, "danger_max": 6,  "hp": 60,  "atk": 12, "def": 5,  "xp": 55,  "gem_min": 8,  "gem_max": 25, "drops": ["pelt_wolf", "mat_bone"]},
            {"id": "skeleton",     "name": "Risen Skeleton",   "danger_min": 5, "danger_max": 8,  "hp": 50,  "atk": 11, "def": 4,  "xp": 50,  "gem_min": 5,  "gem_max": 15, "drops": ["mat_bone", "wpn_sword"]},
            {"id": "ice_beast",    "name": "Ice Wurm",         "danger_min": 6, "danger_max": 8,  "hp": 90,  "atk": 18, "def": 10, "xp": 100, "gem_min": 15, "gem_max": 50, "drops": ["fish_icefish", "mat_bone"]},
            {"id": "fire_drake",   "name": "Fire Drake",       "danger_min": 7, "danger_max": 9,  "hp": 120, "atk": 25, "def": 14, "xp": 150, "gem_min": 25, "gem_max": 80, "drops": ["scale_drake", "mat_gem_shard"]},
            {"id": "wyvern",       "name": "Wyvern",           "danger_min": 8, "danger_max": 10, "hp": 180, "atk": 35, "def": 18, "xp": 250, "gem_min": 50, "gem_max": 150,"drops": ["fang_wyvern", "scale_drake"]},
            {"id": "sand_wraith",  "name": "Sand Wraith",      "danger_min": 6, "danger_max": 9,  "hp": 70,  "atk": 20, "def": 6,  "xp": 90,  "gem_min": 20, "gem_max": 60, "drops": ["mat_gem_shard", "mat_bone"]},
            {"id": "sea_serpent",  "name": "Sea Serpent",      "danger_min": 7, "danger_max": 10, "hp": 150, "atk": 30, "def": 12, "xp": 200, "gem_min": 40, "gem_max": 120,"drops": ["scale_drake", "fish_shark"]},
            {"id": "ancient_lich", "name": "Ancient Lich",     "danger_min": 9, "danger_max": 10, "hp": 250, "atk": 45, "def": 20, "xp": 400, "gem_min": 80, "gem_max": 250,"drops": ["wpn_staff", "mat_gem_shard", "mat_bone"]},
        ]
    }


def load_enemies():
    return _read_json(os.path.join(_data_dir, "enemies.json")) or _default_enemies()


def get_enemies_for_danger(danger_level):
    """Return list of enemies that can spawn at this danger level."""
    all_enemies = load_enemies()["enemies"]
    return [e for e in all_enemies if e["danger_min"] <= danger_level <= e["danger_max"]]


# ─────────────────────────────────────────────────────────────────────────────
# NPC DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _default_npcs():
    return {
        "npcs": [
            {
                "id": "elder_mira",
                "name": "Elder Mira",
                "location": "Hearthstone Village",
                "personality": "A wise and kind village elder in her 70s. Speaks in gentle riddles. Knows the history of every location on the map. Will give newcomers advice freely but trades rare knowledge for rare items.",
                "known_about_players": ["location", "level", "name"],
                "can_trade": False,
                "inventory": [],
            },
            {
                "id": "bram_merchant",
                "name": "Bram the Merchant",
                "location": "Millford",
                "personality": "A jovial, rotund merchant who is always trying to sell something. Very friendly but will overcharge if he thinks you don't know better. Loves gossip about other towns.",
                "known_about_players": ["name", "gems"],
                "can_trade": True,
                "inventory": [
                    {"item_id": "food_bread", "qty": 10, "price": 5},
                    {"item_id": "food_apple", "qty": 20, "price": 2},
                    {"item_id": "wpn_stick",  "qty": 5,  "price": 4},
                    {"item_id": "mat_stone",  "qty": 30, "price": 1},
                ],
            },
            {
                "id": "captain_harrow",
                "name": "Captain Harrow",
                "location": "Irongate City",
                "personality": "A gruff, scarred military captain who distrusts strangers until they prove themselves. Speaks bluntly. Respects strength and dislikes cowardice. Will offer elite contracts to high-level players.",
                "known_about_players": ["name", "level", "atk"],
                "can_trade": False,
                "inventory": [],
            },
            {
                "id": "witch_sylva",
                "name": "Sylva the Bog Witch",
                "location": "Bogmire",
                "personality": "An eccentric swamp witch who cackles at odd moments. Trades unusual items for unusual ingredients. Knows about curses, poisons, and healing. Not evil, just deeply strange.",
                "known_about_players": ["name", "hp", "mana"],
                "can_trade": True,
                "inventory": [
                    {"item_id": "food_elixir", "qty": 3, "price": 60},
                    {"item_id": "mat_bone",    "qty": 15, "price": 3},
                ],
            },
            {
                "id": "marina_fisher",
                "name": "Marina the Fisher",
                "location": "Saltmere Port",
                "personality": "A cheerful, weathered sea captain who buys rare fish for top gem. Loves seafaring tales. Will give tips about the best fishing spots if you bring her something interesting.",
                "known_about_players": ["name", "items"],
                "can_trade": True,
                "inventory": [
                    {"item_id": "food_bread",  "qty": 10, "price": 4},
                    {"item_id": "wpn_spear",   "qty": 2,  "price": 80},
                ],
            },
            {
                "id": "forge_master_dorn",
                "name": "Forge Master Dorn",
                "location": "Irongate City",
                "personality": "A massive dwarf-like man who forges weapons. Speaks in short sentences. Very proud of his craft. Will repair weapons for gems and craft custom items for rare materials.",
                "known_about_players": ["name", "weapons"],
                "can_trade": True,
                "inventory": [
                    {"item_id": "wpn_sword",   "qty": 3, "price": 90},
                    {"item_id": "wpn_axe",     "qty": 4, "price": 70},
                    {"item_id": "wpn_dagger",  "qty": 6, "price": 40},
                ],
            },
            {
                "id": "nomad_renn",
                "name": "Renn the Nomad",
                "location": "Dustwind Crossing",
                "personality": "A mysterious desert wanderer with sand-coloured robes and sharp eyes. Has survived the Dunes of Kor. Trades survival gear and ancient artefacts. Speaks sparingly but meaningfully.",
                "known_about_players": ["name", "location"],
                "can_trade": True,
                "inventory": [
                    {"item_id": "food_stew",     "qty": 5, "price": 15},
                    {"item_id": "mat_gem_shard", "qty": 2, "price": 45},
                ],
            },
            {
                "id": "hunter_brand",
                "name": "Brand the Hunter",
                "location": "Thornwood Village",
                "personality": "A lean, quiet ranger who knows every trail in the Thornwood. Respects experienced hunters. Gives hunting tips based on what a player brings him. Doesn't like people who waste what they kill.",
                "known_about_players": ["name", "items", "level"],
                "can_trade": True,
                "inventory": [
                    {"item_id": "wpn_bow",   "qty": 2, "price": 65},
                    {"item_id": "wpn_axe",   "qty": 3, "price": 55},
                    {"item_id": "food_stew", "qty": 4, "price": 12},
                ],
            },
        ]
    }


def load_npcs():
    return _read_json(os.path.join(_data_dir, "npcs.json")) or _default_npcs()


def get_npcs_at_location(location_name):
    all_npcs = load_npcs()["npcs"]
    return [n for n in all_npcs if n["location"].lower() == location_name.lower()]


# ─────────────────────────────────────────────────────────────────────────────
# MAP GENERATION (Pillow)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_map():
    """Generate world_map_base.png if it doesn't exist."""
    base_path = os.path.join(_data_dir, "world_map_base.png")
    if not os.path.exists(base_path):
        _generate_map(base_path)
    # Also create the annotated copy
    map_path = os.path.join(_data_dir, MAP_FILENAME)
    if not os.path.exists(map_path):
        import shutil
        shutil.copy2(base_path, map_path)


def _generate_map(output_path):
    """
    Procedurally generate a fantasy world map using Pillow.
    Uses layered noise simulation for landmass, then overlays
    colour-coded regions and location markers.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import struct, zlib
    except ImportError:
        print("[game_engine] Pillow not available — map generation skipped.")
        return

    W, H = MAP_WIDTH, MAP_HEIGHT
    print("[game_engine] Generating world map... (this may take a few seconds)")

    # ── 1. Base terrain using multi-octave noise simulation ──────────────────
    # We use a deterministic pseudo-noise based on sin/cos sums as a lightweight
    # noise substitute (no numpy required).
    seed = 42
    rng  = random.Random(seed)

    def noise_val(x, y, scale, octaves=4):
        """Simple layered sine/cosine noise approximation."""
        val = 0.0
        amp = 1.0
        freq = 1.0
        for _ in range(octaves):
            val += amp * (
                math.sin(x * freq / scale + rng.uniform(0, 6.28)) *
                math.cos(y * freq / scale + rng.uniform(0, 6.28))
            )
            amp  *= 0.5
            freq *= 2.0
        return val

    # Pre-generate terrain heights (0.0 – 1.0)
    step = 4   # sample every 4px for speed, interpolate
    sw, sh = W // step + 2, H // step + 2
    heights = [[0.0] * sh for _ in range(sw)]
    for sx in range(sw):
        for sy in range(sh):
            x = sx * step
            y = sy * step
            # Centre bias — makes land form in the middle
            cx_dist = abs(x - W / 2) / (W / 2)
            cy_dist = abs(y - H / 2) / (H / 2)
            centre_falloff = 1.0 - max(cx_dist, cy_dist) ** 1.4
            n = noise_val(x, y, 320, octaves=5)
            heights[sx][sy] = max(0.0, min(1.0, (n + 1.0) / 2.0 * centre_falloff + 0.05))

    # ── 2. Colour map based on height ────────────────────────────────────────
    def height_to_colour(h, x, y):
        # Deep ocean
        if h < 0.25: return (30, 90, 180)
        # Shallow coast
        if h < 0.32: return (70, 140, 210)
        # Sandy beach
        if h < 0.36: return (220, 200, 150)
        # Lowland / grass
        if h < 0.50: return (100, 170, 80)
        # Forest / mid
        if h < 0.62: return (60, 130, 55)
        # Highland
        if h < 0.74: return (140, 120, 90)
        # Rocky
        if h < 0.85: return (160, 155, 145)
        # Snow peak
        return (235, 240, 250)

    img  = Image.new("RGB", (W, H), (30, 90, 180))
    pix  = img.load()

    for px in range(W):
        for py in range(H):
            sx = min(px // step, sw - 2)
            sy = min(py // step, sh - 2)
            # Bilinear interpolation
            fx = (px % step) / step
            fy = (py % step) / step
            h00 = heights[sx][sy]
            h10 = heights[sx+1][sy]
            h01 = heights[sx][sy+1]
            h11 = heights[sx+1][sy+1]
            h = (h00*(1-fx)*(1-fy) + h10*fx*(1-fy) +
                 h01*(1-fx)*fy     + h11*fx*fy)
            pix[px, py] = height_to_colour(h, px, py)

    # ── 3. Slight blur for natural look ──────────────────────────────────────
    img = img.filter(ImageFilter.SMOOTH_MORE)
    draw = ImageDraw.Draw(img)

    # ── 4. Draw rivers (hand-tuned paths following the location data) ─────────
    river_colour = (70, 150, 210)
    river_width  = 3
    # River Crest: Frostpeak → Glacier Pass → River Crest Ford → Millford → Crestlake → Saltmere Port
    river_crest = [
        (680, 260), (820, 280), (1000, 180), (1150, 650), (1050, 720),
        (1100, 880), (1200, 1400)
    ]
    draw.line(river_crest, fill=river_colour, width=river_width)

    # Ashfen tributary
    ashfen_river = [(600, 700), (700, 750), (580, 820), (480, 900)]
    draw.line(ashfen_river, fill=river_colour, width=2)

    # Eastern stream
    east_stream = [(2100, 900), (2000, 1100), (1900, 1400)]
    draw.line(east_stream, fill=river_colour, width=2)

    # ── 5. Place location markers ─────────────────────────────────────────────
    world = _default_world()
    try:
        font_large  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_small  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except Exception:
        font_large  = ImageFont.load_default()
        font_small  = font_large

    CITY_COLOUR    = (220, 50, 50)
    VILLAGE_COLOUR = (240, 160, 30)
    PLACE_COLOUR   = (180, 50, 180)
    WATER_COLOUR   = (30, 160, 240)
    DANGER_COLOUR  = (220, 0, 0)

    def dot_colour(loc):
        if loc.get("is_city"):         return CITY_COLOUR
        if loc.get("is_village"):      return VILLAGE_COLOUR
        if loc.get("danger", 0) >= 8:  return DANGER_COLOUR
        if loc.get("has_water"):       return WATER_COLOUR
        return PLACE_COLOUR

    for loc in world["locations"]:
        cx, cy = loc["coords"]
        r = 7 if (loc.get("is_city") or loc.get("is_village")) else 5
        col = dot_colour(loc)
        # Outline
        draw.ellipse([(cx-r-1, cy-r-1), (cx+r+1, cy+r+1)], fill=(30, 30, 30))
        draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=col)
        # Label
        name = loc["name"]
        font = font_large if (loc.get("is_city") or loc.get("is_village")) else font_small
        draw.text((cx + r + 3, cy - 6), name, fill=(20, 20, 20), font=font)

    # ── 6. Legend ─────────────────────────────────────────────────────────────
    legend_x, legend_y = 20, H - 130
    draw.rectangle([(legend_x-4, legend_y-4), (legend_x+200, legend_y+120)],
                   fill=(255, 255, 255, 200), outline=(100, 100, 100))
    items_legend = [
        (CITY_COLOUR,   "City"),
        (VILLAGE_COLOUR,"Village"),
        (WATER_COLOUR,  "Water/Fishing"),
        (PLACE_COLOUR,  "Location"),
        (DANGER_COLOUR, "Danger Zone"),
    ]
    for i, (col, label) in enumerate(items_legend):
        lx = legend_x + 4
        ly = legend_y + 4 + i * 22
        draw.ellipse([(lx, ly+2), (lx+12, ly+14)], fill=col)
        draw.text((lx + 16, ly), label, fill=(20, 20, 20), font=font_small)

    # ── 7. Title ──────────────────────────────────────────────────────────────
    draw.text((W//2 - 80, 10), "✦ REALM OF AETHERMOOR ✦",
              fill=(255, 255, 240), font=font_large)

    img.save(output_path, "PNG", optimize=True)
    print(f"[game_engine] Map saved to {output_path}")


def render_map_with_players(group_id):
    """
    Returns image bytes (PNG) of the map annotated with current player locations.
    Returns None if Pillow is not available.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import shutil
    except ImportError:
        return None

    base_path = os.path.join(_data_dir, "world_map_base.png")
    if not os.path.exists(base_path):
        _generate_map(base_path)

    img  = Image.open(base_path).copy()
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11)
    except Exception:
        font = ImageFont.load_default()

    players = _load_players()
    world   = load_world()
    loc_map = {loc["name"]: loc for loc in world["locations"]}

    # Place a coloured star for each player in this group
    player_colours = [
        (255, 50,  50 ), (50,  200, 50 ), (50,  100, 255),
        (255, 200, 0  ), (200, 50,  255), (0,   220, 200),
        (255, 120, 0  ), (220, 0,   130),
    ]
    colour_idx = 0

    for key, p in players.items():
        if not key.startswith(f"{group_id}:"):
            continue
        loc_name = p.get("location", "Hearthstone Village")
        loc      = loc_map.get(loc_name)
        if not loc:
            continue
        cx, cy = loc["coords"]
        col    = player_colours[colour_idx % len(player_colours)]
        colour_idx += 1

        # Small star ★ above the location dot
        # Offset slightly so multiple players at same location don't overlap perfectly
        ox = (colour_idx % 3 - 1) * 14
        oy = -20 + (colour_idx % 2) * -8
        draw.ellipse([(cx+ox-6, cy+oy-6), (cx+ox+6, cy+oy+6)],
                     fill=col, outline=(20, 20, 20))
        name = p.get("name", "?")[:8]
        draw.text((cx+ox-len(name)*2, cy+oy+8), name, fill=col, font=font)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# GAME TIME
# ─────────────────────────────────────────────────────────────────────────────

def game_time_now():
    """
    Returns the current in-game datetime.
    2 in-game days pass per 1 real day, so in-game seconds = real_seconds * 2.
    Epoch anchor: real 2025-01-01 00:00 UTC = in-game 2025-01-01 00:00
    """
    real_ts   = time.time()
    game_ts   = real_ts * GAME_TIME_MULTIPLIER
    return datetime.fromtimestamp(game_ts, tz=timezone.utc)


def game_time_str():
    gt = game_time_now()
    hour = gt.hour
    period = "Dawn" if 5<=hour<8 else "Day" if 8<=hour<17 else "Dusk" if 17<=hour<20 else "Night"
    return f"{gt.strftime('%A, %d %b — %H:%M')} ({period})"


def is_game_night():
    return game_time_now().hour < 6 or game_time_now().hour >= 20


# ─────────────────────────────────────────────────────────────────────────────
# TRAVEL SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

# Travel speed: cells per real minute (each connection = 1 step)
# We use distance from pixel coords to estimate travel time.
TRAVEL_SPEED_PIX_PER_REAL_MINUTE = 60   # pixels per real-time minute

def _travel_time_seconds(from_loc, to_loc):
    """
    Real-time seconds to travel between two location dicts.
    Based on pixel distance on the map.
    """
    x1, y1 = from_loc["coords"]
    x2, y2 = to_loc["coords"]
    dist = math.sqrt((x2-x1)**2 + (y2-y1)**2)
    minutes = dist / TRAVEL_SPEED_PIX_PER_REAL_MINUTE
    return max(30, int(minutes * 60))   # minimum 30 seconds


def _are_connected(loc_a_name, loc_b_name):
    loc_a = get_location(loc_a_name)
    loc_b = get_location(loc_b_name)
    if not loc_a or not loc_b:
        return False
    return loc_b_name in loc_a.get("connections", [])


def start_travel(group_id, user_id, destination_name):
    """
    Begin travelling to a destination.
    Returns (success: bool, message: str)
    """
    player = _get_player(group_id, user_id)
    if not player:
        return False, "You haven't registered yet! Use !beginpoints to start."

    if player.get("in_combat"):
        return False, "You can't travel while in combat!"

    dest = find_location_fuzzy(destination_name)
    if not dest:
        return False, f"Unknown location: '{destination_name}'. Check !locations for the full list."

    current_loc = player.get("location", "Hearthstone Village")
    if dest["name"] == current_loc:
        return False, f"You're already at {dest['name']}!"

    if player.get("travelling_to"):
        return False, f"You're already travelling to {player['travelling_to']}! Wait for arrival or the journey will reset."

    if not _are_connected(current_loc, dest["name"]):
        # Find a path hint
        current = get_location(current_loc)
        conns = current.get("connections", []) if current else []
        conn_str = ", ".join(conns) if conns else "none"
        return False, (f"You can't travel directly from {current_loc} to {dest['name']}.\n"
                       f"From {current_loc} you can reach: {conn_str}")

    travel_secs = _travel_time_seconds(get_location(current_loc), dest)
    arrive_at   = time.time() + travel_secs

    player["travelling_to"] = dest["name"]
    player["travel_arrive"] = arrive_at
    _save_player(group_id, user_id, player)

    mins = travel_secs // 60
    secs = travel_secs % 60
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    danger   = dest.get("danger", 0)
    warn     = f" ⚠️ Danger level {danger}/10." if danger >= 5 else ""
    return True, (f"🗺️ {player['name']} sets off towards {dest['name']}!\n"
                  f"Estimated arrival: {time_str}.{warn}")


def check_arrivals(group_id):
    """
    Check all players in a group for completed travel.
    Called periodically from a background thread.
    Returns list of arrival messages to send.
    """
    messages = []
    with _game_lock:
        players = _load_players()
        changed = False
        now = time.time()
        for key, p in players.items():
            if not key.startswith(f"{group_id}:"):
                continue
            if not p.get("travelling_to"):
                continue
            if p.get("travel_arrive", now + 1) <= now:
                dest_name = p["travelling_to"]
                old_loc   = p.get("location", "?")
                p["location"]      = dest_name
                p["travelling_to"] = None
                p["travel_arrive"] = None
                changed = True

                dest = get_location(dest_name)
                danger = dest.get("danger", 0) if dest else 0

                # Random encounter chance based on danger
                encounter_msg = ""
                if danger >= 3 and random.random() < danger * 0.04:
                    enemies = get_enemies_for_danger(danger)
                    if enemies:
                        enemy = random.choice(enemies)
                        encounter_msg = f"\n⚔️ Upon arriving, {p['name']} encounters a {enemy['name']}! Use !fight to engage or !flee to run."

                messages.append(
                    f"📍 {p['name']} has arrived at {dest_name}!{encounter_msg}"
                )
        if changed:
            _save_players(players)
    return messages


# ─────────────────────────────────────────────────────────────────────────────
# FISHING
# ─────────────────────────────────────────────────────────────────────────────

FISH_COOLDOWN = 180   # 3 min real time


def cmd_fish(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    loc = get_location(player.get("location", "Hearthstone Village"))
    if not loc or not loc.get("has_water"):
        return (f"🎣 There's no water here to fish in!\n"
                f"You're at {player.get('location', '?')}. Travel to a water location first.")

    now = time.time()
    cd  = player.get("cd_fish", 0)
    if now < cd:
        remaining = int(cd - now)
        m, s = divmod(remaining, 60)
        return f"🎣 Your line is still drying! Try again in {m}m {s}s."

    # Fish pool for this location
    items_db = load_items()
    loc_name = loc["name"]
    pool = [f for f in items_db["fish"]
            if "any_water" in f.get("locations", []) or loc_name in f.get("locations", [])]
    if not pool:
        pool = items_db["fish"][:3]

    # Rarity weights influenced by luck
    luck = player.get("luck", 5)
    weights = []
    for fish in pool:
        rarity = fish.get("rarity", "common")
        base   = {"common": 60, "uncommon": 25, "rare": 10, "epic": 3, "legendary": 1}.get(rarity, 30)
        weights.append(max(1, base + (luck - 5)))

    caught = random.choices(pool, weights=weights, k=1)[0]
    qty    = random.randint(1, 2)
    xp_gain = {"common": 5, "uncommon": 10, "rare": 20, "epic": 40, "legendary": 100}.get(caught.get("rarity","common"), 5)

    # Add to inventory
    _add_item_to_player(group_id, user_id, caught["id"], caught["name"], qty, "fish")

    # Update cooldown and XP
    player["cd_fish"] = now + FISH_COOLDOWN
    player = _give_xp(player, xp_gain)
    _save_player(group_id, user_id, player)

    rarity_emoji = {"common": "🐟", "uncommon": "🐠", "rare": "🐡", "epic": "🦈", "legendary": "✨🐟✨"}.get(caught.get("rarity","common"), "🐟")
    qty_str = f"x{qty}" if qty > 1 else ""
    return (f"{rarity_emoji} {player['name']} cast their line into the {loc_name}...\n"
            f"Caught: {caught['name']} {qty_str} ({caught['rarity'].capitalize()})\n"
            f"Worth {caught['sell_value']*qty} gems if sold. +{xp_gain} XP. "
            f"({player.get('hp',0)}/{player.get('max_hp',0)} HP | Level {player.get('level',1)})")


# ─────────────────────────────────────────────────────────────────────────────
# HUNTING
# ─────────────────────────────────────────────────────────────────────────────

HUNT_COOLDOWN = 300   # 5 min real time


def cmd_hunt(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    loc = get_location(player.get("location", "Hearthstone Village"))
    if not loc or not loc.get("has_forest"):
        return (f"🏹 There's nothing to hunt here!\n"
                f"Travel to a forest or wilderness location first.")

    now = time.time()
    cd  = player.get("cd_hunt", 0)
    if now < cd:
        remaining = int(cd - now)
        m, s = divmod(remaining, 60)
        return f"🏹 You need to rest before hunting again. Try in {m}m {s}s."

    danger = loc.get("danger", 1)
    items_db = load_items()
    hunt_pool = items_db.get("hunt_drops", [])

    # Chance of monster encounter
    encounter_chance = 0.1 + danger * 0.05
    if random.random() < encounter_chance:
        enemies = get_enemies_for_danger(danger)
        if enemies:
            enemy = random.choice(enemies)
            player["cd_hunt"] = now + HUNT_COOLDOWN
            _save_player(group_id, user_id, player)
            return (f"⚔️ {player['name']} ventures into the {loc['name']} to hunt...\n"
                    f"A wild {enemy['name']} attacks! (HP: {enemy['hp']} ATK: {enemy['atk']})\n"
                    f"Use !fight to engage or !flee to retreat. The hunt will resume after combat.")

    # Successful hunt
    luck = player.get("luck", 5)
    n_drops = random.randint(1, max(1, 1 + luck // 5))
    drops = random.choices(hunt_pool, k=n_drops)
    xp_gain = 0
    loot_lines = []
    for drop in drops:
        _add_item_to_player(group_id, user_id, drop["id"], drop["name"], 1, "material")
        xp_gain += 10
        loot_lines.append(f"  • {drop['name']} (worth {drop.get('sell_value',0)} gems)")

    player["cd_hunt"] = now + HUNT_COOLDOWN
    player = _give_xp(player, xp_gain)
    _save_player(group_id, user_id, player)

    loot_str = "\n".join(loot_lines)
    return (f"🏹 {player['name']} hunts in the {loc['name']}...\n"
            f"Loot:\n{loot_str}\n"
            f"+{xp_gain} XP | Level {player.get('level',1)}")


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _add_item_to_player(group_id, user_id, item_id, item_name, qty, category):
    player = _get_player(group_id, user_id)
    if not player:
        return

    if category == "weapon":
        # Weapon slot logic
        weapons = player.get("weapons", [])
        for slot in weapons:
            if slot["item_id"] == item_id:
                # Stack if stackable
                items_db = load_items()
                wpn_def  = next((w for w in items_db.get("weapons", []) if w["id"] == item_id), None)
                if wpn_def and wpn_def.get("stackable"):
                    slot["qty"] = min(slot["qty"] + qty, wpn_def.get("max_stack", 1))
                return
        if len(weapons) < WEAPON_SLOTS:
            weapons.append({"item_id": item_id, "name": item_name, "qty": qty, "equipped": False})
            player["weapons"] = weapons
    else:
        items = player.get("items", [])
        for slot in items:
            if slot["item_id"] == item_id:
                slot["qty"] = slot.get("qty", 0) + qty
                player["items"] = items
                _save_player(group_id, user_id, player)
                return
        items.append({"item_id": item_id, "name": item_name, "qty": qty, "category": category})
        player["items"] = items

    _save_player(group_id, user_id, player)


def _inventory_summary(player):
    lines = []
    weapons = player.get("weapons", [])
    if weapons:
        lines.append("⚔️ Weapons:")
        for i, w in enumerate(weapons, 1):
            eq = " [E]" if w.get("equipped") else ""
            lines.append(f"  {i}. {w['name']} x{w.get('qty',1)}{eq}")
    items = player.get("items", [])
    if items:
        lines.append("🎒 Items:")
        cats = {}
        for it in items:
            c = it.get("category", "misc")
            cats.setdefault(c, []).append(it)
        for cat, itms in cats.items():
            lines.append(f"  [{cat.capitalize()}]")
            for it in itms:
                lines.append(f"    • {it['name']} x{it.get('qty',1)}")
    chests = player.get("chests", [])
    if chests:
        lines.append("📦 Chests:")
        for i, ch in enumerate(chests, 1):
            lines.append(f"  {i}. {ch.get('name','Chest')} — {ch.get('stored_gems',0)}/{ch.get('capacity',0)} gems stored")
    if not weapons and not items and not chests:
        lines.append("  (empty)")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# XP & LEVELLING
# ─────────────────────────────────────────────────────────────────────────────

def _xp_for_level(level):
    """XP required to reach next level from current level."""
    return int(XP_PER_LEVEL_BASE * (level ** 1.5))


def _give_xp(player, amount):
    """Add XP and handle level-ups. Returns updated player dict."""
    player["xp"] = player.get("xp", 0) + amount
    while player["xp"] >= _xp_for_level(player.get("level", 1)):
        player["xp"]  -= _xp_for_level(player.get("level", 1))
        player["level"] = player.get("level", 1) + 1
        # Stat increases on level up
        player["max_hp"]   = player.get("max_hp", STARTING_MAX_HP) + 5
        player["hp"]       = player["max_hp"]   # heal to full on level up
        player["max_mana"] = player.get("max_mana", STARTING_MAX_MANA) + 3
        player["mana"]     = player["max_mana"]
        player["atk"]      = player.get("atk", STARTING_ATK) + 2
        player["def"]      = player.get("def", STARTING_DEF) + 1
        player["spd"]      = player.get("spd", STARTING_SPD) + 1
        player["luck"]     = player.get("luck", STARTING_LUCK) + 1
    return player


# ─────────────────────────────────────────────────────────────────────────────
# GEM MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _gems_in_chests(player):
    return sum(ch.get("stored_gems", 0) for ch in player.get("chests", []))


def _total_wealth(player):
    return player.get("gems", 0) + _gems_in_chests(player)


def cmd_sell(group_id, user_id, name, args):
    """!sell <item_name> [qty]  — sell fish/materials from inventory for gems."""
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    if not args:
        return "Usage: !sell <item name> [quantity]  e.g. !sell minnow 5"

    # Parse qty if last token is a number
    parts = args.split()
    qty = 1
    if parts and parts[-1].isdigit():
        qty = max(1, int(parts[-1]))
        item_query = " ".join(parts[:-1]).lower()
    else:
        item_query = args.lower()

    items = player.get("items", [])
    match = None
    for it in items:
        if item_query in it["name"].lower():
            match = it
            break

    if not match:
        return f"You don't have any '{item_query}' in your inventory."

    available = match.get("qty", 0)
    qty = min(qty, available)
    if qty <= 0:
        return f"You don't have enough {match['name']} to sell."

    # Look up sell value
    items_db = load_items()
    sell_val = 0
    for cat in items_db.values():
        if isinstance(cat, list):
            for item in cat:
                if item.get("id") == match.get("item_id") or item.get("name").lower() == match["name"].lower():
                    sell_val = item.get("sell_value", 0)
                    break

    total = sell_val * qty
    match["qty"] -= qty
    if match["qty"] <= 0:
        items.remove(match)
    player["items"] = items
    player["gems"]  = player.get("gems", 0) + total
    _save_player(group_id, user_id, player)

    return (f"💎 {player['name']} sold {qty}x {match['name']} for {total} gems!\n"
            f"Balance: {player['gems']} gems (+ {_gems_in_chests(player)} in chests)")


# ─────────────────────────────────────────────────────────────────────────────
# COIN FLIP GAMBLING
# ─────────────────────────────────────────────────────────────────────────────

COIN_COOLDOWN = 60


def cmd_coin(group_id, user_id, name, side, amount_str):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    now = time.time()
    cd  = player.get("cd_coin", 0)
    if now < cd:
        remaining = int(cd - now)
        return f"🪙 The coin is still spinning! Try again in {remaining}s."

    side = side.lower()
    if side not in ("h", "t", "heads", "tails"):
        return "Usage: !coin <h/t> <amount>  e.g. !coin h 50"

    gems = player.get("gems", 0)
    if amount_str.lower() in ("all", "allin"):
        bet = gems
    else:
        try:
            bet = int(amount_str)
        except ValueError:
            return "Amount must be a number or 'all'."

    if bet <= 0:
        return "Bet must be greater than 0."
    if bet > gems:
        return f"You only have {gems} gems. You can't bet {bet}."

    chosen_heads = side in ("h", "heads")
    result_heads = random.random() < 0.5
    win = chosen_heads == result_heads
    result_str  = "Heads 🦅" if result_heads else "Tails 🌊"
    chosen_str  = "Heads 🦅" if chosen_heads else "Tails 🌊"

    if win:
        player["gems"] = gems + bet
        outcome = f"✅ WIN! +{bet} gems → {player['gems']} gems total"
    else:
        player["gems"] = gems - bet
        outcome = f"❌ LOSS! -{bet} gems → {player['gems']} gems total"

    player["cd_coin"] = now + COIN_COOLDOWN
    _save_player(group_id, user_id, player)

    return (f"🪙 {player['name']} flips the coin...\n"
            f"Called: {chosen_str} | Result: {result_str}\n"
            f"{outcome}")


# ─────────────────────────────────────────────────────────────────────────────
# SHOP COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_shop(group_id, user_id, name):
    shop = load_shop()
    items = shop.get("items", [])
    if not items:
        return "🏪 The shop is empty! Ask an admin to add items."
    lines = ["🏪 ─ SHOP ─"]
    for i, it in enumerate(items, 1):
        cap_str = f" [Holds {it['capacity']} gems]" if it.get("category") == "chest" else ""
        lines.append(f"  {i}. {it['name']} — {it['cost']} gems{cap_str}")
        if it.get("description"):
            lines.append(f"     {it['description']}")
    lines.append("\nUse !buy <item name> to purchase.")
    return "\n".join(lines)


def cmd_buy(group_id, user_id, name, item_query):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    shop  = load_shop()
    items = shop.get("items", [])
    q     = item_query.lower().strip()
    match = None
    for it in items:
        if q in it["name"].lower() or q == str(items.index(it) + 1):
            match = it
            break

    if not match:
        return f"No shop item found matching '{item_query}'."

    cost  = match.get("cost", 0)
    gems  = player.get("gems", 0)
    if gems < cost:
        return f"💎 You need {cost} gems but only have {gems}."

    player["gems"] = gems - cost

    if match.get("category") == "chest":
        chest_record = {
            "chest_id":    f"chest_{int(time.time())}_{user_id}",
            "name":        match["name"],
            "tier":        match.get("tier", "standard"),
            "capacity":    match.get("capacity", 100),
            "stored_gems": 0,
            "stored_items": [],
        }
        player.setdefault("chests", []).append(chest_record)
        _save_player(group_id, user_id, player)
        return (f"📦 Purchased {match['name']} for {cost} gems!\n"
                f"It's now in your inventory. Use !chest store <amount> to put gems inside.\n"
                f"Remaining gems: {player['gems']}")
    else:
        _save_player(group_id, user_id, player)
        _add_item_to_player(group_id, user_id, match.get("id", ""), match["name"], 1,
                            match.get("category", "misc"))
        return f"✅ Purchased {match['name']} for {cost} gems! Remaining: {player['gems']} gems."


def cmd_chest(group_id, user_id, name, subcommand, args):
    """
    !chest list          — show all chests
    !chest store <n>     — store n gems in first available chest
    !chest store <n> <#> — store n gems in chest number #
    !chest take <n>      — take n gems from chest
    !chest take <n> <#>  — take from chest #
    """
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    chests = player.get("chests", [])
    sub    = subcommand.lower().strip() if subcommand else "list"

    if sub == "list" or not sub:
        if not chests:
            return "📦 You have no chests. Buy one from the !shop!"
        lines = ["📦 Your Chests:"]
        for i, ch in enumerate(chests, 1):
            lines.append(f"  {i}. {ch.get('name','Chest')} — {ch.get('stored_gems',0)}/{ch.get('capacity',0)} gems")
        return "\n".join(lines)

    # Parse amount and optional chest number
    parts = args.split() if args else []
    try:
        amount = int(parts[0]) if parts else 0
    except ValueError:
        return "Usage: !chest store <amount> [chest#]  or  !chest take <amount> [chest#]"

    chest_idx = 0
    if len(parts) >= 2:
        try:
            chest_idx = int(parts[1]) - 1
        except ValueError:
            pass

    if not chests:
        return "📦 You have no chests. Buy one from the !shop!"
    if chest_idx < 0 or chest_idx >= len(chests):
        chest_idx = 0

    chest = chests[chest_idx]

    if sub == "store":
        gems    = player.get("gems", 0)
        space   = chest["capacity"] - chest.get("stored_gems", 0)
        actual  = min(amount, gems, space)
        if actual <= 0:
            if gems <= 0:
                return "You have no gems to store."
            return f"📦 {chest['name']} is full! ({chest['stored_gems']}/{chest['capacity']} gems)"
        chest["stored_gems"] = chest.get("stored_gems", 0) + actual
        player["gems"]       = gems - actual
        player["chests"]     = chests
        _save_player(group_id, user_id, player)
        return (f"📦 Stored {actual} gems in {chest['name']}.\n"
                f"Chest: {chest['stored_gems']}/{chest['capacity']} | Pocket: {player['gems']} gems")

    if sub == "take":
        stored = chest.get("stored_gems", 0)
        actual = min(amount, stored)
        if actual <= 0:
            return f"📦 {chest['name']} has no gems to withdraw."
        chest["stored_gems"] = stored - actual
        player["gems"]       = player.get("gems", 0) + actual
        player["chests"]     = chests
        _save_player(group_id, user_id, player)
        return (f"📦 Withdrew {actual} gems from {chest['name']}.\n"
                f"Chest: {chest['stored_gems']}/{chest['capacity']} | Pocket: {player['gems']} gems")

    return "Unknown chest command. Use: !chest list / !chest store <n> / !chest take <n>"


# ─────────────────────────────────────────────────────────────────────────────
# STATS / PROFILE
# ─────────────────────────────────────────────────────────────────────────────

def cmd_stats(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."

    loc       = player.get("location", "?")
    travelling = player.get("travelling_to")
    loc_str   = f"✈️ Travelling to {travelling}" if travelling else f"📍 {loc}"
    xp_needed = _xp_for_level(player.get("level", 1))
    xp_bar_len = 10
    xp_filled  = int((player.get("xp", 0) / xp_needed) * xp_bar_len)
    xp_bar     = "█" * xp_filled + "░" * (xp_bar_len - xp_filled)

    clicker_str = "✅ Active" if player.get("has_clicker") else "❌ None"

    return (
        f"━━━ 👤 {player.get('name','?')} ━━━\n"
        f"Level {player.get('level',1)} | XP: [{xp_bar}] {player.get('xp',0)}/{xp_needed}\n"
        f"💎 Gems: {player.get('gems',0)} (+ {_gems_in_chests(player)} in chests)\n"
        f"❤️ HP:   {player.get('hp',0)}/{player.get('max_hp',0)}\n"
        f"💧 Mana: {player.get('mana',0)}/{player.get('max_mana',0)}\n"
        f"⚔️ ATK: {player.get('atk',0)}  🛡️ DEF: {player.get('def',0)}  "
        f"💨 SPD: {player.get('spd',0)}\n"
        f"🍀 Luck: {player.get('luck',0)}  ⚖️ Weight: {player.get('weight',0)}  "
        f"📐 Size: {player.get('size',0)}\n"
        f"🖱️ Clicker: {clicker_str} (+{CLICKER_GEMS_PER_TICK} gem/30s)\n"
        f"{loc_str}\n"
        f"🕒 Game time: {game_time_str()}"
    )


def cmd_gems(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."
    pocket = player.get("gems", 0)
    stored = _gems_in_chests(player)
    return (f"💎 {player.get('name','?')}'s Gems\n"
            f"  Pocket: {pocket}\n"
            f"  In chests: {stored}\n"
            f"  Total: {pocket + stored}")


def cmd_give(group_id, from_user_id, from_name, target_name, amount_str):
    """!give @username <amount>"""
    from_player = _get_player(group_id, from_user_id)
    if not from_player:
        return "You haven't registered yet! Use !beginpoints to start."

    try:
        amount = int(amount_str)
    except ValueError:
        return "Amount must be a whole number."
    if amount <= 0:
        return "Amount must be positive."

    if from_player.get("gems", 0) < amount:
        return f"You only have {from_player.get('gems',0)} gems."

    # Find target player in this group
    players = _load_players()
    target_player = None
    target_key    = None
    target_query  = target_name.lower().replace("@", "")
    for key, p in players.items():
        if key.startswith(f"{group_id}:") and target_query in p.get("name", "").lower():
            target_player = p
            target_key    = key
            break

    if not target_player:
        return f"Couldn't find player '{target_name}' in this group."

    with _game_lock:
        players = _load_players()
        fp = players.get(_player_key(group_id, from_user_id))
        tp = players.get(target_key)
        if not fp or not tp:
            return "Error loading player data."
        if fp.get("gems", 0) < amount:
            return f"You only have {fp.get('gems',0)} gems."
        fp["gems"] = fp.get("gems", 0) - amount
        tp["gems"] = tp.get("gems", 0) + amount
        players[_player_key(group_id, from_user_id)] = fp
        players[target_key] = tp
        _save_players(players)

    return (f"💎 {from_player.get('name','?')} gave {amount} gems to {target_player.get('name','?')}!\n"
            f"{from_player.get('name','?')}: {fp['gems']} gems | "
            f"{target_player.get('name','?')}: {tp['gems']} gems")


# ─────────────────────────────────────────────────────────────────────────────
# LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

def cmd_leaderboard(group_id):
    players = _load_players()
    group_players = {k: v for k, v in players.items() if k.startswith(f"{group_id}:")}
    if not group_players:
        return "No players registered yet! Use !beginpoints to join."

    ranked = sorted(group_players.values(),
                    key=lambda p: p.get("gems", 0) + _gems_in_chests(p), reverse=True)
    lines  = ["💎 ─ GEM LEADERBOARD ─"]
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(ranked[:LEADERBOARD_SIZE]):
        medal  = medals[i] if i < 3 else f"{i+1}."
        total  = p.get("gems", 0) + _gems_in_chests(p)
        lines.append(f"  {medal} {p.get('name','?')} — {total} gems (Lv{p.get('level',1)})")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LOCATIONS COMMAND
# ─────────────────────────────────────────────────────────────────────────────

def cmd_locations(group_id, user_id, filter_str=""):
    player  = _get_player(group_id, user_id)
    current = player.get("location", "?") if player else "?"
    world   = load_world()
    locs    = world["locations"]

    if filter_str:
        f = filter_str.lower()
        locs = [l for l in locs if f in l["name"].lower() or f in l["region"].lower()]

    if not locs:
        return "No locations found matching that filter."

    # Group by region
    regions = {}
    for loc in locs:
        regions.setdefault(loc["region"], []).append(loc)

    lines = [f"🗺️ World Locations (you are at: {current})"]
    for region, rlocs in sorted(regions.items()):
        lines.append(f"\n── {region} ──")
        for loc in rlocs:
            marker  = "📍" if loc["name"] == current else ("🏰" if loc.get("is_city") else ("🏡" if loc.get("is_village") else "·"))
            danger  = f" ⚠️{loc.get('danger',0)}" if loc.get("danger", 0) >= 3 else ""
            water   = " 🎣" if loc.get("has_water") else ""
            forest  = " 🌲" if loc.get("has_forest") else ""
            lines.append(f"  {marker} {loc['name']}{danger}{water}{forest}")
    lines.append("\n🎣=fishing  🌲=hunting  ⚠️=danger level")
    return "\n".join(lines)


def cmd_where(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "You haven't registered yet! Use !beginpoints to start."
    loc  = player.get("location", "?")
    dest = player.get("travelling_to")
    if dest:
        arrive = player.get("travel_arrive", 0)
        rem    = max(0, int(arrive - time.time()))
        m, s   = divmod(rem, 60)
        return f"✈️ {player['name']} is travelling to {dest}. ETA: {m}m {s}s."
    location = get_location(loc)
    if location:
        conn_str = ", ".join(location.get("connections", []))
        return (f"📍 {player['name']} is at {loc} ({location.get('region','?')})\n"
                f"Danger: {location.get('danger',0)}/10\n"
                f"🎣 Fish: {'Yes' if location.get('has_water') else 'No'}  "
                f"🌲 Hunt: {'Yes' if location.get('has_forest') else 'No'}\n"
                f"Connections: {conn_str}")
    return f"📍 {player['name']} is at {loc}."


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST
# ─────────────────────────────────────────────────────────────────────────────

WEATHER_TYPES = [
    ("☀️ Clear skies",      "Calm conditions. Good for travel and fishing.", -2),
    ("⛅ Partly cloudy",    "Pleasant weather. No significant effects.",       0),
    ("🌧️ Rainy",           "Fishing yields slightly improved. Travel slowed.",  1),
    ("⛈️ Thunderstorm",    "Danger levels +1. Travel takes longer.",           2),
    ("🌨️ Snowfall",        "Cold. ATK reduced by 1 in exposed areas.",         2),
    ("🌫️ Dense fog",       "Hunting harder. Monsters harder to spot.",         1),
    ("🌪️ Windstorm",       "Dangerous for high locations. Speed -1.",          3),
    ("🔥 Scorching heat",  "Mana regenerates slower. Deserts extra deadly.",   3),
]


def cmd_forecast(group_id, user_id):
    player = _get_player(group_id, user_id)
    loc    = get_location(player.get("location", "Hearthstone Village")) if player else None

    # Deterministic per-day weather based on date seed
    gt       = game_time_now()
    day_seed = gt.year * 1000 + gt.timetuple().tm_yday
    rng      = random.Random(day_seed)
    weather  = rng.choice(WEATHER_TYPES)

    weather_name, weather_desc, weather_mod = weather
    base_danger = loc.get("danger", 0) if loc else 0
    effective   = min(10, base_danger + weather_mod)

    night_warn = "\n🌙 It is currently night in-game. Monsters are more active." if is_game_night() else ""
    loc_str    = f" at {loc['name']}" if loc else ""

    return (f"🌤️ Daily Forecast{loc_str}:\n"
            f"{weather_name} — {weather_desc}\n"
            f"Effective danger{loc_str}: {effective}/10\n"
            f"Game time: {game_time_str()}{night_warn}")


# ─────────────────────────────────────────────────────────────────────────────
# HELP SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

HELP_SECTIONS = {
    "points": (
        "📖 !help points — Game Overview\n"
        "This is a fantasy RPG played entirely through chat!\n"
        "Sections:\n"
        "  !help points start   — How to begin\n"
        "  !help points basics  — Core commands\n"
        "  !help points travel  — Moving around the world\n"
        "  !help points items   — Inventory, fishing, hunting\n"
        "  !help points gems    — Currency and economy\n"
        "  !help points chests  — Chest storage\n"
        "  !help points stats   — Your character stats\n"
        "  !help points combat  — Fighting (coming soon)\n"
    ),
    "points start": (
        "🌟 Getting Started:\n"
        "1. Use !beginpoints to register your character.\n"
        "   This can only be done once per account.\n"
        "2. You'll start at Hearthstone Village with:\n"
        "   - 50/50 HP  |  30/30 Mana\n"
        "   - 1 passive gem clicker (+1 gem every 30s)\n"
        "3. Check your profile with !stats\n"
        "4. View the world with !map or !locations\n"
        "5. Travel with !go <location name>\n"
        "6. Fish near water with !fish, hunt in forests with !hunt\n"
        "7. Sell loot for gems with !sell <item>\n"
        "8. Buy chests from the !shop to keep gems safe"
    ),
    "points basics": (
        "⚙️ Basic Commands:\n"
        "  !stats          — Your character sheet\n"
        "  !gems           — Your gem balance\n"
        "  !inventory      — Your items\n"
        "  !where          — Your current location\n"
        "  !locations      — World map (text)\n"
        "  !map            — World map (image)\n"
        "  !forecast       — Today's weather & danger\n"
        "  !leaderboard    — Top players by gems\n"
        "  !shop           — Browse the shop\n"
        "  !buy <item>     — Buy from shop\n"
        "  !give @user <n> — Give gems to someone\n"
        "  !coin h/t <n>   — Coin flip gambling (1 min cooldown)"
    ),
    "points travel": (
        "🗺️ Travel:\n"
        "  !go <location>  — Begin travelling to a location\n"
        "  !where          — Check current location & ETA\n"
        "  !locations      — View all locations with danger ratings\n\n"
        "Travel time is based on map distance. You can only travel\n"
        "to connected locations (shown in !where).\n"
        "⚠️ Arriving at a dangerous location may trigger an encounter!\n"
        "🎣=can fish  🌲=can hunt  ⚠️N=danger level (0-10)"
    ),
    "points items": (
        "🎒 Items & Gathering:\n"
        "  !fish           — Fish at a water location (3 min cooldown)\n"
        "  !hunt           — Hunt at a forest location (5 min cooldown)\n"
        "  !sell <item>    — Sell items from inventory for gems\n"
        "  !sell <item> N  — Sell N of an item\n"
        "  !inventory      — View your items\n\n"
        "Fish rarity: Common → Uncommon → Rare → Epic → Legendary\n"
        "Hunt drops: Meat, pelts, claws, and more\n"
        "Higher luck = better catches. Better locations = rarer fish."
    ),
    "points gems": (
        "💎 Gems (Currency):\n"
        "  Earned by: selling fish, hunting loot, clicker income,\n"
        "             winning coin flips, beating enemies\n"
        "  Spent on:  !shop items, chests, NPC trades\n"
        "  !gems       — Check your balance\n"
        "  !give @u N  — Transfer gems to another player\n"
        "  !coin h/t N — Gamble on a coin flip\n"
        "  !chest store N — Put gems safely in a chest\n\n"
        "⚠️ Gems in your pocket can potentially be stolen by\n"
        "other players (when that system launches). Chests are safer!"
    ),
    "points chests": (
        "📦 Chests (Safe Storage):\n"
        "  Buy from !shop — tiers: 50 / 100 / 250 / 500 / 1000 gems\n"
        "  Cost is roughly 1/3 of capacity.\n"
        "  Chests cannot hold other chests.\n"
        "  Commands:\n"
        "    !chest list       — Show your chests\n"
        "    !chest store <n>  — Store n gems\n"
        "    !chest store <n> <#> — Store in chest #\n"
        "    !chest take <n>   — Withdraw gems\n"
        "    !chest take <n> <#>  — Withdraw from chest #"
    ),
    "points stats": (
        "📊 Character Stats:\n"
        "  HP    — Health. Reach 0 and you fall unconscious.\n"
        "  Mana  — Used for magic attacks and healing spells.\n"
        "  ATK   — Attack power in combat.\n"
        "  DEF   — Reduces damage received.\n"
        "  SPD   — Affects turn order and dodge chance.\n"
        "  Luck  — Improves fishing/hunting drops and rare event chances.\n"
        "  Weight— Affects how much you can carry.\n"
        "  Size  — Affects hit/dodge chance.\n\n"
        "Stats increase automatically on level up.\n"
        "Level up by earning XP from fishing, hunting, and combat."
    ),
    "points combat": (
        "⚔️ Combat (Phase 2 — Coming Soon):\n"
        "  Beginner attacks: !punch, !kick\n"
        "  With a stick:     !stab, !bonk\n"
        "  Weapons add more attacks and bonuses.\n"
        "  Items can be thrown in battle.\n"
        "  Monster encounters happen during travel and hunting."
    ),
}


def cmd_help(args):
    key = args.strip().lower() if args else ""
    if key in HELP_SECTIONS:
        return HELP_SECTIONS[key]
    # Default: list all sections
    if key:
        return (f"Unknown help section: '{key}'\n" + HELP_SECTIONS["points"])
    return HELP_SECTIONS["points"]


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

def cmd_beginpoints(group_id, user_id, name):
    existing = _get_player(group_id, user_id)
    if existing:
        return (f"👤 {existing.get('name','?')}, you're already registered!\n"
                f"Use !stats to view your character.")

    player = _new_player(name)
    _save_player(group_id, user_id, player)

    return (
        f"🌟 Welcome to the Realm of Aethermoor, {name}!\n"
        f"Your adventure begins at Hearthstone Village.\n"
        f"────────────────────\n"
        f"❤️ HP: {STARTING_HP}/{STARTING_MAX_HP}  💧 Mana: {STARTING_MANA}/{STARTING_MAX_MANA}\n"
        f"💎 Gems: 0  |  🖱️ Clicker: Active (+1 gem/30s)\n"
        f"────────────────────\n"
        f"Use !stats to see your full character sheet.\n"
        f"Use !help points start for a quick guide.\n"
        f"Use !locations to see where you can travel."
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# Called by AI-FSY.py for every game group message
# ─────────────────────────────────────────────────────────────────────────────

def handle_message(group_id, user_id, name, text):
    """
    Parse and handle a single GroupMe message for the game engine.
    Returns a response string, or None if the message is not a game command.
    """
    if not text:
        return None

    t = text.strip()
    lo = t.lower()

    # Registration
    if lo.startswith("!beginpoints"):
        return cmd_beginpoints(group_id, user_id, name)

    # Stats / profile
    if lo in ("!stats", "!profile", "!me"):
        return cmd_stats(group_id, user_id, name)

    if lo in ("!gems", "!balance", "!bal"):
        return cmd_gems(group_id, user_id, name)

    if lo in ("!inventory", "!inv", "!bag"):
        player = _get_player(group_id, user_id)
        if not player:
            return "You haven't registered yet! Use !beginpoints to start."
        return f"🎒 {player.get('name','?')}'s Inventory:\n" + _inventory_summary(player)

    # Location / travel
    if lo in ("!where", "!location", "!loc"):
        return cmd_where(group_id, user_id, name)

    if lo.startswith("!go "):
        destination = t[4:].strip()
        ok, msg = start_travel(group_id, user_id, destination)
        return msg

    if lo.startswith("!locations") or lo.startswith("!map "):
        filter_str = t.split(" ", 1)[1].strip() if " " in t else ""
        return cmd_locations(group_id, user_id, filter_str)

    if lo == "!map":
        # Send map image if uploader available, else text list
        if _upload_fn:
            img_bytes = render_map_with_players(group_id)
            if img_bytes:
                _upload_fn(group_id, img_bytes, f"🗺️ Realm of Aethermoor — {game_time_str()}")
                return None   # image sent, no text needed
        return cmd_locations(group_id, user_id)

    # Gathering
    if lo == "!fish":
        return cmd_fish(group_id, user_id, name)

    if lo == "!hunt":
        return cmd_hunt(group_id, user_id, name)

    if lo.startswith("!sell"):
        args = t[5:].strip()
        return cmd_sell(group_id, user_id, name, args)

    # Economy
    if lo.startswith("!give"):
        parts = t.split()
        if len(parts) < 3:
            return "Usage: !give @username <amount>"
        # parts: ['!give', '@username', '50']
        target = parts[1].lstrip("@")
        amount_str = parts[2] if len(parts) > 2 else "0"
        return cmd_give(group_id, user_id, name, target, amount_str)

    if lo.startswith("!coin"):
        parts = t.split()
        if len(parts) < 3:
            return "Usage: !coin <h/t> <amount>  e.g. !coin h 50"
        return cmd_coin(group_id, user_id, name, parts[1], parts[2])

    # Shop
    if lo in ("!shop", "!store"):
        return cmd_shop(group_id, user_id, name)

    if lo.startswith("!buy"):
        item_query = t[4:].strip()
        if not item_query:
            return "Usage: !buy <item name>"
        return cmd_buy(group_id, user_id, name, item_query)

    # Chests
    if lo.startswith("!chest"):
        parts  = t.split(None, 2)
        subcmd = parts[1] if len(parts) > 1 else "list"
        args   = parts[2] if len(parts) > 2 else ""
        return cmd_chest(group_id, user_id, name, subcmd, args)

    # Leaderboard
    if lo in ("!leaderboard", "!lb", "!top"):
        return cmd_leaderboard(group_id)

    # Forecast
    if lo in ("!forecast", "!weather", "!daily"):
        return cmd_forecast(group_id, user_id)

    # Help
    if lo.startswith("!help points"):
        section = t[12:].strip()  # everything after "!help points"
        if section:
            return cmd_help(f"points {section}")
        return cmd_help("points")

    return None   # not a game command


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND TRAVEL CHECKER
# Called by AI-FSY.py's background loop for each active game group
# ─────────────────────────────────────────────────────────────────────────────

def tick_group(group_id):
    """
    Process travel arrivals for a group.
    Returns list of messages to send to the group.
    """
    return check_arrivals(group_id)