"""
game_engine.py  -  Fantasy RPG Engine for AI-FSY GroupMe Bot
=============================================================
Full feature set:
  - Player registration & stats (HP, Mana, ATK, DEF, SPD, Luck, Weight, Size)
  - Gem currency + passive clicker income
  - Full inventory (weapons 10-slot, items, armour, chests)
  - Fishing (location-gated, rarity-weighted) & Hunting (danger-based)
  - Travel system (connected graph, 2x game time, arrival notifications)
  - Turn-based PvP combat (!attack, #flee, #throw, #cast)
  - PvE monster combat (encounters on travel/hunt, resolves via commands)
  - Weapon equip system (!equip, #unequip, attack types change per weapon)
  - 40+ NPCs with personalities, inventories, #talk / #trade / #buyfrom
  - 35+ enemy types spanning danger 1-10
  - Expanded item database (weapons, armour, food, potions, materials, fish)
  - Global shop (admin-editable via game_data/shop.json)
  - Chest storage system (tiered, gem-protected)
  - Natural disasters (group-wide random events)
  - Daily forecast (real-time weather affecting danger)
  - Day/night cycle (2x speed)
  - Pillow map generation with player dots
  - Mana spell system (heal, restore, fireball, zap, shield)
  - Full help system (!help points <section>)
"""

import os, json, math, random, time, threading, io, traceback
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
GAME_TIME_MULTIPLIER  = 2
CLICKER_INTERVAL      = 30
CLICKER_GEMS_PER_TICK = 1
STARTING_GEMS         = 0
STARTING_HP           = 50
STARTING_MAX_HP       = 50
STARTING_MANA         = 30
STARTING_MAX_MANA     = 30
STARTING_ATK          = 5
STARTING_DEF          = 3
STARTING_SPD          = 5
STARTING_LUCK         = 5
STARTING_WEIGHT       = 10
STARTING_SIZE         = 5
XP_PER_LEVEL_BASE     = 100
WEAPON_SLOTS          = 10
LEADERBOARD_SIZE      = 10
MAP_WIDTH             = 2400
MAP_HEIGHT            = 1600
MAP_FILENAME          = "world_map.png"
FISH_COOLDOWN         = 180
HUNT_COOLDOWN         = 300
COIN_COOLDOWN         = 60
DISASTER_CHANCE       = 0.004

# ─────────────────────────────────────────────────────────────────────────────
# MODULE STATE
# ─────────────────────────────────────────────────────────────────────────────
_script_dir    = None
_send_fn       = None
_upload_fn     = None
_data_dir      = None
_game_lock     = threading.Lock()
_active_combats = {}

# ─────────────────────────────────────────────────────────────────────────────
# INIT
# ─────────────────────────────────────────────────────────────────────────────
def init(script_dir, send_fn, upload_fn=None):
    global _script_dir, _send_fn, _upload_fn, _data_dir
    _script_dir = script_dir
    _send_fn    = send_fn
    _upload_fn  = upload_fn
    _data_dir   = os.path.join(script_dir, "game_data")
    os.makedirs(_data_dir, exist_ok=True)
    _ensure_default_data()
    _ensure_map()
    threading.Thread(target=_clicker_loop_fn, daemon=True).start()
    print("[game_engine] Initialised.")

def _ensure_default_data():
    for fname, default in [
        ("players.json", {}),
        ("shop.json",    _default_shop()),
        ("world.json",   _default_world()),
        ("items.json",   _default_items()),
        ("enemies.json", _default_enemies()),
        ("npcs.json",    _default_npcs()),
    ]:
        path = os.path.join(_data_dir, fname)
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
def _pk(group_id, user_id):
    return f"{group_id}:{user_id}"

def _load_players():
    return _read_json(os.path.join(_data_dir, "players.json")) or {}

def _save_players(p):
    _write_json(os.path.join(_data_dir, "players.json"), p)

def _get_player(group_id, user_id):
    return _load_players().get(_pk(group_id, user_id))

def _save_player(group_id, user_id, record):
    with _game_lock:
        players = _load_players()
        players[_pk(group_id, user_id)] = record
        _save_players(players)

def _new_player(name):
    return {
        "name": name, "registered": time.time(),
        "gems": STARTING_GEMS, "level": 1, "xp": 0,
        "hp": STARTING_HP, "max_hp": STARTING_MAX_HP,
        "mana": STARTING_MANA, "max_mana": STARTING_MAX_MANA,
        "atk": STARTING_ATK, "def": STARTING_DEF, "spd": STARTING_SPD,
        "luck": STARTING_LUCK, "weight": STARTING_WEIGHT, "size": STARTING_SIZE,
        "has_clicker": True,
        "location": "Hearthstone Village", "travelling_to": None, "travel_arrive": None,
        "weapons": [], "equipped_weapon": None,
        "items": [], "chests": [], "armour": {},
        "cd_fish": 0, "cd_hunt": 0, "cd_coin": 0,
        "in_combat": False, "combat_key": None,
        "story": [],
    }

# ─────────────────────────────────────────────────────────────────────────────
# CLICKER
# ─────────────────────────────────────────────────────────────────────────────
def _clicker_loop_fn():
    while True:
        time.sleep(CLICKER_INTERVAL)
        try:
            with _game_lock:
                players = _load_players()
                for p in players.values():
                    if p.get("has_clicker"):
                        p["gems"] = p.get("gems", 0) + CLICKER_GEMS_PER_TICK
                _save_players(players)
        except Exception as e:
            print(f"[game_engine][clicker] {e}")

# ─────────────────────────────────────────────────────────────────────────────
# GAME TIME
# ─────────────────────────────────────────────────────────────────────────────
def game_time_now():
    return datetime.fromtimestamp(time.time() * GAME_TIME_MULTIPLIER, tz=timezone.utc)

def game_time_str():
    gt = game_time_now()
    h  = gt.hour
    period = "Dawn" if 5<=h<8 else "Day" if 8<=h<17 else "Dusk" if 17<=h<20 else "Night"
    return f"{gt.strftime('%A, %d %b - %H:%M')} ({period})"

def is_game_night():
    h = game_time_now().hour
    return h < 6 or h >= 20

# ─────────────────────────────────────────────────────────────────────────────
# XP & LEVELLING
# ─────────────────────────────────────────────────────────────────────────────
def _xp_for_level(level):
    return int(XP_PER_LEVEL_BASE * (level ** 1.5))

def _give_xp(player, amount):
    player["xp"] = player.get("xp", 0) + amount
    levelled = False
    while player["xp"] >= _xp_for_level(player.get("level", 1)):
        player["xp"]      -= _xp_for_level(player["level"])
        player["level"]    = player.get("level", 1) + 1
        player["max_hp"]   = player.get("max_hp", STARTING_MAX_HP) + 5
        player["hp"]       = player["max_hp"]
        player["max_mana"] = player.get("max_mana", STARTING_MAX_MANA) + 3
        player["mana"]     = player["max_mana"]
        player["atk"]      = player.get("atk", STARTING_ATK) + 2
        player["def"]      = player.get("def", STARTING_DEF) + 1
        player["spd"]      = player.get("spd", STARTING_SPD) + 1
        player["luck"]     = player.get("luck", STARTING_LUCK) + 1
        levelled = True
    return player, levelled

# ─────────────────────────────────────────────────────────────────────────────
# ITEM DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def _default_items():
    return {
        "fish": [
            {"id":"fish_minnow",    "name":"Minnow",           "rarity":"common",    "sell_value":2,   "locations":["any_water"]},
            {"id":"fish_perch",     "name":"River Perch",      "rarity":"common",    "sell_value":5,   "locations":["any_water"]},
            {"id":"fish_trout",     "name":"Silver Trout",     "rarity":"uncommon",  "sell_value":12,  "locations":["Crestlake","River Crest Ford","Millford","Mirestone Oasis"]},
            {"id":"fish_catfish",   "name":"Mudcatfish",       "rarity":"common",    "sell_value":7,   "locations":["Ashfen Marsh","Mirepool Lake","Bogmire"]},
            {"id":"fish_eel",       "name":"Dark Eel",         "rarity":"uncommon",  "sell_value":18,  "locations":["Mirepool Lake","Ashfen Marsh"]},
            {"id":"fish_bass",      "name":"Saltwater Bass",   "rarity":"uncommon",  "sell_value":15,  "locations":["Saltmere Coast","Saltmere Port","Crystal River Delta"]},
            {"id":"fish_crab",      "name":"Blue Crab",        "rarity":"common",    "sell_value":8,   "locations":["Saltmere Coast","Saltmere Port","Shipwreck Cove"]},
            {"id":"fish_shark",     "name":"Reef Shark",       "rarity":"rare",      "sell_value":45,  "locations":["Sunken Reef","Shipwreck Cove"]},
            {"id":"fish_icefish",   "name":"Glacier Fish",     "rarity":"rare",      "sell_value":40,  "locations":["Glacier Pass","Ice Cavern","Frostveil Settlement"]},
            {"id":"fish_pike",      "name":"Frost Pike",       "rarity":"uncommon",  "sell_value":22,  "locations":["Frostveil Settlement","Tundra Flats"]},
            {"id":"fish_deepfish",  "name":"Abyssal Angler",   "rarity":"epic",      "sell_value":120, "locations":["Permafrost Depths","Sunken Reef"]},
            {"id":"fish_golden",    "name":"Golden Carp",      "rarity":"legendary", "sell_value":500, "locations":["Mirestone Oasis","Crestlake"]},
            {"id":"fish_specter",   "name":"Specter Fish",     "rarity":"epic",      "sell_value":90,  "locations":["Mirepool Lake","Cliffside Watch"]},
            {"id":"fish_jungle",    "name":"Jungle Tetra",     "rarity":"uncommon",  "sell_value":20,  "locations":["Verdant Wilds"]},
        ],
        "hunt_drops": [
            {"id":"meat_rabbit",   "name":"Rabbit Meat",  "rarity":"common",   "sell_value":8,  "edible":True, "hp_restore":8},
            {"id":"meat_deer",     "name":"Venison",      "rarity":"uncommon", "sell_value":20, "edible":True, "hp_restore":15},
            {"id":"meat_boar",     "name":"Boar Meat",    "rarity":"uncommon", "sell_value":18, "edible":True, "hp_restore":12},
            {"id":"pelt_rabbit",   "name":"Rabbit Pelt",  "rarity":"common",   "sell_value":6},
            {"id":"pelt_wolf",     "name":"Wolf Pelt",    "rarity":"uncommon", "sell_value":25},
            {"id":"pelt_bear",     "name":"Bear Hide",    "rarity":"rare",     "sell_value":70},
            {"id":"fang_wolf",     "name":"Wolf Fang",    "rarity":"uncommon", "sell_value":15},
            {"id":"claw_bear",     "name":"Bear Claw",    "rarity":"rare",     "sell_value":50},
            {"id":"scale_drake",   "name":"Drake Scale",  "rarity":"epic",     "sell_value":200},
            {"id":"fang_wyvern",   "name":"Wyvern Fang",  "rarity":"epic",     "sell_value":280},
            {"id":"tusk_boar",     "name":"Boar Tusk",    "rarity":"uncommon", "sell_value":22},
            {"id":"feather_hawk",  "name":"Hawk Feather", "rarity":"uncommon", "sell_value":18},
            {"id":"eye_troll",     "name":"Troll Eye",    "rarity":"rare",     "sell_value":60},
        ],
        "food": [
            {"id":"food_apple",       "name":"Apple",          "sell_value":1,  "hp_restore":5,  "throwable":True,  "break_on_throw":True},
            {"id":"food_bread",       "name":"Loaf of Bread",  "sell_value":3,  "hp_restore":10, "throwable":True,  "break_on_throw":True},
            {"id":"food_stew",        "name":"Hearty Stew",    "sell_value":10, "hp_restore":25, "throwable":True,  "break_on_throw":True},
            {"id":"food_elixir",      "name":"Healing Elixir", "sell_value":50, "hp_restore":50, "throwable":False, "break_on_throw":False},
            {"id":"food_berry",       "name":"Wild Berry",     "sell_value":2,  "hp_restore":4,  "throwable":True,  "break_on_throw":True},
            {"id":"food_jerky",       "name":"Dried Jerky",    "sell_value":8,  "hp_restore":15, "throwable":True,  "break_on_throw":True},
            {"id":"food_mushroom",    "name":"Forest Mushroom","sell_value":4,  "hp_restore":8,  "throwable":True,  "break_on_throw":True},
            {"id":"food_potion_mana", "name":"Mana Potion",    "sell_value":60, "mana_restore":25,"throwable":False,"break_on_throw":False},
            {"id":"food_potion_spd",  "name":"Speed Potion",   "sell_value":40, "spd_bonus":3,   "throwable":False, "break_on_throw":False},
        ],
        "weapons": [
            {"id":"wpn_stick",      "name":"Stick",         "atk_bonus":2,  "attacks":["stab","bonk"],            "stackable":True,  "max_stack":5},
            {"id":"wpn_club",       "name":"Club",          "atk_bonus":5,  "attacks":["bonk","smash"],           "stackable":False},
            {"id":"wpn_dagger",     "name":"Dagger",        "atk_bonus":6,  "attacks":["stab","slash"],           "stackable":True,  "max_stack":2},
            {"id":"wpn_sword",      "name":"Iron Sword",    "atk_bonus":12, "attacks":["slash","thrust"],         "stackable":False},
            {"id":"wpn_steel_sword","name":"Steel Sword",   "atk_bonus":18, "attacks":["slash","thrust","guard"], "stackable":False},
            {"id":"wpn_greatsword", "name":"Greatsword",    "atk_bonus":22, "attacks":["cleave","bash"],          "stackable":False},
            {"id":"wpn_bow",        "name":"Short Bow",     "atk_bonus":10, "attacks":["shoot"],                  "stackable":False},
            {"id":"wpn_longbow",    "name":"Longbow",       "atk_bonus":16, "attacks":["shoot","rapid"],          "stackable":False},
            {"id":"wpn_staff",      "name":"Mage Staff",    "atk_bonus":8,  "attacks":["cast","bonk"],            "stackable":False, "mana_weapon":True},
            {"id":"wpn_axe",        "name":"Hand Axe",      "atk_bonus":14, "attacks":["chop","throw"],           "stackable":True,  "max_stack":3},
            {"id":"wpn_battleaxe",  "name":"Battleaxe",     "atk_bonus":20, "attacks":["cleave","chop","bash"],   "stackable":False},
            {"id":"wpn_spear",      "name":"Spear",         "atk_bonus":16, "attacks":["stab","throw"],           "stackable":False},
            {"id":"wpn_halberd",    "name":"Halberd",       "atk_bonus":24, "attacks":["cleave","stab","bash"],   "stackable":False},
            {"id":"wpn_wand",       "name":"Wand",          "atk_bonus":6,  "attacks":["cast","flick"],           "stackable":True,  "max_stack":2, "mana_weapon":True},
            {"id":"wpn_scythe",     "name":"Scythe",        "atk_bonus":19, "attacks":["slash","reap"],           "stackable":False},
            {"id":"wpn_hammer",     "name":"War Hammer",    "atk_bonus":21, "attacks":["smash","bash"],           "stackable":False},
            {"id":"wpn_knife",      "name":"Hunting Knife", "atk_bonus":5,  "attacks":["stab","slash"],           "stackable":True,  "max_stack":3},
            {"id":"wpn_crossbow",   "name":"Crossbow",      "atk_bonus":14, "attacks":["shoot","pierce"],         "stackable":False},
            {"id":"wpn_trident",    "name":"Trident",       "atk_bonus":17, "attacks":["stab","throw"],           "stackable":False},
        ],
        "armour": [
            {"id":"arm_leather",   "name":"Leather Armour", "def_bonus":3,  "sell_value":40},
            {"id":"arm_chainmail", "name":"Chainmail",      "def_bonus":7,  "sell_value":120},
            {"id":"arm_plate",     "name":"Plate Armour",   "def_bonus":14, "sell_value":300},
            {"id":"arm_robe",      "name":"Mage Robe",      "def_bonus":2,  "mana_bonus":15, "sell_value":80},
            {"id":"arm_fur",       "name":"Fur Cloak",      "def_bonus":4,  "sell_value":60},
            {"id":"arm_hood",      "name":"Ranger Hood",    "def_bonus":3,  "luck_bonus":2,  "sell_value":70},
            {"id":"arm_shield",    "name":"Iron Shield",    "def_bonus":5,  "sell_value":90, "slot":"offhand"},
        ],
        "materials": [
            {"id":"mat_stone",       "name":"Stone",          "sell_value":1,  "throwable":True,  "break_on_throw":False},
            {"id":"mat_iron_ore",    "name":"Iron Ore",       "sell_value":8,  "throwable":True,  "break_on_throw":False},
            {"id":"mat_wood",        "name":"Lumber",         "sell_value":4,  "throwable":True,  "break_on_throw":False},
            {"id":"mat_gem_shard",   "name":"Gem Shard",      "sell_value":30, "throwable":True,  "break_on_throw":False},
            {"id":"mat_bone",        "name":"Bone",           "sell_value":5,  "throwable":True,  "break_on_throw":False},
            {"id":"mat_coal",        "name":"Coal",           "sell_value":3,  "throwable":True,  "break_on_throw":False},
            {"id":"mat_silk",        "name":"Silk Thread",    "sell_value":15, "throwable":False, "break_on_throw":False},
            {"id":"mat_sand_glass",  "name":"Desert Glass",   "sell_value":20, "throwable":True,  "break_on_throw":True},
            {"id":"mat_ice_shard",   "name":"Ice Shard",      "sell_value":12, "throwable":True,  "break_on_throw":True},
            {"id":"mat_ancient_rune","name":"Ancient Rune",   "sell_value":80, "throwable":False, "break_on_throw":False},
            {"id":"mat_venom_gland", "name":"Venom Gland",    "sell_value":35, "throwable":False, "break_on_throw":False},
        ],
        "special": [
            {"id":"spc_map_fragment","name":"Map Fragment",   "sell_value":25},
            {"id":"spc_key_iron",    "name":"Iron Key",       "sell_value":50},
            {"id":"spc_crown_shard", "name":"Crown Shard",    "sell_value":150},
            {"id":"spc_lich_phylac", "name":"Lich Phylactery","sell_value":500},
        ],
    }

