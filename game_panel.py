"""
game_panel.py  —  Aethermoor RPG Game Control Panel
=====================================================
A standalone Tkinter window launched from the main AI-FSY control panel.
Closing this window does NOT stop the bot.
Closing the main panel DOES stop the bot (and this window with it).

Tabs:
  1. Dashboard  — live leaderboard, summary stats, player selector
  2. Player      — selected player's full stats, gems, inventory, combat state
  3. World Map   — live Pillow-rendered map with player dots, auto-refreshes
  4. Shop        — view and edit the global shop (add/remove items)
  5. World       — view all locations, danger levels, NPC list
  6. Enemies     — view full enemy database
  7. Settings    — tweak game constants (disaster chance, clicker rate, etc.)
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import threading
import time
import os
import json

# ── These are set by open_game_panel() before the window is created ──────────
_game_engine  = None   # the imported game_engine module
_script_dir   = None   # base directory for game_data

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT  (called from AI-FSY.py)
# ─────────────────────────────────────────────────────────────────────────────
def open_game_panel(game_engine_module, script_dir):
    """
    Launch the game panel in a background thread so it doesn't block the bot.
    Safe to call multiple times — only one window will be open at a time.
    """
    global _game_engine, _script_dir
    _game_engine = game_engine_module
    _script_dir  = script_dir

    def _run():
        try:
            win = GamePanel()
            win.run()
        except Exception as e:
            print(f"[game_panel] Error: {e}")
            import traceback; traceback.print_exc()

    t = threading.Thread(target=_run, daemon=True, name="GamePanel")
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _data_path(filename):
    return os.path.join(_script_dir, "game_data", filename)

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
        print(f"[game_panel] Write error {path}: {e}")

def _all_players():
    """Return players dict from game_data/players.json."""
    return _read_json(_data_path("players.json")) or {}

def _save_players(players):
    _write_json(_data_path("players.json"), players)

def _gems_in_chests(player):
    return sum(ch.get("stored_gems", 0) for ch in player.get("chests", []))

def _total_gems(player):
    return player.get("gems", 0) + _gems_in_chests(player)

# ─────────────────────────────────────────────────────────────────────────────
# COLOUR PALETTE
# ─────────────────────────────────────────────────────────────────────────────
BG        = "#1e1e2e"
BG2       = "#2a2a3e"
BG3       = "#313145"
ACCENT    = "#c084fc"
ACCENT2   = "#818cf8"
GREEN     = "#4ade80"
RED       = "#f87171"
ORANGE    = "#fb923c"
YELLOW    = "#facc15"
TEXT      = "#e2e8f0"
TEXT_DIM  = "#94a3b8"
BORDER    = "#3f3f5f"

BTN_STYLE = dict(relief="flat", padx=8, pady=4, cursor="hand2")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW CLASS
# ─────────────────────────────────────────────────────────────────────────────
class GamePanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("🏰 Aethermoor RPG — Game Panel")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)
        self.root.configure(bg=BG)

        # State
        self._selected_player_key = None
        self._players_cache       = {}
        self._live_var            = tk.BooleanVar(value=True)
        self._map_image_ref       = None   # keep Pillow PhotoImage alive
        self._map_refresh_ms      = 5000   # map auto-refresh interval

        self._build_ui()
        self._start_live_loop()

    def run(self):
        self.root.mainloop()

    # ─────────────────────────────────────────────────────────────────────────
    # UI SKELETON
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG2, pady=8, padx=16)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🏰  Aethermoor RPG — Game Panel",
                 font=("Helvetica", 14, "bold"), bg=BG2, fg=ACCENT).pack(side="left")

        self._clock_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._clock_var,
                 font=("Helvetica", 9), bg=BG2, fg=TEXT_DIM).pack(side="right")

        # Notebook
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook",          background=BG,  borderwidth=0)
        style.configure("TNotebook.Tab",      background=BG3, foreground=TEXT_DIM,
                        padding=[12, 5], font=("Helvetica", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", BG2)],
                  foreground=[("selected", ACCENT)])
        style.configure("Treeview",           background=BG2, foreground=TEXT,
                        fieldbackground=BG2, rowheight=22,
                        font=("Helvetica", 9))
        style.configure("Treeview.Heading",   background=BG3, foreground=ACCENT2,
                        font=("Helvetica", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT2)],
                  foreground=[("selected", "#000000")])
        style.configure("TScrollbar",         background=BG3, troughcolor=BG)
        style.configure("TSeparator",         background=BORDER)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=(4, 6))

        self._build_tab_dashboard(nb)
        self._build_tab_player(nb)
        self._build_tab_map(nb)
        self._build_tab_shop(nb)
        self._build_tab_world(nb)
        self._build_tab_enemies(nb)
        self._build_tab_settings(nb)

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 1 — DASHBOARD
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_dashboard(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  Dashboard  ")

        # Toolbar
        bar = tk.Frame(outer, bg=BG2, padx=10, pady=6)
        bar.pack(fill="x")
        tk.Label(bar, text="Live Player Dashboard", font=("Helvetica", 11, "bold"),
                 bg=BG2, fg=TEXT).pack(side="left")
        ttk.Checkbutton(bar, text="Auto-refresh", variable=self._live_var).pack(side="right", padx=4)
        tk.Button(bar, text="↻ Refresh", bg=ACCENT2, fg="#000", **BTN_STYLE,
                  command=self._refresh_all).pack(side="right", padx=4)
        self._dash_updated = tk.StringVar(value="")
        tk.Label(bar, textvariable=self._dash_updated, font=("Helvetica", 8),
                 bg=BG2, fg=TEXT_DIM).pack(side="right", padx=8)

        # Summary strip
        sbar = tk.Frame(outer, bg=BG3, pady=6, padx=10)
        sbar.pack(fill="x")
        self._sum_labels = {}
        for key, label in [("players","Players"),("total_gems","Total Gems"),
                            ("leader","Leader"),("leader_gems","Leader Gems"),
                            ("avg_level","Avg Level"),("in_combat","In Combat")]:
            col = tk.Frame(sbar, bg=BG3)
            col.pack(side="left", padx=16)
            tk.Label(col, text=label, font=("Helvetica", 8), bg=BG3, fg=TEXT_DIM).pack()
            v = tk.Label(col, text="—", font=("Helvetica", 11, "bold"), bg=BG3, fg=ACCENT)
            v.pack()
            self._sum_labels[key] = v

        # Leaderboard tree
        tree_frame = tk.Frame(outer, bg=BG)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=4)

        sort_row = tk.Frame(tree_frame, bg=BG)
        sort_row.pack(fill="x", pady=(0, 3))
        tk.Label(sort_row, text="Sort by:", font=("Helvetica", 9),
                 bg=BG, fg=TEXT_DIM).pack(side="left")
        self._sort_var = tk.StringVar(value="gems")
        for val, lbl in [("gems","Gems"),("level","Level"),("name","Name"),("hp","HP")]:
            ttk.Radiobutton(sort_row, text=lbl, variable=self._sort_var,
                            value=val, command=self._refresh_table).pack(side="left", padx=3)

        cols = ("rank","name","level","gems","hp","location","status")
        vsb  = ttk.Scrollbar(tree_frame, orient="vertical")
        self._dash_tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                        yscrollcommand=vsb.set, height=18,
                                        selectmode="browse")
        vsb.configure(command=self._dash_tree.yview)
        vsb.pack(side="right", fill="y")
        self._dash_tree.pack(fill="both", expand=True)

        widths = {"rank":35,"name":140,"level":50,"gems":90,"hp":80,"location":170,"status":100}
        for c in cols:
            self._dash_tree.heading(c, text=c.capitalize())
            self._dash_tree.column(c, width=widths.get(c,80), anchor="center" if c!="name" and c!="location" else "w")

        self._dash_tree.tag_configure("gold",    background="#2d2a00", foreground=YELLOW)
        self._dash_tree.tag_configure("silver",  background="#1a1a2e", foreground=TEXT)
        self._dash_tree.tag_configure("bronze",  background="#2a1a0a", foreground=ORANGE)
        self._dash_tree.tag_configure("normal",  background=BG2,       foreground=TEXT)
        self._dash_tree.tag_configure("combat",  background="#2a0a0a", foreground=RED)
        self._dash_tree.tag_configure("travel",  background="#0a1a2a", foreground=ACCENT2)

        self._dash_tree.bind("<<TreeviewSelect>>", self._on_player_select)

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 2 — PLAYER DETAIL
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_player(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  Player  ")

        # Left — stats panel
        left = tk.Frame(outer, bg=BG2, width=340, padx=14, pady=12)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="Selected Player", font=("Helvetica", 10, "bold"),
                 bg=BG2, fg=ACCENT).pack(anchor="w")
        self._pl_name  = tk.Label(left, text="— none selected —", font=("Helvetica", 13, "bold"),
                                   bg=BG2, fg=TEXT)
        self._pl_name.pack(anchor="w", pady=(2, 6))

        # Stat rows
        self._pl_vars = {}
        stat_rows = [
            ("Level",    "level"),  ("XP",      "xp_str"),
            ("HP",       "hp_str"), ("Mana",    "mana_str"),
            ("ATK",      "atk"),    ("DEF",     "def"),
            ("SPD",      "spd"),    ("Luck",    "luck"),
            ("Gems",     "gems"),   ("Chests",  "chest_gems"),
            ("Total $",  "total"),  ("Location","location"),
            ("Weapon",   "weapon"), ("Armour",  "armour"),
            ("Status",   "status"),
        ]
        for label, key in stat_rows:
            row = tk.Frame(left, bg=BG2)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{label}:", font=("Helvetica", 9), width=10,
                     bg=BG2, fg=TEXT_DIM, anchor="w").pack(side="left")
            var = tk.StringVar(value="—")
            tk.Label(row, textvariable=var, font=("Helvetica", 9, "bold"),
                     bg=BG2, fg=TEXT, anchor="w").pack(side="left")
            self._pl_vars[key] = var

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)

        # Gem controls
        tk.Label(left, text="Gem Adjustment", font=("Helvetica", 9, "bold"),
                 bg=BG2, fg=ACCENT2).pack(anchor="w")
        gem_row = tk.Frame(left, bg=BG2)
        gem_row.pack(fill="x", pady=3)
        self._gem_adj_var = tk.StringVar()
        tk.Entry(gem_row, textvariable=self._gem_adj_var, width=10,
                 font=("Helvetica", 9), bg=BG3, fg=TEXT, insertbackground=TEXT,
                 relief="flat").pack(side="left", padx=(0,4))
        for txt, action, color in [("Add","Add",GREEN),("Remove","Remove",ORANGE),("Set","Set",ACCENT2),("Reset","Reset",RED)]:
            tk.Button(gem_row, text=txt, bg=color, fg="#000",
                      command=lambda a=action: self._gem_action(a),
                      **BTN_STYLE).pack(side="left", padx=1)
        self._gem_status = tk.Label(left, text="", font=("Helvetica", 8),
                                     bg=BG2, fg=GREEN, wraplength=300, justify="left")
        self._gem_status.pack(anchor="w")

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)

        # Stat controls
        tk.Label(left, text="Stat Override", font=("Helvetica", 9, "bold"),
                 bg=BG2, fg=ACCENT2).pack(anchor="w")
        so_row = tk.Frame(left, bg=BG2)
        so_row.pack(fill="x", pady=3)
        self._so_stat_var = tk.StringVar(value="hp")
        stat_options = ["hp","max_hp","mana","max_mana","atk","def","spd","luck","level","xp"]
        ttk.Combobox(so_row, textvariable=self._so_stat_var,
                     values=stat_options, width=9, state="readonly").pack(side="left", padx=(0,4))
        self._so_val_var = tk.StringVar()
        tk.Entry(so_row, textvariable=self._so_val_var, width=7,
                 bg=BG3, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=("Helvetica",9)).pack(side="left", padx=(0,4))
        tk.Button(so_row, text="Set", bg=ACCENT2, fg="#000",
                  command=self._set_stat, **BTN_STYLE).pack(side="left")
        self._so_status = tk.Label(left, text="", font=("Helvetica", 8),
                                    bg=BG2, fg=GREEN, wraplength=300, justify="left")
        self._so_status.pack(anchor="w")

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)

        # Teleport
        tk.Label(left, text="Teleport Player", font=("Helvetica", 9, "bold"),
                 bg=BG2, fg=ACCENT2).pack(anchor="w")
        tp_row = tk.Frame(left, bg=BG2)
        tp_row.pack(fill="x", pady=3)
        self._tp_var = tk.StringVar()
        tk.Entry(tp_row, textvariable=self._tp_var, width=20,
                 bg=BG3, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=("Helvetica",9)).pack(side="left", padx=(0,4))
        tk.Button(tp_row, text="TP", bg=ACCENT, fg="#000",
                  command=self._teleport_player, **BTN_STYLE).pack(side="left")

        # Right — inventory + combat
        right = tk.Frame(outer, bg=BG, padx=10, pady=10)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="Inventory", font=("Helvetica", 10, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w")

        inv_frame = tk.Frame(right, bg=BG)
        inv_frame.pack(fill="both", expand=True, pady=(4,0))

        inv_vsb = ttk.Scrollbar(inv_frame, orient="vertical")
        self._inv_tree = ttk.Treeview(inv_frame, columns=("slot","name","qty","category","value"),
                                       show="headings", yscrollcommand=inv_vsb.set, height=12)
        inv_vsb.configure(command=self._inv_tree.yview)
        inv_vsb.pack(side="right", fill="y")
        self._inv_tree.pack(fill="both", expand=True)

        for col, w in [("slot",40),("name",160),("qty",40),("category",90),("value",70)]:
            self._inv_tree.heading(col, text=col.capitalize())
            self._inv_tree.column(col, width=w, anchor="center" if col!="name" else "w")

        inv_btn = tk.Frame(right, bg=BG)
        inv_btn.pack(fill="x", pady=4)
        tk.Button(inv_btn, text="Remove Item", bg=RED, fg="#000",
                  command=self._remove_inv_item, **BTN_STYLE).pack(side="left", padx=2)
        tk.Button(inv_btn, text="Clear Combat", bg=ORANGE, fg="#000",
                  command=self._clear_combat, **BTN_STYLE).pack(side="left", padx=2)
        tk.Button(inv_btn, text="Restore Full HP/Mana", bg=GREEN, fg="#000",
                  command=self._restore_player, **BTN_STYLE).pack(side="left", padx=2)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        tk.Label(right, text="Chests", font=("Helvetica", 10, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w")
        chest_frame = tk.Frame(right, bg=BG)
        chest_frame.pack(fill="x", pady=(4,0))
        chest_vsb = ttk.Scrollbar(chest_frame, orient="vertical")
        self._chest_tree = ttk.Treeview(chest_frame, columns=("name","stored","capacity"),
                                         show="headings", yscrollcommand=chest_vsb.set, height=4)
        chest_vsb.configure(command=self._chest_tree.yview)
        chest_vsb.pack(side="right", fill="y")
        self._chest_tree.pack(fill="x", expand=True)
        for col, w in [("name",140),("stored",70),("capacity",70)]:
            self._chest_tree.heading(col, text=col.capitalize())
            self._chest_tree.column(col, width=w, anchor="center" if col!="name" else "w")

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 3 — WORLD MAP
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_map(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  World Map  ")

        bar = tk.Frame(outer, bg=BG2, padx=10, pady=6)
        bar.pack(fill="x")
        tk.Label(bar, text="Live World Map — Realm of Aethermoor",
                 font=("Helvetica", 11, "bold"), bg=BG2, fg=ACCENT).pack(side="left")

        self._map_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto-refresh (5s)", variable=self._map_auto_var).pack(side="right", padx=4)
        tk.Button(bar, text="↻ Refresh Map", bg=ACCENT2, fg="#000",
                  command=self._refresh_map, **BTN_STYLE).pack(side="right", padx=4)
        self._map_status = tk.Label(bar, text="", font=("Helvetica", 8),
                                     bg=BG2, fg=TEXT_DIM)
        self._map_status.pack(side="right", padx=8)

        # Scrollable canvas for the map
        map_frame = tk.Frame(outer, bg=BG)
        map_frame.pack(fill="both", expand=True)

        h_scroll = ttk.Scrollbar(map_frame, orient="horizontal")
        h_scroll.pack(side="bottom", fill="x")
        v_scroll = ttk.Scrollbar(map_frame, orient="vertical")
        v_scroll.pack(side="right", fill="y")

        self._map_canvas = tk.Canvas(map_frame, bg="#0a0a1a",
                                      xscrollcommand=h_scroll.set,
                                      yscrollcommand=v_scroll.set,
                                      highlightthickness=0)
        self._map_canvas.pack(fill="both", expand=True)
        h_scroll.configure(command=self._map_canvas.xview)
        v_scroll.configure(command=self._map_canvas.yview)

        # Mouse drag to pan
        self._map_canvas.bind("<ButtonPress-1>", self._map_drag_start)
        self._map_canvas.bind("<B1-Motion>",     self._map_drag_move)

        # Zoom
        self._map_scale   = 1.0
        self._map_drag_xy = None
        self._map_canvas.bind("<MouseWheel>", self._map_zoom)

        # Initial render
        self.root.after(300, self._refresh_map)
        self._schedule_map_refresh()

    def _map_drag_start(self, event):
        self._map_canvas.scan_mark(event.x, event.y)

    def _map_drag_move(self, event):
        self._map_canvas.scan_dragto(event.x, event.y, gain=1)

    def _map_zoom(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        self._map_scale = max(0.3, min(3.0, self._map_scale * factor))
        self._render_map_image()

    def _schedule_map_refresh(self):
        if self._map_auto_var.get():
            self._refresh_map()
        self.root.after(self._map_refresh_ms, self._schedule_map_refresh)

    def _refresh_map(self):
        """Render map with current player dots and display on canvas."""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self._map_status.config(text="Pillow not installed.")
            return

        map_base = os.path.join(_script_dir, "game_data", "world_map_base.png")
        if not os.path.exists(map_base):
            self._map_status.config(text="Map not generated yet. Start the bot first.")
            return

        self._render_map_image()
        self._map_status.config(text=f"Updated {time.strftime('%H:%M:%S')}")

    def _render_map_image(self):
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
            import io

            map_base = os.path.join(_script_dir, "game_data", "world_map_base.png")
            img = Image.open(map_base).copy()

            # Draw player dots directly (same logic as game_engine.render_map_with_players
            # but for ALL groups since this is the admin panel)
            draw    = Image.new("RGBA", img.size, (0,0,0,0))
            overlay = ImageDraw.Draw(draw)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11)
            except Exception:
                font = ImageFont.load_default()

            players  = _all_players()
            world    = _game_engine.load_world() if _game_engine else {"locations":[]}
            loc_map  = {loc["name"]: loc for loc in world.get("locations",[])}
            COLOURS  = [
                (255,80,80),(80,220,80),(80,130,255),(255,210,30),
                (200,80,255),(0,230,210),(255,140,30),(220,40,140),
                (40,220,130),(220,220,40),(255,120,120),(120,255,180),
            ]
            ci = 0
            location_counts = {}  # track how many players at each location for offset

            for key, p in players.items():
                loc_name = p.get("location", "")
                if p.get("travelling_to"):
                    loc_name = p.get("travelling_to", loc_name)
                loc = loc_map.get(loc_name)
                if not loc:
                    continue
                cx, cy = loc["coords"]
                count  = location_counts.get(loc_name, 0)
                location_counts[loc_name] = count + 1

                col = COLOURS[ci % len(COLOURS)]
                ci += 1
                ox  = (count % 4 - 1) * 18
                oy  = (count // 4) * -20 - 22

                # Draw coloured circle
                r = 8
                overlay.ellipse([(cx+ox-r, cy+oy-r),(cx+ox+r, cy+oy+r)],
                                fill=col + (220,), outline=(20,20,20,255))
                # Travelling indicator (ring)
                if p.get("travelling_to"):
                    overlay.ellipse([(cx+ox-r-3, cy+oy-r-3),(cx+ox+r+3, cy+oy+r+3)],
                                    outline=(255,255,100,200), width=2)
                # Name label
                nm = p.get("name","?")[:8]
                overlay.text((cx+ox - len(nm)*2, cy+oy+r+2), nm,
                             fill=col+(255,), font=font)

            img = img.convert("RGBA")
            img.alpha_composite(draw)
            img = img.convert("RGB")

            # Scale
            if self._map_scale != 1.0:
                nw = int(img.width  * self._map_scale)
                nh = int(img.height * self._map_scale)
                img = img.resize((nw, nh), Image.LANCZOS)

            photo = ImageTk.PhotoImage(img)
            self._map_image_ref = photo   # keep reference

            self._map_canvas.config(scrollregion=(0, 0, img.width, img.height))
            self._map_canvas.delete("all")
            self._map_canvas.create_image(0, 0, anchor="nw", image=photo)

            # Player count overlay text
            self._map_canvas.create_text(
                10, 10, anchor="nw",
                text=f"Players: {len(players)}  |  Zoom: {self._map_scale:.1f}x  |  "
                     f"Scroll to zoom, drag to pan",
                fill="white", font=("Helvetica", 9)
            )

        except Exception as e:
            self._map_status.config(text=f"Map error: {e}")
            import traceback; traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 4 — SHOP EDITOR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_shop(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  Shop  ")

        bar = tk.Frame(outer, bg=BG2, padx=10, pady=6)
        bar.pack(fill="x")
        tk.Label(bar, text="Global Shop Editor", font=("Helvetica", 11, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left")
        tk.Button(bar, text="↻ Reload", bg=ACCENT2, fg="#000",
                  command=self._shop_load, **BTN_STYLE).pack(side="right", padx=4)

        # Shop tree
        sf = tk.Frame(outer, bg=BG)
        sf.pack(fill="both", expand=True, padx=6, pady=4)

        vsb = ttk.Scrollbar(sf, orient="vertical")
        self._shop_tree = ttk.Treeview(sf,
            columns=("id","name","category","cost","capacity","description"),
            show="headings", yscrollcommand=vsb.set, height=14)
        vsb.configure(command=self._shop_tree.yview)
        vsb.pack(side="right", fill="y")
        self._shop_tree.pack(fill="both", expand=True)

        for col, w in [("id",100),("name",140),("category",80),
                        ("cost",60),("capacity",70),("description",280)]:
            self._shop_tree.heading(col, text=col.capitalize())
            self._shop_tree.column(col, width=w,
                                   anchor="w" if col in ("name","description","id") else "center")

        # Add / remove
        btn_row = tk.Frame(outer, bg=BG, padx=6, pady=6)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="+ Add Item", bg=GREEN, fg="#000",
                  command=self._shop_add, **BTN_STYLE).pack(side="left", padx=2)
        tk.Button(btn_row, text="− Remove Selected", bg=RED, fg="#000",
                  command=self._shop_remove, **BTN_STYLE).pack(side="left", padx=2)

        self._shop_status = tk.Label(outer, text="", font=("Helvetica", 9),
                                      bg=BG, fg=GREEN)
        self._shop_status.pack(anchor="w", padx=8)

        self._shop_load()

    def _shop_load(self):
        shop  = _game_engine.load_shop() if _game_engine else {"items":[]}
        items = shop.get("items", [])
        tree  = self._shop_tree
        tree.delete(*tree.get_children())
        for it in items:
            tree.insert("", "end", values=(
                it.get("id",""),
                it.get("name",""),
                it.get("category",""),
                it.get("cost",""),
                it.get("capacity",""),
                it.get("description",""),
            ))

    def _shop_add(self):
        dlg = ShopItemDialog(self.root)
        self.root.wait_window(dlg.top)
        if dlg.result:
            shop = _game_engine.load_shop() if _game_engine else {"items":[]}
            shop["items"].append(dlg.result)
            _game_engine.save_shop(shop)
            self._shop_load()
            self._shop_status.config(text=f"Added {dlg.result['name']}.", fg=GREEN)

    def _shop_remove(self):
        sel = self._shop_tree.selection()
        if not sel:
            return
        vals = self._shop_tree.item(sel[0], "values")
        item_id = vals[0]
        if not messagebox.askyesno("Remove Item", f"Remove '{vals[1]}' from the shop?"):
            return
        shop = _game_engine.load_shop() if _game_engine else {"items":[]}
        shop["items"] = [it for it in shop["items"] if it.get("id") != item_id]
        _game_engine.save_shop(shop)
        self._shop_load()
        self._shop_status.config(text=f"Removed {vals[1]}.", fg=ORANGE)

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 5 — WORLD / LOCATIONS
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_world(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  World  ")

        bar = tk.Frame(outer, bg=BG2, padx=10, pady=6)
        bar.pack(fill="x")
        tk.Label(bar, text="World Locations & NPCs", font=("Helvetica", 11, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left")

        panes = tk.PanedWindow(outer, orient="horizontal", bg=BG, sashwidth=4)
        panes.pack(fill="both", expand=True, padx=4, pady=4)

        # Locations tree
        loc_frame = tk.Frame(panes, bg=BG)
        panes.add(loc_frame, width=550)

        tk.Label(loc_frame, text="Locations", font=("Helvetica", 10, "bold"),
                 bg=BG, fg=ACCENT2).pack(anchor="w")
        lvsb = ttk.Scrollbar(loc_frame, orient="vertical")
        self._loc_tree = ttk.Treeview(loc_frame,
            columns=("name","region","danger","fish","hunt","city","village"),
            show="headings", yscrollcommand=lvsb.set)
        lvsb.configure(command=self._loc_tree.yview)
        lvsb.pack(side="right", fill="y")
        self._loc_tree.pack(fill="both", expand=True)

        for col, w in [("name",170),("region",110),("danger",55),
                        ("fish",40),("hunt",40),("city",40),("village",50)]:
            self._loc_tree.heading(col, text=col.capitalize())
            self._loc_tree.column(col, width=w, anchor="w" if col in("name","region") else "center")

        self._loc_tree.tag_configure("safe",   foreground=GREEN)
        self._loc_tree.tag_configure("danger", foreground=ORANGE)
        self._loc_tree.tag_configure("deadly", foreground=RED)

        self._loc_tree.bind("<<TreeviewSelect>>", self._on_loc_select)

        # NPC panel
        npc_frame = tk.Frame(panes, bg=BG)
        panes.add(npc_frame, width=360)

        tk.Label(npc_frame, text="NPCs at Location", font=("Helvetica", 10, "bold"),
                 bg=BG, fg=ACCENT2).pack(anchor="w")
        self._npc_text = tk.Text(npc_frame, bg=BG2, fg=TEXT, font=("Courier", 9),
                                  relief="flat", wrap="word", state="disabled")
        nvsb = ttk.Scrollbar(npc_frame, orient="vertical", command=self._npc_text.yview)
        self._npc_text.configure(yscrollcommand=nvsb.set)
        nvsb.pack(side="right", fill="y")
        self._npc_text.pack(fill="both", expand=True)

        self._populate_world()

    def _populate_world(self):
        if not _game_engine:
            return
        world = _game_engine.load_world()
        tree  = self._loc_tree
        tree.delete(*tree.get_children())
        for loc in world.get("locations", []):
            d = loc.get("danger", 0)
            tag = "safe" if d < 3 else ("deadly" if d >= 8 else "danger")
            tree.insert("", "end", tags=(tag,), values=(
                loc["name"], loc.get("region",""),
                f"{d}/10",
                "Y" if loc.get("has_water") else "",
                "Y" if loc.get("has_forest") else "",
                "Y" if loc.get("is_city") else "",
                "Y" if loc.get("is_village") else "",
            ))

    def _on_loc_select(self, event):
        sel = self._loc_tree.selection()
        if not sel:
            return
        vals  = self._loc_tree.item(sel[0], "values")
        loc_name = vals[0]
        npcs  = _game_engine.get_npcs_at_location(loc_name) if _game_engine else []

        txt = self._npc_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("end", f"NPCs at {loc_name}:\n\n")
        if not npcs:
            txt.insert("end", "  (none)\n")
        for npc in npcs:
            txt.insert("end", f"  {npc['name']}\n")
            txt.insert("end", f"    Trade: {'Yes' if npc.get('can_trade') else 'No'}\n")
            if npc.get("inventory"):
                for it in npc["inventory"][:4]:
                    idef = _game_engine._find_item_def(it["item_id"])
                    nm = idef["name"] if idef else it["item_id"]
                    txt.insert("end", f"      - {nm} x{it['qty']} @ {it['price']} gems\n")
            txt.insert("end", f"    Personality:\n")
            # Wrap personality to ~60 chars
            words = npc.get("personality","").split()
            line  = "      "
            for w in words:
                if len(line)+len(w) > 62:
                    txt.insert("end", line+"\n")
                    line = "      "
                line += w + " "
            if line.strip():
                txt.insert("end", line+"\n")
            txt.insert("end", "\n")
        txt.configure(state="disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 6 — ENEMIES
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_enemies(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  Enemies  ")

        bar = tk.Frame(outer, bg=BG2, padx=10, pady=6)
        bar.pack(fill="x")
        tk.Label(bar, text="Enemy Database", font=("Helvetica", 11, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left")

        ef = tk.Frame(outer, bg=BG)
        ef.pack(fill="both", expand=True, padx=6, pady=4)

        vsb = ttk.Scrollbar(ef, orient="vertical")
        self._enemy_tree = ttk.Treeview(ef,
            columns=("name","danger","hp","atk","def","xp","gems","drops"),
            show="headings", yscrollcommand=vsb.set)
        vsb.configure(command=self._enemy_tree.yview)
        vsb.pack(side="right", fill="y")
        self._enemy_tree.pack(fill="both", expand=True)

        for col, w in [("name",140),("danger",80),("hp",50),("atk",50),
                        ("def",50),("xp",60),("gems",90),("drops",220)]:
            self._enemy_tree.heading(col, text=col.capitalize())
            self._enemy_tree.column(col, width=w, anchor="w" if col in("name","drops") else "center")

        self._enemy_tree.tag_configure("low",    foreground=GREEN)
        self._enemy_tree.tag_configure("mid",    foreground=YELLOW)
        self._enemy_tree.tag_configure("high",   foreground=ORANGE)
        self._enemy_tree.tag_configure("elite",  foreground=RED)

        self._populate_enemies()

    def _populate_enemies(self):
        if not _game_engine:
            return
        enemies = _game_engine.load_enemies().get("enemies", [])
        tree    = self._enemy_tree
        tree.delete(*tree.get_children())
        for e in enemies:
            dmin = e.get("danger_min", 0)
            tag  = "low" if dmin <= 2 else ("mid" if dmin <= 4 else ("high" if dmin <= 7 else "elite"))
            drops = ", ".join(e.get("drops", []))
            tree.insert("", "end", tags=(tag,), values=(
                e["name"],
                f"{e['danger_min']}-{e['danger_max']}",
                e.get("hp",""),
                e.get("atk",""),
                e.get("def",""),
                e.get("xp",""),
                f"{e.get('gem_min',0)}-{e.get('gem_max',0)}",
                drops,
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 7 — SETTINGS
    # ─────────────────────────────────────────────────────────────────────────
    def _build_tab_settings(self, nb):
        outer = tk.Frame(nb, bg=BG)
        nb.add(outer, text="  Settings  ")

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb    = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        tab = tk.Frame(canvas, bg=BG, padx=20, pady=16)
        win = canvas.create_window((0,0), window=tab, anchor="nw")
        tab.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

        tk.Label(tab, text="Game Settings", font=("Helvetica", 13, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w")
        tk.Label(tab, text="Changes apply immediately (written to game_data constants file).",
                 font=("Helvetica", 9), bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0,12))

        self._setting_vars = {}
        settings = [
            ("CLICKER_GEMS_PER_TICK", "Gems per clicker tick",     "int",   "How many gems each player earns per 30s tick."),
            ("CLICKER_INTERVAL",      "Clicker interval (seconds)", "int",   "How often the clicker awards gems."),
            ("FISH_COOLDOWN",         "Fish cooldown (seconds)",    "int",   "Cooldown between !fih uses."),
            ("HUNT_COOLDOWN",         "Hunt cooldown (seconds)",    "int",   "Cooldown between #hunt uses."),
            ("COIN_COOLDOWN",         "Coin cooldown (seconds)",    "int",   "Cooldown between #coin uses."),
            ("DISASTER_CHANCE",       "Disaster chance (per tick)", "float", "Probability (0.0-1.0) of a disaster per poll tick."),
            ("GAME_TIME_MULTIPLIER",  "Game time multiplier",       "int",   "How many in-game days per real day (default 2)."),
            ("WEAPON_SLOTS",          "Weapon slots",               "int",   "Max number of weapon types a player can carry."),
            ("LEADERBOARD_SIZE",      "Leaderboard entries",        "int",   "How many players shown on the leaderboard."),
        ]

        for const, label, dtype, hint in settings:
            row = tk.Frame(tab, bg=BG, pady=4)
            row.pack(fill="x")
            tk.Label(row, text=label, font=("Helvetica", 10, "bold"),
                     bg=BG, fg=TEXT, width=28, anchor="w").pack(side="left")
            current_val = str(getattr(_game_engine, const, "?")) if _game_engine else "?"
            var = tk.StringVar(value=current_val)
            self._setting_vars[const] = (var, dtype)
            tk.Entry(row, textvariable=var, width=10, bg=BG3, fg=TEXT,
                     insertbackground=TEXT, relief="flat",
                     font=("Helvetica", 10)).pack(side="left", padx=8)
            tk.Label(row, text=hint, font=("Helvetica", 8),
                     bg=BG, fg=TEXT_DIM, wraplength=380, justify="left").pack(side="left")

        ttk.Separator(tab, orient="horizontal").pack(fill="x", pady=12)

        tk.Button(tab, text="Apply All Settings", bg=GREEN, fg="#000",
                  font=("Helvetica", 10, "bold"), relief="flat", padx=12, pady=6,
                  command=self._apply_settings).pack(anchor="w")
        self._settings_status = tk.Label(tab, text="", font=("Helvetica", 9),
                                          bg=BG, fg=GREEN)
        self._settings_status.pack(anchor="w", pady=4)

        ttk.Separator(tab, orient="horizontal").pack(fill="x", pady=12)

        # Danger danger
        tk.Label(tab, text="Danger Zone", font=("Helvetica", 11, "bold"),
                 bg=BG, fg=RED).pack(anchor="w")
        tk.Button(tab, text="Reset ALL Player Data (irreversible!)",
                  bg=RED, fg="white", font=("Helvetica", 10, "bold"),
                  relief="flat", padx=10, pady=5,
                  command=self._reset_all_players).pack(anchor="w", pady=4)

    def _apply_settings(self):
        if not _game_engine:
            self._settings_status.config(text="game_engine not loaded.", fg=RED)
            return
        applied = []
        errors  = []
        for const, (var, dtype) in self._setting_vars.items():
            raw = var.get().strip()
            try:
                val = float(raw) if dtype == "float" else int(raw)
                setattr(_game_engine, const, val)
                applied.append(const)
            except ValueError:
                errors.append(f"{const}: '{raw}' invalid")
        msg = f"Applied: {', '.join(applied)}"
        if errors:
            msg += f"\nErrors: {'; '.join(errors)}"
        col = GREEN if not errors else ORANGE
        self._settings_status.config(text=msg, fg=col)

    def _reset_all_players(self):
        if not messagebox.askyesno("DANGER",
            "This will wipe ALL player data permanently.\nAre you absolutely sure?"):
            return
        if not messagebox.askyesno("Final Confirmation",
            "ALL player progress, gems, inventory, and levels will be DELETED.\nContinue?"):
            return
        path = _data_path("players.json")
        _write_json(path, {})
        messagebox.showinfo("Done", "All player data has been reset.")
        self._refresh_all()

    # ─────────────────────────────────────────────────────────────────────────
    # LIVE REFRESH LOOP
    # ─────────────────────────────────────────────────────────────────────────
    def _start_live_loop(self):
        self._refresh_all()
        self.root.after(2000, self._live_loop)

    def _live_loop(self):
        if self._live_var.get():
            self._refresh_all()
        self._update_clock()
        self.root.after(2000, self._live_loop)

    def _update_clock(self):
        if _game_engine:
            try:
                self._clock_var.set(_game_engine.game_time_str())
            except Exception:
                pass

    def _refresh_all(self):
        self._players_cache = _all_players()
        self._refresh_table()
        self._refresh_summary()
        self._refresh_selected_player()
        self._dash_updated.set(f"Updated {time.strftime('%H:%M:%S')}")

    def _refresh_summary(self):
        players = self._players_cache
        if not players:
            for v in self._sum_labels.values():
                v.config(text="—")
            return
        total_gems = sum(_total_gems(p) for p in players.values())
        avg_level  = sum(p.get("level",1) for p in players.values()) / len(players)
        in_combat  = sum(1 for p in players.values() if p.get("in_combat"))
        ranked     = sorted(players.values(), key=_total_gems, reverse=True)
        leader     = ranked[0] if ranked else None
        self._sum_labels["players"].config(text=str(len(players)))
        self._sum_labels["total_gems"].config(text=f"{total_gems:,}")
        self._sum_labels["leader"].config(text=leader.get("name","—") if leader else "—")
        self._sum_labels["leader_gems"].config(text=f"{_total_gems(leader):,}" if leader else "—")
        self._sum_labels["avg_level"].config(text=f"{avg_level:.1f}")
        self._sum_labels["in_combat"].config(text=str(in_combat), fg=RED if in_combat else TEXT)

    def _refresh_table(self):
        players = self._players_cache
        sort    = self._sort_var.get()
        key_fn  = {
            "gems":  lambda p: _total_gems(p[1]),
            "level": lambda p: p[1].get("level",1),
            "name":  lambda p: p[1].get("name","").lower(),
            "hp":    lambda p: p[1].get("hp",0),
        }.get(sort, lambda p: _total_gems(p[1]))

        ranked = sorted(players.items(), key=key_fn, reverse=(sort != "name"))
        tree   = self._dash_tree
        tree.delete(*tree.get_children())
        medals = {0:"1st",1:"2nd",2:"3rd"}
        for i,(key,p) in enumerate(ranked):
            status = ""
            tag    = "normal"
            if p.get("in_combat"):
                status = "COMBAT"
                tag    = "combat"
            elif p.get("travelling_to"):
                status = f"→ {p['travelling_to'][:18]}"
                tag    = "travel"
            rank_s = medals.get(i, str(i+1)) if sort=="gems" else str(i+1)
            tree.insert("", "end", iid=key, tags=(tag,), values=(
                rank_s,
                p.get("name","?"),
                p.get("level",1),
                f"{_total_gems(p):,}",
                f"{p.get('hp',0)}/{p.get('max_hp',0)}",
                p.get("location","?")[:28],
                status,
            ))

    def _on_player_select(self, event):
        sel = self._dash_tree.selection()
        if not sel:
            return
        self._selected_player_key = sel[0]
        self._refresh_selected_player()

    def _refresh_selected_player(self):
        key = self._selected_player_key
        if not key or key not in self._players_cache:
            return
        p = self._players_cache[key]
        self._pl_name.config(text=p.get("name","?"))

        xp_need = int(100 * (p.get("level",1) ** 1.5))
        eq_name  = "None"
        if p.get("equipped_weapon") and _game_engine:
            idef = _game_engine._find_item_def(p["equipped_weapon"])
            eq_name = idef["name"] if idef else p["equipped_weapon"]
        armour_str = ", ".join(
            (_game_engine._find_item_def(aid) or {}).get("name", aid)
            for aid in p.get("armour",{}).values()
        ) or "None"
        status = "IN COMBAT" if p.get("in_combat") else (
            f"→ {p.get('travelling_to','')}" if p.get("travelling_to") else "Idle")

        updates = {
            "level":      str(p.get("level",1)),
            "xp_str":     f"{p.get('xp',0)} / {xp_need}",
            "hp_str":     f"{p.get('hp',0)} / {p.get('max_hp',0)}",
            "mana_str":   f"{p.get('mana',0)} / {p.get('max_mana',0)}",
            "atk":        str(p.get("atk",0)),
            "def":        str(p.get("def",0)),
            "spd":        str(p.get("spd",0)),
            "luck":       str(p.get("luck",0)),
            "gems":       f"{p.get('gems',0):,}",
            "chest_gems": f"{_gems_in_chests(p):,}",
            "total":      f"{_total_gems(p):,}",
            "location":   p.get("location","?"),
            "weapon":     eq_name,
            "armour":     armour_str,
            "status":     status,
        }
        for k, v in updates.items():
            if k in self._pl_vars:
                self._pl_vars[k].set(v)

        # Inventory tree
        inv_tree = self._inv_tree
        inv_tree.delete(*inv_tree.get_children())
        slot = 1
        for w in p.get("weapons",[]):
            eq = " [E]" if w.get("equipped") else ""
            inv_tree.insert("", "end", values=(slot, w["name"]+eq, w.get("qty",1), "weapon", ""))
            slot += 1
        for it in p.get("items",[]):
            idef = _game_engine._find_item_def(it.get("item_id","")) if _game_engine else None
            sv   = idef.get("sell_value",0) if idef else 0
            inv_tree.insert("", "end", values=(
                slot, it["name"], it.get("qty",1), it.get("category","misc"),
                f"{sv * it.get('qty',1)} gems"
            ))
            slot += 1

        # Chests tree
        chest_tree = self._chest_tree
        chest_tree.delete(*chest_tree.get_children())
        for ch in p.get("chests",[]):
            chest_tree.insert("", "end", values=(
                ch.get("name","Chest"),
                f"{ch.get('stored_gems',0):,}",
                f"{ch.get('capacity',0):,}",
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # PLAYER ACTIONS
    # ─────────────────────────────────────────────────────────────────────────
    def _gem_action(self, action):
        key = self._selected_player_key
        if not key:
            self._gem_status.config(text="No player selected.", fg=RED)
            return
        players = _all_players()
        p = players.get(key)
        if not p:
            return

        if action == "Reset":
            if not messagebox.askyesno("Confirm", f"Reset {p['name']}'s gems to 0?"):
                return
            p["gems"] = 0
            players[key] = p
            _save_players(players)
            self._gem_status.config(text=f"Reset {p['name']} gems to 0.", fg=ORANGE)
            self._refresh_all()
            return

        raw = self._gem_adj_var.get().strip()
        try:
            amount = int(raw)
        except ValueError:
            self._gem_status.config(text="Enter a valid number.", fg=RED)
            return

        if action == "Add":
            p["gems"] = p.get("gems",0) + amount
            msg = f"+{amount} gems → {p['gems']:,}"
        elif action == "Remove":
            p["gems"] = max(0, p.get("gems",0) - amount)
            msg = f"-{amount} gems → {p['gems']:,}"
        elif action == "Set":
            p["gems"] = amount
            msg = f"Set to {amount:,} gems"
        else:
            return

        players[key] = p
        _save_players(players)
        self._gem_status.config(text=msg, fg=GREEN)
        self._refresh_all()

    def _set_stat(self):
        key = self._selected_player_key
        if not key:
            self._so_status.config(text="No player selected.", fg=RED)
            return
        stat = self._so_stat_var.get()
        raw  = self._so_val_var.get().strip()
        try:
            val = int(raw)
        except ValueError:
            self._so_status.config(text="Value must be an integer.", fg=RED)
            return
        players = _all_players()
        p = players.get(key)
        if not p:
            return
        p[stat] = val
        players[key] = p
        _save_players(players)
        self._so_status.config(text=f"Set {stat} = {val} for {p['name']}.", fg=GREEN)
        self._refresh_all()

    def _teleport_player(self):
        key = self._selected_player_key
        if not key:
            return
        dest_name = self._tp_var.get().strip()
        if not dest_name or not _game_engine:
            return
        dest = _game_engine.find_location_fuzzy(dest_name)
        if not dest:
            messagebox.showerror("Unknown Location", f"'{dest_name}' not found.")
            return
        players = _all_players()
        p = players.get(key)
        if not p:
            return
        p["location"]     = dest["name"]
        p["travelling_to"] = None
        p["travel_arrive"] = None
        players[key] = p
        _save_players(players)
        self._tp_var.set("")
        self._refresh_all()
        self._gem_status.config(text=f"Teleported {p['name']} to {dest['name']}.", fg=GREEN)

    def _remove_inv_item(self):
        key = self._selected_player_key
        if not key:
            return
        sel = self._inv_tree.selection()
        if not sel:
            return
        vals = self._inv_tree.item(sel[0], "values")
        slot_num = int(vals[0]) if vals[0].isdigit() else None
        if slot_num is None:
            return
        if not messagebox.askyesno("Remove Item", f"Remove '{vals[1]}' from inventory?"):
            return

        players = _all_players()
        p = players.get(key)
        if not p:
            return
        # slot_num is 1-based across weapons then items
        slot = 1
        for i, w in enumerate(p.get("weapons",[])):
            if slot == slot_num:
                p["weapons"].pop(i)
                if p.get("equipped_weapon") == w.get("item_id"):
                    p["equipped_weapon"] = None
                players[key] = p; _save_players(players)
                self._refresh_all(); return
            slot += 1
        for i, it in enumerate(p.get("items",[])):
            if slot == slot_num:
                p["items"].pop(i)
                players[key] = p; _save_players(players)
                self._refresh_all(); return
            slot += 1

    def _clear_combat(self):
        key = self._selected_player_key
        if not key:
            return
        players = _all_players()
        p = players.get(key)
        if not p:
            return
        p["in_combat"]  = False
        p["combat_key"] = None
        players[key] = p
        _save_players(players)
        # Also clear from active_combats if possible
        if _game_engine and hasattr(_game_engine, "_active_combats"):
            _game_engine._active_combats.pop(key, None)
        self._refresh_all()
        self._gem_status.config(text=f"Cleared combat state for {p['name']}.", fg=GREEN)

    def _restore_player(self):
        key = self._selected_player_key
        if not key:
            return
        players = _all_players()
        p = players.get(key)
        if not p:
            return
        p["hp"]   = p.get("max_hp",   50)
        p["mana"] = p.get("max_mana", 30)
        players[key] = p
        _save_players(players)
        self._refresh_all()
        self._gem_status.config(text=f"Restored {p['name']} to full HP and Mana.", fg=GREEN)


# ─────────────────────────────────────────────────────────────────────────────
# SHOP ITEM ADD DIALOG
# ─────────────────────────────────────────────────────────────────────────────
class ShopItemDialog:
    def __init__(self, parent):
        self.result = None
        self.top    = tk.Toplevel(parent)
        self.top.title("Add Shop Item")
        self.top.configure(bg=BG)
        self.top.geometry("420x360")
        self.top.grab_set()

        tk.Label(self.top, text="Add Item to Shop", font=("Helvetica", 12, "bold"),
                 bg=BG, fg=ACCENT).pack(pady=10)

        form = tk.Frame(self.top, bg=BG, padx=20)
        form.pack(fill="both", expand=True)

        self._vars = {}
        fields = [
            ("id",          "Item ID (e.g. food_bread)", ""),
            ("name",        "Display Name",               ""),
            ("category",    "Category (chest/weapon/etc)",""),
            ("cost",        "Cost (gems)",                "10"),
            ("capacity",    "Capacity (chests only, else 0)", "0"),
            ("description", "Description",               ""),
        ]
        for key, label, default in fields:
            tk.Label(form, text=label, font=("Helvetica", 9),
                     bg=BG, fg=TEXT_DIM, anchor="w").pack(fill="x")
            var = tk.StringVar(value=default)
            self._vars[key] = var
            tk.Entry(form, textvariable=var, bg=BG3, fg=TEXT,
                     insertbackground=TEXT, relief="flat",
                     font=("Helvetica", 9)).pack(fill="x", pady=(0,4))

        btn_row = tk.Frame(self.top, bg=BG)
        btn_row.pack(pady=10)
        tk.Button(btn_row, text="Add", bg=GREEN, fg="#000",
                  relief="flat", padx=12, pady=4,
                  command=self._submit).pack(side="left", padx=4)
        tk.Button(btn_row, text="Cancel", bg=BG3, fg=TEXT,
                  relief="flat", padx=12, pady=4,
                  command=self.top.destroy).pack(side="left", padx=4)

    def _submit(self):
        try:
            cost     = int(self._vars["cost"].get().strip())
            capacity = int(self._vars["capacity"].get().strip())
        except ValueError:
            messagebox.showerror("Error", "Cost and Capacity must be integers.")
            return
        self.result = {
            "id":          self._vars["id"].get().strip(),
            "name":        self._vars["name"].get().strip(),
            "category":    self._vars["category"].get().strip(),
            "cost":        cost,
            "capacity":    capacity if capacity > 0 else None,
            "description": self._vars["description"].get().strip(),
        }
        if not self.result["id"] or not self.result["name"]:
            messagebox.showerror("Error", "ID and Name are required.")
            self.result = None
            return
        self.top.destroy()