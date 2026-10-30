"""
Global Lobby Browser Service for Warcraft III Lounge (GameRanger Style).
Enables players to host public games that appear instantly in a live
Lobby Browser list for anyone to discover and join with 1 click.
"""

from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Callable, Dict, List, Optional
import urllib.error
import urllib.request

from ..core.config import APP_VERSION, logger

GLOBAL_LOBBY_TOPIC = "w3l_global_lobby_v1"

@dataclass
class LobbyRoom:
    room_id: str
    title: str
    host_username: str
    lounge_code: str
    max_players: int = 10
    current_players: int = 1
    local_port: int = 0
    local_ip: str = ""
    last_seen: float = 0.0
    app_version: str = "1.0.0"
    is_locked: bool = False
    password_hash: str = ""

class LobbyService:
    """
    Broadcasts hosted games to the public Lobby Browser and polls active rooms.
    """

    def __init__(self, username: str):
        self.username = username
        self._is_announcing = False
        self._announce_thread: Optional[threading.Thread] = None
        self._current_hosted_room: Optional[LobbyRoom] = None
        self.active_rooms: Dict[str, LobbyRoom] = {}

    def start_hosting_room(
        self,
        title: str,
        room_code: str,
        max_players: int = 10,
        local_port: int = 0,
        local_ip: str = "",
        password: str = ""
    ) -> LobbyRoom:
        room_id = hashlib.sha256(f"{self.username}_{title}_{time.time()}".encode()).hexdigest()[:12]
        pw_hash = hashlib.sha256(password.strip().encode()).hexdigest()[:16] if password.strip() else ""
        room = LobbyRoom(
            room_id=room_id,
            title=title.strip() or f"{self.username}'s Game",
            host_username=self.username,
            lounge_code=room_code,
            max_players=max_players,
            current_players=1,
            local_port=local_port,
            local_ip=local_ip,
            last_seen=time.time(),
            app_version=APP_VERSION,
            is_locked=bool(pw_hash),
            password_hash=pw_hash
        )
        self._current_hosted_room = room
        self._is_announcing = True
        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True, name="LobbyAnnouncer")
        self._announce_thread.start()
        logger.info(f"Published public room to Lobby Browser: '{room.title}' (ID: {room_id})")
        return room

    def update_player_count(self, count: int):
        if self._current_hosted_room:
            self._current_hosted_room.current_players = count
            self._broadcast_room(self._current_hosted_room)

    def stop_hosting_room(self):
        self._is_announcing = False
        if self._current_hosted_room:
            try:
                # Dispatch close event
                payload = json.dumps({
                    "type": "ROOM_CLOSED",
                    "room_id": self._current_hosted_room.room_id,
                    "host": self.username
                }).encode("utf-8")
                url = f"https://ntfy.sh/{GLOBAL_LOBBY_TOPIC}"
                req = urllib.request.Request(url, data=payload, method="POST")
                urllib.request.urlopen(req, timeout=3.0)
            except Exception:
                pass
            self._current_hosted_room = None

    def _broadcast_room(self, room: LobbyRoom):
        try:
            url = f"https://ntfy.sh/{GLOBAL_LOBBY_TOPIC}"
            payload = json.dumps({
                "type": "ROOM_ANNOUNCE",
                "room_id": room.room_id,
                "title": room.title,
                "host": room.host_username,
                "code": room.lounge_code,
                "max_players": room.max_players,
                "current_players": room.current_players,
                "version": APP_VERSION,
                "is_locked": room.is_locked,
                "password_hash": room.password_hash,
                "ts": time.time()
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            urllib.request.urlopen(req, timeout=4.0)
        except Exception as e:
            logger.debug(f"Lobby announcement error: {e}")

    def _announce_loop(self):
        while self._is_announcing and self._current_hosted_room:
            self._broadcast_room(self._current_hosted_room)
            time.sleep(6.0)

    def fetch_public_rooms(self) -> List[LobbyRoom]:
        """Polls active rooms announced on the global lobby topic."""
        try:
            url = f"https://ntfy.sh/{GLOBAL_LOBBY_TOPIC}/json?poll=1&since=30s"
            req = urllib.request.Request(url, headers={"User-Agent": "W3Lounge/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                now = time.time()
                for line in resp:
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if not line_str:
                        continue
                    try:
                        record = json.loads(line_str)
                        if record.get("event") == "message" and record.get("message"):
                            msg = json.loads(record["message"])
                            msg_type = msg.get("type")
                            r_id = msg.get("room_id")
                            if msg_type == "ROOM_CLOSED":
                                self.active_rooms.pop(r_id, None)
                            elif msg_type == "ROOM_ANNOUNCE" and r_id:
                                ts = msg.get("ts", 0.0)
                                if now - ts < 45.0:  # Fresh room
                                    self.active_rooms[r_id] = LobbyRoom(
                                        room_id=r_id,
                                        title=msg.get("title", "Warcraft III Game"),
                                        host_username=msg.get("host", "Host"),
                                        lounge_code=msg.get("code", ""),
                                        max_players=msg.get("max_players", 10),
                                        current_players=msg.get("current_players", 1),
                                        local_port=0,
                                        local_ip="",
                                        last_seen=ts,
                                        app_version=msg.get("version", "1.0.0"),
                                        is_locked=bool(msg.get("is_locked", False)),
                                        password_hash=str(msg.get("password_hash", ""))
                                    )
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Failed to fetch public rooms: {e}")

        # Clean expired rooms (> 25s without heartbeat)
        now = time.time()
        self.active_rooms = {
            r_id: room for r_id, room in self.active_rooms.items()
            if (now - room.last_seen < 25.0)
        }
        return list(self.active_rooms.values())
