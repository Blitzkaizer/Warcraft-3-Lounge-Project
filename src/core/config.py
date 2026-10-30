"""
Core configuration and constants for Warcraft III Lounge Proxy.
"""

from enum import IntEnum
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Optional

APP_VERSION = "1.0.4"

# Default Warcraft III Classic ports & settings
WC3_DEFAULT_PORT = 6112       # Standard WC3 LAN UDP discovery & TCP game hosting port
DEFAULT_TUNNEL_PORT = 6113    # Default port for P2P internet communication between Lounge peers
BUFFER_SIZE = 4096            # Standard network packet buffer size

# Redundant High-Speed Public HTTPS Signaling Relays with Automatic Failover
SIGNALING_SERVERS = [
    "https://ntfy.envs.net",
    "https://ntfy.tedomum.fr",
    "https://ntfy.adminforge.de",
    "https://ntfy.hostux.net",
    "https://ntfy.sh"
]

# Classic Warcraft III Executable & Registry Names
CLASSIC_WC3_EXECUTABLES = ["war3.exe", "Frozen Throne.exe"]
CLASSIC_REGISTRY_KEY = r"Software\Blizzard Entertainment\Warcraft III"

# Warcraft 3 Game Service (W3GS) protocol header magic byte
W3GS_MAGIC_BYTE = 0xF7

def get_config_dir() -> Path:
    """Returns application configuration directory in %APPDATA%/Warcraft3Lounge."""
    profile = os.environ.get("W3L_PROFILE", "").strip()
    base_dir = Path(os.environ.get("APPDATA", ".")) / "Warcraft3Lounge"
    config_dir = (base_dir / f"profile_{profile}") if profile else base_dir
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return config_dir

def get_settings_file() -> Path:
    return get_config_dir() / "settings.json"

def load_settings() -> dict:
    f = get_settings_file()
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            pass
    return {}

def save_setting(key: str, value: Any) -> None:
    data = load_settings()
    data[key] = value
    try:
        with open(get_settings_file(), "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2)
    except Exception:
        pass

def load_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)

class W3GSPacketId(IntEnum):
    """Common W3GS Packet IDs observed in LAN discovery & handshake."""
    PING_FROM_HOST = 0x01
    SLOT_INFO_REQ = 0x04
    JOIN_REQ = 0x1E
    SEARCH_GAME = 0x2F       # Client searches for games or host broadcast response
    GAME_INFO = 0x30         # Host announces/creates game
    CREATE_GAME = 0x31       # Game announcement update / refresh
    REFRESH_GAME = 0x32      # Keep-alive game info broadcast
    DEASSOCIATE = 0x33       # Host cancels / leaves game
    UNKNOWN = 0xFF

def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configures a unified console logger for the Lounge Proxy."""
    logger = logging.getLogger("W3Lounge")
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

logger = setup_logging()
