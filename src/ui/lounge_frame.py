"""
Main Lounge Dashboard Frame (GameRanger Mode).
Provides:
1. Public Game Lobby Browser: 1-click Join and Host for any Warcraft III match.
2. In-Room Player Roster with Real-Time P2P Ping.
3. In-Room Live Text Chat.
4. 1-Click Synchronized "START GAME" (Launches WC3 for all room players simultaneously).
5. Friend List with 1-Click Direct Invites.
"""

from pathlib import Path
import math
import random
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Optional
import customtkinter as ctk

from ..auth.auth_manager import UserSession
from ..auth.friend_manager import FriendManager, GameInvite
from ..core.config import APP_VERSION, logger
from ..core.sound_effects import (
    is_sound_muted,
    set_sound_muted,
    play_join_sound,
    play_leave_sound,
    play_chat_sound,
    play_launch_sound
)
from ..launcher.game_launcher import GameLauncher
from ..network.lobby_service import LobbyRoom, LobbyService
from ..network.lounge_session import LoungeSession

import sys
from PIL import Image

def _get_asset_path(filename: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).resolve().parent.parent.parent
    return base_dir / "assets" / filename

class PrivateChatDialog(ctk.CTkToplevel):
    """
    Ephemeral Direct Message Dialog.
    Strictly in-memory (RAM only) - messages are destroyed when window is closed.
    Never persisted to disk or cloud database.
    Restricted: Only delivered when the friend is actively online.
    """

    def __init__(
        self,
        master,
        current_username: str,
        friend_name: str,
        friend_manager: FriendManager,
        on_close: Optional[Callable[[str], None]] = None,
        is_online: bool = True
    ):
        super().__init__(master)
        self.current_username = current_username
        self.friend_name = friend_name
        self.friend_manager = friend_manager
        self.on_close = on_close
        self.is_online = is_online

        self.title(f"💬 Private Chat - {self.friend_name}")
        self.geometry("420x480")
        self.minsize(380, 360)
        self.configure(fg_color="#18191c")

        # Top Header
        header = ctk.CTkFrame(self, fg_color="#202225", corner_radius=0, height=52)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        title_lbl = ctk.CTkLabel(
            header,
            text=f"💬 {self.friend_name}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#ffffff"
        )
        title_lbl.pack(side="left", padx=(14, 6), pady=8)

        self.status_badge = ctk.CTkLabel(
            header,
            text="🟢 Online" if self.is_online else "⚪ Offline",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#2ecc71" if self.is_online else "#7f8c8d"
        )
        self.status_badge.pack(side="left", padx=2, pady=8)

        notice_lbl = ctk.CTkLabel(
            header,
            text="🔒 Ephemeral Chat (RAM only)",
            font=ctk.CTkFont(family="Segoe UI", size=10, slant="italic"),
            text_color="#8e9297"
        )
        notice_lbl.pack(side="right", padx=14, pady=8)

        # Message History (Read-Only Textbox)
        self.chat_display = ctk.CTkTextbox(
            self,
            fg_color="#2f3136",
            text_color="#dcddde",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            wrap="word",
            corner_radius=8
        )
        self.chat_display.pack(fill="both", expand=True, padx=12, pady=(10, 8))
        self.chat_display.configure(state="disabled")

        # Bottom Input Row
        self.input_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.input_frame.pack(fill="x", side="bottom", padx=12, pady=(0, 12))

        self.msg_entry = ctk.CTkEntry(
            self.input_frame,
            placeholder_text=f"Message @{self.friend_name}..." if self.is_online else f"@{self.friend_name} is offline...",
            fg_color="#40444b",
            border_color="#202225",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=36
        )
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.msg_entry.bind("<Return>", lambda e: self._send_message())

        self.send_btn = ctk.CTkButton(
            self.input_frame,
            text="Send",
            width=70,
            height=36,
            fg_color="#5865f2" if self.is_online else "#4f545c",
            hover_color="#4752c4" if self.is_online else "#4f545c",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._send_message,
            state="normal" if self.is_online else "disabled"
        )
        self.send_btn.pack(side="right")

        if not self.is_online:
            self.msg_entry.configure(state="disabled")
            self.append_message("System", f"@{self.friend_name} is currently offline. Direct messages require both players to be online.", is_error=True)

        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        if self.is_online:
            self.msg_entry.focus_set()

    def update_presence(self, is_online: bool):
        if self.is_online == is_online:
            return
        self.is_online = is_online
        if is_online:
            self.status_badge.configure(text="🟢 Online", text_color="#2ecc71")
            self.msg_entry.configure(state="normal", placeholder_text=f"Message @{self.friend_name}...")
            self.send_btn.configure(state="normal", fg_color="#5865f2", hover_color="#4752c4")
            self.append_message("System", f"🟢 @{self.friend_name} came online.")
        else:
            self.status_badge.configure(text="⚪ Offline", text_color="#7f8c8d")
            self.msg_entry.configure(state="disabled", placeholder_text=f"@{self.friend_name} is offline...")
            self.send_btn.configure(state="disabled", fg_color="#4f545c", hover_color="#4f545c")
            self.append_message("System", f"⚠️ @{self.friend_name} went offline. Direct messages are restricted.", is_error=True)

    def append_message(self, sender: str, text: str, is_error: bool = False):
        t_str = time.strftime("%H:%M")
        self.chat_display.configure(state="normal")
        if is_error:
            self.chat_display.insert("end", f"[{t_str}] ⚠️ {text}\n")
        else:
            self.chat_display.insert("end", f"[{t_str}] {sender}: {text}\n")
        self.chat_display.see("end")
        self.chat_display.configure(state="disabled")

    def _send_message(self):
        if not self.is_online:
            self.append_message("System", f"Cannot send: @{self.friend_name} is offline.", is_error=True)
            return
        text = self.msg_entry.get().strip()
        if not text:
            return
        self.msg_entry.delete(0, "end")
        self.append_message(self.current_username, text)

        def _do_send():
            ok, err = self.friend_manager.send_direct_message(self.friend_name, text)
            if not ok:
                self.after(0, lambda: self.append_message("System", f"Delivery failed: {err}", is_error=True))

        threading.Thread(target=_do_send, daemon=True).start()

    def _on_window_close(self):
        if self.on_close:
            try:
                self.on_close(self.friend_name)
            except Exception:
                pass
        self.destroy()

class HostGameModal(ctk.CTkToplevel):
    """
    Dedicated Host Game configuration dialog:
    - Custom Game Title (e.g. DotA 5v5 - AP, Legion TD, Melee 1v1)
    - Max Player Slots (2 to 12)
    - Optional Room Password
    """

    def __init__(self, master, default_title: str, on_host: Callable[[str, int, str], None]):
        super().__init__(master)
        self.on_host = on_host
        self.title("🎮 Host Warcraft III Match")
        self.geometry("440x380")
        self.minsize(400, 340)
        self.configure(fg_color="#18191c")
        self.grab_set()

        # Header
        header = ctk.CTkFrame(self, fg_color="#202225", corner_radius=0, height=50)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="⚔️ Create Game Lobby",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#ffffff"
        ).pack(side="left", padx=16, pady=8)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # 1. Game Title
        ctk.CTkLabel(body, text="Match Title:", font=ctk.CTkFont(weight="bold", size=12)).pack(anchor="w", pady=(0, 4))
        self.title_entry = ctk.CTkEntry(body, placeholder_text="e.g. DotA 5v5 AP, Legion TD 4v4", height=34)
        self.title_entry.insert(0, default_title)
        self.title_entry.pack(fill="x", pady=(0, 12))

        # 2. Max Players
        ctk.CTkLabel(body, text="Max Players:", font=ctk.CTkFont(weight="bold", size=12)).pack(anchor="w", pady=(0, 4))
        self.slots_menu = ctk.CTkOptionMenu(
            body,
            values=["2 Players", "4 Players", "6 Players", "8 Players", "10 Players", "12 Players"],
            height=34,
            fg_color="#2f3136",
            button_color="#5865f2",
            button_hover_color="#4752c4"
        )
        self.slots_menu.set("10 Players")
        self.slots_menu.pack(fill="x", pady=(0, 12))

        # 3. Password Checkbox + Entry
        self.has_pw_var = ctk.BooleanVar(value=False)
        self.pw_checkbox = ctk.CTkCheckBox(
            body,
            text="🔒 Private Room (Password Protected)",
            variable=self.has_pw_var,
            command=self._toggle_pw
        )
        self.pw_checkbox.pack(anchor="w", pady=(0, 6))

        self.pw_entry = ctk.CTkEntry(
            body,
            placeholder_text="Enter room password...",
            show="*",
            height=34
        )

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", padx=20, pady=(0, 18))

        cancel_btn = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=90,
            height=36,
            fg_color="#4f545c",
            hover_color="#36393f",
            command=self.destroy
        )
        cancel_btn.pack(side="left")

        host_btn = ctk.CTkButton(
            btn_frame,
            text="🚀 Create Lobby",
            height=36,
            fg_color="#27ae60",
            hover_color="#219150",
            font=ctk.CTkFont(weight="bold", size=13),
            command=self._confirm
        )
        host_btn.pack(side="right", fill="x", expand=True, padx=(10, 0))

        self.title_entry.focus_set()

    def _toggle_pw(self):
        if self.has_pw_var.get():
            self.pw_entry.pack(fill="x", pady=(0, 8), before=self.pw_checkbox)
            self.pw_entry.focus_set()
        else:
            self.pw_entry.pack_forget()

    def _confirm(self):
        title = self.title_entry.get().strip()
        if not title:
            return
        slots_str = self.slots_menu.get().split()[0]
        max_players = int(slots_str) if slots_str.isdigit() else 10
        password = self.pw_entry.get().strip() if self.has_pw_var.get() else ""
        self.destroy()
        self.on_host(title, max_players, password)