def load_items():
    return _read_json(os.path.join(_data_dir, "items.json")) or _default_items()

def _find_item_def(item_id):
    if not item_id:
        return None
    db = load_items()
    for cat in db.values():
        if isinstance(cat, list):
            for it in cat:
                if it.get("id") == item_id:
                    return it
    return None

# ─────────────────────────────────────────────────────────────────────────────
# ENEMY DATABASE  (35 enemies)
# ─────────────────────────────────────────────────────────────────────────────
def _default_enemies():
    return {"enemies": [
        {"id":"rat",           "name":"Giant Rat",         "danger_min":1, "danger_max":2,  "hp":12,  "atk":3,  "def":0,  "xp":8,   "gem_min":0,  "gem_max":3,   "drops":["pelt_rabbit","meat_rabbit"]},
        {"id":"goblin",        "name":"Goblin",            "danger_min":1, "danger_max":3,  "hp":20,  "atk":4,  "def":1,  "xp":15,  "gem_min":1,  "gem_max":5,   "drops":["mat_stone","wpn_stick"]},
        {"id":"slime",         "name":"Mud Slime",         "danger_min":1, "danger_max":2,  "hp":18,  "atk":3,  "def":2,  "xp":10,  "gem_min":0,  "gem_max":4,   "drops":["mat_stone"]},
        {"id":"crow",          "name":"Giant Crow",        "danger_min":1, "danger_max":3,  "hp":15,  "atk":5,  "def":1,  "xp":12,  "gem_min":0,  "gem_max":3,   "drops":["feather_hawk"]},
        {"id":"wolf",          "name":"Grey Wolf",         "danger_min":2, "danger_max":4,  "hp":35,  "atk":8,  "def":2,  "xp":30,  "gem_min":2,  "gem_max":8,   "drops":["pelt_wolf","fang_wolf","meat_rabbit"]},
        {"id":"boar",          "name":"Wild Boar",         "danger_min":2, "danger_max":4,  "hp":40,  "atk":9,  "def":3,  "xp":28,  "gem_min":1,  "gem_max":6,   "drops":["meat_boar","tusk_boar"]},
        {"id":"bandit",        "name":"Bandit",            "danger_min":2, "danger_max":5,  "hp":40,  "atk":10, "def":3,  "xp":35,  "gem_min":5,  "gem_max":20,  "drops":["wpn_dagger","mat_iron_ore"]},
        {"id":"bandit_chief",  "name":"Bandit Chief",      "danger_min":3, "danger_max":5,  "hp":65,  "atk":14, "def":5,  "xp":55,  "gem_min":10, "gem_max":30,  "drops":["wpn_sword","mat_iron_ore"]},
        {"id":"imp",           "name":"Fire Imp",          "danger_min":3, "danger_max":5,  "hp":30,  "atk":11, "def":2,  "xp":40,  "gem_min":3,  "gem_max":15,  "drops":["mat_coal","mat_gem_shard"]},
        {"id":"swamp_leech",   "name":"Swamp Leech",       "danger_min":3, "danger_max":5,  "hp":28,  "atk":8,  "def":1,  "xp":25,  "gem_min":2,  "gem_max":8,   "drops":["mat_venom_gland"]},
        {"id":"goblin_shaman", "name":"Goblin Shaman",     "danger_min":3, "danger_max":5,  "hp":35,  "atk":12, "def":2,  "xp":45,  "gem_min":5,  "gem_max":18,  "drops":["wpn_wand","mat_bone"]},
        {"id":"swamp_beast",   "name":"Swamp Beast",       "danger_min":4, "danger_max":6,  "hp":60,  "atk":12, "def":5,  "xp":55,  "gem_min":8,  "gem_max":25,  "drops":["pelt_wolf","mat_bone"]},
        {"id":"troll",         "name":"Mountain Troll",    "danger_min":4, "danger_max":6,  "hp":80,  "atk":15, "def":8,  "xp":70,  "gem_min":10, "gem_max":35,  "drops":["mat_bone","mat_stone","pelt_bear","eye_troll"]},
        {"id":"harpy",         "name":"Harpy",             "danger_min":4, "danger_max":6,  "hp":45,  "atk":13, "def":4,  "xp":50,  "gem_min":6,  "gem_max":22,  "drops":["feather_hawk","mat_bone"]},
        {"id":"cave_spider",   "name":"Cave Spider",       "danger_min":4, "danger_max":6,  "hp":38,  "atk":11, "def":3,  "xp":42,  "gem_min":4,  "gem_max":16,  "drops":["mat_venom_gland","mat_silk"]},
        {"id":"skeleton",      "name":"Risen Skeleton",    "danger_min":5, "danger_max":8,  "hp":50,  "atk":11, "def":4,  "xp":50,  "gem_min":5,  "gem_max":15,  "drops":["mat_bone","wpn_sword"]},
        {"id":"skel_archer",   "name":"Skeleton Archer",   "danger_min":5, "danger_max":7,  "hp":40,  "atk":13, "def":3,  "xp":48,  "gem_min":5,  "gem_max":18,  "drops":["mat_bone","wpn_bow"]},
        {"id":"necromancer",   "name":"Necromancer",       "danger_min":6, "danger_max":8,  "hp":65,  "atk":18, "def":5,  "xp":90,  "gem_min":15, "gem_max":45,  "drops":["wpn_staff","mat_ancient_rune","mat_bone"]},
        {"id":"werewolf",      "name":"Werewolf",          "danger_min":5, "danger_max":7,  "hp":90,  "atk":20, "def":7,  "xp":85,  "gem_min":12, "gem_max":40,  "drops":["pelt_wolf","claw_bear"]},
        {"id":"sand_wraith",   "name":"Sand Wraith",       "danger_min":6, "danger_max":9,  "hp":70,  "atk":20, "def":6,  "xp":90,  "gem_min":20, "gem_max":60,  "drops":["mat_gem_shard","mat_sand_glass"]},
        {"id":"golem_stone",   "name":"Stone Golem",       "danger_min":5, "danger_max":7,  "hp":100, "atk":14, "def":14, "xp":80,  "gem_min":10, "gem_max":30,  "drops":["mat_stone","mat_iron_ore"]},
        {"id":"sea_serpent",   "name":"Sea Serpent",       "danger_min":7, "danger_max":10, "hp":150, "atk":30, "def":12, "xp":200, "gem_min":40, "gem_max":120, "drops":["scale_drake","fish_shark"]},
        {"id":"ice_beast",     "name":"Ice Wurm",          "danger_min":6, "danger_max":8,  "hp":90,  "atk":18, "def":10, "xp":100, "gem_min":15, "gem_max":50,  "drops":["fish_icefish","mat_ice_shard"]},
        {"id":"frost_giant",   "name":"Frost Giant",       "danger_min":7, "danger_max":9,  "hp":130, "atk":25, "def":12, "xp":160, "gem_min":25, "gem_max":75,  "drops":["mat_ice_shard","pelt_bear"]},
        {"id":"fire_drake",    "name":"Fire Drake",        "danger_min":7, "danger_max":9,  "hp":120, "atk":25, "def":14, "xp":150, "gem_min":25, "gem_max":80,  "drops":["scale_drake","mat_gem_shard"]},
        {"id":"wyvern",        "name":"Wyvern",            "danger_min":8, "danger_max":10, "hp":180, "atk":35, "def":18, "xp":250, "gem_min":50, "gem_max":150, "drops":["fang_wyvern","scale_drake"]},
        {"id":"lich_minion",   "name":"Lich Minion",       "danger_min":8, "danger_max":10, "hp":85,  "atk":22, "def":8,  "xp":120, "gem_min":20, "gem_max":60,  "drops":["mat_bone","mat_ancient_rune"]},
        {"id":"demon_knight",  "name":"Demon Knight",      "danger_min":8, "danger_max":10, "hp":160, "atk":32, "def":16, "xp":220, "gem_min":45, "gem_max":130, "drops":["wpn_battleaxe","mat_gem_shard"]},
        {"id":"elder_dragon",  "name":"Elder Dragon",      "danger_min":9, "danger_max":10, "hp":250, "atk":40, "def":20, "xp":350, "gem_min":70, "gem_max":200, "drops":["scale_drake","fang_wyvern","mat_ancient_rune"]},
        {"id":"ancient_lich",  "name":"Ancient Lich",      "danger_min":9, "danger_max":10, "hp":300, "atk":45, "def":22, "xp":450, "gem_min":100,"gem_max":300, "drops":["wpn_staff","mat_gem_shard","spc_lich_phylac"]},
        {"id":"abyss_horror",  "name":"Abyss Horror",      "danger_min":10,"danger_max":10, "hp":400, "atk":55, "def":25, "xp":600, "gem_min":150,"gem_max":500, "drops":["mat_ancient_rune","spc_crown_shard"]},
        {"id":"pirate",        "name":"Pirate",            "danger_min":5, "danger_max":7,  "hp":55,  "atk":15, "def":6,  "xp":60,  "gem_min":10, "gem_max":40,  "drops":["wpn_crossbow","mat_iron_ore"]},
        {"id":"mummy",         "name":"Desert Mummy",      "danger_min":6, "danger_max":8,  "hp":80,  "atk":16, "def":9,  "xp":85,  "gem_min":15, "gem_max":50,  "drops":["mat_ancient_rune","mat_sand_glass"]},
        {"id":"magma_elem",    "name":"Magma Elemental",   "danger_min":8, "danger_max":10, "hp":140, "atk":30, "def":15, "xp":200, "gem_min":40, "gem_max":110, "drops":["mat_gem_shard","mat_coal"]},
    ]}

def load_enemies():
    return _read_json(os.path.join(_data_dir, "enemies.json")) or _default_enemies()

def get_enemies_for_danger(danger_level):
    return [e for e in load_enemies()["enemies"]
            if e["danger_min"] <= danger_level <= e["danger_max"]]

