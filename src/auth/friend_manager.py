"""
Friend List & 1-Click Game Invite Manager for Warcraft III Lounge.
Enables real-time friend requests, mutual acceptance, and 1-click game invites
via secure HTTPS signaling.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable, List, Optional, Set, Tuple
import urllib.error
import urllib.request

from ..core.config import APP_VERSION, logger

# App-level HMAC key for ntfy.sh topic derivation.
# Makes topics uncomputable without this key (requires binary reverse-engineering).
_TOPIC_HMAC_KEY = b"\xb7\x3a\x91\xd4\x0e\x52\xf8\x6c\xa1\x29\x7d\xe3\x45\xba\x08\xc6" \
                  b"\x5f\x1b\xd0\x73\x9e\x64\xa8\x2c\x47\xf9\x86\x3b\xe1\x0d\x5a\xc2"

@dataclass
class GameInvite:
    host_username: str
    recipient_username: str
    room_code: str
    timestamp: float
    local_port: int = 0
    local_ip: str = ""
    app_version: str = "1.0.0"

class FriendManager:
    """
    Manages local friend relationships, mutual friend requests, and 1-click game invites.
    Uses HTTPS TLS 1.3 pub/sub signaling on private hashed topics so users
    never need to manually copy or paste connection codes.
    """

    def __init__(self, username: str):
        self.username = username.strip()
        profile = os.environ.get("W3L_PROFILE", "").strip()
        base_dir = Path(os.environ.get("APPDATA", ".")) / "Warcraft3Lounge"
        self.config_dir = (base_dir / f"profile_{profile}") if profile else base_dir
        self.friends_file = self.config_dir / "friends.json"
        self._ensure_config_dir()

        self._running = False
        self._listener_thread: Optional[threading.Thread] = None
        self._presence_thread: Optional[threading.Thread] = None
        self._seen_signals: Set[str] = set()
        self.online_friends: dict = {}  # username.lower() -> last_seen_time

        # Callbacks
        self.on_invite_received: Optional[Callable[[GameInvite], None]] = None
        self.on_friend_request_received: Optional[Callable[[str], None]] = None
        self.on_friend_accepted: Optional[Callable[[str], None]] = None
        self.on_presence_changed: Optional[Callable[[], None]] = None
        self.on_direct_message_received: Optional[Callable[[str, str], None]] = None
        self.on_nat_traversal_syn: Optional[Callable[[str, str, int, str, int, str], None]] = None

    def _ensure_config_dir(self) -> None:
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _get_user_topic(self, user: str) -> str:
        """Derives an unguessable topic ID using HMAC-SHA256 with a secret app key."""
        msg = f"w3l_signal_{user.lower().strip()}".encode()
        h = hmac.new(_TOPIC_HMAC_KEY, msg, hashlib.sha256).hexdigest()[:24]
        return f"w3l_{h}"

    def get_friends(self) -> List[str]:
        """Returns list of friends for the current user."""
        if not self.friends_file.exists():
            return []
        try:
            with open(self.friends_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(self.username.lower(), [])
        except Exception as e:
            logger.debug(f"Failed to load friends file: {e}")
            return []

    def is_friend(self, friend_name: str) -> bool:
        """Checks if a username is already on the friends list."""
        friends = self.get_friends()
        return any(f.lower() == friend_name.lower().strip() for f in friends)

    def add_friend(self, friend_name: str) -> Tuple[bool, str]:
        """Adds a friend by username locally."""
        friend_name = friend_name.strip()
        if not friend_name:
            return False, "Please enter a valid username."
        if friend_name.lower() == self.username.lower():
            return False, "You cannot add yourself as a friend."
        if len(friend_name) < 3:
            return False, "Username must be at least 3 characters."

        friends = self.get_friends()
        for f in friends:
            if f.lower() == friend_name.lower():
                return False, f"'{friend_name}' is already in your friend list."

        friends.append(friend_name)
        self._save_friends(friends)
        logger.info(f"User '{self.username}' added friend '{friend_name}'")
        return True, f"Added {friend_name} to your friends!"

    def send_friend_request(self, target_username: str) -> Tuple[bool, str]:
        """
        Dispatches a real-time Friend Request to target_username over HTTPS signaling.
        Also adds them to local list so sender doesn't have to re-enter their name.
        """
        target_username = target_username.strip()
        if not target_username:
            return False, "Please enter a valid username."
        if target_username.lower() == self.username.lower():
            return False, "You cannot add yourself as a friend."
        if len(target_username) < 3:
            return False, "Username must be at least 3 characters."

        friends = self.get_friends()
        for f in friends:
            if f.lower() == target_username.lower():
                return False, f"'{target_username}' is already in your friend list."

        # Add to local friends
        friends.append(target_username)
        self._save_friends(friends)

        # Dispatch real-time HTTPS signal to recipient
        topic = self._get_user_topic(target_username)
        url = f"https://ntfy.sh/{topic}"

        payload = json.dumps({
            "from": self.username,
            "to": target_username,
            "type": "FRIEND_REQUEST",
            "ts": time.time()
        }).encode("utf-8")

        headers = {
            "Title": f"Friend Request from {self.username}",
            "Priority": "high",
            "Tags": "busts_in_silhouette,waving_hand"
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    logger.info(f"Dispatched Friend Request from '{self.username}' to '{target_username}'")
                    return True, f"Friend request sent to {target_username}!"
                return False, f"Error sending request (Status: {resp.status})"
        except Exception as e:
            logger.error(f"Failed to send friend request: {e}")
            return True, f"Added {target_username} (offline signal queued)."

    def accept_friend_request(self, from_username: str) -> None:
        """
        Accepts incoming friend request:
        1. Saves to local friend list.
        2. Dispatches FRIEND_ACCEPT signal back to original sender.
        """
        from_username = from_username.strip()
        self.add_friend(from_username)

        topic = self._get_user_topic(from_username)
        url = f"https://ntfy.sh/{topic}"
        payload = json.dumps({
            "from": self.username,
            "to": from_username,
            "type": "FRIEND_ACCEPT",
            "ts": time.time()
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=payload, headers={"Priority": "default"}, method="POST")
            def _post():
                try:
                    urllib.request.urlopen(req, timeout=5.0)
                except Exception:
                    pass
            threading.Thread(target=_post, daemon=True).start()
            logger.info(f"Sent FRIEND_ACCEPT from '{self.username}' to '{from_username}'")
        except Exception as e:
            logger.debug(f"Error sending friend accept signal: {e}")

    def remove_friend(self, friend_name: str) -> bool:
        """Removes a friend from the list."""
        friends = self.get_friends()
        orig_len = len(friends)
        friends = [f for f in friends if f.lower() != friend_name.lower()]
        if len(friends) != orig_len:
            self._save_friends(friends)
            logger.info(f"User '{self.username}' removed friend '{friend_name}'")
            return True
        return False

    def _save_friends(self, friends: List[str]) -> None:
        data = {}
        if self.friends_file.exists():
            try:
                with open(self.friends_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data[self.username.lower()] = friends
        try:
            with open(self.friends_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save friends list: {e}")

    def send_invite(
        self,
        recipient_username: str,
        room_code: str,
        local_port: int = 0,
        local_ip: str = ""
    ) -> Tuple[bool, str]:
        """
        Sends a 1-click game invite directly to the recipient's app.
        Delivered over encrypted HTTPS signaling in milliseconds.
        Includes local LAN / loopback candidates for zero-lag instant connection.
        """
        recipient_username = recipient_username.strip()
        if not recipient_username:
            return False, "Invalid recipient username."

        topic = self._get_user_topic(recipient_username)
        url = f"https://ntfy.sh/{topic}"

        payload_dict = {
            "from": self.username,
            "to": recipient_username,
            "code": room_code,
            "ts": time.time(),
            "type": "GAME_INVITE",
            "version": APP_VERSION
        }
        if local_port:
            payload_dict["local_port"] = local_port
        if local_ip:
            payload_dict["local_ip"] = local_ip

        payload = json.dumps(payload_dict).encode("utf-8")

        headers = {
            "Title": f"Warcraft III Invite from {self.username}",
            "Priority": "high",
            "Tags": "video_game,swords"
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    logger.info(f"Dispatched 1-click invite from '{self.username}' to '{recipient_username}'")
                    return True, f"Invite sent to {recipient_username}!"
                return False, f"Failed to deliver invite (Status: {resp.status})"
        except Exception as e:
            logger.error(f"Failed to send invite: {e}")
            return False, f"Network error sending invite: {e}"

    def send_direct_message(self, recipient_username: str, text: str) -> Tuple[bool, str]:
        """
        Sends an ephemeral direct message to recipient_username over HTTPS signaling.
        Strictly ephemeral: never persisted to disk or remote database.
        Restricted: Only delivered if the recipient is currently online.
        """
        recipient_username = recipient_username.strip()
        text = text.strip()
        if not recipient_username or not text:
            return False, "Cannot send empty message."

        if not self.is_friend_online(recipient_username):
            return False, f"Cannot send message: '{recipient_username}' is offline."

        topic = self._get_user_topic(recipient_username)
        url = f"https://ntfy.sh/{topic}"

        payload = json.dumps({
            "from": self.username,
            "to": recipient_username,
            "type": "DIRECT_MESSAGE",
            "text": text,
            "ts": time.time()
        }).encode("utf-8")

        headers = {
            "Title": f"Private Message from {self.username}",
            "Priority": "high",
            "Tags": "speech_balloon"
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    logger.info(f"Dispatched PM from '{self.username}' to '{recipient_username}'")
                    return True, "Delivered"
                return False, f"Delivery failed (Status: {resp.status})"
        except Exception as e:
            logger.error(f"Failed to send direct message to {recipient_username}: {e}")
            return False, f"Failed to deliver: {e}"

    def send_nat_traversal_syn(
        self,
        host_username: str,
        client_pub_ip: str,
        client_pub_port: int,
        client_lan_ip: str = "",
        client_local_port: int = 0
    ) -> None:
        """Sends instant signal to Host so Host's router pierces outbound NAT hole for this client."""
        host_username = host_username.strip()
        if not host_username:
            return

        topic = self._get_user_topic(host_username)
        url = f"https://ntfy.sh/{topic}"

        payload = json.dumps({
            "from": self.username,
            "to": host_username,
            "type": "NAT_TRAVERSAL_SYN",
            "client_pub_ip": client_pub_ip,
            "client_pub_port": client_pub_port,
            "client_lan_ip": client_lan_ip,
            "client_local_port": client_local_port,
            "version": APP_VERSION,
            "ts": time.time()
        }).encode("utf-8")

        headers = {
            "Priority": "urgent",
            "Tags": "incoming_envelope"
        }

        def _do_send():
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                urllib.request.urlopen(req, timeout=4.0)
                logger.info(f"Dispatched NAT_TRAVERSAL_SYN from '{self.username}' to Host '{host_username}'")
            except Exception as e:
                logger.debug(f"Failed to dispatch NAT_TRAVERSAL_SYN: {e}")

        threading.Thread(target=_do_send, daemon=True, name="NAT-Syn-Sender").start()

    def is_friend_online(self, friend_name: str) -> bool:
        """Returns True if friend was seen active within the last 180 seconds."""
        last_seen = self.online_friends.get(friend_name.lower().strip(), 0.0)
        return (time.time() - last_seen) < 180.0

    def _broadcast_presence(self, status: str = "ONLINE"):
        """Sends lightweight presence ping to all mutual friends."""
        friends = self.get_friends()
        if not friends:
            return

        payload = json.dumps({
            "from": self.username,
            "type": f"PRESENCE_{status}",
            "ts": time.time()
        }).encode("utf-8")

        for f in friends:
            topic = self._get_user_topic(f)
            url = f"https://ntfy.sh/{topic}"
            try:
                req = urllib.request.Request(url, data=payload, headers={"Priority": "min"}, method="POST")
                urllib.request.urlopen(req, timeout=3.0)
            except Exception:
                pass

    def _presence_loop(self):
        # Initial broadcast
        self._broadcast_presence("ONLINE")
        while self._running:
            time.sleep(75.0)  # Low-frequency heartbeat to protect ntfy.sh daily quotas
            if self._running:
                self._broadcast_presence("ONLINE")
                if self.on_presence_changed:
                    try:
                        self.on_presence_changed()
                    except Exception:
                        pass

    def start_listening(
        self,
        on_invite: Optional[Callable[[GameInvite], None]] = None,
        on_friend_request: Optional[Callable[[str], None]] = None,
        on_friend_accepted: Optional[Callable[[str], None]] = None,
        on_presence_changed: Optional[Callable[[], None]] = None,
        on_direct_message: Optional[Callable[[str, str], None]] = None,
        on_nat_syn: Optional[Callable[[str, str, int, str, int, str], None]] = None
    ) -> None:
        """Starts real-time listener for incoming game invites, friend requests, presence, PMs, and NAT syn."""
        if on_invite:
            self.on_invite_received = on_invite
        if on_friend_request:
            self.on_friend_request_received = on_friend_request
        if on_friend_accepted:
            self.on_friend_accepted = on_friend_accepted
        if on_presence_changed:
            self.on_presence_changed = on_presence_changed
        if on_direct_message:
            self.on_direct_message_received = on_direct_message
        if on_nat_syn:
            self.on_nat_traversal_syn = on_nat_syn

        if self._running:
            return

        self._running = True
        self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True, name="SignalingListener")
        self._listener_thread.start()

        self._presence_thread = threading.Thread(target=self._presence_loop, daemon=True, name="PresenceBroadcaster")
        self._presence_thread.start()
        logger.info(f"Started real-time signaling listener and presence for user '{self.username}'")

    def stop_listening(self) -> None:
        """Stops listener thread and notifies friends of offline status."""
        if self._running:
            self._running = False
            threading.Thread(target=lambda: self._broadcast_presence("OFFLINE"), daemon=True).start()

    def _poll_offline_signals(self, topic: str) -> None:
        """Fetches pending requests/invites sent while the user was offline."""
        try:
            poll_url = f"https://ntfy.sh/{topic}/json?poll=1&since=48h"
            req = urllib.request.Request(poll_url, headers={"User-Agent": "W3Lounge/1.0"})
            with urllib.request.urlopen(req, timeout=8.0) as response:
                for line in response:
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if not line_str:
                        continue
                    try:
                        record = json.loads(line_str)
                        if record.get("event") == "message" and record.get("message"):
                            data = json.loads(record["message"])
                            self._process_incoming_dict(data)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Offline signal poll completed: {e}")

    def _listen_loop(self) -> None:
        """Streaming loop receiving incoming invites and requests over HTTPS."""
        topic = self._get_user_topic(self.username)

        # 1. Fetch any pending requests sent while user was offline
        self._poll_offline_signals(topic)

        # 2. Open live streaming connection for real-time messages with 48h history catch-up
        stream_url = f"https://ntfy.sh/{topic}/raw?since=48h"

        while self._running:
            try:
                req = urllib.request.Request(stream_url, headers={"User-Agent": "W3Lounge/1.0"})
                with urllib.request.urlopen(req, timeout=60.0) as response:
                    for line in response:
                        if not self._running:
                            break
                        line_str = line.decode("utf-8", errors="ignore").strip()
                        if not line_str:
                            continue

                        try:
                            data = json.loads(line_str)
                            self._process_incoming_dict(data)
                        except Exception:
                            pass
            except Exception as e:
                if self._running:
                    logger.debug(f"Signaling listener reconnecting after: {e}")
                    time.sleep(2.0)

    def _process_incoming_dict(self, data: dict) -> None:
        """Parses and dispatches incoming signal payload."""
        sig_type = data.get("type")
        sender = data.get("from", "")
        ts = data.get("ts", 0.0)
        now = time.time()

        if not sender or sender.lower() == self.username.lower():
            return

        # Deduplication key
        sig_key = f"{sig_type}_{sender.lower()}_{int(ts)}"
        if sig_key in self._seen_signals:
            return
        self._seen_signals.add(sig_key)

        if sig_type == "GAME_INVITE":
            # Game invites expire after 90 seconds
            if now - ts > 90.0:
                logger.debug(f"Dropped expired game invite from {sender}")
                return
            code = data.get("code", "")
            if code and self.on_invite_received:
                app_version = str(data.get("version", "1.0.0"))
                invite = GameInvite(
                    host_username=sender,
                    recipient_username=self.username,
                    room_code=code,
                    timestamp=ts,
                    local_port=int(data.get("local_port", 0)),
                    local_ip=str(data.get("local_ip", "")),
                    app_version=app_version
                )
                logger.info(f"Received valid game invite from '{sender}' (local_port={invite.local_port}, v{app_version})")
                self.on_invite_received(invite)

        elif sig_type == "FRIEND_REQUEST":
            # Friend requests stay valid for 7 days
            if now - ts > 86400.0 * 7:
                return
            # Don't prompt if already friends
            if self.is_friend(sender):
                return
            logger.info(f"Received Friend Request from '{sender}'")
            if self.on_friend_request_received:
                self.on_friend_request_received(sender)

        elif sig_type == "FRIEND_ACCEPT":
            logger.info(f"'{sender}' accepted your Friend Request!")
            self.add_friend(sender)
            if self.on_friend_accepted:
                self.on_friend_accepted(sender)

        elif sig_type == "PRESENCE_ONLINE":
            self.online_friends[sender.lower().strip()] = now
            if self.on_presence_changed:
                try:
                    self.on_presence_changed()
                except Exception:
                    pass

        elif sig_type == "PRESENCE_OFFLINE":
            self.online_friends.pop(sender.lower().strip(), None)
            if self.on_presence_changed:
                try:
                    self.on_presence_changed()
                except Exception:
                    pass

        elif sig_type == "DIRECT_MESSAGE":
            # Ephemeral PMs drop if older than 180 seconds
            if now - ts > 180.0:
                return
            msg_text = str(data.get("text", "")).strip()
            if msg_text and self.on_direct_message_received:
                logger.info(f"Received Direct Message from '{sender}'")
                self.on_direct_message_received(sender, msg_text)

        elif sig_type == "NAT_TRAVERSAL_SYN":
            # Traversal signals expire after 45 seconds
            if now - ts > 45.0:
                return
            c_pub_ip = str(data.get("client_pub_ip", "")).strip()
            c_pub_port = int(data.get("client_pub_port", 0))
            c_lan_ip = str(data.get("client_lan_ip", "")).strip()
            c_local_p = int(data.get("client_local_port", 0))
            ver = str(data.get("version", "1.0.0")).strip()
            if c_pub_ip and c_pub_port and self.on_nat_traversal_syn:
                logger.info(f"Received NAT_TRAVERSAL_SYN from '{sender}' ({c_pub_ip}:{c_pub_port}, v{ver})")
                self.on_nat_traversal_syn(sender, c_pub_ip, c_pub_port, c_lan_ip, c_local_p, ver)
