"""
Main Desktop Application Window for Warcraft III Lounge.
Manages window lifecycle, styling, and frame switching between Auth and Lounge views.
"""

import os
from pathlib import Path
import sys
from typing import Optional
import customtkinter as ctk

from ..auth.auth_manager import AuthManager, UserSession
from ..core.config import APP_VERSION, load_setting, logger, save_setting
from ..core.single_instance import SingleInstanceController
from .auth_frame import AuthFrame
from .lounge_frame import LoungeFrame
from .tray_manager import TrayManager

class W3LoungeApp(ctk.CTk):
    """Primary application GUI window."""

    def __init__(self, instance_controller: Optional[SingleInstanceController] = None):
        super().__init__()

        self.instance_controller = instance_controller

        # Appearance configuration
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        profile = os.environ.get("W3L_PROFILE", "").strip()
        title_suffix = f" [{profile}]" if profile else ""
        self.title(f"Warcraft III Lounge v{APP_VERSION}{title_suffix}")

        # Set Window Icon
        self._apply_app_icon()

        # Window geometry: native Garena classic dimensions (1024x620, non-resizable)
        width, height = 1024, 620
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            pos_x = max(0, (screen_w - width) // 2)
            pos_y = max(0, (screen_h - height) // 2)
            self.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        except Exception:
            self.geometry(f"{width}x{height}")

        self.resizable(False, False)

        self.auth_manager = AuthManager()
        self.current_frame: Optional[ctk.CTkFrame] = None

        # Windows System Tray
        self.tray_manager = TrayManager(app=self, on_exit=self.quit_app)

        # Single-Instance Listener Activation Hook
        if self.instance_controller:
            def on_restore_signal():
                self.after(0, self.tray_manager.restore_window)
            self.instance_controller.is_primary_instance(on_restore_signal)

        # Close-to-Tray: Clicking 'X' minimizes to system tray instead of terminating
        self.protocol("WM_DELETE_WINDOW", self._on_window_close_clicked)

        # Decide initial view based on saved session
        if self.auth_manager.current_user.is_authenticated:
            self._show_lounge_view(self.auth_manager.current_user)
        else:
            self._show_auth_view()

    def _apply_app_icon(self):
        """Applies custom multi-resolution icon to the window and taskbar."""
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_dir = Path(sys._MEIPASS)
        else:
            base_dir = Path(__file__).resolve().parent.parent.parent

        ico_path = base_dir / "assets" / "icon.ico"
        png_path = base_dir / "assets" / "icon.png"

        try:
            if ico_path.exists():
                self.iconbitmap(str(ico_path))
            elif png_path.exists():
                from PIL import ImageTk, Image
                img = ImageTk.PhotoImage(Image.open(str(png_path)))
                self.wm_iconphoto(True, img)
        except Exception as e:
            logger.debug(f"Could not apply window icon: {e}")

    def _show_auth_view(self):
        if self.current_frame:
            self.current_frame.destroy()
        self.current_frame = AuthFrame(
            self,
            auth_manager=self.auth_manager,
            on_auth_success=self._show_lounge_view
        )
        self.current_frame.pack(fill="both", expand=True, padx=20, pady=20)

    def _show_lounge_view(self, user: UserSession):
        if self.current_frame:
            self.current_frame.destroy()
        self.current_frame = LoungeFrame(
            self,
            current_user=user,
            on_logout=self._handle_logout
        )
        self.current_frame.pack(fill="both", expand=True, padx=0, pady=0)

    def _handle_logout(self):
        if isinstance(self.current_frame, LoungeFrame):
            self.current_frame.friend_manager.stop_listening()
        self.auth_manager.logout()
        self._show_auth_view()

    def _on_window_close_clicked(self):
        """Window 'X' clicked: minimize to system tray instead of destroying application."""
        logger.info("Window close requested: minimizing to Windows System Tray.")
        try:
            save_setting("window_geometry", self.geometry())
        except Exception:
            pass

        if hasattr(self, "tray_manager") and self.tray_manager and self.tray_manager.icon:
            self.tray_manager.minimize_to_tray(notify=True)
        else:
            self.quit_app()

    def quit_app(self):
        """Full exit: terminates application process, tray icon, and background sockets."""
        logger.info("Terminating application...")
        try:
            save_setting("window_geometry", self.geometry())
        except Exception:
            pass

        if hasattr(self, "instance_controller") and self.instance_controller:
            self.instance_controller.close()

        if hasattr(self, "tray_manager") and self.tray_manager:
            self.tray_manager.stop()

        if isinstance(self.current_frame, LoungeFrame):
            self.current_frame.friend_manager.stop_listening()
            if self.current_frame.session:
                self.current_frame.session.shutdown()

        self.destroy()

def launch_gui():
    """Entry point for GUI mode with single-instance enforcement."""
    controller = SingleInstanceController()
    # Check if this process is duplicate
    is_primary = controller.is_primary_instance(on_restore_request=lambda: None)
    if not is_primary:
        # Signaled primary to restore, exiting duplicate immediately
        sys.exit(0)

    app = W3LoungeApp(instance_controller=controller)
    app.mainloop()