# ─────────────────────────────────────────────────────────────────────────────
# NPC DATABASE  (40 NPCs)
# ─────────────────────────────────────────────────────────────────────────────
def _default_npcs():
    return {"npcs": [
        # HEARTLAND
        {"id":"elder_mira",    "name":"Elder Mira",         "location":"Hearthstone Village",
         "personality":"A wise and kind village elder in her 70s. Speaks in gentle riddles. She has watched adventurers pass through for fifty years and has seen them all fail in the same ways. She gives newcomers advice freely but will only trade rare knowledge for rare items.",
         "known_about_players":["location","level","name"],"can_trade":False,"inventory":[]},
        {"id":"lena_inn",      "name":"Lena the Innkeeper", "location":"Hearthstone Village",
         "personality":"A round-cheeked no-nonsense woman who runs the village inn. She has heard every tall tale twice and believes none of them. Sells food and gossip in equal measure. Secretly has a soft spot for underdogs.",
         "known_about_players":["name","hp"],"can_trade":True,
         "inventory":[{"item_id":"food_bread","qty":20,"price":4},{"item_id":"food_stew","qty":10,"price":12},{"item_id":"food_apple","qty":30,"price":2}]},
        {"id":"aldric_farm",   "name":"Aldric the Farmer",  "location":"Hearthstone Village",
         "personality":"A sunburnt farmer with enormous hands and a slow smile. Suspicious of outsiders but warm once you earn his trust. Knows which berries are poisonous and which are edible.",
         "known_about_players":["name"],"can_trade":True,
         "inventory":[{"item_id":"food_berry","qty":15,"price":2},{"item_id":"food_mushroom","qty":8,"price":4}]},
        {"id":"pip_boy",       "name":"Pip",                "location":"Hearthstone Village",
         "personality":"A 12-year-old boy absolutely convinced he is the greatest adventurer alive despite never having left the village. Enthusiastic and slightly annoying but knows every shortcut in the Heartland.",
         "known_about_players":["name"],"can_trade":False,"inventory":[]},
        {"id":"bram_merch",    "name":"Bram the Merchant",  "location":"Millford",
         "personality":"A jovial rotund merchant always trying to sell something. Very friendly but will overcharge if he thinks you do not know better. Loves gossip. Has visited every city at least once.",
         "known_about_players":["name","gems"],"can_trade":True,
         "inventory":[{"item_id":"food_bread","qty":10,"price":5},{"item_id":"food_apple","qty":20,"price":2},{"item_id":"wpn_stick","qty":5,"price":4},{"item_id":"mat_stone","qty":30,"price":1}]},
        {"id":"miller_gus",    "name":"Gus the Miller",     "location":"Millford",
         "personality":"A flour-dusted old man who runs the mill. Hard of hearing but sharp of mind. Pays fair prices for materials. Grumbles constantly but goes out of his way to help anyone in genuine trouble.",
         "known_about_players":["name"],"can_trade":True,
         "inventory":[{"item_id":"food_bread","qty":25,"price":3},{"item_id":"mat_wood","qty":10,"price":3}]},
        # IRON NORTH
        {"id":"capt_harrow",   "name":"Captain Harrow",     "location":"Irongate City",
         "personality":"A gruff scarred military captain who distrusts strangers. Speaks bluntly. Respects strength and dislikes cowardice. Fought in three wars and lost two fingers in the last one. Offers contracts to high-level players.",
         "known_about_players":["name","level","atk"],"can_trade":False,"inventory":[]},
        {"id":"forge_dorn",    "name":"Forge Master Dorn",  "location":"Irongate City",
         "personality":"A massive dwarf-like man of few words who forges weapons. Very proud of his craft. Will repair weapons for gems and craft custom items for rare materials. Dismisses anything not iron or steel as a toy.",
         "known_about_players":["name","weapons"],"can_trade":True,
         "inventory":[{"item_id":"wpn_sword","qty":3,"price":90},{"item_id":"wpn_axe","qty":4,"price":70},{"item_id":"wpn_dagger","qty":6,"price":40},{"item_id":"arm_chainmail","qty":2,"price":130}]},
        {"id":"lyra_scribe",   "name":"Lyra the Scribe",    "location":"Irongate City",
         "personality":"An ink-stained young woman who records the city history and all traveller reports. Knows more about every region than almost anyone. Shares information if you share information. Writing a book and always wants stories.",
         "known_about_players":["name","location","level","story"],"can_trade":False,"inventory":[]},
        {"id":"sgt_brand",     "name":"Sergeant Brand",     "location":"Irongate City",
         "personality":"A stocky guardswoman with a permanent frown and a reputation for fairness. Enforces city rules strictly but will look the other way for someone who helped the city. Knows which merchants are dodgy.",
         "known_about_players":["name","level"],"can_trade":False,"inventory":[]},
        {"id":"finn_peddler",  "name":"Finn the Peddler",   "location":"Forge Road",
         "personality":"A wiry young man carrying an impossibly large pack. Sells a little of everything at slightly above market price. Extremely chatty and knows rumours from every town. Claims to have survived a Wyvern attack.",
         "known_about_players":["name","gems"],"can_trade":True,
         "inventory":[{"item_id":"food_jerky","qty":8,"price":10},{"item_id":"mat_iron_ore","qty":5,"price":9},{"item_id":"wpn_knife","qty":3,"price":12}]},
        {"id":"hermit_ossian", "name":"Ossian the Hermit",  "location":"Stoneback Ridge",
         "personality":"A lean weatherbeaten old man who has lived on the ridge for thirty years. Speaks very little but everything he says is worth hearing. Knows the mountains like his own hands.",
         "known_about_players":["name","level"],"can_trade":False,"inventory":[]},
        {"id":"scout_kira",    "name":"Kira the Scout",     "location":"Glacier Pass",
         "personality":"A young woman in white furs who scouts the mountain passes. Brisk and professional, treats every conversation like a mission briefing. Knows where the safe camps are and where the ice breaks.",
         "known_about_players":["name","location"],"can_trade":True,
         "inventory":[{"item_id":"food_jerky","qty":6,"price":10},{"item_id":"mat_ice_shard","qty":4,"price":13}]},
        # ASHFEN
        {"id":"witch_sylva",   "name":"Sylva the Bog Witch","location":"Bogmire",
         "personality":"An eccentric swamp witch who cackles at odd moments. Trades unusual items for unusual ingredients. Knows about curses poisons and healing. Not evil, just deeply strange. Claims to be at least two hundred years old.",
         "known_about_players":["name","hp","mana"],"can_trade":True,
         "inventory":[{"item_id":"food_elixir","qty":3,"price":60},{"item_id":"mat_bone","qty":15,"price":3},{"item_id":"food_potion_mana","qty":2,"price":65},{"item_id":"mat_venom_gland","qty":4,"price":38}]},
        {"id":"ferrin_trap",   "name":"Ferrin the Trapper", "location":"Bogmire",
         "personality":"A quiet man with muddy boots and sharp eyes. Sets traps throughout the marsh and knows every path. Does not ask questions. Trades pelts and gives route information for the right price.",
         "known_about_players":["name","items"],"can_trade":True,
         "inventory":[{"item_id":"pelt_wolf","qty":2,"price":27},{"item_id":"pelt_rabbit","qty":5,"price":7},{"item_id":"food_jerky","qty":4,"price":9}]},
        {"id":"widow_orellia", "name":"Orellia",            "location":"Ashfen Marsh",
         "personality":"A grieving widow who lives alone at the marsh's edge. Her husband was taken by something in the water years ago. Kind but carries deep sadness. Will warn players about specific dangers.",
         "known_about_players":["name"],"can_trade":False,"inventory":[]},
        # THORNWOOD
        {"id":"ranger_tomas",  "name":"Ranger Tomas",       "location":"Thornwood Village",
         "personality":"A veteran ranger with grey-streaked hair who patrols the Thornwood border. Calm, reliable, precise. Has mapped every trail in the forest. Dislikes people who over-hunt.",
         "known_about_players":["name","level","items"],"can_trade":True,
         "inventory":[{"item_id":"wpn_bow","qty":2,"price":65},{"item_id":"wpn_knife","qty":3,"price":11},{"item_id":"food_stew","qty":5,"price":12}]},
        {"id":"druid_fenwick", "name":"Druid Fenwick",      "location":"Thornwood Village",
         "personality":"An old druid who speaks to trees and expects them to speak back. Guardian of the Deepwood and deeply suspicious of anyone heading there. Offers blessings and herbal remedies only to those who show respect for nature.",
         "known_about_players":["name","location"],"can_trade":True,
         "inventory":[{"item_id":"food_mushroom","qty":10,"price":5},{"item_id":"food_berry","qty":12,"price":3},{"item_id":"food_elixir","qty":1,"price":55}]},
        {"id":"hob_wood",      "name":"Hob the Woodcutter", "location":"Thornwood Path",
         "personality":"A cheerful giant who spends his days chopping trees and singing badly. Utterly fearless about the forest but jumps at spiders. Sells wood and can direct travellers to Thornwood Village.",
         "known_about_players":["name"],"can_trade":True,
         "inventory":[{"item_id":"mat_wood","qty":20,"price":3},{"item_id":"wpn_axe","qty":1,"price":60}]},
        {"id":"prof_aldgate",  "name":"Professor Aldgate",  "location":"Deepwood Heart",
         "personality":"A panicked scholar who got extremely lost looking for a rare plant. He has been in the Deepwood for three days and is absolutely terrified. Knows a lot about botany and ancient ruins but nothing about surviving.",
         "known_about_players":["name","level"],"can_trade":True,
         "inventory":[{"item_id":"spc_map_fragment","qty":1,"price":0},{"item_id":"mat_ancient_rune","qty":1,"price":85}]},
        # SALTMERE
        {"id":"marina_capt",   "name":"Marina the Captain", "location":"Saltmere Port",
         "personality":"A cheerful weathered sea captain who buys rare fish for top gem. Loves seafaring tales. Has sailed to every coastal location on the map and a few that are not.",
         "known_about_players":["name","items"],"can_trade":True,
         "inventory":[{"item_id":"food_bread","qty":10,"price":4},{"item_id":"wpn_spear","qty":2,"price":80},{"item_id":"wpn_trident","qty":1,"price":120}]},
        {"id":"harbormaster",  "name":"Harbormaster Ros",   "location":"Saltmere Port",
         "personality":"A brisk official woman who manages the port. Knows every ship that comes and goes and taxes all of them. Not corrupt but not friendly either. Has information about pirate activity near Shipwreck Cove.",
         "known_about_players":["name","gems"],"can_trade":False,"inventory":[]},
        {"id":"greycoat",      "name":"Greycoat",           "location":"Shipwreck Cove",
         "personality":"A retired pirate who is absolutely not a pirate anymore and will be very offended if you imply otherwise. Runs a quote unquote salvage business. Sells unusual items and knows where the wrecks are.",
         "known_about_players":["name","gems","level"],"can_trade":True,
         "inventory":[{"item_id":"wpn_crossbow","qty":1,"price":80},{"item_id":"mat_iron_ore","qty":8,"price":7},{"item_id":"spc_map_fragment","qty":2,"price":30},{"item_id":"food_jerky","qty":6,"price":8}]},
        {"id":"old_teller",    "name":"Old Teller",         "location":"Saltmere Coast",
         "personality":"An ancient fisherman who sits on the cliffs watching the tide. Claims he can predict the weather three days ahead by smell alone. He is mostly right. Gives fishing advice and warns about sea serpents.",
         "known_about_players":["name"],"can_trade":False,"inventory":[]},
        # RIDGEBACK
        {"id":"warden_aldis",  "name":"Warden Aldis",       "location":"Ridgeback Keep",
         "personality":"A tall serious man who has held the keep for twenty years. Respects competence above all. Will give high-level players access to the keep armory. Disapproves of recklessness but will not stop anyone.",
         "known_about_players":["name","level","atk","def"],"can_trade":True,
         "inventory":[{"item_id":"arm_plate","qty":1,"price":320},{"item_id":"arm_chainmail","qty":2,"price":125},{"item_id":"wpn_steel_sword","qty":2,"price":160}]},
        {"id":"prospector_venn","name":"Venn the Prospector","location":"Ridgeback Hills",
         "personality":"An excitable prospector covered in dust who has been searching for a legendary gem mine for eleven years. He is convinced it exists. Buys raw materials at good prices.",
         "known_about_players":["name","items"],"can_trade":True,
         "inventory":[{"item_id":"mat_stone","qty":20,"price":1},{"item_id":"mat_iron_ore","qty":5,"price":7},{"item_id":"wpn_axe","qty":1,"price":58}]},
        {"id":"sage_embera",   "name":"Sage Embera",        "location":"Ember Plateau",
         "personality":"A calm soot-stained woman who studies the volcanic plateau. Not afraid of fire at all, which concerns everyone who meets her. Can identify unusual materials and knows the plateau's safe paths.",
         "known_about_players":["name","level"],"can_trade":True,
         "inventory":[{"item_id":"mat_coal","qty":10,"price":4},{"item_id":"mat_gem_shard","qty":2,"price":32}]},
        # DUSTWIND
        {"id":"nomad_renn",    "name":"Renn the Nomad",     "location":"Dustwind Crossing",
         "personality":"A mysterious desert wanderer with sand-coloured robes and sharp eyes. Has survived the Dunes of Kor twice. Trades survival gear and ancient artefacts. Every word is precise.",
         "known_about_players":["name","location"],"can_trade":True,
         "inventory":[{"item_id":"food_stew","qty":5,"price":15},{"item_id":"mat_gem_shard","qty":2,"price":45},{"item_id":"food_jerky","qty":10,"price":9}]},
        {"id":"healer_saffron","name":"Healer Saffron",     "location":"Mirestone Oasis",
         "personality":"A serene healer in flowing robes who tends the oasis. Believes the water has genuine healing properties and has evidence to support it. Will heal injured players for a fee.",
         "known_about_players":["name","hp","mana"],"can_trade":True,
         "inventory":[{"item_id":"food_elixir","qty":5,"price":55},{"item_id":"food_potion_mana","qty":3,"price":62}]},
        {"id":"azar_guide",    "name":"Azar the Guide",     "location":"Dunes of Kor",
         "personality":"A taciturn desert guide with sun-cracked lips who has survived the dunes by knowing exactly where not to go. Will guide players for gems. Refuses to take anyone somewhere that will get them killed.",
         "known_about_players":["name","level","hp"],"can_trade":False,"inventory":[]},
        {"id":"tomb_sentinel", "name":"The Undying Sentinel","location":"Tomb of Kor",
         "personality":"An ancient animated statue that has guarded the Tomb of Kor for a thousand years. Speaks in archaic formal register. Permits entry to those who demonstrate worthiness and destroys those who do not. Has no concept of small talk.",
         "known_about_players":["name","level","atk","def","hp"],"can_trade":False,"inventory":[]},
        # TUNDRA
        {"id":"bjorn_trader",  "name":"Bjorn the Trader",   "location":"Frostveil Settlement",
         "personality":"A massive bearded man who trades in furs and dried fish. Loud, warm, extremely fond of arm-wrestling contests. Pays premium for rare pelts. Gives good fire-making tips for the tundra.",
         "known_about_players":["name","items"],"can_trade":True,
         "inventory":[{"item_id":"arm_fur","qty":2,"price":65},{"item_id":"food_jerky","qty":15,"price":8},{"item_id":"mat_ice_shard","qty":5,"price":14}]},
        {"id":"shaman_eira",   "name":"Shaman Eira",        "location":"Frostveil Settlement",
         "personality":"An elderly woman who speaks to the spirits of the ice. The settlement's doctor, priest, and judge rolled into one. She can sense when someone is carrying something dangerous.",
         "known_about_players":["name","hp","mana","items"],"can_trade":True,
         "inventory":[{"item_id":"food_elixir","qty":2,"price":58},{"item_id":"food_potion_mana","qty":2,"price":60}]},
        {"id":"gunnar_fish",   "name":"Gunnar",             "location":"Frostveil Settlement",
         "personality":"A young man who fishes through holes in the ice all day. Very boring person to talk to about anything except ice fishing, on which subject he is absolutely riveting. Knows where the Glacier Fish run.",
         "known_about_players":["name"],"can_trade":False,"inventory":[]},
        # RUINS
        {"id":"ghost_archivist","name":"The Archivist",     "location":"Hollow City Ruins",
         "personality":"The ghost of the city former librarian. She does not know she is dead. Speaks as though the city still stands and refers to events from five hundred years ago. Has enormous knowledge of history but becomes confused if the current state of the city is described to her.",
         "known_about_players":["name","level"],"can_trade":False,"inventory":[]},
        {"id":"cobb_scav",     "name":"Cobb the Scavenger", "location":"Ruinsgate",
         "personality":"A twitchy young man who makes his living scavenging the ruins. Very nervous. Knows every good scavenging spot but will not share them freely. Will buy unusual items at low prices and sell them high.",
         "known_about_players":["name","items","gems"],"can_trade":True,
         "inventory":[{"item_id":"spc_key_iron","qty":1,"price":55},{"item_id":"mat_ancient_rune","qty":1,"price":90}]},
        # EASTERN WILDS
        {"id":"yeva_scout",    "name":"Yeva the Scout",     "location":"Verdant Wilds",
         "personality":"A quick-moving young woman in green who knows every inch of the jungle. Speaks in short excited sentences. Has catalogued over two hundred species of animal. Will guide players to the best hunting spots.",
         "known_about_players":["name","location"],"can_trade":True,
         "inventory":[{"item_id":"food_berry","qty":20,"price":2},{"item_id":"mat_silk","qty":3,"price":16},{"item_id":"wpn_bow","qty":1,"price":68}]},
        {"id":"keeper_vael",   "name":"Keeper Vael",        "location":"Ancient Shrine",
         "personality":"A serene androgynous figure in faded robes who tends the shrine to a forgotten god. Speaks in a soft voice that somehow carries perfectly. Offers blessings to the worthy and will not say what worthy means. Players who visit regularly notice their luck improving.",
         "known_about_players":["name","level","luck"],"can_trade":False,"inventory":[]},
        # WESTERN CLIFFS
        {"id":"keeper_morrow", "name":"Keeper Morrow",      "location":"Cliffside Watch",
         "personality":"A tall thin man who has kept the lighthouse for forty years. Technically retired but his replacement has not arrived yet, and that was twelve years ago. Has watched the sea every day and has theories about what lives in it.",
         "known_about_players":["name"],"can_trade":True,
         "inventory":[{"item_id":"food_bread","qty":5,"price":5},{"item_id":"mat_stone","qty":10,"price":1}]},
    ]}

def load_npcs():
    return _read_json(os.path.join(_data_dir, "npcs.json")) or _default_npcs()

def get_npcs_at_location(location_name):
    return [n for n in load_npcs()["npcs"]
            if n["location"].lower() == location_name.lower()]

def _find_npc(query):
    q = query.lower().strip()
    all_npcs = load_npcs()["npcs"]
    for n in all_npcs:
        if n["name"].lower() == q:
            return n
    matches = [n for n in all_npcs if q in n["name"].lower()]
    return matches[0] if len(matches) == 1 else None

