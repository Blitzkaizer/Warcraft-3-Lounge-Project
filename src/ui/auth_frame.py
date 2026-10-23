"""
Authentication Frame (Login / Register) for Warcraft III Lounge.
Custom in-app account system with username + password. No email verification required.
"""

import threading
from typing import Callable, Optional
import customtkinter as ctk

from ..auth.auth_manager import AuthManager, UserSession
from ..core.config import logger

class AuthFrame(ctk.CTkFrame):
    """View presenting custom Login & Register forms."""

    def __init__(self, master, auth_manager: AuthManager, on_auth_success: Callable[[UserSession], None]):
        super().__init__(master, corner_radius=12)
        self.auth_manager = auth_manager
        self.on_auth_success = on_auth_success
        self.is_register_mode = False

        self._build_ui()

    def _build_ui(self):
        # Header
        self.title_label = ctk.CTkLabel(
            self,
            text="Warcraft III Lounge",
            font=ctk.CTkFont(size=28, weight="bold")
        )
        self.title_label.pack(pady=(45, 25))

        # Card container
        self.form_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.form_frame.pack(padx=40, fill="x")

        # Username
        self.username_label = ctk.CTkLabel(self.form_frame, text="Username", anchor="w")
        self.username_label.pack(fill="x", pady=(5, 2))
        self.username_entry = ctk.CTkEntry(self.form_frame, placeholder_text="e.g. Arthas99", height=38)
        self.username_entry.pack(fill="x", pady=(0, 10))

        # Password
        self.password_label = ctk.CTkLabel(self.form_frame, text="Password", anchor="w")
        self.password_label.pack(fill="x", pady=(5, 2))
        self.password_entry = ctk.CTkEntry(self.form_frame, placeholder_text="••••••••", show="*", height=38)
        self.password_entry.pack(fill="x", pady=(0, 15))

        # Enter key triggers login/register
        self.username_entry.bind("<Return>", lambda e: self._handle_submit())
        self.password_entry.bind("<Return>", lambda e: self._handle_submit())

        # Status / Error Label
        self.status_label = ctk.CTkLabel(
            self.form_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#e74c3c",
            wraplength=450,
            justify="center"
        )
        self.status_label.pack(pady=(0, 10))

        # Submit button
        self.submit_btn = ctk.CTkButton(
            self.form_frame,
            text="Login",
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._handle_submit
        )
        self.submit_btn.pack(fill="x", pady=(5, 10))

        # Toggle between Login and Register
        self.toggle_btn = ctk.CTkButton(
            self.form_frame,
            text="Need an account? Register here",
            fg_color="transparent",
            hover_color=("#3a3a3a", "#2b2b2b"),
            text_color="#3498db",
            command=self._toggle_mode
        )
        self.toggle_btn.pack(pady=(0, 25))

    def _toggle_mode(self):
        self.is_register_mode = not self.is_register_mode
        if self.is_register_mode:
            self.submit_btn.configure(text="Create Account", fg_color="#27ae60", hover_color="#219150")
            self.toggle_btn.configure(text="Already have an account? Login here")
            self.status_label.configure(text="")
        else:
            self.submit_btn.configure(text="Login", fg_color="#1f6aa5", hover_color="#144870")
            self.toggle_btn.configure(text="Need an account? Register here")
            self.status_label.configure(text="")

    def _handle_submit(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username or not password:
            self.status_label.configure(text="Please provide both username and password.", text_color="#e74c3c")
            return

        if self.is_register_mode:
            if len(username) < 3:
                self.status_label.configure(text="Username must be at least 3 characters.", text_color="#e74c3c")
                return
            if len(password) < 6:
                self.status_label.configure(text="Password must be at least 6 characters.", text_color="#e74c3c")
                return

            self.submit_btn.configure(state="disabled", text="Creating Account...")

            def run_reg():
                success, msg = self.auth_manager.register(username, password)

                def on_done():
                    self.submit_btn.configure(state="normal", text="Create Account")
                    if success:
                        self.is_register_mode = True  # will flip to False in toggle
                        self._toggle_mode()
                        self.username_entry.delete(0, "end")
                        self.username_entry.insert(0, username)
                        self.password_entry.delete(0, "end")
                        self.password_entry.focus_set()
                        self.status_label.configure(
                            text=f"✅ Account '{username}' created! Enter password to log in.",
                            text_color="#2ecc71"
                        )
                    else:
                        self.status_label.configure(text=msg, text_color="#e74c3c")

                self.after(0, on_done)

            threading.Thread(target=run_reg, daemon=True, name="AuthRegisterThread").start()
        else:
            self.submit_btn.configure(state="disabled", text="Logging In...")

            def run_login():
                success, msg = self.auth_manager.login(username, password)

                def on_done():
                    self.submit_btn.configure(state="normal", text="Login")
                    if success:
                        self.status_label.configure(text=msg, text_color="#2ecc71")
                        self.on_auth_success(self.auth_manager.current_user)
                    else:
                        self.status_label.configure(text=msg, text_color="#e74c3c")

                self.after(0, on_done)

            threading.Thread(target=run_login, daemon=True, name="AuthLoginThread").start()
