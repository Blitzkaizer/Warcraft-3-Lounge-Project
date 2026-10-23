"""
Lounge Session Manager for Warcraft III.
Coordinates Host and Client roles using P2P sockets and TCP-over-UDP multiplexing.
Guarantees:
- Never binds to UDP 0.0.0.0:6112 (prevents WC3 LAN crash).
- Cryptographic 64-bit HMAC-SHA256 authenticated tunnel.
- TCP game streams travel through the hole-punched UDP tunnel (zero port-forwarding).
"""

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..core.config import WC3_DEFAULT_PORT, logger
from ..core.packet_parser import PacketParser
from ..core.tcp_tunnel import ClientTCPBridge, HostTCPBridge
from ..core.udp_proxy import UDPInjector
from ..launcher.game_launcher import GameLauncher
from .lounge_code import LoungeCode
from .nat_puncher import NATPuncher

class LoungeSession:
    """Represents an active Warcraft III P2P Lounge session."""

    def __init__(self, is_host: bool = True, player_name: str = "Player"):
        self.is_host = is_host
        self.player_name = player_name

        self.puncher: Optional[NATPuncher] = None
        self.udp_injector = UDPInjector()
        self.client_tcp_bridge: Optional[ClientTCPBridge] = None
        self.host_tcp_bridge: Optional[HostTCPBridge] = None
        self.launcher = GameLauncher()

        self.lounge_code: Optional[str] = None
        self.target_host_endpoint: Optional[Tuple[str, int]] = None
        self.room_secret: bytes = LoungeCode.generate_secret()

        self.connected_peers: Dict[Tuple[str, int], str] = {}
        self._host_announce_thread: Optional[threading.Thread] = None
        self._is_running = False

        # Callbacks
        self.on_peer_joined: Optional[Callable[[Tuple[str, int], str], None]] = None
        self.on_match_detected: Optional[Callable[[str, int, int], None]] = None
        self.on_status_change: Optional[Callable[[str], None]] = None
        self.on_game_launched: Optional[Callable[[], None]] = None
        self.on_peer_ping: Optional[Callable[[str, float], None]] = None
        self.on_peer_left: Optional[Callable[[str], None]] = None
        self.on_chat_received: Optional[Callable[[str, str], None]] = None

    def start_host(self) -> Optional[str]:
        """
        Starts hosting a Lounge session:
        1. Generates 64-bit cryptographic room secret.
        2. Binds dynamic random port and resolves STUN.
        3. Starts HostTCPBridge to receive multiplexed TCP streams from peers over UDP.
        """
        self.is_host = True
        self._is_running = True
        self.room_secret = LoungeCode.generate_secret()

        self._notify_status(f"Host '{self.player_name}' initializing secure P2P socket...")

        self.host_tcp_bridge = HostTCPBridge(
            target_port=WC3_DEFAULT_PORT,
            on_send_udp_chunk=self._on_host_send_tcp_to_peer,
            on_stream_close=self._on_host_tcp_stream_close
        )
        self.host_tcp_bridge.start()

        self.puncher = NATPuncher(
            player_name=self.player_name,
            room_secret=self.room_secret,
            on_peer_connected=self._handle_peer_connected,
            on_tcp_data=self._handle_host_tcp_data_from_peer,
            on_stream_close=self._handle_tcp_close_from_peer,
            on_event=self._handle_event,
            on_peer_ping=self._handle_peer_ping,
            on_peer_disconnected=self._handle_peer_disconnected,
            on_chat_message=self._handle_chat_message
        )

        if not self.puncher.initialize():
            self._notify_status("Failed to obtain STUN public endpoint.")
            return None

        stun = self.puncher.stun_result
        if not stun:
            return None

        self.lounge_code = LoungeCode.encode(stun.public_ip, stun.public_port, self.room_secret)
        self._notify_status(f"Lounge created! Code: {self.lounge_code}")

        self._host_announce_thread = threading.Thread(
            target=self._host_announce_loop,
            name="Host-Announcer",
            daemon=True
        )
        self._host_announce_thread.start()

        return self.lounge_code

    def join_lounge(self, code: str, local_port: int = 0, local_ip: str = "") -> bool:
        """
        Joins an existing Lounge session:
        1. Decodes 64-bit secret, host IP, and port from code.
        2. Binds dynamic port and punches hole through router.
        3. Supports local candidates (127.0.0.1, LAN IP) for instant connection if peers are local.
        4. Starts ClientTCPBridge on local 127.0.0.1:6112.
        """
        self.is_host = False
        self._is_running = True
        decoded = LoungeCode.decode(code)
        if not decoded:
            self._notify_status("Invalid or malformed Lounge Code.")
            return False

        host_ip, host_port, room_secret = decoded
        self.target_host_endpoint = (host_ip, host_port)
        self.room_secret = room_secret
        self.lounge_code = code

        self._notify_status(f"Decoded Host Endpoint: {host_ip}:{host_port}")

        self.client_tcp_bridge = ClientTCPBridge(
            local_bind_port=WC3_DEFAULT_PORT,
            on_send_udp_chunk=self._on_client_send_tcp_to_host,
            on_stream_close=self._on_client_tcp_stream_close
        )
        self.client_tcp_bridge.start()

        self.puncher = NATPuncher(
            player_name=self.player_name,
            room_secret=room_secret,
            on_peer_connected=self._handle_peer_connected,
            on_w3gs_packet=self._handle_remote_w3gs_packet,
            on_tcp_data=self._handle_client_tcp_data_from_host,
            on_stream_close=self._handle_tcp_close_from_peer,
            on_event=self._handle_event,
            on_peer_ping=self._handle_peer_ping,
            on_peer_disconnected=self._handle_peer_disconnected,
            on_chat_message=self._handle_chat_message
        )

        if not self.puncher.initialize():
            self._notify_status("Failed to initialize client network.")
            return False

        fallback_targets: List[Tuple[str, int]] = []
        if local_port:
            fallback_targets.append(("127.0.0.1", local_port))
            if local_ip and local_ip != "127.0.0.1":
                fallback_targets.append((local_ip, local_port))

        self._notify_status(f"Connecting to Lounge Host at {host_ip}:{host_port}...")
        punch_thread = threading.Thread(
            target=self._punch_and_setup_client,
            args=(host_ip, host_port, room_secret, fallback_targets),
            daemon=True
        )
        punch_thread.start()

        return True

    def launch_game(self, extra_args: Optional[List[str]] = None) -> bool:
        logger.info(f"Triggering Warcraft III launch for '{self.player_name}'...")
        success = self.launcher.launch(extra_args)

        if success and self.on_game_launched:
            self.on_game_launched()

        if self.is_host and self.puncher:
            self.puncher.send_event_to_peers("CMD:LAUNCH")

        return success

    def _host_announce_loop(self):
        while self._is_running:
            if self.puncher and self.puncher.connected_peers:
                match_name = f"{self.player_name}'s Lounge"
                pkt = PacketParser.create_synthetic_w3gs_packet(game_name=match_name, port=WC3_DEFAULT_PORT)
                self.puncher.broadcast_w3gs_to_peers(pkt)
            time.sleep(2.5)

    def _punch_and_setup_client(
        self,
        host_ip: str,
        host_port: int,
        room_secret: bytes,
        fallback_targets: Optional[List[Tuple[str, int]]] = None
    ):
        success = self.puncher.punch_to_target(
            host_ip,
            host_port,
            room_secret,
            fallback_targets=fallback_targets
        )
        if success:
            self._notify_status(f"Successfully connected to Lounge Host at {host_ip}:{host_port}!")
        else:
            self._notify_status("Connection timed out. Host may be offline or router blocked probes.")

    # TCP-over-UDP Forwarding Handlers
    def _on_client_send_tcp_to_host(self, stream_id: int, data: bytes):
        if self.puncher and self.target_host_endpoint:
            self.puncher.send_tcp_data_to_peer(self.target_host_endpoint, stream_id, data)

    def _on_client_tcp_stream_close(self, stream_id: int):
        if self.puncher and self.target_host_endpoint:
            self.puncher.send_tcp_close_to_peer(self.target_host_endpoint, stream_id)

    def _handle_client_tcp_data_from_host(self, addr: Tuple[str, int], stream_id: int, data: bytes):
        if self.client_tcp_bridge:
            self.client_tcp_bridge.receive_udp_chunk(stream_id, data)

    def _on_host_send_tcp_to_peer(self, peer: Tuple[str, int], stream_id: int, data: bytes):
        if self.puncher:
            self.puncher.send_tcp_data_to_peer(peer, stream_id, data)

    def _on_host_tcp_stream_close(self, peer: Tuple[str, int], stream_id: int):
        if self.puncher:
            self.puncher.send_tcp_close_to_peer(peer, stream_id)

    def _handle_host_tcp_data_from_peer(self, peer: Tuple[str, int], stream_id: int, data: bytes):
        if self.host_tcp_bridge:
            self.host_tcp_bridge.handle_incoming_chunk(peer, stream_id, data)

    def _handle_tcp_close_from_peer(self, peer: Tuple[str, int], stream_id: int):
        if self.is_host and self.host_tcp_bridge:
            self.host_tcp_bridge.close_stream(peer, stream_id)
        elif not self.is_host and self.client_tcp_bridge:
            self.client_tcp_bridge.close_stream(stream_id)

    def _handle_remote_w3gs_packet(self, data: bytes):
        if self.is_host:
            return

        game_info = PacketParser.parse_game_info(data)
        if game_info:
            logger.info(f"[Lounge Sync] Discovered Host Match: '{game_info.game_name}' ({game_info.slots_open}/{game_info.slots_total} slots)")
            if self.on_match_detected:
                self.on_match_detected(game_info.game_name, game_info.slots_open, game_info.slots_total)

        self.udp_injector.inject(data, "127.0.0.1", WC3_DEFAULT_PORT)
        try:
            self.udp_injector.inject(data, "255.255.255.255", WC3_DEFAULT_PORT)
        except Exception:
            pass

    def _handle_peer_connected(self, peer: Tuple[str, int], username: str):
        logger.info(f"Player '{username}' connected to Lounge from {peer[0]}:{peer[1]}")
        self.connected_peers[peer] = username
        self._notify_status(f"Player '{username}' connected!")
        if self.on_peer_joined:
            self.on_peer_joined(peer, username)

    def _handle_peer_disconnected(self, peer: Tuple[str, int], username: str):
        logger.info(f"Player '{username}' left the Lounge from {peer[0]}:{peer[1]}")
        if peer in self.connected_peers:
            del self.connected_peers[peer]
        self._notify_status(f"Player '{username}' left the room.")
        if self.on_peer_left:
            self.on_peer_left(username)

    def _handle_event(self, event_text: str):
        self._notify_status(f"Room Event: {event_text}")
        if event_text == "CMD:LAUNCH":
            logger.info("Host initiated game launch! Auto-launching local Warcraft III...")
            self.launch_game()

    def _handle_chat_message(self, sender: str, message: str):
        logger.info(f"[Room Chat] {sender}: {message}")
        if self.on_chat_received:
            self.on_chat_received(sender, message)

    def send_chat(self, message: str):
        if self.puncher:
            self.puncher.send_chat_message(message)

    def start_game_for_all(self):
        """Broadcasts launch command to all peers and launches host game."""
        if self.puncher:
            self.puncher.send_event_to_peers("CMD:LAUNCH")
        self.launch_game()

    def _handle_peer_ping(self, peer_name: str, rtt_ms: float):
        if self.on_peer_ping:
            self.on_peer_ping(peer_name, rtt_ms)

    def _notify_status(self, text: str):
        logger.info(f"[Lounge] {text}")
        if self.on_status_change:
            self.on_status_change(text)

    def shutdown(self):
        self._is_running = False
        if self.udp_injector:
            self.udp_injector.close()
        if self.client_tcp_bridge:
            self.client_tcp_bridge.stop()
            self.client_tcp_bridge = None
        if self.host_tcp_bridge:
            self.host_tcp_bridge.stop()
            self.host_tcp_bridge = None
        if self.puncher:
            self.puncher.shutdown()
            self.puncher = None
        logger.info("Lounge session ended.")