# ─────────────────────────────────────────────────────────────────────────────
# WORLD / MAP DATA
# ─────────────────────────────────────────────────────────────────────────────
def _default_world():
    return {"locations": [
        # HEARTLAND
        {"name":"Hearthstone Village","region":"Heartland","description":"A cosy starting village surrounded by golden fields. The scent of fresh bread fills the air.",
         "danger":0,"has_water":False,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[1200,800],"connections":["Millford","Thornwood Path","Goldenfield Plains","Crestlake","River Crest Ford"],"weather_modifier":0},
        {"name":"Millford","region":"Heartland","description":"A busy mill town straddling the River Crest. Merchants trade grain and fish.",
         "danger":1,"has_water":True,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[1050,720],"connections":["Hearthstone Village","Crestlake","Irongate City","Ashfen Marsh","River Crest Ford"],"weather_modifier":0},
        {"name":"Goldenfield Plains","region":"Heartland","description":"Vast open grasslands stretching to the horizon. Peaceful but exposed.",
         "danger":1,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1350,900],"connections":["Hearthstone Village","Crestlake","Dustwind Crossing","Ridgeback Hills"],"weather_modifier":1},
        {"name":"Crestlake","region":"Heartland","description":"A calm lake ringed by reeds. Famous for silver trout and quiet evenings.",
         "danger":1,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1100,880],"connections":["Hearthstone Village","Millford","Goldenfield Plains","Thornwood Path"],"weather_modifier":-1},
        {"name":"River Crest Ford","region":"Heartland","description":"A wide shallow crossing of the River Crest. Popular with travellers and the creatures that hunt them.",
         "danger":2,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1150,650],"connections":["Millford","Hearthstone Village","Irongate City"],"weather_modifier":0},
        # IRON NORTH
        {"name":"Irongate City","region":"Iron North","description":"A great walled city of forges and smiths. The skies are perpetually grey with soot.",
         "danger":2,"has_water":False,"has_forest":False,"is_city":True,"is_village":False,
         "coords":[900,500],"connections":["Millford","Ashfen Marsh","Stoneback Ridge","Forge Road","River Crest Ford"],"weather_modifier":1},
        {"name":"Forge Road","region":"Iron North","description":"A wide trade road hammered flat by countless ore carts.",
         "danger":2,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[850,620],"connections":["Irongate City","Stoneback Ridge","Millford"],"weather_modifier":0},
        {"name":"Stoneback Ridge","region":"Iron North","description":"A jagged spine of granite. Mountain goats and bandits share these narrow paths.",
         "danger":4,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[750,400],"connections":["Irongate City","Forge Road","Frostpeak Summit","Glacier Pass","Stormcap Mountains"],"weather_modifier":2},
        {"name":"Frostpeak Summit","region":"Iron North","description":"The highest point in the north. Snow falls year-round. Only the hardiest survive.",
         "danger":7,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[680,260],"connections":["Stoneback Ridge","Glacier Pass","Stormcap Mountains"],"weather_modifier":4},
        {"name":"Stormcap Mountains","region":"Iron North","description":"Towering peaks permanently wreathed in storm clouds. Wyverns and trolls make this their home.",
         "danger":8,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[520,350],"connections":["Stoneback Ridge","Frostpeak Summit"],"weather_modifier":5},
        {"name":"Glacier Pass","region":"Iron North","description":"A treacherous ice corridor through the mountains. Avalanches are common.",
         "danger":6,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[820,280],"connections":["Stoneback Ridge","Frostpeak Summit","Tundra Flats"],"weather_modifier":3},
        # ASHFEN
        {"name":"Ashfen Marsh","region":"Ashfen","description":"A fog-choked swamp of dead trees and black water. Things move beneath the surface.",
         "danger":5,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[700,750],"connections":["Millford","Irongate City","Bogmire","Thornwood Path","Mirepool Lake"],"weather_modifier":2},
        {"name":"Mirepool Lake","region":"Ashfen","description":"A dark still lake at the heart of the marsh. Things live in its depths that have not been named yet.",
         "danger":5,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[600,700],"connections":["Ashfen Marsh","Bogmire"],"weather_modifier":2},
        {"name":"Bogmire","region":"Ashfen","description":"A sunken village half-swallowed by the marsh. Its few residents are peculiar.",
         "danger":4,"has_water":True,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[580,820],"connections":["Ashfen Marsh","Witchwood","Saltmere Coast","Mirepool Lake"],"weather_modifier":1},
        {"name":"Witchwood","region":"Ashfen","description":"An ancient twisted forest at the swamp's edge. The trees seem to watch you.",
         "danger":6,"has_water":False,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[480,900],"connections":["Bogmire","Deepwood Heart","Cliffside Watch"],"weather_modifier":2},
        # THORNWOOD
        {"name":"Thornwood Path","region":"Thornwood","description":"A well-worn trail through dense forest. Stay on the path.",
         "danger":2,"has_water":False,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[950,900],"connections":["Hearthstone Village","Crestlake","Ashfen Marsh","Thornwood Village","Deepwood Heart"],"weather_modifier":-1},
        {"name":"Thornwood Village","region":"Thornwood","description":"A cheerful forest village where rangers and woodcutters make their home.",
         "danger":1,"has_water":False,"has_forest":True,"is_city":False,"is_village":True,
         "coords":[870,1000],"connections":["Thornwood Path","Deepwood Heart","Saltmere Coast"],"weather_modifier":-1},
        {"name":"Deepwood Heart","region":"Thornwood","description":"The dense ancient core of the Thornwood. No light reaches the floor. Very dangerous.",
         "danger":7,"has_water":False,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[650,1050],"connections":["Thornwood Path","Thornwood Village","Witchwood","Ruinsgate"],"weather_modifier":0},
        # SALTMERE
        {"name":"Saltmere Coast","region":"Saltmere","description":"A windswept coastline of white cliffs and crashing waves. Excellent fishing.",
         "danger":2,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[800,1200],"connections":["Bogmire","Thornwood Village","Saltmere Port","Shipwreck Cove"],"weather_modifier":1},
        {"name":"Saltmere Port","region":"Saltmere","description":"A bustling port city smelling of brine and adventure. The largest market on the coast.",
         "danger":3,"has_water":True,"has_forest":False,"is_city":True,"is_village":False,
         "coords":[950,1350],"connections":["Saltmere Coast","Shipwreck Cove","Dustwind Crossing","Sunken Reef","Crystal River Delta"],"weather_modifier":1},
        {"name":"Shipwreck Cove","region":"Saltmere","description":"A hidden cove littered with the bones of ships. Pirates and sea creatures lurk here.",
         "danger":6,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[700,1380],"connections":["Saltmere Coast","Saltmere Port","Sunken Reef"],"weather_modifier":2},
        {"name":"Sunken Reef","region":"Saltmere","description":"A partially submerged reef. Rare fish abound, but so do sea serpents.",
         "danger":8,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1050,1450],"connections":["Saltmere Port","Shipwreck Cove"],"weather_modifier":3},
        {"name":"Crystal River Delta","region":"Saltmere","description":"Where the Crystal River fans into the sea. Best fishing on the continent.",
         "danger":2,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1200,1400],"connections":["Saltmere Port","Dustwind Crossing"],"weather_modifier":0},
        # RIDGEBACK
        {"name":"Ridgeback Hills","region":"Ridgeback","description":"Rolling amber hills dotted with ruins. Good hunting, moderate danger.",
         "danger":3,"has_water":False,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[1550,850],"connections":["Goldenfield Plains","Dustwind Crossing","Ridgeback Keep","Ember Plateau"],"weather_modifier":1},
        {"name":"Ridgeback Keep","region":"Ridgeback","description":"A fortified keep atop the highest hill. Once a military stronghold, now a trading post.",
         "danger":2,"has_water":False,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[1700,750],"connections":["Ridgeback Hills","Ember Plateau","Dustwind Crossing","Verdant Wilds"],"weather_modifier":2},
        {"name":"Ember Plateau","region":"Ridgeback","description":"A plateau of dark volcanic rock still warm to the touch. Fire lizards nest here.",
         "danger":5,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1850,680],"connections":["Ridgeback Hills","Ridgeback Keep","Ashcrag Caldera"],"weather_modifier":3},
        {"name":"Ashcrag Caldera","region":"Ridgeback","description":"The mouth of a dormant (mostly) volcano. Magma elementals and fire drakes roam freely.",
         "danger":9,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1980,580],"connections":["Ember Plateau"],"weather_modifier":5},
        # DUSTWIND
        {"name":"Dustwind Crossing","region":"Dustwind","description":"A crossroads carved into the desert. Caravans stop here, as do bandits.",
         "danger":3,"has_water":False,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[1400,1100],"connections":["Goldenfield Plains","Ridgeback Hills","Ridgeback Keep","Saltmere Port","Dunes of Kor","Mirestone Oasis","Crystal River Delta"],"weather_modifier":2},
        {"name":"Mirestone Oasis","region":"Dustwind","description":"A miraculous oasis shimmering with gem-clear water. Rumours say it heals wounds.",
         "danger":2,"has_water":True,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[1550,1250],"connections":["Dustwind Crossing","Dunes of Kor"],"weather_modifier":-1},
        {"name":"Dunes of Kor","region":"Dustwind","description":"Endless scorching dunes hiding ancient tombs and sand wraiths.",
         "danger":7,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1750,1300],"connections":["Dustwind Crossing","Mirestone Oasis","Tomb of Kor"],"weather_modifier":4},
        {"name":"Tomb of Kor","region":"Dustwind","description":"An ancient buried citadel. The air smells of old magic and something far worse.",
         "danger":10,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1900,1400],"connections":["Dunes of Kor"],"weather_modifier":5},
        # TUNDRA
        {"name":"Tundra Flats","region":"Tundra","description":"Frozen featureless plains. The cold can kill you in hours.",
         "danger":6,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1000,180],"connections":["Glacier Pass","Frostveil Settlement","Ice Cavern"],"weather_modifier":4},
        {"name":"Frostveil Settlement","region":"Tundra","description":"A hardy community of fur-traders and ice-fishers. Warm fires, colder hearts.",
         "danger":4,"has_water":True,"has_forest":False,"is_city":False,"is_village":True,
         "coords":[1200,130],"connections":["Tundra Flats","Ice Cavern","Permafrost Depths"],"weather_modifier":3},
        {"name":"Ice Cavern","region":"Tundra","description":"A labyrinthine cave system carved by ancient glaciers. Ice beasts prowl inside.",
         "danger":7,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1100,80],"connections":["Tundra Flats","Frostveil Settlement","Permafrost Depths"],"weather_modifier":4},
        {"name":"Permafrost Depths","region":"Tundra","description":"Below the ice cavern: a frozen underworld where ancient creatures are locked in ice, some still alive.",
         "danger":10,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[1050,40],"connections":["Ice Cavern","Frostveil Settlement"],"weather_modifier":5},
        # RUINLANDS
        {"name":"Ruinsgate","region":"Ruinlands","description":"A crumbling archway marking the entrance to a great lost city.",
         "danger":6,"has_water":False,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[550,1150],"connections":["Deepwood Heart","Hollow City Ruins"],"weather_modifier":1},
        {"name":"Hollow City Ruins","region":"Ruinlands","description":"The gutted remains of a once-great city. Undead walk its empty streets at night.",
         "danger":8,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[430,1300],"connections":["Ruinsgate","The Abyss Gate"],"weather_modifier":2},
        {"name":"The Abyss Gate","region":"Ruinlands","description":"A swirling portal of dark energy. Only the reckless go here.",
         "danger":10,"has_water":False,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[310,1450],"connections":["Hollow City Ruins"],"weather_modifier":5},
        # EASTERN WILDS
        {"name":"Verdant Wilds","region":"Eastern Wilds","description":"Lush untamed jungle in the far east. Exotic creatures, rare plants, hidden treasures.",
         "danger":5,"has_water":True,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[2100,900],"connections":["Ember Plateau","Ridgeback Keep","Ancient Shrine"],"weather_modifier":-1},
        {"name":"Ancient Shrine","region":"Eastern Wilds","description":"A moss-covered shrine to a forgotten god. Players who pray here report strange luck.",
         "danger":4,"has_water":False,"has_forest":True,"is_city":False,"is_village":False,
         "coords":[2250,1050],"connections":["Verdant Wilds"],"weather_modifier":-2},
        # WESTERN CLIFFS
        {"name":"Cliffside Watch","region":"Western Cliffs","description":"A crumbling watchtower on a cliff overlooking the western sea. Windswept and lonely.",
         "danger":3,"has_water":True,"has_forest":False,"is_city":False,"is_village":False,
         "coords":[200,700],"connections":["Witchwood","Bogmire"],"weather_modifier":2},
    ]}

def load_world():
    return _read_json(os.path.join(_data_dir, "world.json")) or _default_world()

def get_location(name):
    if not name:
        return None
    for loc in load_world()["locations"]:
        if loc["name"].lower() == name.lower():
            return loc
    return None

def find_location_fuzzy(query):
    world = load_world()
    q = query.lower().strip()
    for loc in world["locations"]:
        if loc["name"].lower() == q:
            return loc
    matches = [l for l in world["locations"] if q in l["name"].lower()]
    return matches[0] if len(matches) == 1 else None

# ─────────────────────────────────────────────────────────────────────────────
# SHOP
# ─────────────────────────────────────────────────────────────────────────────
def _default_shop():
    return {"items": [
        {"id":"chest_50",   "name":"Small Chest",      "category":"chest","description":"Holds up to 50 gems.","cost":17,  "capacity":50,  "tier":"small"},
        {"id":"chest_100",  "name":"Standard Chest",   "category":"chest","description":"Holds up to 100 gems.","cost":33, "capacity":100, "tier":"standard"},
        {"id":"chest_250",  "name":"Iron Chest",       "category":"chest","description":"Holds up to 250 gems.","cost":83, "capacity":250, "tier":"iron"},
        {"id":"chest_500",  "name":"Reinforced Chest", "category":"chest","description":"Holds up to 500 gems.","cost":167,"capacity":500, "tier":"reinforced"},
        {"id":"chest_1000", "name":"Vault Chest",      "category":"chest","description":"Holds up to 1000 gems.","cost":333,"capacity":1000,"tier":"vault"},
    ]}

def load_shop():
    return _read_json(os.path.join(_data_dir, "shop.json")) or _default_shop()

def save_shop(shop):
    _write_json(os.path.join(_data_dir, "shop.json"), shop)

# ─────────────────────────────────────────────────────────────────────────────
# MAP GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_map():
    base_path = os.path.join(_data_dir, "world_map_base.png")
    if not os.path.exists(base_path):
        _generate_map(base_path)
    map_path = os.path.join(_data_dir, MAP_FILENAME)
    if not os.path.exists(map_path):
        import shutil
        shutil.copy2(base_path, map_path)

