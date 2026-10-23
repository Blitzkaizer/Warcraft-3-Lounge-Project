"""
Warcraft III Classic Auto-Launcher.
Auto-detects installation path via Windows Registry and common directories,
caches path in settings, and programmatically launches war3.exe via subprocess.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional
import winreg

from ..core.config import CLASSIC_REGISTRY_KEY, CLASSIC_WC3_EXECUTABLES, logger

COMMON_INSTALL_PATHS = [
    r"C:\Program Files (x86)\Warcraft III",
    r"C:\Program Files\Warcraft III",
    r"C:\Games\Warcraft III",
    r"D:\Games\Warcraft III",
    r"D:\Warcraft III",
    r"E:\Games\Warcraft III",
    r"E:\Warcraft III",
]

class GameLauncher:
    """Manages Warcraft III executable discovery, configuration, and launch."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or (Path(os.environ.get("APPDATA", ".")) / "Warcraft3Lounge")
        self.config_file = self.config_dir / "launcher_config.json"
        self._ensure_config_dir()
        self.exe_path: Optional[str] = self.get_saved_path() or self.detect_executable()
        self.process: Optional[subprocess.Popen] = None

    def _ensure_config_dir(self) -> None:
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def get_saved_path(self) -> Optional[str]:
        """Loads previously saved war3.exe path from settings JSON."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    path = data.get("war3_executable_path")
                    if path and Path(path).is_file():
                        return path
            except Exception as e:
                logger.debug(f"Error reading launcher config: {e}")
        return None

    def save_path(self, path: str) -> bool:
        """Saves verified war3.exe path to config file."""
        if not Path(path).is_file():
            return False
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump({"war3_executable_path": str(path)}, f, indent=2)
            self.exe_path = path
            logger.info(f"Saved Warcraft III executable path: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save launcher config: {e}")
            return False

    def detect_executable(self) -> Optional[str]:
        """
        Scans Windows Registry keys and common folders to locate war3.exe.
        """
        # 1. Try Windows Registry (HKCU and HKLM 64-bit & 32-bit views)
        reg_views = [
            (winreg.HKEY_CURRENT_USER, CLASSIC_REGISTRY_KEY),
            (winreg.HKEY_LOCAL_MACHINE, CLASSIC_REGISTRY_KEY),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blizzard Entertainment\Warcraft III"),
        ]

        for root_key, subkey in reg_views:
            try:
                with winreg.OpenKey(root_key, subkey) as key:
                    install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                    if install_path:
                        for exe in CLASSIC_WC3_EXECUTABLES:
                            candidate = Path(install_path) / exe
                            if candidate.is_file():
                                logger.info(f"Found Warcraft III via registry: {candidate}")
                                self.save_path(str(candidate))
                                return str(candidate)
            except (OSError, FileNotFoundError):
                continue

        # 2. Try common installation directories
        for folder in COMMON_INSTALL_PATHS:
            for exe in CLASSIC_WC3_EXECUTABLES:
                candidate = Path(folder) / exe
                if candidate.is_file():
                    logger.info(f"Found Warcraft III in common path: {candidate}")
                    self.save_path(str(candidate))
                    return str(candidate)

        logger.warning("Could not auto-detect Warcraft III installation folder.")
        return None

    def is_running(self) -> bool:
        """Checks if launched game process is still active."""
        if self.process is not None:
            return self.process.poll() is None
        return False

    @staticmethod
    def configure_game_port(port: int = 6112) -> bool:
        """
        Sets netgameport in HKCU Windows Registry to default 6112.
        """
        try:
            gameplay_key = r"Software\Blizzard Entertainment\Warcraft III\Gameplay"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, gameplay_key) as key:
                winreg.SetValueEx(key, "netgameport", 0, winreg.REG_DWORD, port)
                logger.info(f"Configured Warcraft III netgameport to {port} in registry.")
                return True
        except Exception as e:
            logger.debug(f"Could not configure netgameport: {e}")
            return False

    def launch(self, extra_args: Optional[List[str]] = None) -> bool:
        """
        Launches Warcraft III directly via subprocess.
        Sets working directory to game folder so game MPQ files load correctly.
        """
        self.configure_game_port(6112)

        if not self.exe_path or not Path(self.exe_path).is_file():
            # Retry detection
            self.exe_path = self.detect_executable()
            if not self.exe_path:
                logger.error("Cannot launch: Warcraft III executable not found. Set path manually.")
                return False

        game_dir = str(Path(self.exe_path).parent)
        cmd = [self.exe_path]
        if extra_args:
            cmd.extend(extra_args)

        logger.info(f"Launching Warcraft III: {' '.join(cmd)} (CWD: {game_dir})")

        try:
            # Creation flags to detach and run smoothly on Windows
            self.process = subprocess.Popen(
                cmd,
                cwd=game_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            )
            logger.info(f"Warcraft III started successfully with PID: {self.process.pid}")
            return True
        except Exception as e:
            logger.error(f"Failed to launch Warcraft III executable: {e}")
            return False
