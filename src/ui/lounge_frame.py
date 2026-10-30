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

class PrivateChatDialog(ctk.CTkToplevel):
    """
    Ephemeral Direct Message Dialog.
    Strictly in-memory (RAM only) - messages are destroyed when window is closed.
    Never persisted to disk or cloud database.
    """

    def __init__(
        self,
        master,
        current_username: str,
        friend_name: str,
        friend_manager: FriendManager,
        on_close: Optional[Callable[[str], None]] = None
    ):
        super().__init__(master)
        self.current_username = current_username
        self.friend_name = friend_name
        self.friend_manager = friend_manager
        self.on_close = on_close

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
        title_lbl.pack(side="left", padx=14, pady=8)

        notice_lbl = ctk.CTkLabel(
            header,
            text="🔒 Ephemeral Chat (Destroyed on Close)",
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
        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.pack(fill="x", side="bottom", padx=12, pady=(0, 12))

        self.msg_entry = ctk.CTkEntry(
            input_frame,
            placeholder_text=f"Message @{self.friend_name}...",
            fg_color="#40444b",
            border_color="#202225",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=36
        )
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.msg_entry.bind("<Return>", lambda e: self._send_message())

        send_btn = ctk.CTkButton(
            input_frame,
            text="Send",
            width=70,
            height=36,
            fg_color="#5865f2",
            hover_color="#4752c4",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._send_message
        )
        send_btn.pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.msg_entry.focus_set()

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
        self.current_room_title: str = "Warcraft III Lounge"
        self._is_polling_rooms = True
        self._active_pm_dialogs: dict = {}

        self._build_ui()
        self._refresh_game_path_ui()
        self._refresh_friends_list()

        # Start signaling listener for friend requests, direct invites, presence, and PMs
        self.friend_manager.start_listening(
            on_invite=self._handle_incoming_invite,
            on_friend_request=self._handle_incoming_friend_request,
            on_friend_accepted=self._handle_friend_accepted,
            on_presence_changed=lambda: self.after(0, self._refresh_friends_list),
            on_direct_message=self._handle_incoming_direct_message
        )

        # Start live lobby browser polling
        self._start_lobby_polling()

    def _build_ui(self):
        # 1. Top Bar: User info + Logout
        self.top_bar = ctk.CTkFrame(self, fg_color=("gray85", "#1e1e1e"), height=50)
        self.top_bar.pack(fill="x", padx=15, pady=(15, 8))

        self.user_label = ctk.CTkLabel(
            self.top_bar,
            text=f"🎮 Logged in as: {self.current_user.username}",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.user_label.pack(side="left", padx=(15, 8), pady=10)

        # Version Badge
        self.version_badge = ctk.CTkLabel(
            self.top_bar,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#dfe4ea", "#2b2b2b"),
            corner_radius=6,
            padx=6,
            pady=2
        )
        self.version_badge.pack(side="left", padx=2, pady=10)

        self.logout_btn = ctk.CTkButton(
            self.top_bar,
            text="Logout",
            width=70,
            height=28,
            fg_color="#c0392b",
            hover_color="#962d22",
            command=self._handle_logout
        )
        self.logout_btn.pack(side="right", padx=15, pady=10)

        # Sound Mute Toggle (Item 5)
        self.mute_btn = ctk.CTkButton(
            self.top_bar,
            text="🔇 Muted" if is_sound_muted() else "🔊 Sound",
            width=75,
            height=28,
            font=ctk.CTkFont(size=12),
            fg_color="#7f8c8d" if is_sound_muted() else "#2980b9",
            hover_color="#95a5a6" if is_sound_muted() else "#3498db",
            command=self._toggle_sound_mute
        )
        self.mute_btn.pack(side="right", padx=(5, 5), pady=10)

        # 2. Game Path Bar
        self.path_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.path_bar.pack(fill="x", padx=15, pady=(0, 8))

        self.path_label = ctk.CTkLabel(
            self.path_bar,
            text="Warcraft III Path: Detecting...",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            anchor="w"
        )
        self.path_label.pack(side="left", fill="x", expand=True, padx=5)

        self.browse_btn = ctk.CTkButton(
            self.path_bar,
            text="Browse...",
            width=80,
            height=26,
            command=self._browse_executable
        )
        self.browse_btn.pack(side="right", padx=5)

        # 3. Notification Banners
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

        # 4. Main 2-Column Split: Left = Friends / Fast Actions, Right = Lobby Browser OR Active Room
        self.main_split = ctk.CTkFrame(self, fg_color="transparent")
        self.main_split.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.main_split.columnconfigure(0, weight=0, minsize=240)  # Left panel
        self.main_split.columnconfigure(1, weight=1)              # Right panel

        # Build Left Friends & Quick Test Panel
        self._build_friends_panel()

        # Build Right Container
        self.lounge_container = ctk.CTkFrame(self.main_split, fg_color="transparent")
        self.lounge_container.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self.lobby_selection_frame = ctk.CTkFrame(self.lounge_container, fg_color="transparent")
        self.active_room_frame = ctk.CTkFrame(self.lounge_container, fg_color="transparent")

        self._build_lobby_browser_ui()
        self._build_active_room_ui()

        self.lobby_selection_frame.pack(fill="both", expand=True)

    def _build_friends_panel(self):
        """Constructs left-hand Friends management & test pane."""
        self.friends_card = ctk.CTkFrame(self.main_split, corner_radius=10)
        self.friends_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        # Friends Title
        ctk.CTkLabel(
            self.friends_card,
            text="👥 Friends",
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(pady=(12, 6), padx=15, anchor="w")

        # Add Friend Input + Button
        self.add_friend_box = ctk.CTkFrame(self.friends_card, fg_color="transparent")
        self.add_friend_box.pack(fill="x", padx=10, pady=(0, 8))

        self.friend_entry = ctk.CTkEntry(
            self.add_friend_box,
            placeholder_text="Username...",
            height=30,
            font=ctk.CTkFont(size=12)
        )
        self.friend_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.add_friend_btn = ctk.CTkButton(
            self.add_friend_box,
            text="+ Add",
            width=50,
            height=30,
            command=self._handle_add_friend
        )
        self.add_friend_btn.pack(side="right")

        # Friends Scrollable List
        self.friends_scroll = ctk.CTkScrollableFrame(self.friends_card, fg_color="transparent")
        self.friends_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_lobby_browser_ui(self):
        """Constructs the GameRanger-style Public Game Browser."""
        # Top Action Bar inside Browser
        browser_header = ctk.CTkFrame(self.lobby_selection_frame, fg_color="transparent")
        browser_header.pack(fill="x", pady=(0, 8))

        # Host Game Button (Left)
        self.host_game_btn = ctk.CTkButton(
            browser_header,
            text="➕ Host Game",
            width=120,
            height=32,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#27ae60",
            hover_color="#219150",
            command=self._open_host_modal
        )
        self.host_game_btn.pack(side="left")

        # Refresh button (Right)
        self.refresh_btn = ctk.CTkButton(
            browser_header,
            text="🔄 Refresh",
            width=80,
            height=32,
            fg_color="#4b6584",
            hover_color="#3867d6",
            command=self._refresh_rooms_list
        )
        self.refresh_btn.pack(side="right")

        # Table Header
        table_hdr = ctk.CTkFrame(self.lobby_selection_frame, fg_color=("#dfe4ea", "#2f3542"), height=30, corner_radius=6)
        table_hdr.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(table_hdr, text="Game Title", font=ctk.CTkFont(weight="bold", size=12), anchor="w").pack(side="left", fill="x", expand=True, padx=(12, 5))
        ctk.CTkLabel(table_hdr, text="Host", font=ctk.CTkFont(weight="bold", size=12), width=100, anchor="w").pack(side="left", padx=5)
        ctk.CTkLabel(table_hdr, text="Players", font=ctk.CTkFont(weight="bold", size=12), width=65).pack(side="left", padx=5)
        ctk.CTkLabel(table_hdr, text="Action", font=ctk.CTkFont(weight="bold", size=12), width=85).pack(side="right", padx=10)

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
            row = ctk.CTkFrame(self.rooms_scroll, fg_color=("gray92", "#242424"), corner_radius=6, height=42)
            row.pack(fill="x", pady=3)

            title_lbl = ctk.CTkLabel(row, text=f"⚔️ {r.title}", font=ctk.CTkFont(weight="bold", size=13), anchor="w")
            title_lbl.pack(side="left", fill="x", expand=True, padx=(12, 5), pady=8)

            host_lbl = ctk.CTkLabel(row, text=r.host_username, text_color="gray", width=100, anchor="w", font=ctk.CTkFont(size=12))
            host_lbl.pack(side="left", padx=5, pady=8)

            players_lbl = ctk.CTkLabel(row, text=f"{r.current_players}/{r.max_players}", width=65, font=ctk.CTkFont(size=12, weight="bold"))
            players_lbl.pack(side="left", padx=5, pady=8)

            join_btn = ctk.CTkButton(
                row,
                text="Join Game",
                width=80,
                height=28,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="#27ae60",
                hover_color="#219150",
                command=lambda rm=r: self._join_public_room(rm)
            )
            join_btn.pack(side="right", padx=10, pady=6)

        # Refresh friends list to reflect any friends now hosting rooms
        self._refresh_friends_list()

    def _open_host_modal(self):
        """Opens quick modal dialog to host a game."""
        dialog = ctk.CTkInputDialog(
            text="Enter Game Title (e.g. DotA 5v5, Legion TD, Melee 1v1):",
            title="Host Public Game"
        )
        title = dialog.get_input()
        if title:
            self._start_hosting(title.strip())

    def _build_active_room_ui(self):
        """Constructs the GameRanger-style In-Room Screen with Live Chat & Launch button."""
        # Top Room Banner
        self.room_top_bar = ctk.CTkFrame(self.active_room_frame, fg_color=("#dfe4ea", "#242424"), corner_radius=8, height=46)
        self.room_top_bar.pack(fill="x", pady=(0, 10))

        self.room_title_label = ctk.CTkLabel(
            self.room_top_bar,
            text="🎮 Warcraft III Lounge Room",
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.room_title_label.pack(side="left", padx=15, pady=8)

        self.leave_btn = ctk.CTkButton(
            self.room_top_bar,
            text="Leave Room",
            width=85,
            height=28,
            fg_color="#c0392b",
            hover_color="#962d22",
            command=self._leave_lounge
        )
        self.leave_btn.pack(side="right", padx=12, pady=8)

        # Match Status Bar
        self.match_card = ctk.CTkFrame(self.active_room_frame, fg_color="#1e3799", corner_radius=8)
        self.match_card_label = ctk.CTkLabel(
            self.match_card,
            text="Status: Room open. Waiting for players to get ready...",
            font=ctk.CTkFont(size=13)
        )
        self.match_card_label.pack(padx=15, pady=8)
        self.match_card.pack(fill="x", pady=(0, 10))

        # Main Split inside Room: Left = Players (Roster + Ping), Right = Room Chat
        self.room_split = ctk.CTkFrame(self.active_room_frame, fg_color="transparent")
        self.room_split.pack(fill="both", expand=True, pady=(0, 10))
        self.room_split.columnconfigure(0, weight=1, minsize=260)  # Players
        self.room_split.columnconfigure(1, weight=1, minsize=320)  # Chat

        # Left: Players Panel
        players_panel = ctk.CTkFrame(self.room_split, corner_radius=8)
        players_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.players_header_lbl = ctk.CTkLabel(
            players_panel,
            text="👥 Players in Room",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.players_header_lbl.pack(anchor="w", padx=12, pady=(10, 4))

        self.players_scroll = ctk.CTkScrollableFrame(players_panel, fg_color="transparent")
        self.players_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        # Right: Live Room Chat Panel
        chat_panel = ctk.CTkFrame(self.room_split, corner_radius=8)
        chat_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        ctk.CTkLabel(
            chat_panel,
            text="💬 Room Lobby Chat",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=12, pady=(10, 4))

        self.chat_display = ctk.CTkTextbox(chat_panel, wrap="word", font=ctk.CTkFont(size=12))
        self.chat_display.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.chat_display.configure(state="disabled")

        chat_input_box = ctk.CTkFrame(chat_panel, fg_color="transparent")
        chat_input_box.pack(fill="x", padx=8, pady=(0, 8))

        self.chat_entry = ctk.CTkEntry(chat_input_box, placeholder_text="Chat with room players...", height=30)
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.chat_entry.bind("<Return>", lambda event: self._send_room_chat())

        self.chat_send_btn = ctk.CTkButton(chat_input_box, text="Send", width=60, height=30, command=self._send_room_chat)
        self.chat_send_btn.pack(side="right")

        # Bottom Action Bar
        self.room_actions = ctk.CTkFrame(self.active_room_frame, fg_color="transparent")
        self.room_actions.pack(fill="x", pady=(2, 0))

        # Host START GAME button (GameRanger style sync launch)
        self.start_game_btn = ctk.CTkButton(
            self.room_actions,
            text="🚀 START GAME (Launch All Players)",
            height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#27ae60",
            hover_color="#219150",
            command=self._handle_start_game
        )
        self.start_game_btn.pack(side="left", fill="x", expand=True)

        # Client Waiting Status Badge
        self.client_waiting_badge = ctk.CTkLabel(
            self.room_actions,
            text="⏳ Waiting for Host to Launch Game...",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f1c40f"
        )

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

        self.start_game_btn.configure(
            state="disabled",
            text="🚀 Launching Warcraft III...",
            fg_color="#e67e22"
        )
        self.session.start_game_for_all()

        # Re-enable button after 5 seconds so host is never permanently locked out
        def reenable_launch():
            if hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
                self.start_game_btn.configure(
                    state="normal",
                    text="🚀 Re-Launch Match",
                    fg_color="#27ae60",
                    hover_color="#219150"
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
                            text="🚀 START GAME (Launch All Players)",
                            fg_color="#27ae60",
                            hover_color="#219150"
                        )
                    if hasattr(self, "match_card") and self.match_card.winfo_exists():
                        self.match_card.configure(fg_color="#1e3799")
                        self.match_card_label.configure(text="Game closed. Ready to launch next match!")

                self.after(0, on_game_exit)

            threading.Thread(target=watch_game, daemon=True, name="GameProcessWatcher").start()

        self.after(0, update)

    def _start_hosting(self, title: str):
        self.create_lounge_btn = None
        self.current_room_title = title
        threading.Thread(target=self._async_host, args=(title,), daemon=True).start()

    def _async_host(self, title: str):
        self.session = LoungeSession(is_host=True, player_name=self.current_user.username)
        self.session.on_peer_joined = self._on_peer_joined_callback
        self.session.on_peer_left = self._on_peer_left_callback
        self.session.on_match_detected = self._on_match_detected_callback
        self.session.on_peer_ping = self._on_peer_ping_callback
        self.session.on_chat_received = self._on_chat_received_callback
        self.session.on_game_launched = self._on_game_launched_callback

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
                    local_port=local_port,
                    local_ip=local_ip
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

                self.start_game_btn.pack(fill="x", expand=True)
                self.client_waiting_badge.pack_forget()

                self.lobby_selection_frame.pack_forget()
                self.active_room_frame.pack(fill="both", expand=True)
                self._append_chat("System", f"Room '{title}' created! Waiting for players to join.")

        self.after(0, update)

    def _join_public_room(self, room: LobbyRoom):
        """1-Click Join from Public Lobby Browser."""
        self.current_room_title = room.title
        threading.Thread(
            target=self._async_join,
            args=(room.lounge_code, room.local_port, room.local_ip),
            daemon=True
        ).start()

    def _async_join(self, code: str, local_port: int = 0, local_ip: str = ""):
        self.session = LoungeSession(is_host=False, player_name=self.current_user.username)
        self.session.on_peer_joined = self._on_peer_joined_callback
        self.session.on_peer_left = self._on_peer_left_callback
        self.session.on_match_detected = self._on_match_detected_callback
        self.session.on_peer_ping = self._on_peer_ping_callback
        self.session.on_chat_received = self._on_chat_received_callback
        self.session.on_game_launched = self._on_game_launched_callback

        success = self.session.join_lounge(code, local_port=local_port, local_ip=local_ip)

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

                self.start_game_btn.pack_forget()
                self.client_waiting_badge.pack(fill="x", expand=True)

                self.lobby_selection_frame.pack_forget()
                self.active_room_frame.pack(fill="both", expand=True)
                self._append_chat("System", f"Joined room '{self.current_room_title}'! Ready to play.")
            else:
                messagebox.showerror("Join Failed", "Could not join room. The room code may be invalid or network initialization failed.")

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

        if hasattr(self, "start_game_btn") and self.start_game_btn.winfo_exists():
            self.start_game_btn.configure(
                state="normal",
                text="🚀 START GAME (Launch All Players)",
                fg_color="#27ae60",
                hover_color="#219150"
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

    def _add_player_card(self, username: str, is_self: bool = False, is_host: bool = False, initial_text: Optional[str] = None):
        target_key = None
        for key in self.player_rows.keys():
            if key.lower().strip() == username.lower().strip():
                target_key = key
                break

        if target_key:
            if initial_text:
                _, ping_lbl = self.player_rows[target_key]
                color = "#2ecc71" if "Connected" in initial_text else "gray"
                ping_lbl.configure(text=initial_text, text_color=color)
            return

        row = ctk.CTkFrame(self.players_scroll, fg_color=("gray90", "#2b2b2b"), corner_radius=6)
        row.pack(fill="x", pady=3, padx=2)

        role_tag = " (Host / You)" if (is_self and is_host) else (" (You)" if is_self else (" (Host)" if is_host else ""))
        name_lbl = ctk.CTkLabel(
            row,
            text=f"👤 {username}{role_tag}",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w"
        )
        name_lbl.pack(side="left", padx=10, pady=6)

        ping_text = "🟢 Local (0 ms)" if is_self else (initial_text or "⚪ Measuring...")
        ping_color = "#2ecc71" if (is_self or (initial_text and "Connected" in initial_text)) else "gray"
        ping_lbl = ctk.CTkLabel(
            row,
            text=ping_text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ping_color
        )
        ping_lbl.pack(side="right", padx=10, pady=6)

        self.player_rows[username] = (row, ping_lbl)
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
                _, ping_lbl = self.player_rows[target_key]
                if rtt_ms < 60.0:
                    icon = "🟢"
                    color = "#2ecc71"
                elif rtt_ms < 120.0:
                    icon = "🟡"
                    color = "#f1c40f"
                else:
                    icon = "🔴"
                    color = "#e74c3c"
                ping_lbl.configure(text=f"{icon} {int(rtt_ms)} ms", text_color=color)

        self.after(0, update)

    def _on_peer_joined_callback(self, peer, username):
        def update():
            play_join_sound()
            self._add_player_card(username, is_self=False, is_host=False, initial_text="🟢 Connected")
            self._append_chat("System", f"Player '{username}' joined the room!")
        self.after(0, update)

    def _on_peer_left_callback(self, username: str):
        def update():
            play_leave_sound()
            target_key = None
            for p in self.player_rows.keys():
                if p.lower().strip() == username.lower().strip():
                    target_key = p
                    break
            if target_key:
                row, _ = self.player_rows.pop(target_key)
                row.destroy()
                self._update_players_count_display()
                self._append_chat("System", f"Player '{username}' left the room.")
        self.after(0, update)

    def _on_match_detected_callback(self, match_name, open_slots, total_slots):
        def update():
            self.match_card.configure(fg_color="#27ae60")
            self.match_card_label.configure(
                text=f"Match Live: '{match_name}' | Slots: {open_slots}/{total_slots} | Visible in WC3 LAN!"
            )
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
            row = ctk.CTkFrame(self.friends_scroll, fg_color=("gray90", "#2b2b2b"), corner_radius=6)
            row.pack(fill="x", pady=3, padx=2)

            is_online = self._is_friend_online_or_hosting(friend)
            dot_char = "🟢" if is_online else "⚪"
            dot_color = "#2ecc71" if is_online else "#7f8c8d"

            status_lbl = ctk.CTkLabel(
                row,
                text=dot_char,
                font=ctk.CTkFont(size=12),
                text_color=dot_color,
                width=20
            )
            status_lbl.pack(side="left", padx=(8, 2), pady=5)

            name_lbl = ctk.CTkLabel(
                row,
                text=friend,
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w"
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=(2, 5), pady=5)

            # Invite Button: strictly enabled only if friend is online!
            if is_online:
                invite_btn = ctk.CTkButton(
                    row,
                    text="Invite",
                    width=65,
                    height=26,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color="#27ae60",
                    hover_color="#219150",
                    state="normal"
                )
                invite_btn.configure(command=lambda f=friend, b=invite_btn: self._handle_invite_friend(f, b))
            else:
                invite_btn = ctk.CTkButton(
                    row,
                    text="Offline",
                    width=65,
                    height=26,
                    font=ctk.CTkFont(size=11),
                    fg_color=("#bdc3c7", "#3d3d3d"),
                    text_color=("gray30", "gray70"),
                    state="disabled"
                )
            invite_btn.pack(side="right", padx=6, pady=5)

            # Right-click context menu on row & label for Private Message and Remove Friend
            def show_friend_menu(event, f=friend, b=invite_btn):
                menu = tk.Menu(self, tearoff=0)
                menu.add_command(
                    label=f"💬 Private Message '{f}'",
                    command=lambda: self._open_private_chat(f)
                )
                if self._is_friend_online_or_hosting(f):
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

            row.bind("<Button-3>", lambda e, f=friend, b=invite_btn: show_friend_menu(e, f, b))
            name_lbl.bind("<Button-3>", lambda e, f=friend, b=invite_btn: show_friend_menu(e, f, b))
            status_lbl.bind("<Button-3>", lambda e, f=friend, b=invite_btn: show_friend_menu(e, f, b))

    def _open_private_chat(self, friend_name: str) -> PrivateChatDialog:
        """Opens or brings to front an ephemeral in-memory direct message dialog."""
        key = friend_name.lower().strip()
        dialog = self._active_pm_dialogs.get(key)
        if dialog and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            return dialog

        dialog = PrivateChatDialog(
            master=self,
            current_username=self.current_user.username,
            friend_name=friend_name,
            friend_manager=self.friend_manager,
            on_close=self._on_private_chat_closed
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
        self.after(0, show)

    def _accept_invite(self):
        if not self.active_invite:
            return
        invite = self.active_invite
        self.invite_banner.pack_forget()
        self.active_invite = None
        self.current_room_title = f"{invite.host_username}'s Game"
        threading.Thread(
            target=self._async_join,
            args=(invite.room_code, invite.local_port, invite.local_ip),
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