def _generate_map(output_path):
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:
        print("[game_engine] Pillow not available - map generation skipped.")
        return

    W, H = MAP_WIDTH, MAP_HEIGHT
    print("[game_engine] Generating world map...")
    rng = random.Random(42)

    step = 4
    sw, sh = W // step + 2, H // step + 2
    heights = [[0.0] * sh for _ in range(sw)]
    for sx in range(sw):
        for sy in range(sh):
            x, y = sx * step, sy * step
            cx = abs(x - W / 2) / (W / 2)
            cy = abs(y - H / 2) / (H / 2)
            falloff = 1.0 - max(cx ** 1.5, cy ** 1.5)
            n = 0.0
            amp, freq = 1.0, 1.0
            for _ in range(6):
                n += amp * (math.sin(x * freq / 280 + rng.uniform(0, 6.28)) *
                            math.cos(y * freq / 280 + rng.uniform(0, 6.28)))
                amp *= 0.5
                freq *= 2.0
            heights[sx][sy] = max(0.0, min(1.0, (n + 1) / 2 * falloff + 0.05))

    def h2c(h):
        if h < 0.22: return (20, 70, 160)
        if h < 0.30: return (50, 120, 200)
        if h < 0.34: return (80, 155, 215)
        if h < 0.38: return (210, 195, 145)
        if h < 0.46: return (115, 175, 85)
        if h < 0.56: return (90, 150, 70)
        if h < 0.65: return (65, 120, 55)
        if h < 0.73: return (130, 110, 80)
        if h < 0.83: return (155, 150, 140)
        return (235, 238, 248)

    img = Image.new("RGB", (W, H))
    pix = img.load()
    for px in range(W):
        for py in range(H):
            sx = min(px // step, sw - 2)
            sy = min(py // step, sh - 2)
            fx = (px % step) / step
            fy = (py % step) / step
            h = (heights[sx][sy] * (1 - fx) * (1 - fy) +
                 heights[sx + 1][sy] * fx * (1 - fy) +
                 heights[sx][sy + 1] * (1 - fx) * fy +
                 heights[sx + 1][sy + 1] * fx * fy)
            pix[px, py] = h2c(h)

    img = img.filter(ImageFilter.SMOOTH_MORE)
    draw = ImageDraw.Draw(img)

    rc = (70, 150, 210)
    rivers = [
        [(680,260),(820,280),(1000,180),(1150,650),(1050,720),(1100,880),(1200,1400)],
        [(600,700),(700,750),(580,820),(480,900)],
        [(2100,900),(2000,1100),(1900,1400)],
        [(1550,1250),(1400,1100),(1200,1400)],
    ]
    for rp in rivers:
        draw.line(rp, fill=rc, width=3)

    world = _default_world()
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except Exception:
        font_b = font_s = ImageFont.load_default()

    CITY_COL   = (220, 50, 50)
    VIL_COL    = (240, 160, 30)
    WATER_COL  = (30, 160, 240)
    DANGER_COL = (200, 0, 0)
    PLACE_COL  = (170, 50, 200)

    def dot_col(loc):
        if loc.get("is_city"):       return CITY_COL
        if loc.get("is_village"):    return VIL_COL
        if loc.get("danger", 0) >= 8: return DANGER_COL
        if loc.get("has_water"):     return WATER_COL
        return PLACE_COL

    for loc in world["locations"]:
        cx, cy = loc["coords"]
        r   = 7 if (loc.get("is_city") or loc.get("is_village")) else 5
        col = dot_col(loc)
        draw.ellipse([(cx-r-1, cy-r-1), (cx+r+1, cy+r+1)], fill=(20, 20, 20))
        draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=col)
        font = font_b if (loc.get("is_city") or loc.get("is_village")) else font_s
        draw.text((cx + r + 3, cy - 6), loc["name"], fill=(15, 15, 15), font=font)

    region_labels = [
        ("HEARTLAND",      1200, 870), ("IRON NORTH",   800, 470),
        ("ASHFEN",          620, 780), ("THORNWOOD",    860, 990),
        ("SALTMERE",        870,1300), ("RIDGEBACK",   1680, 820),
        ("DUSTWIND",       1600,1150), ("TUNDRA",      1100, 160),
        ("RUINLANDS",       400,1200), ("EASTERN WILDS",2100,830),
        ("WESTERN CLIFFS",  140, 670),
    ]
    for label, lx, ly in region_labels:
        draw.text((lx, ly), label, fill=(255, 255, 230), font=font_b)

    lx, ly = 18, H - 128
    draw.rectangle([(lx-3, ly-3), (lx+195, ly+118)], fill=(240, 240, 240))
    for i, (col, lbl) in enumerate([
        (CITY_COL,   "City"),
        (VIL_COL,    "Village"),
        (WATER_COL,  "Water / Fishing"),
        (PLACE_COL,  "Location"),
        (DANGER_COL, "Danger Zone"),
    ]):
        yy = ly + 4 + i * 22
        draw.ellipse([(lx+3, yy+2), (lx+15, yy+14)], fill=col)
        draw.text((lx + 19, yy), lbl, fill=(20, 20, 20), font=font_s)

    draw.text((W // 2 - 100, 8), "  REALM OF AETHERMOOR  ", fill=(255, 255, 230), font=font_b)
    img.save(output_path, "PNG", optimize=True)
    print(f"[game_engine] Map saved: {output_path}")

def render_map_with_players(group_id):
    try:
        from PIL import Image, ImageDraw, ImageFont
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
    COLOURS = [
        (255, 50, 50), (50, 200, 50), (50, 100, 255), (255, 200, 0),
        (200, 50, 255), (0, 220, 200), (255, 120, 0), (220, 0, 130),
    ]
    ci = 0
    for key, p in players.items():
        if not key.startswith(f"{group_id}:"):
            continue
        loc = loc_map.get(p.get("location", ""))
        if not loc:
            continue
        cx, cy = loc["coords"]
        col = COLOURS[ci % len(COLOURS)]
        ci += 1
        ox = (ci % 3 - 1) * 14
        oy = -22 + (ci % 2) * -8
        draw.ellipse([(cx+ox-7, cy+oy-7), (cx+ox+7, cy+oy+7)], fill=col, outline=(20, 20, 20))
        nm = p.get("name", "?")[:8]
        draw.text((cx + ox - len(nm) * 2, cy + oy + 9), nm, fill=col, font=font)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

# ─────────────────────────────────────────────────────────────────────────────
# TRAVEL
# ─────────────────────────────────────────────────────────────────────────────
TRAVEL_SPEED = 60  # pixels per real minute

def _travel_time(from_loc, to_loc):
    x1, y1 = from_loc["coords"]
    x2, y2 = to_loc["coords"]
    dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    return max(30, int(dist / TRAVEL_SPEED * 60))

def _are_connected(a, b):
    la = get_location(a)
    return la is not None and b in la.get("connections", [])

def start_travel(group_id, user_id, dest_name):
    p = _get_player(group_id, user_id)
    if not p:
        return False, "Register first with #beginpoints."
    if p.get("in_combat"):
        return False, "Cannot travel while in combat!"
    if p.get("travelling_to"):
        return False, f"Already travelling to {p['travelling_to']}!"
    dest = find_location_fuzzy(dest_name)
    if not dest:
        return False, f"Unknown location: '{dest_name}'. See #locations."
    cur_name = p.get("location", "Hearthstone Village")
    if dest["name"] == cur_name:
        return False, f"Already at {dest['name']}!"
    if not _are_connected(cur_name, dest["name"]):
        cur = get_location(cur_name)
        conns = ", ".join(cur.get("connections", [])) if cur else "none"
        return False, (f"Cannot travel directly to {dest['name']} from {cur_name}.\n"
                       f"Reachable: {conns}")
    secs = _travel_time(get_location(cur_name), dest)
    p["travelling_to"] = dest["name"]
    p["travel_arrive"] = time.time() + secs
    _save_player(group_id, user_id, p)
    m, s = divmod(secs, 60)
    warn = f" Warning: Danger {dest['danger']}/10." if dest.get("danger", 0) >= 5 else ""
    return True, f"Heading to {dest['name']}! ETA: {m}m {s}s.{warn}"

def check_arrivals(group_id):
    messages = []
    with _game_lock:
        players = _load_players()
        now = time.time()
        changed = False
        for key, p in players.items():
            if not key.startswith(f"{group_id}:"):
                continue
            if not p.get("travelling_to"):
                continue
            if p.get("travel_arrive", now + 1) > now:
                continue
            dest_name = p["travelling_to"]
            p["location"]      = dest_name
            p["travelling_to"] = None
            p["travel_arrive"] = None
            changed = True
            dest   = get_location(dest_name)
            danger = dest.get("danger", 0) if dest else 0
            enc_msg = ""
            if danger >= 2 and random.random() < min(0.5, danger * 0.04):
                enemies = get_enemies_for_danger(danger)
                if enemies:
                    enemy = random.choice(enemies)
                    uid = key.split(":", 1)[1]
                    _start_pve_combat(group_id, uid, p, enemy)
                    p["in_combat"]  = True
                    p["combat_key"] = key
                    enc_msg = (f"\nA {enemy['name']} attacks {p['name']}! "
                               f"Use #fight <attack> or #flee.")
            messages.append(f"Arrived at {dest_name}!{enc_msg}")
        if changed:
            _save_players(players)
    return messages

# ─────────────────────────────────────────────────────────────────────────────
# COMBAT
# ─────────────────────────────────────────────────────────────────────────────
def _start_pve_combat(group_id, user_id, player, enemy):
    key = _pk(group_id, user_id)
    _active_combats[key] = {
        "type":      "pve",
        "group_id":  group_id,
        "user_id":   user_id,
        "enemy":     dict(enemy),
        "enemy_hp":  enemy["hp"],
        "last_action": time.time(),
    }

def _player_atk_total(player):
    base = player.get("atk", STARTING_ATK)
    eq   = player.get("equipped_weapon")
    if eq:
        for w in load_items().get("weapons", []):
            if w["id"] == eq:
                return base + w.get("atk_bonus", 0)
    return base

def _player_def_total(player):
    base = player.get("def", STARTING_DEF)
    for _, item_id in player.get("armour", {}).items():
        idef = _find_item_def(item_id)
        if idef:
            base += idef.get("def_bonus", 0)
    return base

def _get_available_attacks(player):
    eq = player.get("equipped_weapon")
    if eq:
        for w in load_items().get("weapons", []):
            if w["id"] == eq:
                return w.get("attacks", ["punch"])
    return ["punch", "kick"]

def _calc_damage(atk_val, def_val, luck=5):
    raw  = atk_val * random.uniform(0.8, 1.2)
    lb   = (luck - 5) * 0.5
    dmg  = max(1, int(raw - def_val * 0.4 + lb))
    crit = random.random() < 0.05 + (luck - 5) * 0.01
    if crit:
        dmg = int(dmg * 1.75)
    return dmg, crit

def _nearest_safe(location_name):
    world = load_world()
    safe  = [l for l in world["locations"] if l.get("is_village") or l.get("is_city")]
    if not safe:
        return "Hearthstone Village"
    cur = get_location(location_name)
    if not cur:
        return "Hearthstone Village"
    cx, cy = cur["coords"]
    return min(safe, key=lambda l: math.sqrt((l["coords"][0]-cx)**2+(l["coords"][1]-cy)**2))["name"]

def cmd_fight(group_id, user_id, name, attack_word):
    key    = _pk(group_id, user_id)
    combat = _active_combats.get(key)
    if not combat:
        return "Not in combat. Travel or hunt to find enemies."
    player = _get_player(group_id, user_id)
    if not player:
        return "Player data error."
    attack_word = (attack_word or "punch").lower().strip()
    if combat["type"] == "pve":
        return _pve_turn(group_id, user_id, player, combat, attack_word)
    if combat["type"] == "pvp":
        return _pvp_turn(group_id, user_id, player, combat, attack_word)
    return "Unknown combat type."

def _pve_turn(group_id, user_id, player, combat, attack_word):
    key      = _pk(group_id, user_id)
    enemy    = combat["enemy"]
    enemy_hp = combat["enemy_hp"]

    pdmg, pcrit = _calc_damage(_player_atk_total(player), enemy.get("def", 0), player.get("luck", 5))
    crit_s       = " CRITICAL!" if pcrit else ""
    enemy_hp    -= pdmg
    lines = [f"{player['name']} uses {attack_word}! -{pdmg} HP to {enemy['name']}.{crit_s}"]

    if enemy_hp <= 0:
        xp_gain  = enemy.get("xp", 10)
        gem_gain = random.randint(enemy.get("gem_min", 0), enemy.get("gem_max", 5))
        player, levelled = _give_xp(player, xp_gain)
        player["gems"]       = player.get("gems", 0) + gem_gain
        player["in_combat"]  = False
        player["combat_key"] = None
        drops_text = ""
        for drop_id in enemy.get("drops", []):
            if random.random() < 0.4:
                idef = _find_item_def(drop_id)
                if idef:
                    _add_item_to_player(group_id, user_id, drop_id, idef.get("name", "?"), 1, idef.get("category", "material"))
                    drops_text += f"\n  - {idef.get('name','?')}"
        _save_player(group_id, user_id, player)
        del _active_combats[key]
        lvl_s = f"\nLEVEL UP! Now level {player['level']}!" if levelled else ""
        return (f"{''.join(lines)}\n{enemy['name']} defeated!\n"
                f"  +{xp_gain} XP  +{gem_gain} gems{drops_text}{lvl_s}\n"
                f"HP: {player['hp']}/{player['max_hp']}")

    edm, ecrit = _calc_damage(enemy.get("atk", 5), _player_def_total(player))
    ecrit_s     = " CRITICAL!" if ecrit else ""
    player["hp"] = max(0, player.get("hp", 0) - edm)
    lines.append(f"{enemy['name']} strikes back! -{edm} HP.{ecrit_s}")
    lines.append(f"HP: {player['hp']}/{player['max_hp']}  |  {enemy['name']}: {enemy_hp}/{enemy['hp']} HP")

    if player["hp"] <= 0:
        lost = min(player.get("gems", 0), int(player.get("gems", 0) * 0.1))
        player["gems"]       = player.get("gems", 0) - lost
        player["hp"]         = max(1, player["max_hp"] // 4)
        player["in_combat"]  = False
        player["combat_key"] = None
        player["location"]   = _nearest_safe(player.get("location", "Hearthstone Village"))
        _save_player(group_id, user_id, player)
        del _active_combats[key]
        return ("\n".join(lines) +
                f"\n{player['name']} has been defeated! Lost {lost} gems.\n"
                f"Respawned at {player['location']} with {player['hp']} HP.\n"
                f"Use #rest to recover.")

    combat["enemy_hp"]    = enemy_hp
    combat["last_action"] = time.time()
    _active_combats[key]  = combat
    _save_player(group_id, user_id, player)
    lines.append(f"  Attacks: {', '.join(_get_available_attacks(player))}")
    return "\n".join(lines)

def cmd_flee(group_id, user_id, name):
    key    = _pk(group_id, user_id)
    combat = _active_combats.get(key)
    if not combat:
        return "Not in combat."
    player = _get_player(group_id, user_id)
    enemy  = combat.get("enemy", {})
    spd    = player.get("spd", STARTING_SPD)
    flee_chance = max(0.1, min(0.9, 0.4 + (spd - enemy.get("atk", 5) // 3) * 0.05))
    if random.random() < flee_chance:
        player["in_combat"]  = False
        player["combat_key"] = None
        _save_player(group_id, user_id, player)
        del _active_combats[key]
        return f"{name} fled from the {enemy.get('name','enemy')} successfully!"
    edm, _ = _calc_damage(enemy.get("atk", 5), _player_def_total(player))
    player["hp"] = max(1, player.get("hp", 0) - edm)
    _save_player(group_id, user_id, player)
    return (f"{name} tried to flee but failed!\n"
            f"  {enemy.get('name','Enemy')} hits for {edm} damage.\n"
            f"  HP: {player['hp']}/{player['max_hp']}")

def cmd_attack_player(group_id, user_id, name, target_name):
    attacker = _get_player(group_id, user_id)
    if not attacker:
        return "Register first with #beginpoints."
    if attacker.get("in_combat"):
        return "Already in combat!"
    players = _load_players()
    tgt_key = tgt_p = None
    for k, p in players.items():
        if k.startswith(f"{group_id}:") and target_name.lower() in p.get("name", "").lower():
            tgt_key = k
            tgt_p   = p
            break
    if not tgt_p:
        return f"Player '{target_name}' not found."
    tgt_id = tgt_key.split(":", 1)[1]
    if tgt_key == _pk(group_id, user_id):
        return "Cannot attack yourself."
    if tgt_p.get("in_combat"):
        return f"{tgt_p['name']} is already in combat."
    if attacker.get("location") != tgt_p.get("location"):
        return f"{tgt_p['name']} is at {tgt_p.get('location','?')}. Must be at same location to attack."
    ck = _pk(group_id, user_id)
    _active_combats[ck] = {
        "type":    "pvp", "group_id": group_id,
        "p1_id":   user_id, "p2_id": tgt_id,
        "p1_name": name,    "p2_name": tgt_p["name"],
        "turn":    user_id, "last_action": time.time(),
    }
    attacker["in_combat"] = True; attacker["combat_key"] = ck
    tgt_p["in_combat"]    = True; tgt_p["combat_key"]    = ck
    _save_player(group_id, user_id, attacker)
    _save_player(group_id, tgt_id,  tgt_p)
    return (f"{name} challenges {tgt_p['name']} to combat!\n"
            f"{name}'s turn. #fight <attack> or #flee.\n"
            f"Attacks: {', '.join(_get_available_attacks(attacker))}")

def _pvp_turn(group_id, user_id, player, combat, attack_word):
    if combat["turn"] != user_id:
        other_name = combat["p1_name"] if combat["p2_id"] == user_id else combat["p2_name"]
        return f"It's {other_name}'s turn!"
    p1_id    = combat["p1_id"]
    other_id = combat["p2_id"] if user_id == p1_id else combat["p1_id"]
    other    = _get_player(group_id, other_id)
    dmg, crit = _calc_damage(_player_atk_total(player), _player_def_total(other), player.get("luck", 5))
    crit_s    = " CRIT!" if crit else ""
    other["hp"] = max(0, other.get("hp", 0) - dmg)
    _save_player(group_id, other_id, other)
    lines = [f"{player['name']} {attack_word}s {other['name']}! -{dmg} HP.{crit_s}",
             f"  {other['name']}: {other['hp']}/{other['max_hp']} HP"]
    key = _pk(group_id, user_id)
    if other["hp"] <= 0:
        xp_gain   = max(20, other.get("level", 1) * 25)
        gem_steal = max(0, int(other.get("gems", 0) * 0.15))
        player, levelled = _give_xp(player, xp_gain)
        player["gems"] = player.get("gems", 0) + gem_steal
        other["gems"]  = other.get("gems", 0) - gem_steal
        other["hp"]    = max(1, other["max_hp"] // 4)
        other["in_combat"] = False; other["combat_key"] = None
        other["location"]  = _nearest_safe(other.get("location", ""))
        player["in_combat"] = False; player["combat_key"] = None
        _save_player(group_id, user_id, player)
        _save_player(group_id, other_id, other)
        if key in _active_combats: del _active_combats[key]
        lvl_s = f"\nLEVEL UP! Lv{player['level']}!" if levelled else ""
        return ("\n".join(lines) +
                f"\n{player['name']} wins! +{xp_gain} XP, stole {gem_steal} gems.{lvl_s}")
    combat["turn"]       = other_id
    combat["last_action"] = time.time()
    _active_combats[key] = combat
    return "\n".join(lines) + f"\n  {other['name']}'s turn. Attacks: {', '.join(_get_available_attacks(other))}"

# ─────────────────────────────────────────────────────────────────────────────
# EQUIP / UNEQUIP
# ─────────────────────────────────────────────────────────────────────────────
def cmd_equip(group_id, user_id, name, item_query):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    q = item_query.lower().strip()
    for w in player.get("weapons", []):
        if q in w["name"].lower() or q == w.get("item_id", "").lower():
            player["equipped_weapon"] = w["item_id"]
            for ww in player["weapons"]:
                ww["equipped"] = (ww["item_id"] == w["item_id"])
            _save_player(group_id, user_id, player)
            idef = _find_item_def(w["item_id"])
            attacks = idef.get("attacks", ["attack"]) if idef else ["attack"]
            return f"{name} equips {w['name']}! Attacks: {', '.join(attacks)}"
    for it in player.get("items", []):
        if q in it["name"].lower():
            idef = _find_item_def(it.get("item_id", ""))
            if idef and idef.get("def_bonus"):
                slot = idef.get("slot", "body")
                player.setdefault("armour", {})[slot] = it["item_id"]
                _save_player(group_id, user_id, player)
                return f"{name} equips {it['name']}! DEF +{idef.get('def_bonus', 0)}"
    return f"No weapon or armour matching '{item_query}' in inventory."

def cmd_unequip(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    if not player.get("equipped_weapon"):
        return "Nothing equipped."
    for w in player.get("weapons", []):
        w["equipped"] = False
    player["equipped_weapon"] = None
    _save_player(group_id, user_id, player)
    return f"{name} unequips. Attacks: punch, kick"

# ─────────────────────────────────────────────────────────────────────────────
# THROW IN COMBAT
# ─────────────────────────────────────────────────────────────────────────────
def cmd_throw(group_id, user_id, name, item_query):
    key    = _pk(group_id, user_id)
    combat = _active_combats.get(key)
    if not combat:
        return "Not in combat. Throwing items is a combat action."
    player = _get_player(group_id, user_id)
    if not player:
        return "Player data error."
    q = item_query.lower().strip()
    items = player.get("items", [])
    match = next((it for it in items if q in it["name"].lower()), None)
    if not match:
        return f"No item '{item_query}' in inventory."
    idef = _find_item_def(match.get("item_id", ""))
    if not idef or not idef.get("throwable", False):
        return f"{match['name']} cannot be thrown."

    luck     = player.get("luck", STARTING_LUCK)
    throw_dmg = random.randint(1 + luck // 3, 8 + luck // 2)
    crit      = random.random() < 0.08
    if crit:
        throw_dmg = int(throw_dmg * 1.5)
    crit_s = " Critical throw!" if crit else ""

    lost = False
    if idef.get("break_on_throw", True):
        match["qty"] = match.get("qty", 1) - 1
        if match["qty"] <= 0:
            items.remove(match)
        lost = True
    else:
        if random.random() < 0.15:
            match["qty"] = match.get("qty", 1) - 1
            if match["qty"] <= 0:
                items.remove(match)
            lost = True
    player["items"] = items

    if combat["type"] == "pve":
        enemy = combat["enemy"]
        combat["enemy_hp"] = max(0, combat["enemy_hp"] - throw_dmg)
        result = f"{name} throws {match['name']} at {enemy['name']} for {throw_dmg} dmg!{crit_s}"
        if lost:
            result += f" ({match['name']} {'shattered' if idef.get('break_on_throw') else 'lost'})"
        if combat["enemy_hp"] <= 0:
            player["in_combat"] = False; player["combat_key"] = None
            _save_player(group_id, user_id, player)
            del _active_combats[key]
            return result + f"\n{enemy['name']} defeated!"
        result += f"\n  {enemy['name']}: {combat['enemy_hp']}/{enemy['hp']} HP"
        _active_combats[key] = combat
    elif combat["type"] == "pvp":
        other_id = combat["p2_id"] if user_id == combat["p1_id"] else combat["p1_id"]
        other = _get_player(group_id, other_id)
        if not other:
            return "Error finding opponent."
        other["hp"] = max(0, other.get("hp", 0) - throw_dmg)
        _save_player(group_id, other_id, other)
        result = (f"{name} throws {match['name']} at {other['name']} for {throw_dmg} dmg!{crit_s}\n"
                  f"  {other['name']}: {other['hp']}/{other['max_hp']} HP")
        combat["turn"] = other_id
        _active_combats[key] = combat
    else:
        result = "Unknown combat type."

    _save_player(group_id, user_id, player)
    return result

# ─────────────────────────────────────────────────────────────────────────────
# MANA SPELLS
# ─────────────────────────────────────────────────────────────────────────────
SPELLS = {
    "heal":     {"cost": 10, "description": "Restore 20 HP to yourself."},
    "restore":  {"cost": 20, "description": "Restore 40 HP to yourself."},
    "fireball": {"cost": 15, "description": "Deal 15-30 magic damage."},
    "zap":      {"cost": 8,  "description": "Deal 8-15 quick magic damage."},
    "shield":   {"cost": 12, "description": "Boost your DEF by 3 for this turn."},
}

def cmd_cast(group_id, user_id, name, spell_name):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    sp = SPELLS.get(spell_name.lower())
    if not sp:
        return f"Unknown spell. Available: {', '.join(SPELLS.keys())}"
    if player.get("mana", 0) < sp["cost"]:
        return f"Not enough mana! Need {sp['cost']}, have {player.get('mana',0)}."

    player["mana"] = player.get("mana", 0) - sp["cost"]
    key    = _pk(group_id, user_id)
    combat = _active_combats.get(key)

    if spell_name in ("heal", "restore"):
        heal = 20 if spell_name == "heal" else 40
        player["hp"] = min(player["max_hp"], player.get("hp", 0) + heal)
        _save_player(group_id, user_id, player)
        return f"{name} casts {spell_name.capitalize()}! +{heal} HP. ({player['hp']}/{player['max_hp']}, {player['mana']} mana)"

    if spell_name == "shield":
        player["def"] = player.get("def", STARTING_DEF) + 3
        _save_player(group_id, user_id, player)
        return f"{name} casts Shield! DEF +3 for this turn. ({player['mana']} mana)"

    if not combat:
        return f"Must be in combat to cast {spell_name}."

    dmg  = random.randint(15, 30) if spell_name == "fireball" else random.randint(8, 15)
    crit = random.random() < 0.05 + (player.get("luck", 5) - 5) * 0.01
    if crit:
        dmg = int(dmg * 1.75)
    crit_s = " Critical!" if crit else ""
    _save_player(group_id, user_id, player)

    if combat["type"] == "pve":
        combat["enemy_hp"] = max(0, combat["enemy_hp"] - dmg)
        enemy = combat["enemy"]
        result = f"{name} casts {spell_name.capitalize()}! {dmg} magic dmg.{crit_s}"
        if combat["enemy_hp"] <= 0:
            player["in_combat"] = False; player["combat_key"] = None
            _save_player(group_id, user_id, player)
            del _active_combats[key]
            return result + f"\n{enemy['name']} defeated by magic!"
        result += f"\n  {enemy['name']}: {combat['enemy_hp']}/{enemy['hp']} HP"
        _active_combats[key] = combat
        return result
    elif combat["type"] == "pvp":
        other_id = combat["p2_id"] if user_id == combat["p1_id"] else combat["p1_id"]
        other = _get_player(group_id, other_id)
        if other:
            other["hp"] = max(0, other.get("hp", 0) - dmg)
            _save_player(group_id, other_id, other)
            combat["turn"] = other_id
            _active_combats[key] = combat
            return (f"{name} casts {spell_name.capitalize()}! {dmg} dmg.{crit_s}\n"
                    f"  {other['name']}: {other['hp']}/{other['max_hp']} HP")
    return "Cast but no valid target."

# ─────────────────────────────────────────────────────────────────────────────
# REST
# ─────────────────────────────────────────────────────────────────────────────
def cmd_rest(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    if player.get("in_combat"):
        return "Cannot rest during combat!"
    hp = player.get("hp", 0); max_hp = player.get("max_hp", STARTING_MAX_HP)
    mn = player.get("mana", 0); max_mn = player.get("max_mana", STARTING_MAX_MANA)
    if hp >= max_hp and mn >= max_mn:
        return f"{name} is already at full HP and Mana."
    r_hp = min(max_hp - hp, max(5, max_hp // 5))
    r_mn = min(max_mn - mn, max(3, max_mn // 5))
    player["hp"]   = hp + r_hp
    player["mana"] = mn + r_mn
    _save_player(group_id, user_id, player)
    return (f"{name} rests... +{r_hp} HP, +{r_mn} Mana.\n"
            f"  HP: {player['hp']}/{max_hp}  Mana: {player['mana']}/{max_mn}")

# ─────────────────────────────────────────────────────────────────────────────
# NPC INTERACTION
# ─────────────────────────────────────────────────────────────────────────────
def cmd_talk(group_id, user_id, name, npc_query):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    if not npc_query.strip():
        npcs_here = get_npcs_at_location(player.get("location", ""))
        if not npcs_here:
            return f"Nobody to talk to at {player.get('location','?')}."
        return "People here: " + ", ".join(n["name"] for n in npcs_here) + "\nUse #talk <name> to speak with them."
    npc = _find_npc(npc_query)
    if not npc:
        return f"Cannot find '{npc_query}' here."
    if npc["location"].lower() != player.get("location", "").lower():
        return f"{npc['name']} is at {npc['location']}, not here."

    known = npc.get("known_about_players", ["name"])
    ctx = []
    if "name"     in known: ctx.append(f"name: {player.get('name','?')}")
    if "level"    in known: ctx.append(f"level: {player.get('level',1)}")
    if "hp"       in known: ctx.append(f"HP: {player.get('hp',0)}/{player.get('max_hp',0)}")
    if "mana"     in known: ctx.append(f"Mana: {player.get('mana',0)}/{player.get('max_mana',0)}")
    if "gems"     in known: ctx.append(f"gems: {player.get('gems',0)}")
    if "location" in known: ctx.append(f"at: {player.get('location','?')}")
    if "atk"      in known: ctx.append(f"ATK: {player.get('atk',0)}")
    if "def"      in known: ctx.append(f"DEF: {player.get('def',0)}")
    if "items"    in known:
        nm = [it["name"] for it in player.get("items", [])[:5]]
        ctx.append(f"carrying: {', '.join(nm) if nm else 'nothing'}")
    if "weapons"  in known:
        wn = [w["name"] for w in player.get("weapons", [])[:3]]
        ctx.append(f"weapons: {', '.join(wn) if wn else 'none'}")
    if "story"    in known:
        st = player.get("story", [])
        ctx.append(f"history: {'; '.join(st[-3:]) if st else 'newcomer'}")

    npc_inv_str = ""
    if npc.get("can_trade") and npc.get("inventory"):
        inv_s = [f"{_find_item_def(it['item_id'])['name'] if _find_item_def(it['item_id']) else it['item_id']} for {it['price']} gems"
                 for it in npc["inventory"][:4]]
        npc_inv_str = f"You sell: {'; '.join(inv_s)}. "

    prompt = (
        f"You are {npc['name']}, an NPC in the fantasy world of Aethermoor.\n"
        f"Personality: {npc['personality']}\n"
        f"You are at {npc['location']}.\n"
        f"{npc_inv_str}"
        f"Player info you know: {', '.join(ctx)}.\n"
        f"Respond in character in 2-4 sentences. Plain text only. No markdown. No meta-commentary.\n"
        f"The player says hello."
    )

    response = f"{npc['name']} nods thoughtfully."
    try:
        import importlib
        main_mod = importlib.import_module("__main__")
        if hasattr(main_mod, "run_ollama"):
            response = main_mod.run_ollama(prompt, user_id=f"npc_{npc['id']}", sender_name=npc["name"])
    except Exception:
        pass

    trade_hint = f"\nUse #trade {npc['name'].split()[0]} to see wares." if npc.get("can_trade") else ""
    return f"{npc['name']}: \"{response}\"{trade_hint}"

def cmd_trade(group_id, user_id, name, npc_query):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    npc = _find_npc(npc_query)
    if not npc:
        return f"Cannot find '{npc_query}'."
    if npc["location"].lower() != player.get("location", "").lower():
        return f"{npc['name']} is at {npc['location']}, not here."
    if not npc.get("can_trade"):
        return f"{npc['name']} does not trade."
    inv = npc.get("inventory", [])
    if not inv:
        return f"{npc['name']} has nothing to sell."
    lines = [f"{npc['name']}'s wares:"]
    for i, it in enumerate(inv, 1):
        idef  = _find_item_def(it["item_id"])
        iname = idef["name"] if idef else it["item_id"]
        lines.append(f"  {i}. {iname} x{it['qty']} - {it['price']} gems")
    lines.append(f"\nUse #buyfrom {npc['name'].split()[0]} <item> to purchase.")
    return "\n".join(lines)

def cmd_buyfrom(group_id, user_id, name, npc_query, item_query):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    npc = _find_npc(npc_query)
    if not npc or npc["location"].lower() != player.get("location", "").lower():
        return f"{npc_query} is not here."
    if not npc.get("can_trade"):
        return f"{npc['name']} does not trade."
    q     = item_query.lower().strip()
    match = None
    for it in npc.get("inventory", []):
        idef = _find_item_def(it["item_id"])
        if idef and q in idef["name"].lower():
            match = it
            break
    if not match:
        return f"{npc['name']} does not have '{item_query}'."
    if match["qty"] <= 0:
        return f"{npc['name']} is out of that item."
    if player.get("gems", 0) < match["price"]:
        return f"Not enough gems. Need {match['price']}, have {player.get('gems',0)}."
    player["gems"] -= match["price"]
    idef = _find_item_def(match["item_id"])
    cat  = "weapon" if match["item_id"].startswith("wpn_") else ("armour" if match["item_id"].startswith("arm_") else "item")
    _save_player(group_id, user_id, player)
    _add_item_to_player(group_id, user_id, match["item_id"], idef["name"] if idef else match["item_id"], 1, cat)
    player = _get_player(group_id, user_id)
    return f"Bought {idef['name'] if idef else match['item_id']} from {npc['name']} for {match['price']} gems. Balance: {player.get('gems',0)}"

# ─────────────────────────────────────────────────────────────────────────────
# FISHING & HUNTING
# ─────────────────────────────────────────────────────────────────────────────
def cmd_fish(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    loc = get_location(player.get("location", ""))
    if not loc or not loc.get("has_water"):
        return f"No water here. Travel to a water location first.\n(You are at {player.get('location','?')})"
    now = time.time()
    if now < player.get("cd_fish", 0):
        rem = int(player["cd_fish"] - now); m, s = divmod(rem, 60)
        return f"Line still drying. Try in {m}m {s}s."
    items_db  = load_items()
    loc_name  = loc["name"]
    pool      = [f for f in items_db["fish"]
                 if "any_water" in f.get("locations", []) or loc_name in f.get("locations", [])]
    if not pool:
        pool = items_db["fish"][:3]
    luck    = player.get("luck", STARTING_LUCK)
    weights = []
    for f in pool:
        base = {"common":60,"uncommon":25,"rare":10,"epic":3,"legendary":1}.get(f.get("rarity","common"),30)
        weights.append(max(1, base + (luck - 5)))
    caught = random.choices(pool, weights=weights, k=1)[0]
    qty    = random.randint(1, 2)
    xp     = {"common":5,"uncommon":10,"rare":20,"epic":40,"legendary":100}.get(caught.get("rarity","common"),5)
    _add_item_to_player(group_id, user_id, caught["id"], caught["name"], qty, "fish")
    player = _get_player(group_id, user_id)
    player["cd_fish"] = now + FISH_COOLDOWN
    player, levelled  = _give_xp(player, xp)
    _save_player(group_id, user_id, player)
    emoji = {"common":"Fish","uncommon":"Fish","rare":"Rare fish","epic":"Epic fish","legendary":"LEGENDARY FISH"}.get(caught.get("rarity","common"),"Fish")
    lvl_s = f" LEVEL UP! Lv{player['level']}!" if levelled else ""
    return (f"{emoji}: {caught['name']} x{qty} ({caught['rarity'].capitalize()})\n"
            f"Worth {caught['sell_value']*qty} gems if sold. +{xp} XP.{lvl_s}\n"
            f"HP: {player['hp']}/{player['max_hp']} | Lv{player.get('level',1)}")

def cmd_hunt(group_id, user_id, name):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    loc = get_location(player.get("location", ""))
    if not loc or not loc.get("has_forest"):
        return "Nothing to hunt here. Travel to a forest location first."
    now = time.time()
    if now < player.get("cd_hunt", 0):
        rem = int(player["cd_hunt"] - now); m, s = divmod(rem, 60)
        return f"Need to rest before hunting. Try in {m}m {s}s."
    danger = loc.get("danger", 1)
    if random.random() < 0.08 + danger * 0.045:
        enemies = get_enemies_for_danger(danger)
        if enemies:
            enemy = random.choice(enemies)
            _start_pve_combat(group_id, user_id, player, enemy)
            player["in_combat"] = True; player["combat_key"] = _pk(group_id, user_id)
            player["cd_hunt"]   = now + HUNT_COOLDOWN
            _save_player(group_id, user_id, player)
            return (f"{name} ventures into {loc['name']}...\n"
                    f"A wild {enemy['name']} attacks! HP:{enemy['hp']} ATK:{enemy['atk']}\n"
                    f"Use #fight <attack> or #flee. Attacks: {', '.join(_get_available_attacks(player))}")
    items_db = load_items()
    pool     = items_db.get("hunt_drops", [])
    luck     = player.get("luck", STARTING_LUCK)
    drops    = random.choices(pool, k=random.randint(1, max(1, 1 + luck // 5)))
    xp_gain  = 0
    loot_lines = []
    for drop in drops:
        _add_item_to_player(group_id, user_id, drop["id"], drop["name"], 1, "material")
        xp_gain += 10
        loot_lines.append(f"  - {drop['name']} ({drop.get('sell_value',0)} gems)")
    player = _get_player(group_id, user_id)
    player["cd_hunt"] = now + HUNT_COOLDOWN
    player, levelled  = _give_xp(player, xp_gain)
    _save_player(group_id, user_id, player)
    lvl_s = f"\nLEVEL UP! Lv{player['level']}!" if levelled else ""
    return (f"{name} hunts in {loc['name']}...\n"
            + "\n".join(loot_lines) +
            f"\n+{xp_gain} XP | Lv{player.get('level',1)}{lvl_s}")

# ─────────────────────────────────────────────────────────────────────────────
# SELL
# ─────────────────────────────────────────────────────────────────────────────
def cmd_sell(group_id, user_id, name, args):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    if not args:
        return "Usage: #sell <item name> [qty]"
    parts = args.split()
    qty = 1
    if parts and parts[-1].isdigit():
        qty = max(1, int(parts[-1]))
        item_query = " ".join(parts[:-1]).lower()
    else:
        item_query = args.lower()
    items = player.get("items", [])
    match = next((it for it in items if item_query in it["name"].lower()), None)
    if not match:
        return f"No '{item_query}' in inventory."
    qty   = min(qty, match.get("qty", 0))
    idef  = _find_item_def(match.get("item_id", ""))
    sv    = idef.get("sell_value", 0) if idef else 0
    total = sv * qty
    match["qty"] -= qty
    if match["qty"] <= 0:
        items.remove(match)
    player["items"] = items
    player["gems"]  = player.get("gems", 0) + total
    _save_player(group_id, user_id, player)
    return (f"Sold {qty}x {match['name']} for {total} gems!\n"
            f"Balance: {player['gems']} gems (+ {_gems_in_chests(player)} in chests)")

# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _add_item_to_player(group_id, user_id, item_id, item_name, qty, category):
    player = _get_player(group_id, user_id)
    if not player:
        return
    if category in ("weapon", "wpn") or item_id.startswith("wpn_"):
        weapons = player.get("weapons", [])
        for slot in weapons:
            if slot["item_id"] == item_id:
                idef = _find_item_def(item_id)
                if idef and idef.get("stackable"):
                    slot["qty"] = min(slot.get("qty", 1) + qty, idef.get("max_stack", 1))
                _save_player(group_id, user_id, player)
                return
        if len(weapons) < WEAPON_SLOTS:
            weapons.append({"item_id": item_id, "name": item_name, "qty": qty, "equipped": False})
            player["weapons"] = weapons
    else:
        items = player.get("items", [])
        for slot in items:
            if slot.get("item_id") == item_id:
                slot["qty"] = slot.get("qty", 0) + qty
                player["items"] = items
                _save_player(group_id, user_id, player)
                return
        items.append({"item_id": item_id, "name": item_name, "qty": qty, "category": category})
        player["items"] = items
    _save_player(group_id, user_id, player)

def _gems_in_chests(player):
    return sum(ch.get("stored_gems", 0) for ch in player.get("chests", []))

def _inventory_summary(player):
    lines = []
    weapons = player.get("weapons", [])
    if weapons:
        lines.append("Weapons:")
        for w in weapons:
            eq = " [E]" if w.get("equipped") else ""
            lines.append(f"  - {w['name']} x{w.get('qty',1)}{eq}")
    armour = player.get("armour", {})
    if armour:
        lines.append("Armour:")
        for slot, aid in armour.items():
            idef = _find_item_def(aid)
            lines.append(f"  - {idef['name'] if idef else aid} ({slot})")
    items = player.get("items", [])
    if items:
        cats = {}
        for it in items:
            cats.setdefault(it.get("category", "misc"), []).append(it)
        for cat, itms in cats.items():
            lines.append(f"{cat.capitalize()}:")
            for it in itms:
                lines.append(f"  - {it['name']} x{it.get('qty',1)}")
    chests = player.get("chests", [])
    if chests:
        lines.append("Chests:")
        for ch in chests:
            lines.append(f"  - {ch.get('name','Chest')} - {ch.get('stored_gems',0)}/{ch.get('capacity',0)} gems")
    if not weapons and not items and not chests and not armour:
        lines.append("  (empty)")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# COIN FLIP
# ─────────────────────────────────────────────────────────────────────────────
def cmd_coin(group_id, user_id, name, side, amount_str):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    now = time.time()
    if now < player.get("cd_coin", 0):
        return f"Coin still spinning! Try in {int(player['cd_coin']-now)}s."
    side = side.lower()
    if side not in ("h", "t", "heads", "tails"):
        return "Usage: #coin <h/t> <amount>"
    gems = player.get("gems", 0)
    if amount_str.lower() in ("all", "allin"):
        bet = gems
    else:
        try:
            bet = int(amount_str)
        except ValueError:
            return "Amount must be a number."
    if bet <= 0:
        return "Bet must be positive."
    if bet > gems:
        return f"Only have {gems} gems."
    chosen_heads = side in ("h", "heads")
    result_heads = random.random() < 0.5
    win = chosen_heads == result_heads
    res_s = "Heads" if result_heads else "Tails"
    cho_s = "Heads" if chosen_heads else "Tails"
    if win:
        player["gems"] = gems + bet
        outcome = f"WIN! +{bet} gems -> {player['gems']}"
    else:
        player["gems"] = gems - bet
        outcome = f"LOSS! -{bet} gems -> {player['gems']}"
    player["cd_coin"] = now + COIN_COOLDOWN
    _save_player(group_id, user_id, player)
    return f"{name} flips the coin!\nCalled: {cho_s} | Result: {res_s}\n{outcome}"

# ─────────────────────────────────────────────────────────────────────────────
# SHOP / BUY / GIVE / CHEST
# ─────────────────────────────────────────────────────────────────────────────
def cmd_shop(group_id, user_id, name):
    shop  = load_shop()
    items = shop.get("items", [])
    if not items:
        return "The shop is empty! An admin can add items via the control panel."
    lines = ["SHOP:"]
    for i, it in enumerate(items, 1):
        cap = f" [Holds {it['capacity']} gems]" if it.get("category") == "chest" else ""
        lines.append(f"  {i}. {it['name']} - {it['cost']} gems{cap}")
    lines.append("\nUse #buy <item name> to purchase.")
    return "\n".join(lines)

def cmd_buy(group_id, user_id, name, item_query):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    shop  = load_shop()
    q     = item_query.lower().strip()
    match = next((it for it in shop.get("items", []) if q in it["name"].lower()), None)
    if not match:
        return f"No shop item matching '{item_query}'."
    cost = match.get("cost", 0)
    if player.get("gems", 0) < cost:
        return f"Need {cost} gems, have {player.get('gems',0)}."
    player["gems"] -= cost
    if match.get("category") == "chest":
        player.setdefault("chests", []).append({
            "chest_id":    f"ch_{int(time.time())}_{user_id}",
            "name":        match["name"],
            "tier":        match.get("tier", "standard"),
            "capacity":    match.get("capacity", 100),
            "stored_gems": 0,
            "stored_items": [],
        })
        _save_player(group_id, user_id, player)
        return f"Bought {match['name']} for {cost} gems! Use #chest store <n>. Remaining: {player['gems']}"
    _save_player(group_id, user_id, player)
    return f"Bought {match['name']} for {cost} gems! Remaining: {player['gems']}"

def cmd_give_gems(group_id, from_id, from_name, target_name, amount_str):
    from_p = _get_player(group_id, from_id)
    if not from_p:
        return "Register first with #beginpoints."
    try:
        amount = int(amount_str)
    except ValueError:
        return "Amount must be a number."
    if amount <= 0:
        return "Amount must be positive."
    if from_p.get("gems", 0) < amount:
        return f"Only have {from_p.get('gems',0)} gems."
    players = _load_players()
    tgt_key = tgt_p = None
    for k, p in players.items():
        if k.startswith(f"{group_id}:") and target_name.lower() in p.get("name", "").lower():
            tgt_key = k; tgt_p = p; break
    if not tgt_p:
        return f"Player '{target_name}' not found."
    with _game_lock:
        players = _load_players()
        fp = players.get(_pk(group_id, from_id))
        tp = players.get(tgt_key)
        if not fp or not tp:
            return "Error."
        if fp.get("gems", 0) < amount:
            return f"Only have {fp.get('gems',0)} gems."
        fp["gems"] -= amount
        tp["gems"]  = tp.get("gems", 0) + amount
        players[_pk(group_id, from_id)] = fp
        players[tgt_key] = tp
        _save_players(players)
    return f"{from_name} gave {amount} gems to {tgt_p.get('name','?')}!"

def cmd_chest(group_id, user_id, name, subcmd, args):
    player = _get_player(group_id, user_id)
    if not player:
        return "Register first with #beginpoints."
    chests = player.get("chests", [])
    sub    = (subcmd or "list").lower()
    if sub == "list":
        if not chests:
            return "No chests. Buy one from #shop!"
        lines = ["Your Chests:"]
        for i, ch in enumerate(chests, 1):
            lines.append(f"  {i}. {ch.get('name','Chest')} - {ch.get('stored_gems',0)}/{ch.get('capacity',0)} gems")
        return "\n".join(lines)
    parts = args.split() if args else []
    try:
        amount = int(parts[0]) if parts else 0
    except ValueError:
        return "Usage: #chest store/take <amount> [chest#]"
    ci = 0
    if len(parts) >= 2:
        try:
            ci = int(parts[1]) - 1
        except ValueError:
            pass
    if not chests:
        return "No chests. Buy one from #shop!"
    ci = max(0, min(ci, len(chests) - 1))
    ch = chests[ci]
    if sub == "store":
        space  = ch["capacity"] - ch.get("stored_gems", 0)
        actual = min(amount, player.get("gems", 0), space)
        if actual <= 0:
            return f"{ch['name']} is full or you have no gems."
        ch["stored_gems"] = ch.get("stored_gems", 0) + actual
        player["gems"] -= actual; player["chests"] = chests
        _save_player(group_id, user_id, player)
        return f"Stored {actual} gems in {ch['name']}. ({ch['stored_gems']}/{ch['capacity']})"
    if sub == "take":
        actual = min(amount, ch.get("stored_gems", 0))
        if actual <= 0:
            return f"{ch['name']} has no gems."
        ch["stored_gems"] -= actual
        player["gems"] = player.get("gems", 0) + actual
        player["chests"] = chests
        _save_player(group_id, user_id, player)
        return f"Withdrew {actual} gems. ({ch['stored_gems']}/{ch['capacity']}) | Pocket: {player['gems']}"
    return "Usage: #chest list / #chest store <n> / #chest take <n>"

# ─────────────────────────────────────────────────────────────────────────────
# STATS / PROFILE / LEADERBOARD / LOCATION
# ─────────────────────────────────────────────────────────────────────────────
def cmd_stats(group_id, user_id, name):
    p = _get_player(group_id, user_id)
    if not p:
        return "Register first with #beginpoints."
    xp_need = _xp_for_level(p.get("level", 1))
    filled  = int((p.get("xp", 0) / xp_need) * 10)
    xp_bar  = "#" * filled + "-" * (10 - filled)
    loc_str = (f"Travelling to {p['travelling_to']}" if p.get("travelling_to")
               else p.get("location", "?"))
    eq_name = "None"
    if p.get("equipped_weapon"):
        idef = _find_item_def(p["equipped_weapon"])
        eq_name = idef["name"] if idef else p["equipped_weapon"]
    combat_s = " [IN COMBAT]" if p.get("in_combat") else ""
    return (f"--- {p.get('name','?')} ---{combat_s}\n"
            f"Level {p.get('level',1)} | XP [{xp_bar}] {p.get('xp',0)}/{xp_need}\n"
            f"Gems: {p.get('gems',0)} (+ {_gems_in_chests(p)} in chests)\n"
            f"HP: {p.get('hp',0)}/{p.get('max_hp',0)}  Mana: {p.get('mana',0)}/{p.get('max_mana',0)}\n"
            f"ATK:{p.get('atk',0)}  DEF:{p.get('def',0)}  SPD:{p.get('spd',0)}\n"
            f"Luck:{p.get('luck',0)}  Weight:{p.get('weight',0)}  Size:{p.get('size',0)}\n"
            f"Weapon: {eq_name}\n"
            f"Clicker: +{CLICKER_GEMS_PER_TICK} gem/30s\n"
            f"Location: {loc_str}\n"
            f"Time: {game_time_str()}")

def cmd_gems(group_id, user_id, name):
    p = _get_player(group_id, user_id)
    if not p:
        return "Register first with #beginpoints."
    return (f"{p.get('name','?')}'s Gems\n"
            f"  Pocket: {p.get('gems',0)}\n"
            f"  In chests: {_gems_in_chests(p)}\n"
            f"  Total: {p.get('gems',0)+_gems_in_chests(p)}")

def cmd_leaderboard(group_id):
    players = _load_players()
    gp = {k: v for k, v in players.items() if k.startswith(f"{group_id}:")}
    if not gp:
        return "No players yet. Use #beginpoints to join."
    ranked = sorted(gp.values(), key=lambda p: p.get("gems", 0) + _gems_in_chests(p), reverse=True)
    lines  = ["Gem Leaderboard:"]
    medals = ["1st", "2nd", "3rd"]
    for i, p in enumerate(ranked[:LEADERBOARD_SIZE]):
        m = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"  {m} {p.get('name','?')} - {p.get('gems',0)+_gems_in_chests(p)} gems (Lv{p.get('level',1)})")
    return "\n".join(lines)

def cmd_where(group_id, user_id, name):
    p = _get_player(group_id, user_id)
    if not p:
        return "Register first with #beginpoints."
    if p.get("travelling_to"):
        rem = max(0, int(p.get("travel_arrive", 0) - time.time()))
        m, s = divmod(rem, 60)
        return f"{p['name']} is travelling to {p['travelling_to']}. ETA: {m}m {s}s."
    loc = get_location(p.get("location", ""))
    if loc:
        conns     = ", ".join(loc.get("connections", []))
        npcs_here = get_npcs_at_location(loc["name"])
        npc_s     = "\nPeople here: " + ", ".join(n["name"] for n in npcs_here) if npcs_here else ""
        return (f"{p['name']} at {loc['name']} ({loc.get('region','?')})\n"
                f"Danger: {loc.get('danger',0)}/10  "
                f"Fish: {'Yes' if loc.get('has_water') else 'No'}  "
                f"Hunt: {'Yes' if loc.get('has_forest') else 'No'}\n"
                f"Travel to: {conns}{npc_s}")
    return f"{p['name']} at {p.get('location','?')}."

def cmd_locations(group_id, user_id, filter_str=""):
    p   = _get_player(group_id, user_id)
    cur = p.get("location", "?") if p else "?"
    world = load_world(); locs = world["locations"]
    if filter_str:
        f = filter_str.lower()
        locs = [l for l in locs if f in l["name"].lower() or f in l["region"].lower()]
    if not locs:
        return "No locations found."
    regions = {}
    for loc in locs:
        regions.setdefault(loc["region"], []).append(loc)
    lines = [f"World Locations (you: {cur})"]
    for region, rlocs in sorted(regions.items()):
        lines.append(f"\n-- {region} --")
        for loc in rlocs:
            mk = ">>" if loc["name"] == cur else ("City" if loc.get("is_city") else ("Village" if loc.get("is_village") else " "))
            dg = f" [D{loc.get('danger',0)}]" if loc.get("danger", 0) >= 3 else ""
            wt = " [Fish]" if loc.get("has_water") else ""
            ft = " [Hunt]" if loc.get("has_forest") else ""
            lines.append(f"  {mk} {loc['name']}{dg}{wt}{ft}")
    lines.append("\n[Fish]=fishing  [Hunt]=hunting  [DN]=danger level")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# FORECAST & DISASTERS
# ─────────────────────────────────────────────────────────────────────────────
WEATHER_TYPES = [
    ("Clear skies",    "Calm. Good for travel.", -2),
    ("Partly cloudy",  "Pleasant.",               0),
    ("Rainy",          "Fishing slightly better.", 1),
    ("Thunderstorm",   "Danger +1.",               2),
    ("Snowfall",       "Cold. Exposed areas ATK-1.",2),
    ("Dense fog",      "Hard to spot enemies.",     1),
    ("Windstorm",      "Dangerous heights. SPD-1.", 3),
    ("Scorching heat", "Mana regen slower.",        3),
]

DISASTERS = [
    ("Volcanic tremors rattle the ground! All players lose 5 HP.", 5, 0),
    ("A tidal surge floods low areas! Coastal players lose 8 HP.", 8, 0),
    ("A lightning strike hits! All players lose 10 HP.", 10, 0),
    ("A deadly windstorm! All players lose 3 HP and 5 gems.", 3, 5),
    ("A meteor shower! All players lose 12 HP.", 12, 0),
    ("Blizzard rolls in! All players lose 6 HP.", 6, 0),
    ("Wildfire! Forest players take 15 HP damage.", 15, 0),
    ("Plague of giant spiders! All players lose 4 HP.", 4, 0),
]

def cmd_forecast(group_id, user_id):
    p   = _get_player(group_id, user_id)
    loc = get_location(p.get("location", "")) if p else None
    gt  = game_time_now()
    day_seed = gt.year * 1000 + gt.timetuple().tm_yday
    w_name, w_desc, w_mod = random.Random(day_seed).choice(WEATHER_TYPES)
    base  = loc.get("danger", 0) if loc else 0
    eff   = min(10, base + w_mod)
    night = "\nNight in-game. Monsters more active." if is_game_night() else ""
    loc_s = f" at {loc['name']}" if loc else ""
    return (f"Daily Forecast{loc_s}:\n{w_name} - {w_desc}\n"
            f"Effective danger{loc_s}: {eff}/10\n"
            f"Game time: {game_time_str()}{night}")

def maybe_trigger_disaster(group_id):
    if random.random() > DISASTER_CHANCE:
        return None
    desc, hp_loss, gem_loss = random.choice(DISASTERS)
    with _game_lock:
        players = _load_players()
        affected = []
        for key, p in players.items():
            if not key.startswith(f"{group_id}:"):
                continue
            p["hp"]   = max(1, p.get("hp", 1) - hp_loss)
            p["gems"] = max(0, p.get("gems", 0) - gem_loss)
            affected.append(p.get("name", "?"))
        _save_players(players)
    if not affected:
        return None
    return f"DISASTER! {desc}\nAffected: {', '.join(affected)}"

# ─────────────────────────────────────────────────────────────────────────────
# REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────
def cmd_beginpoints(group_id, user_id, name):
    if _get_player(group_id, user_id):
        p = _get_player(group_id, user_id)
        return f"{p.get('name','?')}, you're already registered! Use #stats to view your character."
    player = _new_player(name)
    _save_player(group_id, user_id, player)
    return (f"Welcome to the Realm of Aethermoor, {name}!\n"
            f"Your adventure begins at Hearthstone Village.\n"
            f"HP: {STARTING_HP}/{STARTING_MAX_HP}  Mana: {STARTING_MANA}/{STARTING_MAX_MANA}\n"
            f"Gems: 0  |  Clicker: Active (+1 gem/30s)\n"
            f"#stats - character sheet\n"
            f"#help points start - quick guide\n"
            f"#locations - see the world")

# ─────────────────────────────────────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────────────────────────────────────
HELP_SECTIONS = {
    "points": (
        "--- AETHERMOOR RPG ---\n"
        "#help points start   - How to begin\n"
        "#help points basics  - Core commands\n"
        "#help points travel  - Moving around\n"
        "#help points combat  - Fighting\n"
        "#help points items   - Inventory & gathering\n"
        "#help points gems    - Currency\n"
        "#help points chests  - Safe storage\n"
        "#help points stats   - Character stats\n"
        "#help points npcs    - Talking to people"
    ),
    "points start": (
        "Getting Started:\n"
        "1. #beginpoints - register (once only)\n"
        "2. #stats - view your character\n"
        "3. #locations / #map - see the world\n"
        "4. #go <location> - travel somewhere\n"
        "5. #fih (near water) or #hunt (in forests)\n"
        "6. #sell <item> - sell loot for gems\n"
        "7. #shop / #buy - spend gems\n"
        "8. #chest store - keep gems safe"
    ),
    "points basics": (
        "Basic Commands:\n"
        "  #stats / #me        - character sheet\n"
        "  #gems               - gem balance\n"
        "  #inventory / #inv   - your items\n"
        "  #where              - current location\n"
        "  #locations          - world map (text)\n"
        "  #map                - world map (image)\n"
        "  #forecast           - daily weather\n"
        "  #leaderboard / #lb  - top players\n"
        "  #shop               - browse shop\n"
        "  #buy <item>         - buy from shop\n"
        "  #give @user <n>     - give gems\n"
        "  #coin h/t <n>       - coin flip\n"
        "  #rest               - recover HP/Mana"
    ),
    "points travel": (
        "Travel:\n"
        "  #go <location>  - travel to a connected location\n"
        "  #where          - current location + ETA\n"
        "  #locations      - all locations with danger ratings\n"
        "Arriving at dangerous zones may trigger monster encounters!\n"
        "[Fish]=fishing  [Hunt]=hunting  [DN]=danger level"
    ),
    "points combat": (
        "Combat:\n"
        "  Monsters trigger during travel and hunting.\n"
        "  PvP: #attack @player (must be at same location)\n"
        "  #fight <attack>    - use an attack\n"
        "  #throw <item>      - throw an item\n"
        "  #cast <spell>      - use a mana spell\n"
        "  #flee              - try to escape\n"
        "  #equip <weapon>    - equip a weapon\n"
        "  #unequip           - unequip weapon\n"
        "  #attacks           - show available attacks\n"
        "  #spells            - list all spells\n"
        "Spells: heal, restore, fireball, zap, shield\n"
        "Default attacks: punch, kick\n"
        "Weapon attacks vary by weapon type."
    ),
    "points items": (
        "Items & Gathering:\n"
        "  #fih           - fish (water locations, 3min CD)\n"
        "  #hunt           - hunt (forest locations, 5min CD)\n"
        "  #sell <item>    - sell items for gems\n"
        "  #sell <item> N  - sell N of item\n"
        "  #inventory      - view items\n"
        "  #equip <weapon> - equip weapon\n"
        "Fish: Common to Uncommon to Rare to Epic to Legendary\n"
        "Hunt: Meat, pelts, claws, feathers, and more\n"
        "Higher luck = better drops."
    ),
    "points gems": (
        "Gems (Currency):\n"
        "  Earned: selling loot, clicker, combat wins, coin flips\n"
        "  Spent:  shop items, NPC trades\n"
        "  #gems        - check balance\n"
        "  #give @u N   - transfer gems\n"
        "  #coin h/t N  - gamble\n"
        "  #chest store - lock gems safely\n"
        "Pocket gems can be lost on defeat (10%)!\n"
        "Chests protect gems. Chests cannot hold other chests."
    ),
    "points chests": (
        "Chests (Safe Storage):\n"
        "  Buy from #shop - 50/100/250/500/1000 gem capacity\n"
        "  Cost is roughly 1/3 of capacity.\n"
        "  #chest list       - show your chests\n"
        "  #chest store <n>  - store n gems\n"
        "  #chest store <n> <#> - store in chest #\n"
        "  #chest take <n>   - withdraw gems\n"
        "  #chest take <n> <#>  - from chest #"
    ),
    "points stats": (
        "Character Stats:\n"
        "  HP    - Health. Falls to 1 on defeat, lose 10% gems.\n"
        "  Mana  - Used for spells.\n"
        "  ATK   - Attack power.\n"
        "  DEF   - Reduces damage taken.\n"
        "  SPD   - Affects flee chance and turn order.\n"
        "  Luck  - Improves drops, crits, and event chances.\n"
        "  Weight - Carry capacity.\n"
        "  Size   - Affects hit/dodge.\n"
        "All stats increase on level up automatically."
    ),
    "points npcs": (
        "NPC Interaction:\n"
        "  #talk               - see who is here\n"
        "  #talk <name>        - speak with an NPC\n"
        "  #trade <name>       - see an NPC shop\n"
        "  #buyfrom <npc> <item> - buy from NPC\n"
        "NPCs have personalities and know different things about you.\n"
        "Some trade, some give info, some react to your level and gear."
    ),
}

def cmd_help(args):
    key = args.strip().lower() if args else ""
    if key in HELP_SECTIONS:
        return HELP_SECTIONS[key]
    if key:
        return f"Unknown section '{key}'.\n" + HELP_SECTIONS["points"]
    return HELP_SECTIONS["points"]

# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND TICK
# ─────────────────────────────────────────────────────────────────────────────
def tick_group(group_id):
    messages = list(check_arrivals(group_id))
    disaster = maybe_trigger_disaster(group_id)
    if disaster:
        messages.append(disaster)
    return messages

# ─────────────────────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────
def handle_message(group_id, user_id, name, text):
    if not text:
        return None
    t     = text.strip()
    lo    = t.lower()
    parts = t.split()
    cmd   = parts[0].lower() if parts else ""

    if cmd == "#beginpoints":
        return cmd_beginpoints(group_id, user_id, name)
    if cmd in ("#stats", "#me", "#profile"):
        return cmd_stats(group_id, user_id, name)
    if cmd in ("#gems", "#bal", "#balance"):
        return cmd_gems(group_id, user_id, name)
    if cmd in ("#inventory", "#inv", "#bag"):
        p = _get_player(group_id, user_id)
        if not p:
            return "Register first with #beginpoints."
        return f"{p.get('name','?')}'s Inventory:\n" + _inventory_summary(p)
    if cmd in ("#where", "#location", "#loc"):
        return cmd_where(group_id, user_id, name)
    if cmd == "#go" and len(parts) >= 2:
        return start_travel(group_id, user_id, " ".join(parts[1:]))[1]
    if cmd == "#locations":
        return cmd_locations(group_id, user_id, " ".join(parts[1:]))
    if lo == "#map":
        if _upload_fn:
            img = render_map_with_players(group_id)
            if img:
                _upload_fn(group_id, img, f"Realm of Aethermoor - {game_time_str()}")
                return None
        return cmd_locations(group_id, user_id)
    if cmd == "#fih":
        return cmd_fish(group_id, user_id, name)
    if cmd == "#hunt":
        return cmd_hunt(group_id, user_id, name)
    if cmd == "#sell":
        return cmd_sell(group_id, user_id, name, " ".join(parts[1:]))
    if cmd in ("#shop", "#store"):
        return cmd_shop(group_id, user_id, name)
    if cmd == "#buy" and len(parts) >= 2:
        return cmd_buy(group_id, user_id, name, " ".join(parts[1:]))
    if cmd == "#chest":
        subcmd = parts[1] if len(parts) > 1 else "list"
        args   = " ".join(parts[2:]) if len(parts) > 2 else ""
        return cmd_chest(group_id, user_id, name, subcmd, args)
    if cmd == "#give" and len(parts) >= 3:
        return cmd_give_gems(group_id, user_id, name, parts[1].lstrip("@"), parts[2])
    if cmd == "#coin" and len(parts) >= 3:
        return cmd_coin(group_id, user_id, name, parts[1], parts[2])
    if cmd in ("#leaderboard", "#lb", "#top"):
        return cmd_leaderboard(group_id)
    if cmd in ("#forecast", "#weather", "#daily"):
        return cmd_forecast(group_id, user_id)
    if cmd == "#rest":
        return cmd_rest(group_id, user_id, name)
    if cmd == "#equip" and len(parts) >= 2:
        return cmd_equip(group_id, user_id, name, " ".join(parts[1:]))
    if cmd == "#unequip":
        return cmd_unequip(group_id, user_id, name)
    if cmd == "#fight":
        return cmd_fight(group_id, user_id, name, parts[1] if len(parts) > 1 else "punch")
    if cmd == "#flee":
        return cmd_flee(group_id, user_id, name)
    if cmd == "#throw" and len(parts) >= 2:
        return cmd_throw(group_id, user_id, name, " ".join(parts[1:]))
    if cmd == "#cast" and len(parts) >= 2:
        return cmd_cast(group_id, user_id, name, parts[1])
    if cmd == "#attack" and len(parts) >= 2:
        return cmd_attack_player(group_id, user_id, name, parts[1].lstrip("@"))
    if cmd == "#spells":
        lines = ["Available Spells:"]
        for sn, sd in SPELLS.items():
            lines.append(f"  {sn} - {sd['cost']} mana: {sd['description']}")
        return "\n".join(lines)
    if cmd == "#attacks":
        p = _get_player(group_id, user_id)
        if not p:
            return "Register first with #beginpoints."
        return f"Available attacks: {', '.join(_get_available_attacks(p))}"
    if cmd == "#talk":
        return cmd_talk(group_id, user_id, name, " ".join(parts[1:]) if len(parts) > 1 else "")
    if cmd == "#trade" and len(parts) >= 2:
        return cmd_trade(group_id, user_id, name, " ".join(parts[1:]))
    if cmd == "#buyfrom" and len(parts) >= 3:
        return cmd_buyfrom(group_id, user_id, name, parts[1], " ".join(parts[2:]))
    if lo.startswith("#help points"):
        section = t[12:].strip()
        return cmd_help(("points " + section).strip() if section else "points")
    return None