class StartGlowBox(ctk.CTkFrame):
    """
    Pixel-perfect Garena START Button Container with dynamic sparkling,
    glowing, and circling effect when all room players are ready.
    """
    def __init__(
        self,
        master,
        btn_img=None,
        hover_img=None,
        dis_img=None,
        command=None,
        force_command=None,
        width: int = 84,
        height: int = 80,
        **kwargs
    ):
        super().__init__(master, width=width, height=height, fg_color="#101826", corner_radius=6, **kwargs)
        self.pack_propagate(False)
        self.btn_img = btn_img
        self.hover_img = hover_img
        self.dis_img = dis_img
        self.command = command
        self.force_command = force_command

        # Canvas for animated circling beam, neon glow, and shimmering sparkles
        self.canvas = tk.Canvas(self, bg="#101826", highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # Embedded CTkButton (starts centered)
        self.button = ctk.CTkButton(
            self,
            text="",
            image=btn_img,
            width=70,
            height=73,
            border_spacing=0,
            fg_color="transparent",
            hover_color="#182334",
            corner_radius=4,
            command=self._on_btn_click
        )
        self.button.place(relx=0.5, rely=0.5, anchor="center")
        self.button.lift()

        # Hover state bindings
        def _on_enter(e):
            if self.button.cget("state") == "normal" and self.hover_img:
                self.button.configure(image=self.hover_img)

        def _on_leave(e):
            if self.button.cget("state") == "normal" and self.btn_img:
                self.button.configure(image=self.btn_img)

        self.button.bind("<Enter>", _on_enter)
        self.button.bind("<Leave>", _on_leave)

        # Allow right-click on button or canvas for force start
        if force_command:
            self.button.bind("<Button-3>", force_command)
            self.canvas.bind("<Button-3>", force_command)

        # Canvas click forwards to button action
        self.canvas.bind("<Button-1>", lambda e: self._on_btn_click())

        self.is_glowing = False
        self._anim_job = None
        self._t = 0.0
        self._pulse_t = 0.0
        self.sparkles = []

        self.canvas.bind("<Configure>", lambda e: self._draw_idle())

    def _on_btn_click(self):
        if self.button.cget("state") == "normal" and self.command:
            self.command()

    def _draw_idle(self):
        if self.is_glowing or not self.winfo_exists():
            return
        self.canvas.delete("glow")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w > 10 and h > 10:
            self.canvas.create_rectangle(3, 3, w - 3, h - 3, outline="#1c2838", width=2, tags="idle")

    def start_glow(self):
        if self.is_glowing:
            return
        self.is_glowing = True
        self.canvas.delete("idle")
        self._init_sparkles()
        self._animate()

    def stop_glow(self):
        self.is_glowing = False
        if self._anim_job:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        self.canvas.delete("glow")
        self._draw_idle()

    def _init_sparkles(self):
        self.sparkles = []
        for _ in range(9):
            self.sparkles.append(self._new_sparkle())

    def _new_sparkle(self):
        w = max(self.canvas.winfo_width(), 80)
        h = max(self.canvas.winfo_height(), 80)
        t = random.random()
        x, y = self._get_perimeter_point(t, w, h)
        x += random.randint(-4, 4)
        y += random.randint(-4, 4)
        return {
            "x": x,
            "y": y,
            "life": random.uniform(0.0, 0.85),
            "speed": random.uniform(0.045, 0.095),
            "max_s": random.randint(4, 7),
            "color": random.choice([
                "#ffffff", "#00ffff", "#ffd700", "#fff4aa", "#70e1ff", "#e0ffff", "#f6e58d"
            ])
        }

    def _get_perimeter_point(self, t, w, h):
        m = 3
        pw = max(w - 2 * m, 10)
        ph = max(h - 2 * m, 10)
        perimeter = 2 * (pw + ph)
        d = (t % 1.0) * perimeter
        if d < pw:
            return m + d, m
        d -= pw
        if d < ph:
            return m + pw, m + d
        d -= ph
        if d < pw:
            return m + pw - d, m + ph
        d -= ph
        return m, m + ph - d

    def _animate(self):
        if not self.is_glowing or not self.winfo_exists():
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10:
            self._anim_job = self.after(33, self._animate)
            return

        self.canvas.delete("glow")

        # 1. Radiant pulsing neon border
        self._pulse_t += 0.09
        pulse = (math.sin(self._pulse_t) + 1.0) / 2.0
        g = int(80 + pulse * 145)
        b = int(185 + pulse * 70)
        glow_color = f"#00{g:02x}{b:02x}"
        self.canvas.create_rectangle(3, 3, w - 3, h - 3, outline=glow_color, width=2, tags="glow")

        # 2. Circling energy comets (Dual orbiting comets chasing each other)
        self._t = (self._t + 0.014) % 1.0
        tail_steps = [
            (0.000, "#ffffff", 3.8),
            (0.012, "#e0ffff", 3.2),
            (0.025, "#00f0ff", 2.8),
            (0.040, "#00a8ff", 2.3),
            (0.055, "#0066ff", 1.8),
            (0.070, "#0033aa", 1.4),
            (0.085, "#001155", 1.0),
        ]
        for offset in (0.0, 0.5):
            head_t = (self._t + offset) % 1.0
            # Draw tail points
            for dt, col, rad in tail_steps:
                tx, ty = self._get_perimeter_point(head_t - dt, w, h)
                self.canvas.create_oval(tx - rad, ty - rad, tx + rad, ty + rad, fill=col, outline="", tags="glow")
            # Starburst at the comet head
            hx, hy = self._get_perimeter_point(head_t, w, h)
            self.canvas.create_line(hx - 4, hy, hx + 4, hy, fill="#ffffff", width=1.5, tags="glow")
            self.canvas.create_line(hx, hy - 4, hx, hy + 4, fill="#ffffff", width=1.5, tags="glow")

        # 3. Shimmering diamond sparkles
        for i, sp in enumerate(self.sparkles):
            sp["life"] += sp["speed"]
            if sp["life"] >= 1.0:
                self.sparkles[i] = self._new_sparkle()
                continue
            cur_s = sp["max_s"] * math.sin(sp["life"] * math.pi)
            if cur_s > 1:
                sx, sy = sp["x"], sp["y"]
                c = sp["color"]
                # 4-point cross star
                self.canvas.create_line(sx - cur_s, sy, sx + cur_s, sy, fill=c, width=1.5, tags="glow")
                self.canvas.create_line(sx, sy - cur_s, sx, sy + cur_s, fill=c, width=1.5, tags="glow")
                # Secondary diagonal cross for bright bursts
                if cur_s > 3.5:
                    diag = cur_s * 0.55
                    self.canvas.create_line(sx - diag, sy - diag, sx + diag, sy + diag, fill=c, width=1, tags="glow")
                    self.canvas.create_line(sx - diag, sy + diag, sx + diag, sy - diag, fill=c, width=1, tags="glow")
                    self.canvas.create_oval(sx - 1.5, sy - 1.5, sx + 1.5, sy + 1.5, fill="#ffffff", outline="", tags="glow")

        self._anim_job = self.after(33, self._animate)

class LoungeFrame(ctk.CTkFrame):
    """Primary application dashboard for Warcraft III Lounge (GameRanger Experience)."""

    def __init__(self, master, current_user: UserSession, on_logout: Callable[[], None]):
        super().__init__(master, corner_radius=12)
        self.current_user = current_user
        self.on_logout = on_logout

        self.session: Optional[LoungeSession] = None
        self.launcher = GameLauncher()
        self.friend_manager = FriendManager(username=self.current_user.username)
        self.lobby_service = LobbyService(username=self.current_user.username)

        self.active_invite: Optional[GameInvite] = None
        self.player_rows: dict = {}
        self.player_readiness: dict = {}
        self.is_self_ready: bool = False
        self.current_room_title: str = "Warcraft III Lounge"
        self._is_polling_rooms = True
        self._active_pm_dialogs: dict = {}

        # Load Garena authentic graphical icons and wide banner
        self._load_garena_assets()

        self._build_ui()
        self._refresh_game_path_ui()
        self._refresh_friends_list()

        # Start signaling listener for friend requests, direct invites, presence, PMs, and NAT traversal
        self.friend_manager.start_listening(
            on_invite=self._handle_incoming_invite,
            on_friend_request=self._handle_incoming_friend_request,
            on_friend_accepted=self._handle_friend_accepted,
            on_presence_changed=lambda: self.after(0, self._refresh_friends_list),
            on_direct_message=self._handle_incoming_direct_message,
            on_nat_syn=self._handle_incoming_nat_syn
        )

        # Start live lobby browser polling
        self._start_lobby_polling()

        # Check for GitHub releases in the background
        from ..core.update_checker import check_for_updates_async
        check_for_updates_async(on_update_available=self._show_update_banner)

    def _show_update_banner(self, latest_tag: str, release_url: str):
        def show():
            banner = ctk.CTkFrame(self, fg_color="#1b4f72", corner_radius=8, height=40)
            banner.pack(fill="x", padx=15, pady=(8, 0), before=self.top_bar)
            banner.pack_propagate(False)

            msg = ctk.CTkLabel(
                banner,
                text=f"🚀 Update Available: {latest_tag} is ready for download!",
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                text_color="#ffffff"
            )
            msg.pack(side="left", padx=12, pady=6)

            close_btn = ctk.CTkButton(
                banner,
                text="✕",
                width=24,
                height=24,
                fg_color="transparent",
                hover_color="#154360",
                command=banner.destroy
            )
            close_btn.pack(side="right", padx=6, pady=6)

            import webbrowser
            download_btn = ctk.CTkButton(
                banner,
                text="Download Update",
                width=120,
                height=26,
                fg_color="#27ae60",
                hover_color="#219150",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                command=lambda: webbrowser.open(release_url)
            )
            download_btn.pack(side="right", padx=6, pady=6)

        self.after(0, show)

    def _load_garena_assets(self):
        """Loads authentic Garena icons and DotA header graphic."""
        self.banner_img = None
        self.sta_chat_img = None
        self.sta_battle_img = None
        self.start_dial_img = None
        self.start_btn_img = None
        self.start_btn_hover_img = None
        self.start_btn_dis_img = None
        self.start_btn_act_img = None

        try:
            banner_path = _get_asset_path("garena_banner_1024.png")
            if not banner_path.exists():
                banner_path = _get_asset_path("garena_banner.png")
            if banner_path.exists():
                raw_b = Image.open(banner_path)
                self.banner_img = ctk.CTkImage(light_image=raw_b, dark_image=raw_b, size=(1024, 87))

            chat_path = _get_asset_path("garena_sta_chat.png")
            if chat_path.exists():
                raw_chat = Image.open(chat_path)
                self.sta_chat_img = ctk.CTkImage(light_image=raw_chat, dark_image=raw_chat, size=(20, 16))

            battle_path = _get_asset_path("garena_sta_battle.png")
            if battle_path.exists():
                raw_battle = Image.open(battle_path)
                self.sta_battle_img = ctk.CTkImage(light_image=raw_battle, dark_image=raw_battle, size=(20, 16))

            dial_path = _get_asset_path("garena_power_dial.png")
            if dial_path.exists():
                raw_dial = Image.open(dial_path)
                self.start_dial_img = ctk.CTkImage(light_image=raw_dial, dark_image=raw_dial, size=(26, 26))

            start_path = _get_asset_path("garena_start_btn.png")
            if start_path.exists():
                raw_start = Image.open(start_path)
                self.start_btn_img = ctk.CTkImage(light_image=raw_start, dark_image=raw_start, size=(70, 73))

            hover_path = _get_asset_path("garena_start_btn_hover.png")
            if hover_path.exists():
                raw_hover = Image.open(hover_path)
                self.start_btn_hover_img = ctk.CTkImage(light_image=raw_hover, dark_image=raw_hover, size=(70, 73))

            dis_path = _get_asset_path("garena_start_btn_disabled.png")
            if dis_path.exists():
                raw_dis = Image.open(dis_path)
                self.start_btn_dis_img = ctk.CTkImage(light_image=raw_dis, dark_image=raw_dis, size=(70, 73))

            act_path = _get_asset_path("garena_start_btn_launching.png")
            if act_path.exists():
                raw_act = Image.open(act_path)
                self.start_btn_act_img = ctk.CTkImage(light_image=raw_act, dark_image=raw_act, size=(70, 73))
        except Exception as e:
            logger.debug(f"Failed to load Garena icons: {e}")

    def _build_ui(self):
        # 1. Garena DotA Top Artwork Banner Frame (Fixed 1024x87 Classic)
        self.banner_frame = ctk.CTkFrame(self, fg_color="#0e1217", corner_radius=0, height=87)
        self.banner_frame.pack(fill="x", side="top")
        self.banner_frame.pack_propagate(False)

        self.banner_lbl = ctk.CTkLabel(
            self.banner_frame,
            text="",
            image=self.banner_img if self.banner_img else None
        )
        self.banner_lbl.pack(fill="both", expand=True)

        # 2. Garena Classic Navigation Bar (Clean Functional Tabs + User Stats + Actions)
        self.top_bar = ctk.CTkFrame(self, fg_color="#121822", height=42, corner_radius=0)
        self.top_bar.pack(fill="x", side="top", pady=(0, 2))

        # Garena Room Level & EXP Badge on the left
        self.room_lvl_badge = ctk.CTkLabel(
            self.top_bar,
            text="🔥 ROOM LVL: 1",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#f39c12"
        )
        self.room_lvl_badge.pack(side="left", padx=(14, 6), pady=6)

        self.exp_badge = ctk.CTkLabel(
            self.top_bar,
            text="0%",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#0b0f15",
            corner_radius=4,
            padx=8,
            pady=2,
            text_color="#bdc3c7"
        )
        self.exp_badge.pack(side="left", padx=2, pady=6)

        # Right Bar Controls
        self.logout_btn = ctk.CTkButton(
            self.top_bar,
            text="Logout",
            width=65,
            height=28,
            fg_color="#c0392b",
            hover_color="#962d22",
            command=self._handle_logout
        )
        self.logout_btn.pack(side="right", padx=(4, 12), pady=6)

        self.mute_btn = ctk.CTkButton(
            self.top_bar,
            text="🔇 Muted" if is_sound_muted() else "🔊 Sound",
            width=72,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color="#7f8c8d" if is_sound_muted() else "#2980b9",
            hover_color="#95a5a6" if is_sound_muted() else "#3498db",
            command=self._toggle_sound_mute
        )
        self.mute_btn.pack(side="right", padx=4, pady=6)

        self.version_badge = ctk.CTkLabel(
            self.top_bar,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#1c2833",
            corner_radius=4,
            padx=6,
            pady=2
        )
        self.version_badge.pack(side="right", padx=4, pady=6)

        self.user_label = ctk.CTkLabel(
            self.top_bar,
            text=f"👤 {self.current_user.username}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#ecf0f1"
        )
        self.user_label.pack(side="right", padx=(8, 4), pady=6)

        # 3. Game Path Sub-Bar (Compact Sleek Sub-Header)
        self.path_bar = ctk.CTkFrame(self, fg_color="#0e131a", height=26, corner_radius=0)
        self.path_bar.pack(fill="x", padx=0, pady=(0, 6))

        self.path_label = ctk.CTkLabel(
            self.path_bar,
            text="Warcraft III Path: Detecting...",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#95a5a6",
            anchor="w"
        )
        self.path_label.pack(side="left", fill="x", expand=True, padx=12)

        self.browse_btn = ctk.CTkButton(
            self.path_bar,
            text="📁 Browse Executable",
            width=130,
            height=20,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#2c3e50",
            hover_color="#34495e",
            command=self._browse_executable
        )
        self.browse_btn.pack(side="right", padx=10, pady=2)

        # 4. Notification Banners
        self.invite_banner = ctk.CTkFrame(self, fg_color="#16a085", corner_radius=8)
        self.invite_label = ctk.CTkLabel(
            self.invite_banner,
            text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="white"
        )
        self.invite_label.pack(side="left", padx=15, pady=10)

        self.invite_decline_btn = ctk.CTkButton(
            self.invite_banner,
            text="Decline",
            width=70,
            height=30,
            fg_color="#7f8c8d",
            hover_color="#95a5a6",
            command=self._decline_invite
        )
        self.invite_decline_btn.pack(side="right", padx=10, pady=8)

        self.invite_accept_btn = ctk.CTkButton(
            self.invite_banner,
            text="Accept & Join Game",
            width=140,
            height=30,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#27ae60",
            hover_color="#219150",
            command=self._accept_invite
        )
        self.invite_accept_btn.pack(side="right", padx=(10, 5), pady=8)

        # Friend Request Banner
        self.friend_req_banner = ctk.CTkFrame(self, fg_color="#2980b9", corner_radius=8)
        self.friend_req_label = ctk.CTkLabel(
            self.friend_req_banner,
            text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="white"
        )
        self.friend_req_label.pack(side="left", padx=15, pady=10)

        self.friend_req_decline_btn = ctk.CTkButton(
            self.friend_req_banner,
            text="Decline",
            width=70,
            height=30,
            fg_color="#7f8c8d",
            hover_color="#95a5a6",
            command=self._decline_friend_request
        )
        self.friend_req_decline_btn.pack(side="right", padx=10, pady=8)

        self.friend_req_accept_btn = ctk.CTkButton(
            self.friend_req_banner,
            text="Accept Friend",
            width=115,
            height=30,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#27ae60",
            hover_color="#219150",
            command=self._accept_friend_request
        )
        self.friend_req_accept_btn.pack(side="right", padx=(10, 5), pady=8)

        # 5. Main 2-Column Split: Left = Friends Panel, Right = Lobby Browser OR Active Room
        self.main_split = ctk.CTkFrame(self, fg_color="transparent")
        self.main_split.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.main_split.columnconfigure(0, weight=0, minsize=220)  # Left Friends panel
        self.main_split.columnconfigure(1, weight=1)              # Right container
        self.main_split.rowconfigure(0, weight=1)                 # Full vertical expansion to bottom

        # Build Left Friends Panel
        self._build_friends_panel()

        # Build Right Container
        self.lounge_container = ctk.CTkFrame(self.main_split, fg_color="transparent")
        self.lounge_container.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.lobby_selection_frame = ctk.CTkFrame(self.lounge_container, fg_color="transparent")
        self.active_room_frame = ctk.CTkFrame(self.lounge_container, fg_color="transparent")

        self._build_lobby_browser_ui()
        self._build_active_room_ui()

        self.lobby_selection_frame.pack(fill="both", expand=True)

    def _build_friends_panel(self):
        """Constructs left-hand Friends management pane with Garena styling."""
        self.friends_card = ctk.CTkFrame(self.main_split, fg_color="#10151f", corner_radius=6)
        self.friends_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        # Friends Title
        ctk.CTkLabel(
            self.friends_card,
            text="👥 Friends",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#ecf0f1"
        ).pack(pady=(10, 6), padx=12, anchor="w")

        # Add Friend Input + Button
        self.add_friend_box = ctk.CTkFrame(self.friends_card, fg_color="transparent")
        self.add_friend_box.pack(fill="x", padx=8, pady=(0, 6))

        self.friend_entry = ctk.CTkEntry(
            self.add_friend_box,
            placeholder_text="Username...",
            height=28,
            fg_color="#18202c",
            border_color="#243343",
            text_color="#ffffff",
            font=ctk.CTkFont(size=11)
        )
        self.friend_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.add_friend_btn = ctk.CTkButton(
            self.add_friend_box,
            text="+ Add",
            width=48,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#27ae60",
            hover_color="#219150",
            command=self._handle_add_friend
        )
        self.add_friend_btn.pack(side="right")

        # Friends Scrollable List
        self.friends_scroll = ctk.CTkScrollableFrame(self.friends_card, fg_color="transparent")
        self.friends_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 6))

    def _build_lobby_browser_ui(self):
        """Constructs the Garena Public Game Browser."""
        # Top Action Bar inside Browser
        browser_header = ctk.CTkFrame(self.lobby_selection_frame, fg_color="transparent")
        browser_header.pack(fill="x", pady=(0, 6))

        # Host Game Button (Left)
        self.host_game_btn = ctk.CTkButton(
            browser_header,
            text="➕ Host Game",
            width=120,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#1e3799",
            hover_color="#273c75",
            command=self._open_host_modal
        )
        self.host_game_btn.pack(side="left")

        # Join by Code Button (Right next to Host Game)
        self.join_code_btn = ctk.CTkButton(
            browser_header,
            text="🔗 Join by Code",
            width=120,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#2c3e50",
            hover_color="#34495e",
            command=self._open_join_by_code_modal
        )
        self.join_code_btn.pack(side="left", padx=(8, 0))

        # Refresh button (Right)
        self.refresh_btn = ctk.CTkButton(
            browser_header,
            text="🔄 Refresh",
            width=80,
            height=32,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#1c2833",
            hover_color="#2c3e50",
            command=self._refresh_rooms_list
        )
        self.refresh_btn.pack(side="right")

        # Table Header
        table_hdr = ctk.CTkFrame(self.lobby_selection_frame, fg_color="#141a24", height=28, corner_radius=4)
        table_hdr.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(table_hdr, text="Game Title", font=ctk.CTkFont(family="Segoe UI", weight="bold", size=11), text_color="#bdc3c7", anchor="w").pack(side="left", fill="x", expand=True, padx=(12, 5))
        ctk.CTkLabel(table_hdr, text="Host", font=ctk.CTkFont(family="Segoe UI", weight="bold", size=11), text_color="#bdc3c7", width=100, anchor="w").pack(side="left", padx=5)
        ctk.CTkLabel(table_hdr, text="Players", font=ctk.CTkFont(family="Segoe UI", weight="bold", size=11), text_color="#bdc3c7", width=65).pack(side="left", padx=5)
        ctk.CTkLabel(table_hdr, text="Action", font=ctk.CTkFont(family="Segoe UI", weight="bold", size=11), text_color="#bdc3c7", width=85).pack(side="right", padx=10)

        # Rooms Scrollable Container
        self.rooms_scroll = ctk.CTkScrollableFrame(self.lobby_selection_frame, fg_color="transparent")
        self.rooms_scroll.pack(fill="both", expand=True)

        self._render_empty_rooms()

    def _render_empty_rooms(self):
        for child in self.rooms_scroll.winfo_children():
            child.destroy()
        empty_lbl = ctk.CTkLabel(
            self.rooms_scroll,
            text="No active games in browser right now.\nClick '➕ Host Game' above to start one!",
            text_color="gray",
            font=ctk.CTkFont(size=13),
            justify="center"
        )
        empty_lbl.pack(pady=60)

    def _start_lobby_polling(self):
        def loop():
            while self._is_polling_rooms:
                if not self.session:  # Only poll when in browser view
                    try:
                        rooms = self.lobby_service.fetch_public_rooms()
                        self.after(0, lambda r=rooms: self._update_rooms_ui(r))
                    except Exception:
                        pass
                time.sleep(3.5)

        threading.Thread(target=loop, daemon=True, name="LobbyPollThread").start()

    def _refresh_rooms_list(self):
        threading.Thread(target=lambda: self._update_rooms_ui(self.lobby_service.fetch_public_rooms()), daemon=True).start()

    def _update_rooms_ui(self, rooms: list):
        if self.session:
            return  # Inside active room

        for child in self.rooms_scroll.winfo_children():
            child.destroy()

        if not rooms:
            self._render_empty_rooms()
            return

        for r in rooms:
            row = ctk.CTkFrame(self.rooms_scroll, fg_color="#10151f", corner_radius=4, height=38)
            row.pack(fill="x", pady=2)

            room_title = f"🔒 {r.title}" if r.is_locked else f"⚔️ {r.title}"
            title_lbl = ctk.CTkLabel(row, text=room_title, font=ctk.CTkFont(family="Segoe UI", weight="bold", size=12), text_color="#ecf0f1", anchor="w")
            title_lbl.pack(side="left", fill="x", expand=True, padx=(12, 5), pady=6)

            host_lbl = ctk.CTkLabel(row, text=r.host_username, text_color="#7f8c8d", width=100, anchor="w", font=ctk.CTkFont(size=11))
            host_lbl.pack(side="left", padx=5, pady=6)

            players_lbl = ctk.CTkLabel(row, text=f"{r.current_players}/{r.max_players}", width=65, font=ctk.CTkFont(size=11, weight="bold"), text_color="#f39c12")
            players_lbl.pack(side="left", padx=5, pady=6)

            join_btn = ctk.CTkButton(
                row,
                text="Join Game",
                width=80,
                height=26,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#27ae60",
                hover_color="#219150",
                command=lambda rm=r: self._join_public_room(rm)
            )
            join_btn.pack(side="right", padx=10, pady=5)

        # Refresh friends list to reflect any friends now hosting rooms
        self._refresh_friends_list()

    def _open_host_modal(self):
        """Opens full custom modal dialog to configure and host a game."""
        default_title = f"{self.current_user.username}'s Game"
        HostGameModal(self, default_title, on_host=self._start_hosting)

    def _open_join_by_code_modal(self):
        """Allows direct joining by entering or pasting a W3L- room code."""
        dialog = ctk.CTkInputDialog(
            text="Paste the Host's Room Code (starts with W3L-):\n\n(Tip: The Host can copy their code with 1 click inside their room!)",
            title="🔗 Join Room by Code"
        )
        code = dialog.get_input()
        if not code or not code.strip():
            return
        code = code.strip()
        if not code.startswith("W3L-"):
            messagebox.showerror("Invalid Code", "Room code must start with 'W3L-'. Please verify the code and try again.")
            return
        self.current_room_title = "Private Room"
        threading.Thread(
            target=self._async_join,
            args=(code,),
            daemon=True
        ).start()

    def _build_active_room_ui(self):
        """Constructs the classic Garena Room Screen with Live Chat on Left & Player Table on Right."""
        # Top Room Banner & Match Status Bar
        self.match_card = ctk.CTkFrame(self.active_room_frame, fg_color="#101826", corner_radius=4, height=36)
        self.match_card.pack(fill="x", pady=(0, 6))

        self.room_title_label = ctk.CTkLabel(
            self.match_card,
            text="🎮 Warcraft III Lounge Room",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#00d2d3"
        )
        self.room_title_label.pack(side="left", padx=12, pady=6)

        self.match_card_label = ctk.CTkLabel(
            self.match_card,
            text="Status: Room open. Waiting for players...",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#bdc3c7"
        )
        self.match_card_label.pack(side="left", padx=10, pady=6)

        self.leave_btn = ctk.CTkButton(
            self.match_card,
            text="🚪 Leave Room",
            width=95,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#c0392b",
            hover_color="#962d22",
            command=self._leave_lounge
        )
        self.leave_btn.pack(side="right", padx=10, pady=5)

        # Main Split inside Room: Left = Chat (55%), Right = Garena Player Roster (45%)
        self.room_split = ctk.CTkFrame(self.active_room_frame, fg_color="transparent")
        self.room_split.pack(fill="both", expand=True, pady=(0, 6))
        self.room_split.columnconfigure(0, weight=6, minsize=320)  # Left: Chat
        self.room_split.columnconfigure(1, weight=5, minsize=280)  # Right: Player Table
        self.room_split.rowconfigure(0, weight=1)                 # Full vertical expansion to bottom

        # ----------------- LEFT: Live Room Chat Panel (Garena Console) -----------------
        chat_panel = ctk.CTkFrame(self.room_split, fg_color="#0c1017", corner_radius=6)
        chat_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        chat_hdr = ctk.CTkFrame(chat_panel, fg_color="#141a24", height=28, corner_radius=4)
        chat_hdr.pack(fill="x", padx=6, pady=(6, 4))
        ctk.CTkLabel(
            chat_hdr,
            text="💬 Room Lobby Chat",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#ecf0f1"
        ).pack(side="left", padx=10, pady=3)

        self.chat_display = ctk.CTkTextbox(
            chat_panel,
            fg_color="#080b10",
            text_color="#dcddde",
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
            corner_radius=4
        )
        self.chat_display.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.chat_display.configure(state="disabled")

        # Chat Input Row
        chat_input_box = ctk.CTkFrame(chat_panel, fg_color="transparent")
        chat_input_box.pack(fill="x", padx=6, pady=(0, 4))

        self.chat_entry = ctk.CTkEntry(
            chat_input_box,
            placeholder_text="Type a message to room players...",
            height=30,
            fg_color="#141a24",
            border_color="#1c2833",
            text_color="#ffffff",
            font=ctk.CTkFont(size=12)
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.chat_entry.bind("<Return>", lambda event: self._send_room_chat())

        self.chat_send_btn = ctk.CTkButton(
            chat_input_box,
            text="Send",
            width=60,
            height=30,
            fg_color="#2980b9",
            hover_color="#3498db",
            font=ctk.CTkFont(weight="bold", size=11),
            command=self._send_room_chat
        )
        self.chat_send_btn.pack(side="right")

        # ----------------- RIGHT: Garena Player Table -----------------
        players_panel = ctk.CTkFrame(self.room_split, fg_color="#0c1017", corner_radius=6)
        players_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self.players_header_lbl = ctk.CTkLabel(
            players_panel,
            text="👥 Players in Room (1)",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#ecf0f1"
        )
        self.players_header_lbl.pack(anchor="w", padx=10, pady=(6, 2))

        # Garena Column Header Row
        tbl_hdr = ctk.CTkFrame(players_panel, fg_color="#141a24", height=26, corner_radius=4)
        tbl_hdr.pack(fill="x", padx=6, pady=(2, 4))
        ctk.CTkLabel(tbl_hdr, text="STA", font=ctk.CTkFont(size=10, weight="bold"), text_color="#bdc3c7", width=30).pack(side="left", padx=2)
        ctk.CTkLabel(tbl_hdr, text="Lvl", font=ctk.CTkFont(size=10, weight="bold"), text_color="#bdc3c7", width=28).pack(side="left", padx=2)
        ctk.CTkLabel(tbl_hdr, text="Username", font=ctk.CTkFont(size=10, weight="bold"), text_color="#bdc3c7", anchor="w").pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkLabel(tbl_hdr, text="Ping", font=ctk.CTkFont(size=10, weight="bold"), text_color="#bdc3c7", width=58).pack(side="left", padx=2)
        ctk.CTkLabel(tbl_hdr, text="Status", font=ctk.CTkFont(size=10, weight="bold"), text_color="#bdc3c7", width=80).pack(side="right", padx=6)

        self.players_scroll = ctk.CTkScrollableFrame(players_panel, fg_color="transparent")
        self.players_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        # ----------------- BOTTOM: Garena START GAME Bar -----------------
        self.room_actions = ctk.CTkFrame(self.active_room_frame, fg_color="#101826", corner_radius=6, height=84)
        self.room_actions.pack(fill="x", pady=(2, 0))
        self.room_actions.pack_propagate(False)

        # Ready to Play Checkbox (Always visible on left of bottom dock)
        self.ready_checkbox = ctk.CTkCheckBox(
            self.room_actions,
            text=" Ready to Play",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#2ecc71",
            fg_color="#27ae60",
            hover_color="#2ecc71",
            border_color="#2ecc71",
            border_width=2,
            command=self._handle_ready_toggle
        )
        self.ready_checkbox.pack(side="left", padx=(16, 12))

        # Host Console Status Label
        self.host_status_label = ctk.CTkLabel(
            self.room_actions,
            text="⚔️ Host Console: Waiting for players to check Ready",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#bdc3c7"
        )
        self.host_status_label.pack(side="left", padx=8)

        # Client Waiting Status Badge
        self.client_waiting_badge = ctk.CTkLabel(
            self.room_actions,
            text="⏳ Waiting for Host to Launch Game... (Warcraft III will launch automatically)",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#f1c40f"
        )

        # Pixel-Perfect Blue Garena START Outer Box with Circling Sparkling Glow
        self.start_glow_box = StartGlowBox(
            self.room_actions,
            btn_img=self.start_btn_img,
            hover_img=self.start_btn_hover_img,
            dis_img=self.start_btn_dis_img,
            command=self._handle_start_game,
            force_command=self._handle_force_start_game,
            width=84,
            height=80
        )
        self.start_game_btn = self.start_glow_box.button
        self.start_glow_box.pack(side="right", padx=12, pady=2)

    def _clear_chat(self):
        """Clears the room chat display."""
        if hasattr(self, "chat_display") and self.chat_display.winfo_exists():
            self.chat_display.configure(state="normal")
            self.chat_display.delete("1.0", "end")
            self.chat_display.configure(state="disabled")

    def _send_room_chat(self):
        msg = self.chat_entry.get().strip()
        if not msg:
            return
        self.chat_entry.delete(0, "end")
        if self.session:
            self.session.send_chat(msg)
            self._append_chat(self.current_user.username, msg)

    def _append_chat(self, sender: str, text: str):
        self.chat_display.configure(state="normal")
        ts_str = time.strftime("%H:%M")
        self.chat_display.insert("end", f"[{ts_str}] {sender}: {text}\n")
        self.chat_display.see("end")
        self.chat_display.configure(state="disabled")

    def _handle_start_game(self):
        """Host clicks START GAME: triggers synchronized launch across all PCs."""
        if not self.session or not self.session.is_host:
            return

        if hasattr(self, "start_glow_box"):
            self.start_glow_box.stop_glow()

        self.start_game_btn.configure(
            state="disabled",
            image=getattr(self, "start_btn_act_img", self.start_btn_img)
        )
        if hasattr(self, "host_status_label") and self.host_status_label.winfo_exists():
            self.host_status_label.configure(
                text="🚀 Launching Warcraft III for all room players...",
                text_color="#e67e22"
            )
        self.session.start_game_for_all()

        # Re-enable button after 5 seconds so host is never permanently locked out
        def reenable_launch():
            if hasattr(self, "_update_start_button_state"):
                self._update_start_button_state()
            elif hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
                self.start_game_btn.configure(
                    state="normal",
                    image=getattr(self, "start_btn_img", None)
                )
            if hasattr(self, "host_status_label") and self.host_status_label.winfo_exists():
                self.host_status_label.configure(
                    text="⚔️ Host Console: Click START when all players are ready to launch Warcraft III",
                    text_color="#bdc3c7"
                )
        self.after(5000, reenable_launch)

    def _on_game_launched_callback(self):
        """Triggered on Host and Clients when WC3 is launched."""
        def update():
            play_launch_sound()

            if hasattr(self, "match_card") and self.match_card.winfo_exists():
                self.match_card.configure(fg_color="#27ae60")
                if self.session and self.session.is_host:
                    self.match_card_label.configure(
                        text="Game Launched! Host your match in Local Area Network (LAN) tab for players to join."
                    )
                else:
                    self.match_card_label.configure(
                        text="Host launched game! Head straight to Local Area Network (LAN) tab in Warcraft III."
                    )

            for p_dict in self.player_rows.values():
                try:
                    if self.sta_battle_img:
                        p_dict["sta"].configure(image=self.sta_battle_img, text="")
                    else:
                        p_dict["sta"].configure(text="⚔️")
                except Exception:
                    pass

            # Auto-minimize Lounge to true Windows System Tray
            def auto_minimize_tray():
                top = self.winfo_toplevel()
                if hasattr(top, "tray_manager") and top.tray_manager:
                    top.tray_manager.minimize_to_tray(notify=True)
                else:
                    try:
                        top.iconify()
                    except Exception:
                        pass
            self.after(1200, auto_minimize_tray)

            # Background process watcher: restores window when Warcraft III exits
            def watch_game():
                time.sleep(3.0)
                while self.session and self.session.launcher and self.session.launcher.is_running():
                    time.sleep(1.5)

                def on_game_exit():
                    top = self.winfo_toplevel()
                    if hasattr(top, "tray_manager") and top.tray_manager:
                        top.tray_manager.restore_window()
                    else:
                        try:
                            top.deiconify()
                            top.lift()
                            top.focus_force()
                        except Exception:
                            pass

                    if hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
                        self.start_game_btn.configure(
                            state="normal",
                            image=self.start_btn_img
                        )
                    if hasattr(self, "host_status_label") and self.host_status_label.winfo_exists():
                        self.host_status_label.configure(
                            text="🎮 Warcraft III is Running! (Click START to launch again if needed)",
                            text_color="#2ecc71"
                        )
                    if hasattr(self, "match_card") and self.match_card.winfo_exists():
                        self.match_card.configure(fg_color="#1e3799")
                        self.match_card_label.configure(text="Game closed. Ready to launch next match!")

                self.after(0, on_game_exit)

            threading.Thread(target=watch_game, daemon=True, name="GameProcessWatcher").start()

        self.after(0, update)

    def _start_hosting(self, title: str, max_players: int = 10, password: str = ""):
        self.create_lounge_btn = None
        self.current_room_title = title
        threading.Thread(target=self._async_host, args=(title, max_players, password), daemon=True).start()

    def _async_host(self, title: str, max_players: int = 10, password: str = ""):
        self.session = LoungeSession(is_host=True, player_name=self.current_user.username)
        self.session.on_peer_joined = self._on_peer_joined_callback
        self.session.on_peer_left = self._on_peer_left_callback
        self.session.on_match_detected = self._on_match_detected_callback
        self.session.on_peer_ping = self._on_peer_ping_callback
        self.session.on_peer_reconnecting = self._on_peer_reconnecting_callback
        self.session.on_chat_received = self._on_chat_received_callback
        self.session.on_game_launched = self._on_game_launched_callback
        self.session.on_player_ready_changed = self._on_player_ready_changed_callback
        self.session.on_player_kicked = self._on_player_kicked_callback
        self.session.on_kicked_by_host = self._on_kicked_by_host_callback

        code = self.session.start_host()

        def update():
            if code:
                # Announce to Lobby Browser
                local_port = self.session.puncher.socket.getsockname()[1] if (self.session.puncher and self.session.puncher.socket) else 0
                local_ip = "127.0.0.1"
                try:
                    if self.session.puncher and self.session.puncher.stun_result:
                        local_ip = self.session.puncher.stun_result.local_ip or "127.0.0.1"
                except Exception:
                    pass

                self.lobby_service.start_hosting_room(
                    title=title,
                    room_code=code,
                    max_players=max_players,
                    local_port=local_port,
                    local_ip=local_ip,
                    password=password
                )

                self.room_title_label.configure(text=f"🎮 Room: {title} (Host: {self.current_user.username})")
                self.match_card.configure(fg_color="#1e3799")
                self.match_card_label.configure(text="Host: Open Warcraft III -> Local Area Network and host match!")

                # Preserve any player cards already added (e.g. from invite flow)
                # Only add self if not already present
                if self.current_user.username not in self.player_rows:
                    self._add_player_card(self.current_user.username, is_self=True, is_host=True)

                # Also re-add any peers that connected during startup
                if self.session and self.session.connected_peers:
                    for peer, username in self.session.connected_peers.items():
                        if username not in self.player_rows:
                            self._add_player_card(username, is_self=False, is_host=False)

                if hasattr(self, "start_glow_box") and self.start_glow_box.winfo_exists():
                    self.start_glow_box.pack(side="right", padx=12, pady=2)
                elif hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
                    self.start_game_btn.pack(side="right", padx=12, pady=3)
                if hasattr(self, "ready_checkbox") and self.ready_checkbox.winfo_exists():
                    self.ready_checkbox.pack(side="left", padx=(16, 12))
                    self.ready_checkbox.select()
                    self.is_self_ready = True
                    self.player_readiness[self.current_user.username] = True
                if hasattr(self, "host_status_label") and self.host_status_label.winfo_exists():
                    self.host_status_label.pack(side="left", padx=8)
                if hasattr(self, "client_waiting_badge") and self.client_waiting_badge.winfo_exists():
                    self.client_waiting_badge.pack_forget()

                self.lobby_selection_frame.pack_forget()
                self.active_room_frame.pack(fill="both", expand=True)
                self._append_chat("System", f"Room '{title}' created! Waiting for players to join.")
                self._update_start_button_state()

        self.after(0, update)

    def _on_peer_reconnecting_callback(self, username: str):
        def update():
            target_key = None
            for key in self.player_rows.keys():
                if key.lower().strip() == username.lower().strip():
                    target_key = key
                    break
            if target_key:
                row_data = self.player_rows[target_key]
                row_data["ping"].configure(text="🟡 Reconnecting...", text_color="#f39c12")
        self.after(0, update)

    def _handle_incoming_nat_syn(
        self,
        sender: str,
        client_pub_ip: str,
        client_pub_port: int,
        client_lan_ip: str,
        client_local_port: int,
        client_version: str
    ):
        """Host: punches outbound hole toward joining client so host firewall permits inbound traffic."""
        if not self.session or not self.session.is_host:
            return

        if client_version != APP_VERSION:
            logger.warning(f"Peer '{sender}' is running version v{client_version} (Host on v{APP_VERSION})")
            self.after(0, lambda: self._append_chat("System", f"⚠️ Notice: '{sender}' is running v{client_version}. Both players should run v{APP_VERSION}."))

        fallbacks = []
        if client_local_port:
            fallbacks.append(("127.0.0.1", client_local_port))
            if client_lan_ip and client_lan_ip != "127.0.0.1":
                fallbacks.append((client_lan_ip, client_local_port))

        self.session.punch_toward_peer(client_pub_ip, client_pub_port, fallback_targets=fallbacks)

    def _join_public_room(self, room: LobbyRoom):
        """1-Click Join from Public Lobby Browser with password check."""
        if room.is_locked:
            import hashlib
            dialog = ctk.CTkInputDialog(
                text=f"Room '{room.title}' is private.\nEnter password to join:",
                title="🔒 Password Required"
            )
            pw = dialog.get_input()
            if pw is None:
                return
            entered_hash = hashlib.sha256(pw.strip().encode()).hexdigest()[:16]
            if entered_hash != room.password_hash:
                messagebox.showerror("Access Denied", "Incorrect room password. Please try again.")
                return

        self.current_room_title = room.title
        threading.Thread(
            target=self._async_join,
            args=(room.lounge_code, room.local_port, room.local_ip, room.host_username, room.app_version),
            daemon=True
        ).start()

    def _async_join(
        self,
        code: str,
        local_port: int = 0,
        local_ip: str = "",
        host_username: str = "",
        expected_version: str = ""
    ):
        if expected_version and expected_version != APP_VERSION:
            def warn_ver():
                messagebox.showwarning(
                    "Version Mismatch",
                    f"Room host is running v{expected_version}, but you are running v{APP_VERSION}.\n\n"
                    f"Both players must update to v{APP_VERSION} for multiplayer to work properly."
                )
            self.after(0, warn_ver)

        # Always terminate previous session or hosting before joining to release sockets
        self.lobby_service.stop_hosting_room()
        if self.session:
            try:
                self.session.shutdown()
            except Exception:
                pass
            self.session = None

        from ..core.tcp_tunnel import ClientTCPBridge
        if ClientTCPBridge.is_port_in_use(WC3_DEFAULT_PORT):
            def port_err():
                messagebox.showerror(
                    "Port Conflict",
                    "Warcraft III port 6112 is already in use by another program or Warcraft III.\n\n"
                    "Please close Warcraft III first, then click 'Join Game'. (The app will automatically launch Warcraft III for you when the host starts the game.)"
                )
            self.after(0, port_err)
            return

        self.session = LoungeSession(is_host=False, player_name=self.current_user.username)
        self.session.on_peer_joined = self._on_peer_joined_callback
        self.session.on_peer_left = self._on_peer_left_callback
        self.session.on_match_detected = self._on_match_detected_callback
        self.session.on_peer_ping = self._on_peer_ping_callback
        self.session.on_peer_reconnecting = self._on_peer_reconnecting_callback
        self.session.on_chat_received = self._on_chat_received_callback
        self.session.on_game_launched = self._on_game_launched_callback
        self.session.on_player_ready_changed = self._on_player_ready_changed_callback
        self.session.on_player_kicked = self._on_player_kicked_callback
        self.session.on_kicked_by_host = self._on_kicked_by_host_callback

        def _send_punch_syn(h_user, c_ip, c_port, c_lan, c_lport):
            self.friend_manager.send_nat_traversal_syn(h_user, c_ip, c_port, c_lan, c_lport)

        success = self.session.join_lounge(
            code,
            local_port=local_port,
            local_ip=local_ip,
            host_username=host_username,
            on_request_host_punch=_send_punch_syn
        )

        def update():
            if success:
                self.room_title_label.configure(text=f"🎮 Room: {self.current_room_title}")
                self.match_card.configure(fg_color="#1e3799")
                self.match_card_label.configure(text="Connected to Room! Chat with host or wait for game launch.")
                self._reset_player_cards(is_host=False)

                # Sync any peers that connected before the UI update ran
                if self.session and self.session.connected_peers:
                    for peer, username in self.session.connected_peers.items():
                        if username not in self.player_rows:
                            is_host_peer = True  # As a client, connected peer is the host
                            self._add_player_card(username, is_self=False, is_host=is_host_peer)

                if hasattr(self, "start_glow_box") and self.start_glow_box.winfo_exists():
                    self.start_glow_box.stop_glow()
                    self.start_glow_box.pack_forget()
                elif hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
                    self.start_game_btn.pack_forget()
                if hasattr(self, "host_status_label") and self.host_status_label.winfo_exists():
                    self.host_status_label.pack_forget()
                if hasattr(self, "ready_checkbox") and self.ready_checkbox.winfo_exists():
                    self.ready_checkbox.pack(side="left", padx=(16, 12))
                    self.ready_checkbox.deselect()
                    self.is_self_ready = False
                    self.player_readiness[self.current_user.username] = False
                if hasattr(self, "client_waiting_badge") and self.client_waiting_badge.winfo_exists():
                    self.client_waiting_badge.pack(side="left", padx=8)

                self.lobby_selection_frame.pack_forget()
                self.active_room_frame.pack(fill="both", expand=True)
                self._append_chat("System", f"Joined room '{self.current_room_title}'! Check 'Ready to Play' when set.")
            else:
                from ..core.tcp_tunnel import ClientTCPBridge
                if ClientTCPBridge.is_port_in_use(WC3_DEFAULT_PORT):
                    messagebox.showerror(
                        "Port Conflict",
                        "Warcraft III port 6112 is in use. Please close Warcraft III before joining."
                    )
                else:
                    messagebox.showerror(
                        "Join Failed",
                        "Could not connect to host. The room code may be invalid, or network initialization timed out."
                    )

        self.after(0, update)

    def _on_chat_received_callback(self, sender: str, message: str):
        def update():
            if sender.lower().strip() != self.current_user.username.lower().strip():
                play_chat_sound()
            self._append_chat(sender, message)
        self.after(0, update)

    def _leave_lounge(self):
        self.lobby_service.stop_hosting_room()
        if self.session:
            self.session.shutdown()
            self.session = None

        self.player_readiness.clear()
        self.is_self_ready = False
        if hasattr(self, "start_glow_box"):
            self.start_glow_box.stop_glow()
        if hasattr(self, "ready_checkbox") and self.ready_checkbox.winfo_exists():
            self.ready_checkbox.deselect()

        if hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
            self.start_game_btn.configure(
                state="normal",
                image=self.start_btn_img
            )
        if hasattr(self, "host_status_label") and self.host_status_label.winfo_exists():
            self.host_status_label.configure(
                text="⚔️ Host Console: Waiting for players to check Ready",
                text_color="#bdc3c7"
            )

        for child in self.players_scroll.winfo_children():
            child.destroy()
        self.player_rows.clear()

        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", "end")
        self.chat_display.configure(state="disabled")

        self.active_room_frame.pack_forget()
        self.lobby_selection_frame.pack(fill="both", expand=True)
        self._refresh_rooms_list()

    def _handle_ready_toggle(self):
        """Toggles 'Ready to Play' checkbox for self."""
        self.is_self_ready = bool(self.ready_checkbox.get())
        self.player_readiness[self.current_user.username] = self.is_self_ready
        if self.session:
            self.session.set_ready(self.is_self_ready)

        status_txt = "READY!" if self.is_self_ready else "NOT READY."
        self._append_chat("System", f"You are now {status_txt}")
        # Update self status in player roster
        if self.current_user.username in self.player_rows:
            entry = self.player_rows[self.current_user.username]
            is_host = (self.session.is_host if self.session else False)
            if is_host:
                color = "#2ecc71" if self.is_self_ready else "#f39c12"
                txt = "👑 READY" if self.is_self_ready else "👑 Host"
            else:
                color = "#2ecc71" if self.is_self_ready else "#95a5a6"
                txt = "🟢 READY" if self.is_self_ready else "⚪ NOT READY"
            entry["rank"].configure(text=txt, text_color=color)

        if self.session and self.session.is_host:
            self._update_start_button_state()

    def _on_player_ready_changed_callback(self, username: str, is_ready: bool):
        """Called when a player toggles ready."""
        def update():
            self.player_readiness[username] = is_ready
            status_txt = "READY!" if is_ready else "NOT READY."
            self._append_chat("System", f"Player '{username}' is {status_txt}")
            # Update row in roster
            target_key = None
            for key in self.player_rows.keys():
                if key.lower().strip() == username.lower().strip():
                    target_key = key
                    break
            if target_key and target_key in self.player_rows:
                entry = self.player_rows[target_key]
                color = "#2ecc71" if is_ready else "#95a5a6"
                txt = "🟢 READY" if is_ready else "⚪ NOT READY"
                entry["rank"].configure(text=txt, text_color=color)

            self._update_start_button_state()
        self.after(0, update)

    def _update_start_button_state(self):
        """Updates host START button state and status message based on peer readiness."""
        if not self.session or not self.session.is_host:
            return

        if not self.is_self_ready:
            if hasattr(self, "start_glow_box"):
                self.start_glow_box.stop_glow()
            self.start_game_btn.configure(
                state="disabled",
                image=getattr(self, "start_btn_dis_img", self.start_btn_img)
            )
            self.host_status_label.configure(
                text="⚠️ You are NOT READY. Check 'Ready to Play' • Right-click START to force launch",
                text_color="#f39c12"
            )
            return

        non_host_players = [
            u for u in self.player_rows.keys()
            if u.lower().strip() != self.current_user.username.lower().strip()
        ]

        if not non_host_players:
            # Host alone: allowed to start solo
            all_ready = True
            ready_count = 0
            total_count = 0
        else:
            total_count = len(non_host_players)
            ready_count = sum(1 for u in non_host_players if self.player_readiness.get(u, False))
            all_ready = (ready_count == total_count)

        if all_ready:
            self.start_game_btn.configure(
                state="normal",
                image=self.start_btn_img
            )
            if hasattr(self, "start_glow_box"):
                self.start_glow_box.start_glow()
            if not non_host_players:
                self.host_status_label.configure(
                    text="⚔️ Host Console: Solo testing / Ready to launch Warcraft III",
                    text_color="#bdc3c7"
                )
            else:
                self.host_status_label.configure(
                    text=f"✅ All players READY ({ready_count}/{total_count})! Click START to launch",
                    text_color="#2ecc71"
                )
        else:
            if hasattr(self, "start_glow_box"):
                self.start_glow_box.stop_glow()
            self.start_game_btn.configure(
                state="disabled",
                image=getattr(self, "start_btn_dis_img", self.start_btn_img)
            )
            self.host_status_label.configure(
                text=f"⚠️ Waiting for players to be Ready ({ready_count}/{total_count}) • Right-click START to force launch",
                text_color="#f39c12"
            )

    def _handle_force_start_game(self, event=None):
        """Host right-clicks START button for permissive force launch."""
        if not self.session or not self.session.is_host:
            return

        non_host_players = [
            u for u in self.player_rows.keys()
            if u.lower().strip() != self.current_user.username.lower().strip()
        ]
        unready_players = [u for u in non_host_players if not self.player_readiness.get(u, False)]
        if not unready_players:
            # All ready anyway
            self._handle_start_game()
            return

        unready_names = ", ".join(f"'{u}'" for u in unready_players)
        msg = f"The following players have not checked Ready:\n{unready_names}\n\nDo you want to Force Launch Warcraft III for all players anyway?"
        if messagebox.askyesno("Force Launch Game", msg):
            self._append_chat("System", "Host force-launched the game without all players ready!")
            self._handle_start_game()

    def _kick_player(self, username: str):
        """Host kicks a player from the room."""
        if not self.session or not self.session.is_host:
            return
        if messagebox.askyesno("Kick Player", f"Are you sure you want to kick '{username}' from the room?"):
            logger.info(f"Host kicking player '{username}'...")
            self.session.kick_player(username)
            self._append_chat("System", f"Player '{username}' was kicked by the host.")
            # Remove from roster
            target_key = None
            for p in list(self.player_rows.keys()):
                if p.lower().strip() == username.lower().strip():
                    target_key = p
                    break
            if target_key:
                p_data = self.player_rows.pop(target_key)
                if isinstance(p_data, dict):
                    p_data["row"].destroy()
                self._update_players_count_display()
            self.player_readiness.pop(username, None)
            self._update_start_button_state()

    def _on_kicked_by_host_callback(self):
        """Called when local client is kicked by the host."""
        def update():
            logger.warning("Local client was kicked from room by host.")
            self._leave_lounge()
            messagebox.showwarning("Kicked from Room", "You were kicked from the room by the host.")
        self.after(0, update)

    def _on_player_kicked_callback(self, username: str):
        """Called when another player is kicked by the host."""
        def update():
            self._append_chat("System", f"Player '{username}' was kicked by the host.")
            target_key = None
            for p in list(self.player_rows.keys()):
                if p.lower().strip() == username.lower().strip():
                    target_key = p
                    break
            if target_key:
                p_data = self.player_rows.pop(target_key)
                if isinstance(p_data, dict):
                    p_data["row"].destroy()
                self._update_players_count_display()
            self.player_readiness.pop(username, None)
            self._update_start_button_state()
        self.after(0, update)

    def _add_player_card(self, username: str, is_self: bool = False, is_host: bool = False, initial_text: Optional[str] = None):
        target_key = None
        for key in self.player_rows.keys():
            if key.lower().strip() == username.lower().strip():
                target_key = key
                break

        if target_key:
            if initial_text:
                entry = self.player_rows[target_key]
                color = "#2ecc71" if "Connected" in initial_text else "#7f8c8d"
                entry["ping"].configure(text=initial_text, text_color=color)
            return

        row = ctk.CTkFrame(self.players_scroll, fg_color="#141a24", corner_radius=4, height=30)
        row.pack(fill="x", pady=2, padx=2)

        # 1. STA Icon (Status: authentic Garena chat bubble or battle swords)
        if self.sta_chat_img:
            sta_lbl = ctk.CTkLabel(row, image=self.sta_chat_img, text="", width=30)
        else:
            sta_lbl = ctk.CTkLabel(row, text="💬", width=30, font=ctk.CTkFont(size=12))
        sta_lbl.pack(side="left", padx=2, pady=3)

        # 2. Lvl (Host = 25, Client = 1)
        lvl_val = "25" if is_host else "1"
        lvl_lbl = ctk.CTkLabel(
            row,
            text=lvl_val,
            width=28,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#f39c12" if is_host else "#bdc3c7"
        )
        lvl_lbl.pack(side="left", padx=2, pady=3)

        # 3. Username
        tag = " (You)" if is_self else ""
        name_lbl = ctk.CTkLabel(
            row,
            text=f"{username}{tag}",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#ffffff",
            anchor="w"
        )
        name_lbl.pack(side="left", fill="x", expand=True, padx=4, pady=3)

        # 4. Ping
        ping_text = "0 ms" if is_self else (initial_text or "...")
        ping_color = "#2ecc71" if (is_self or (initial_text and "Connected" in initial_text)) else "#7f8c8d"
        ping_lbl = ctk.CTkLabel(
            row,
            text=ping_text,
            width=58,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=ping_color
        )
        ping_lbl.pack(side="left", padx=2, pady=3)

        # 5. Status / Readiness (Host or Ready/Not Ready)
        if is_host:
            ready_state = self.player_readiness.get(username, True)
            status_text = "👑 READY" if ready_state else "👑 Host"
            status_color = "#2ecc71" if ready_state else "#f39c12"
        else:
            ready_state = self.player_readiness.get(username, False)
            status_text = "🟢 READY" if ready_state else "⚪ NOT READY"
            status_color = "#2ecc71" if ready_state else "#95a5a6"

        rank_lbl = ctk.CTkLabel(
            row,
            text=status_text,
            width=80,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=status_color
        )
        rank_lbl.pack(side="right", padx=6, pady=3)

        self.player_rows[username] = {
            "row": row,
            "ping": ping_lbl,
            "sta": sta_lbl,
            "lvl": lvl_lbl,
            "name": name_lbl,
            "rank": rank_lbl
        }

        # Right-click kick menu for Host to Kick non-host players
        if not is_self:
            def _show_kick_menu(event, u=username):
                if not (self.session and self.session.is_host):
                    return
                menu = tk.Menu(self, tearoff=0, bg="#1a232f", fg="#ecf0f1", activebackground="#c0392b", activeforeground="#ffffff")
                menu.add_command(label=f"🥾 Kick '{u}' from Room", command=lambda: self._kick_player(u))
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()

            for w in (row, name_lbl, sta_lbl, lvl_lbl, ping_lbl, rank_lbl):
                w.bind("<Button-3>", _show_kick_menu)

        self._update_players_count_display()

    def _reset_player_cards(self, is_host: bool):
        for child in self.players_scroll.winfo_children():
            child.destroy()
        self.player_rows.clear()
        self._add_player_card(self.current_user.username, is_self=True, is_host=is_host)

    def _update_players_count_display(self):
        count = len(self.player_rows)
        self.players_header_lbl.configure(text=f"👥 Players in Room ({count})")
        if self.session and self.session.is_host:
            self.lobby_service.update_player_count(count)

    def _on_peer_ping_callback(self, peer_name: str, rtt_ms: float):
        def update():
            target_key = None
            for key in self.player_rows.keys():
                if key.lower().strip() == peer_name.lower().strip():
                    target_key = key
                    break

            if not target_key:
                is_host_peer = not (self.session.is_host if self.session else True)
                self._add_player_card(peer_name, is_self=False, is_host=is_host_peer)
                target_key = peer_name

            if target_key in self.player_rows:
                entry = self.player_rows[target_key]
                if rtt_ms < 60.0:
                    color = "#2ecc71"
                elif rtt_ms < 120.0:
                    color = "#f1c40f"
                else:
                    color = "#e74c3c"
                entry["ping"].configure(text=f"{int(rtt_ms)} ms", text_color=color)

        self.after(0, update)

    def _on_peer_joined_callback(self, peer, username):
        def update():
            play_join_sound()
            self._add_player_card(username, is_self=False, is_host=False, initial_text="Connected")
            self._append_chat("System", f"Player '{username}' joined the room!")
            self.player_readiness[username] = False
            self._update_start_button_state()
            # If local player is a client and already ready, sync readiness to the peer
            if self.session and not self.session.is_host and self.is_self_ready:
                self.session.set_ready(True)
        self.after(0, update)

    def _on_peer_left_callback(self, username: str):
        def update():
            play_leave_sound()
            target_key = None
            for p in list(self.player_rows.keys()):
                if p.lower().strip() == username.lower().strip():
                    target_key = p
                    break
            if target_key:
                p_data = self.player_rows.pop(target_key)
                if isinstance(p_data, dict):
                    p_data["row"].destroy()
                elif isinstance(p_data, tuple):
                    p_data[0].destroy()
                self.player_readiness.pop(username, None)
                self._update_players_count_display()
                self._append_chat("System", f"Player '{username}' left the room.")
                self._update_start_button_state()
        self.after(0, update)

    def _on_match_detected_callback(self, match_name, open_slots, total_slots):
        def update():
            self.match_card.configure(fg_color="#27ae60")
            self.match_card_label.configure(
                text=f"Match Live: '{match_name}' | Slots: {open_slots}/{total_slots} | Visible in WC3 LAN!"
            )
            for p_dict in self.player_rows.values():
                try:
                    if self.sta_battle_img:
                        p_dict["sta"].configure(image=self.sta_battle_img, text="")
                    else:
                        p_dict["sta"].configure(text="⚔️")
                except Exception:
                    pass
        self.after(0, update)

    def _launch_game(self):
        if self.session:
            self.session.launch_game()
        else:
            self.launcher.launch()

    def _toggle_sound_mute(self):
        currently_muted = is_sound_muted()
        set_sound_muted(not currently_muted)
        new_muted = is_sound_muted()
        self.mute_btn.configure(
            text="🔇 Muted" if new_muted else "🔊 Sound",
            fg_color="#7f8c8d" if new_muted else "#2980b9",
            hover_color="#95a5a6" if new_muted else "#3498db"
        )

    def _is_friend_online_or_hosting(self, friend_name: str) -> bool:
        """Checks if a friend is online via signaling presence OR hosting a room in the browser."""
        if self.friend_manager.is_friend_online(friend_name):
            return True
        f_clean = friend_name.lower().strip()
        for room in self.lobby_service.active_rooms.values():
            if room.host_username.lower().strip() == f_clean:
                return True
        return False

    # Friends List & Signaling Handlers
    def _refresh_friends_list(self):
        # Update any open Private Chat dialogs with latest online presence
        for f_name, dialog in list(self._active_pm_dialogs.items()):
            if dialog and dialog.winfo_exists():
                is_on = self._is_friend_online_or_hosting(dialog.friend_name)
                dialog.update_presence(is_on)

        for child in self.friends_scroll.winfo_children():
            child.destroy()

        friends = self.friend_manager.get_friends()
        if not friends:
            empty_lbl = ctk.CTkLabel(
                self.friends_scroll,
                text="No friends yet.\nType username above\nand click '+ Add'!",
                text_color="gray",
                font=ctk.CTkFont(size=12),
                justify="center"
            )
            empty_lbl.pack(pady=20, padx=10)
            return

        for friend in friends:
            row = ctk.CTkFrame(self.friends_scroll, fg_color="#141a24", corner_radius=4)
            row.pack(fill="x", pady=2, padx=2)

            is_online = self._is_friend_online_or_hosting(friend)
            dot_char = "🟢" if is_online else "⚪"
            dot_color = "#2ecc71" if is_online else "#7f8c8d"

            status_lbl = ctk.CTkLabel(
                row,
                text=dot_char,
                font=ctk.CTkFont(size=12),
                text_color=dot_color,
                width=18
            )
            status_lbl.pack(side="left", padx=(6, 2), pady=4)

            name_lbl = ctk.CTkLabel(
                row,
                text=friend,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                text_color="#ecf0f1",
                anchor="w"
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=(2, 4), pady=4)

            # Invite Button: always enabled so you can invite friends anytime
            invite_btn = ctk.CTkButton(
                row,
                text="Invite",
                width=60,
                height=24,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#27ae60" if is_online else "#1e3752",
                hover_color="#219150" if is_online else "#2c3e50",
                state="normal"
            )
            invite_btn.configure(command=lambda f=friend, b=invite_btn: self._handle_invite_friend(f, b))
            invite_btn.pack(side="right", padx=6, pady=4)

            # Right-click context menu on row & label for Private Message and Remove Friend
            def show_friend_menu(event, f=friend, b=invite_btn, online=is_online):
                menu = tk.Menu(self, tearoff=0)
                menu.add_command(
                    label=f"💬 Private Message '{f}'",
                    command=lambda: self._open_private_chat(f)
                )
                menu.add_command(
                    label=f"🎮 Invite '{f}'",
                    command=lambda: self._handle_invite_friend(f, b)
                )
                menu.add_separator()
                menu.add_command(
                    label=f"❌ Remove Friend '{f}'",
                    command=lambda: self._handle_remove_friend(f)
                )
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()

            row.bind("<Button-3>", lambda e, f=friend, b=invite_btn, on=is_online: show_friend_menu(e, f, b, on))
            name_lbl.bind("<Button-3>", lambda e, f=friend, b=invite_btn, on=is_online: show_friend_menu(e, f, b, on))
            status_lbl.bind("<Button-3>", lambda e, f=friend, b=invite_btn, on=is_online: show_friend_menu(e, f, b, on))

    def _open_private_chat(self, friend_name: str) -> Optional[PrivateChatDialog]:
        """Opens or brings to front an ephemeral in-memory direct message dialog."""
        is_online = self._is_friend_online_or_hosting(friend_name)
        key = friend_name.lower().strip()
        dialog = self._active_pm_dialogs.get(key)
        if dialog and dialog.winfo_exists():
            dialog.update_presence(is_online)
            dialog.lift()
            dialog.focus_force()
            return dialog

        dialog = PrivateChatDialog(
            master=self,
            current_username=self.current_user.username,
            friend_name=friend_name,
            friend_manager=self.friend_manager,
            on_close=self._on_private_chat_closed,
            is_online=is_online
        )
        self._active_pm_dialogs[key] = dialog
        dialog.lift()
        dialog.focus_force()
        return dialog

    def _on_private_chat_closed(self, friend_name: str):
        key = friend_name.lower().strip()
        self._active_pm_dialogs.pop(key, None)

    def _handle_incoming_direct_message(self, sender: str, text: str):
        def show():
            play_chat_sound()
            dialog = self._open_private_chat(sender)
            dialog.append_message(sender, text)
            top = self.winfo_toplevel()
            if hasattr(top, "tray_manager") and top.tray_manager:
                top.tray_manager.show_notification(
                    f"💬 Message from {sender}",
                    text[:120]
                )
        self.after(0, show)

    def _handle_add_friend(self):
        target = self.friend_entry.get().strip()
        if not target:
            return
        success, msg = self.friend_manager.send_friend_request(target)
        self.friend_entry.delete(0, "end")
        if success:
            self._refresh_friends_list()
            self.friend_entry.configure(placeholder_text=f"Request sent to {target}!")
        else:
            self.friend_entry.configure(placeholder_text=msg[:25])

    def _handle_remove_friend(self, friend: str):
        if messagebox.askyesno(
            "Remove Friend",
            f"Are you sure you want to remove '{friend}' from your friends list?"
        ):
            if self.friend_manager.remove_friend(friend):
                self._refresh_friends_list()

    def _handle_incoming_friend_request(self, from_user: str):
        def show():
            self.pending_friend_requester = from_user
            self.friend_req_label.configure(text=f"👤 {from_user} sent you a Friend Request!")
            self.friend_req_banner.pack(fill="x", padx=15, pady=(0, 10), before=self.main_split)
        self.after(0, show)

    def _accept_friend_request(self):
        if hasattr(self, "pending_friend_requester") and self.pending_friend_requester:
            target = self.pending_friend_requester
            self.friend_req_banner.pack_forget()
            self.pending_friend_requester = None
            self.friend_manager.accept_friend_request(target)
            self._refresh_friends_list()

    def _decline_friend_request(self):
        self.friend_req_banner.pack_forget()
        self.pending_friend_requester = None

    def _handle_friend_accepted(self, friend_name: str):
        self.after(0, self._refresh_friends_list)

    def _handle_invite_friend(self, friend: str, btn: Optional[ctk.CTkButton] = None):
        if btn and btn.winfo_exists():
            btn.configure(state="disabled", text="Invited...")
            def reenable():
                if btn.winfo_exists():
                    btn.configure(state="normal", text="Invite")
            self.after(2000, reenable)

        if not self.session or not self.session.is_host or not self.session.lounge_code:
            self._start_hosting(f"{self.current_user.username}'s Game")
            self._add_player_card(friend, is_self=False, is_host=False, initial_text="⚪ Invited (Connecting...)")
            # Invite must wait for hosting to finish (lounge_code becomes available)
            def _wait_and_send_invite():
                for _ in range(60):  # Wait up to 6 seconds for session to be ready
                    if self.session and self.session.lounge_code:
                        self._send_invite_to(friend)
                        return
                    time.sleep(0.1)
                logger.warning(f"Timed out waiting for session to start before sending invite to {friend}")
            threading.Thread(target=_wait_and_send_invite, daemon=True, name="InviteWaiter").start()
            return

        self._add_player_card(friend, is_self=False, is_host=False, initial_text="⚪ Invited (Connecting...)")
        self._send_invite_to(friend)

    def _send_invite_to(self, friend: str):
        if not self.session or not self.session.lounge_code:
            return
        local_port = 0
        local_ip = "127.0.0.1"
        if self.session.puncher and self.session.puncher.socket:
            try:
                local_port = self.session.puncher.socket.getsockname()[1]
                if self.session.puncher.stun_result:
                    local_ip = self.session.puncher.stun_result.local_ip or "127.0.0.1"
            except Exception:
                pass

        threading.Thread(
            target=lambda: self.friend_manager.send_invite(
                friend,
                self.session.lounge_code,
                local_port=local_port,
                local_ip=local_ip
            ),
            daemon=True
        ).start()

    def _handle_incoming_invite(self, invite: GameInvite):
        def show():
            self.active_invite = invite
            self.invite_label.configure(text=f"🎮 {invite.host_username} invited you to play Warcraft III!")
            self.invite_banner.pack(fill="x", padx=15, pady=(0, 10), before=self.main_split)
            self.after(75000, self._decline_invite)
            top = self.winfo_toplevel()
            if hasattr(top, "tray_manager") and top.tray_manager:
                top.tray_manager.show_notification(
                    "🎮 Warcraft III Game Invite",
                    f"{invite.host_username} invited you to play Warcraft III!"
                )
        self.after(0, show)

    def _accept_invite(self):
        if not self.active_invite:
            return
        invite = self.active_invite
        self.invite_banner.pack_forget()
        self.active_invite = None
        self.current_room_title = f"{invite.host_username}'s Game"
        self.lobby_service.stop_hosting_room()
        if self.session:
            try:
                self.session.shutdown()
            except Exception:
                pass
            self.session = None
        threading.Thread(
            target=self._async_join,
            args=(invite.room_code, invite.local_port, invite.local_ip, invite.host_username, invite.app_version),
            daemon=True
        ).start()

    def _decline_invite(self):
        self.invite_banner.pack_forget()
        self.active_invite = None

    def _refresh_game_path_ui(self):
        if self.launcher.exe_path:
            self.path_label.configure(
                text=f"Warcraft III Path: {self.launcher.exe_path}",
                text_color="#2ecc71"
            )
        else:
            self.path_label.configure(
                text="Warcraft III Path: Not found! Click Browse to locate war3.exe",
                text_color="#e74c3c"
            )

    def _browse_executable(self):
        filename = filedialog.askopenfilename(
            title="Select Warcraft III Executable",
            filetypes=[("Executable Files", "war3.exe;Frozen Throne.exe"), ("All Files", "*.*")]
        )
        if filename:
            self.launcher.save_path(filename)
            self._refresh_game_path_ui()

    def _handle_logout(self):
        self._is_polling_rooms = False
        self.lobby_service.stop_hosting_room()
        self.friend_manager.stop_listening()
        for dialog in list(self._active_pm_dialogs.values()):
            try:
                dialog.destroy()
            except Exception:
                pass
        self._active_pm_dialogs.clear()
        if self.session:
            self.session.shutdown()
            self.session = None
        self.on_logout()
