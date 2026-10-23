"""
Audio Sound Effects Engine for Warcraft III Lounge.
Provides GameRanger-style sound cues:
- Player Join Room
- Player Leave Room
- Room Chat Message
- Match Launch Fanfare

Uses Windows native audio (winsound) with zero external dependencies.
"""

import math
import os
from pathlib import Path
import struct
import sys
import threading
import wave

from .config import load_setting, logger, save_setting

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# Sound directory setup (supports development directory and PyInstaller _MEIPASS)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent

SOUNDS_DIR = BASE_DIR / "assets" / "sounds"
SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

JOIN_WAV = SOUNDS_DIR / "join.wav"
LEAVE_WAV = SOUNDS_DIR / "leave.wav"
CHAT_WAV = SOUNDS_DIR / "chat.wav"
LAUNCH_WAV = SOUNDS_DIR / "launch.wav"

_IS_MUTED = bool(load_setting("sound_muted", False))

def is_sound_muted() -> bool:
    return _IS_MUTED

def set_sound_muted(muted: bool) -> None:
    global _IS_MUTED
    _IS_MUTED = bool(muted)
    save_setting("sound_muted", _IS_MUTED)
    logger.info(f"Sound notifications {'MUTED' if _IS_MUTED else 'UNMUTED'}.")

def _synthesize_wav(path: Path, notes, sample_rate=22050):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            frames = bytearray()
            for freq, duration_ms in notes:
                n_samples = int(sample_rate * (duration_ms / 1000.0))
                for i in range(n_samples):
                    t = float(i) / sample_rate
                    # Gentle bell envelope
                    env = math.exp(-3.5 * float(i) / n_samples)
                    val = int(32767.0 * 0.35 * env * math.sin(2.0 * math.pi * freq * t))
                    frames.extend(struct.pack("<h", max(-32768, min(32767, val))))
            wav.writeframes(frames)
    except Exception as e:
        logger.debug(f"Failed to synthesize sound {path.name}: {e}")

def _ensure_sounds():
    if not JOIN_WAV.exists():
        _synthesize_wav(JOIN_WAV, [(740, 110), (988, 160)])
    if not LEAVE_WAV.exists():
        _synthesize_wav(LEAVE_WAV, [(988, 110), (740, 160)])
    if not CHAT_WAV.exists():
        _synthesize_wav(CHAT_WAV, [(1046, 85)])
    if not LAUNCH_WAV.exists():
        _synthesize_wav(LAUNCH_WAV, [(523, 100), (659, 100), (784, 120), (1046, 320)])

_ensure_sounds()

def _play_async(sound_path: Path):
    if _IS_MUTED or not HAS_WINSOUND or not sound_path.exists():
        return
    try:
        winsound.PlaySound(str(sound_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        logger.debug(f"Error playing sound {sound_path.name}: {e}")

def play_join_sound():
    """Chime played when a player joins the lounge."""
    _play_async(JOIN_WAV)

def play_leave_sound():
    """Chime played when a player leaves the lounge."""
    _play_async(LEAVE_WAV)

def play_chat_sound():
    """Notification chime played when an incoming chat message is received."""
    _play_async(CHAT_WAV)

def play_launch_sound():
    """Alert fanfare played when the match is launched."""
    _play_async(LAUNCH_WAV)
