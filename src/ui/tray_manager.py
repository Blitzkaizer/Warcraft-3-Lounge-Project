"""
Windows System Tray Manager for Warcraft III Lounge.
Enables true background system tray operation via pystray:
- Minimizes window completely out of taskbar into the system notification tray.
- Restores on tray click or when match finishes.
- Tray context menu (Open Lounge, Exit).
"""

from pathlib import Path
import sys
import threading
from typing import Callable, Optional
from PIL import Image, ImageDraw

from ..core.config import logger

try:
    import pystray
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

class TrayManager:
    """Manages system notification tray icon and window visibility."""

    def __init__(self, app, on_exit: Optional[Callable[[], None]] = None):
        self.app = app
        self.on_exit = on_exit
        self.icon: Optional[pystray.Icon] = None
        self._is_running = False

        if HAS_PYSTRAY:
            self._init_tray()

    def _get_icon_image(self) -> Image.Image:
        base_dir = Path(sys._MEIPASS) if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else Path(__file__).resolve().parent.parent.parent
        for candidate in [base_dir / "assets" / "tray_icon.png", base_dir / "assets" / "icon.png", Path("assets") / "tray_icon.png"]:
            if candidate.exists():
                try:
                    return Image.open(str(candidate))
                except Exception:
                    pass

        # Fallback generated icon: Gold badge on dark blue
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2, 2, 61, 61), fill="#1e3799", outline="#f1c40f", width=3)
        d.text((16, 18), "W3", fill="#f1c40f")
        return img

    def _init_tray(self):
        try:
            image = self._get_icon_image()
            menu = pystray.Menu(
                pystray.MenuItem("🖥️ Open Lounge", self.restore_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("❌ Exit Lounge", self._handle_exit)
            )

            self.icon = pystray.Icon(
                name="Warcraft3Lounge",
                icon=image,
                title="Warcraft III Lounge Companion",
                menu=menu
            )

            # Start tray icon loop in background thread
            tray_thread = threading.Thread(target=self._run_icon, daemon=True, name="SystemTrayThread")
            tray_thread.start()
            logger.info("System Tray icon initialized successfully.")
        except Exception as e:
            logger.debug(f"Failed to initialize system tray icon: {e}")

    def _run_icon(self):
        try:
            self._is_running = True
            self.icon.run()
        except Exception as e:
            logger.debug(f"Tray loop error: {e}")

    def show_notification(self, title: str, message: str):
        """Displays a native Windows toast notification via the system tray."""
        if self.icon and self._is_running:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                logger.debug(f"Tray notification failed: {e}")

    def minimize_to_tray(self, notify: bool = False):
        """Hides window completely into system tray."""
        def hide():
            try:
                self.app.withdraw()
            except Exception:
                pass

        self.app.after(0, hide)
        if notify and self.icon:
            try:
                self.icon.notify(
                    "Warcraft III Lounge is active in the background.",
                    title="Warcraft III Lounge"
                )
            except Exception:
                pass

    def restore_window(self, icon=None, item=None):
        """Restores window from system tray to foreground."""
        def show():
            try:
                self.app.deiconify()
                self.app.state("normal")
                self.app.attributes("-topmost", True)
                self.app.attributes("-topmost", False)
                self.app.lift()
                self.app.focus_force()
            except Exception:
                pass

        self.app.after(0, show)

    def _handle_exit(self, icon=None, item=None):
        def do_exit():
            self.stop()
            if self.on_exit:
                self.on_exit()
            else:
                self.app.destroy()

        self.app.after(0, do_exit)

    def stop(self):
        """Stops and cleans up the tray icon."""
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
