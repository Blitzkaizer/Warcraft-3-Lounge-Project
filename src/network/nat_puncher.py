"""
UDP Hole Puncher and P2P Tunnel Engine with Cryptographic Verification.
Features:
- Dynamic random OS port binding (port 0) so ports are never static or predictable.
- 64-bit Cryptographic HMAC-SHA256 packet filtering: drops spoofed/unauthorized traffic.
- Maximum 12 peers enforcement (DoS protection).
- Multiplexes TCP-over-UDP game stream chunks.
"""

import hashlib
import socket
import struct
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..core.config import BUFFER_SIZE, logger
from .protocol import LoungeMessageType, SecureTunnelPacket
from .stun import STUNClient, STUNResult

SIO_UDP_CONNRESET = 0x9800000C
MAX_LOUNGE_PEERS = 12
MAX_PACKETS_PER_SEC_PER_IP = 80   # Game only needs 20-30 pkts/sec; 80 gives generous headroom

class NATPuncher:
    """
    Cryptographically secure UDP hole puncher and P2P packet router with Per-IP Rate Limiting.
    """

    def __init__(
        self,
        bind_port: int = 0,
        player_name: str = "Player",
        room_secret: bytes = b"W3LoungeDefault1",
        on_peer_connected: Optional[Callable[[Tuple[str, int], str], None]] = None,
        on_w3gs_packet: Optional[Callable[[bytes], None]] = None,
        on_tcp_data: Optional[Callable[[Tuple[str, int], int, bytes], None]] = None,
        on_stream_close: Optional[Callable[[Tuple[str, int], int], None]] = None,
        on_event: Optional[Callable[[str], None]] = None,
        on_peer_ping: Optional[Callable[[str, float], None]] = None,
        on_peer_disconnected: Optional[Callable[[Tuple[str, int], str], None]] = None,
        on_chat_message: Optional[Callable[[str, str], None]] = None,
        on_peer_reconnecting: Optional[Callable[[Tuple[str, int], str], None]] = None
    ):
        self.bind_port = bind_port
        self.player_name = player_name
        self.room_secret = room_secret
        self.secret_key = self._derive_key(room_secret)

        self.on_peer_connected = on_peer_connected
        self.on_w3gs_packet = on_w3gs_packet
        self.on_tcp_data = on_tcp_data
        self.on_stream_close = on_stream_close
        self.on_event = on_event
        self.on_peer_ping = on_peer_ping
        self.on_peer_disconnected = on_peer_disconnected
        self.on_chat_message = on_chat_message
        self.on_peer_reconnecting = on_peer_reconnecting

        self.socket: Optional[socket.socket] = None
        self.stun_result: Optional[STUNResult] = None
        self._is_running = False
        self._rx_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None

        self.connected_peers: Dict[Tuple[str, int], float] = {}
        self.peer_names: Dict[Tuple[str, int], str] = {}
        self.peer_rtt: Dict[Tuple[str, int], float] = {}
        self.reconnecting_peers: Set[Tuple[str, int]] = set()
        self.unauthorized_dropped = 0

        # Rate Limiting: IP -> list of recent timestamps
        self._packet_history: Dict[str, List[float]] = {}
        self.rate_limited_dropped = 0

    @staticmethod
    def _derive_key(secret: bytes) -> bytes:
        return hashlib.sha256(b"W3LoungeKey_" + secret).digest()

    def set_room_secret(self, secret: bytes):
        self.room_secret = secret
        self.secret_key = self._derive_key(secret)

    def initialize(self) -> bool:
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if sys.platform == "win32":
                try:
                    self.socket.ioctl(SIO_UDP_CONNRESET, struct.pack("I", 0))
                except Exception:
                    pass

            self.socket.bind(("0.0.0.0", self.bind_port))
            self.socket.settimeout(1.0)
            local_port = self.socket.getsockname()[1]
            logger.info(f"[Security] Bound Dynamic Ephemeral Port: {local_port}")

            self.stun_result = STUNClient.get_public_mapping(sock=self.socket)
            if not self.stun_result:
                logger.warning("Could not resolve STUN mapping.")
                return False

            self._is_running = True
            self._rx_thread = threading.Thread(target=self._receive_loop, name="P2P-Receiver", daemon=True)
            self._rx_thread.start()

            self._keepalive_thread = threading.Thread(target=self._keepalive_loop, name="P2P-Keepalive", daemon=True)
            self._keepalive_thread.start()

            return True

        except Exception as e:
            logger.error(f"Failed to initialize NATPuncher: {e}")
            self.shutdown()
            return False

    def send_leave_and_disconnect(self) -> None:
        """Broadcasts a signed PEER_LEAVE packet to all connected peers before exiting."""
        if not self.socket or not self.connected_peers:
            return
        pkt = SecureTunnelPacket.pack(
            LoungeMessageType.PEER_LEAVE,
            self.player_name.encode("utf-8"),
            secret_key=self.secret_key
        )
        for _ in range(3):
            for peer in list(self.connected_peers.keys()):
                try:
                    self.socket.sendto(pkt, peer)
                except Exception:
                    pass
            time.sleep(0.015)

    def shutdown(self) -> None:
        if self._is_running:
            self.send_leave_and_disconnect()
        self._is_running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        self.connected_peers.clear()
        logger.info("NATPuncher shutdown complete.")

    def punch_to_target(
        self,
        target_ip: str,
        target_port: int,
        room_secret: bytes,
        fallback_targets: Optional[List[Tuple[str, int]]] = None,
        max_attempts: int = 30
    ) -> bool:
        self.set_room_secret(room_secret)
        targets: List[Tuple[str, int]] = [(target_ip, target_port)]
        if fallback_targets:
            for fb in fallback_targets:
                if fb and fb not in targets:
                    targets.append(fb)

        logger.info(f"Initiating signed hole punch toward candidates: {targets}")

        probe_payload = room_secret + self.player_name.encode("utf-8")
        probe_packet = SecureTunnelPacket.pack(
            LoungeMessageType.PUNCH_PROBE,
            probe_payload,
            secret_key=self.secret_key
        )

        for _ in range(1, max_attempts + 1):
            if not self._is_running:
                break
            for t in targets:
                if t in self.connected_peers:
                    logger.info(f"Hole punch confirmed! Connected to {t[0]}:{t[1]}")
                    return True

            for t in targets:
                try:
                    self.socket.sendto(probe_packet, t)
                except Exception as e:
                    logger.debug(f"Hole punch probe error to {t}: {e}")

            time.sleep(0.15)

        return any(t in self.connected_peers for t in targets)

    def punch_toward_peer(
        self,
        target_ip: str,
        target_port: int,
        fallback_targets: Optional[List[Tuple[str, int]]] = None,
        num_probes: int = 15
    ) -> None:
        """
        Sends outbound PUNCH_PROBE packets toward an incoming client to pierce host's NAT firewall.
        Enables simultaneous bidirectional hole punching for restricted cone NATs.
        """
        if not self.socket or not self._is_running:
            return

        targets: List[Tuple[str, int]] = [(target_ip, target_port)]
        if fallback_targets:
            for fb in fallback_targets:
                if fb and fb not in targets:
                    targets.append(fb)

        probe_payload = self.room_secret + self.player_name.encode("utf-8")
        probe_packet = SecureTunnelPacket.pack(
            LoungeMessageType.PUNCH_PROBE,
            probe_payload,
            secret_key=self.secret_key
        )

        def _do_punch():
            logger.info(f"[Host NAT Punch] Piercing outbound firewall toward joining peer: {targets}")
            for _ in range(num_probes):
                if not self._is_running:
                    break
                if any(t in self.connected_peers for t in targets):
                    logger.info(f"[Host NAT Punch] Peer connected: {target_ip}:{target_port}")
                    break
                for t in targets:
                    try:
                        self.socket.sendto(probe_packet, t)
                    except Exception as e:
                        logger.debug(f"Host probe error to {t}: {e}")
                time.sleep(0.1)

        threading.Thread(target=_do_punch, name="Host-Punch-Thread", daemon=True).start()

    def broadcast_w3gs_to_peers(self, data: bytes) -> None:
        pkt = SecureTunnelPacket.pack(
            LoungeMessageType.W3GS_BROADCAST,
            data,
            secret_key=self.secret_key
        )
        self._send_to_all_peers(pkt)

    def send_tcp_data_to_peer(self, target: Tuple[str, int], stream_id: int, data: bytes) -> None:
        pkt = SecureTunnelPacket.pack(
            LoungeMessageType.TCP_DATA,
            data,
            stream_id=stream_id,
            secret_key=self.secret_key
        )
        if self.socket:
            try:
                self.socket.sendto(pkt, target)
            except Exception:
                pass

    def send_tcp_close_to_peer(self, target: Tuple[str, int], stream_id: int) -> None:
        pkt = SecureTunnelPacket.pack(
            LoungeMessageType.TCP_CLOSE,
            b"",
            stream_id=stream_id,
            secret_key=self.secret_key
        )
        if self.socket:
            try:
                self.socket.sendto(pkt, target)
            except Exception:
                pass

    def send_event_to_peers(self, event_text: str) -> None:
        pkt = SecureTunnelPacket.pack(
            LoungeMessageType.LOUNGE_EVENT,
            event_text.encode("utf-8"),
            secret_key=self.secret_key
        )
        self._send_to_all_peers(pkt)

    def send_chat_message(self, message: str) -> None:
        """Broadcasts a room lobby chat message to all connected peers."""
        chat_payload = f"{self.player_name}: {message}".encode("utf-8")
        pkt = SecureTunnelPacket.pack(
            LoungeMessageType.CHAT_MESSAGE,
            chat_payload,
            secret_key=self.secret_key
        )
        self._send_to_all_peers(pkt)

    def _send_to_all_peers(self, raw_bytes: bytes) -> None:
        if not self.socket:
            return
        for peer in list(self.connected_peers.keys()):
            try:
                self.socket.sendto(raw_bytes, peer)
            except Exception:
                pass

    def _is_rate_limited(self, ip: str) -> bool:
        """Sliding 1-second window check: limits an IP to MAX_PACKETS_PER_SEC_PER_IP."""
        now = time.time()
        timestamps = self._packet_history.get(ip, [])
        # Keep only timestamps within the last 1.0 second
        timestamps = [t for t in timestamps if now - t < 1.0]
        if len(timestamps) >= MAX_PACKETS_PER_SEC_PER_IP:
            self.rate_limited_dropped += 1
            return True
        timestamps.append(now)
        self._packet_history[ip] = timestamps
        return False

    def _receive_loop(self) -> None:
        while self._is_running:
            try:
                if not self.socket:
                    break
                raw_data, addr = self.socket.recvfrom(BUFFER_SIZE)
                if not raw_data:
                    continue

                # 1. Rate Limiter: Drop flood packets before spending any CPU on decryption
                sender_ip = addr[0]
                if self._is_rate_limited(sender_ip):
                    continue

                # 2. Cryptographic signature verification
                unpacked = SecureTunnelPacket.unpack(raw_data, secret_key=self.secret_key)
                if not unpacked:
                    self.unauthorized_dropped += 1
                    continue

                msg_type, stream_id, payload = unpacked

                # 3. Peer State Enforcement: Game data only accepted from authenticated peers
                if msg_type in (LoungeMessageType.W3GS_BROADCAST, LoungeMessageType.TCP_DATA, LoungeMessageType.TCP_CLOSE):
                    if addr not in self.connected_peers:
                        self.unauthorized_dropped += 1
                        continue

                if msg_type == LoungeMessageType.PUNCH_PROBE:
                    # DoS Protection: Cap peers at MAX_LOUNGE_PEERS
                    if len(self.connected_peers) >= MAX_LOUNGE_PEERS and addr not in self.connected_peers:
                        logger.warning(f"Lounge full ({MAX_LOUNGE_PEERS} peers). Ignoring connection from {addr}")
                        continue

                    remote_user = payload[8:].decode("utf-8", errors="replace") if len(payload) > 8 else "Friend"
                    logger.info(f"Verified PUNCH_PROBE from '{remote_user}' at {addr[0]}:{addr[1]}")

                    ack_packet = SecureTunnelPacket.pack(
                        LoungeMessageType.PUNCH_ACK,
                        self.player_name.encode("utf-8"),
                        secret_key=self.secret_key
                    )
                    self.socket.sendto(ack_packet, addr)

                    self.peer_names[addr] = remote_user
                    if addr not in self.connected_peers:
                        self.connected_peers[addr] = time.time()
                        if self.on_peer_connected:
                            self.on_peer_connected(addr, remote_user)

                elif msg_type == LoungeMessageType.PUNCH_ACK:
                    remote_user = payload.decode("utf-8", errors="replace") if payload else "Host"
                    logger.info(f"Verified PUNCH_ACK from '{remote_user}' at {addr[0]}:{addr[1]}")
                    self.peer_names[addr] = remote_user
                    if addr not in self.connected_peers:
                        self.connected_peers[addr] = time.time()
                        if self.on_peer_connected:
                            self.on_peer_connected(addr, remote_user)

                elif msg_type == LoungeMessageType.PING:
                    self.connected_peers[addr] = time.time()
                    # Echo payload containing client send timestamp back to sender
                    pong_pkt = SecureTunnelPacket.pack(
                        LoungeMessageType.PONG,
                        payload=payload,
                        secret_key=self.secret_key
                    )
                    self.socket.sendto(pong_pkt, addr)

                elif msg_type == LoungeMessageType.PONG:
                    self.connected_peers[addr] = time.time()
                    if len(payload) >= 8:
                        try:
                            sent_time = struct.unpack("!d", payload[:8])[0]
                            rtt_ms = max(1.0, (time.time() - sent_time) * 1000.0)
                            self.peer_rtt[addr] = round(rtt_ms, 1)
                            peer_name = self.peer_names.get(addr, f"{addr[0]}:{addr[1]}")
                            if self.on_peer_ping:
                                self.on_peer_ping(peer_name, rtt_ms)
                        except Exception:
                            pass

                elif msg_type == LoungeMessageType.W3GS_BROADCAST:
                    self.connected_peers[addr] = time.time()
                    if self.on_w3gs_packet:
                        self.on_w3gs_packet(payload)

                elif msg_type == LoungeMessageType.TCP_DATA:
                    self.connected_peers[addr] = time.time()
                    if self.on_tcp_data:
                        self.on_tcp_data(addr, stream_id, payload)

                elif msg_type == LoungeMessageType.TCP_CLOSE:
                    self.connected_peers[addr] = time.time()
                    if self.on_stream_close:
                        self.on_stream_close(addr, stream_id)

                elif msg_type == LoungeMessageType.LOUNGE_EVENT:
                    self.connected_peers[addr] = time.time()
                    if self.on_event:
                        try:
                            self.on_event(payload.decode("utf-8", errors="replace"))
                        except Exception:
                            pass

                elif msg_type == LoungeMessageType.CHAT_MESSAGE:
                    self.connected_peers[addr] = time.time()
                    if self.on_chat_message:
                        try:
                            full_text = payload.decode("utf-8", errors="replace")
                            parts = full_text.split(": ", 1)
                            sender = parts[0] if len(parts) > 1 else self.peer_names.get(addr, "Friend")
                            msg = parts[1] if len(parts) > 1 else full_text
                            self.on_chat_message(sender, msg)
                        except Exception:
                            pass

                elif msg_type == LoungeMessageType.PEER_LEAVE:
                    remote_user = payload.decode("utf-8", errors="replace") if payload else self.peer_names.get(addr, "Friend")
                    logger.info(f"Player '{remote_user}' ({addr[0]}:{addr[1]}) gracefully left the room.")
                    if addr in self.connected_peers:
                        del self.connected_peers[addr]
                    if addr in self.peer_rtt:
                        del self.peer_rtt[addr]
                    if self.on_peer_disconnected:
                        self.on_peer_disconnected(addr, remote_user)

            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if self._is_running:
                    logger.debug(f"Error in NAT receiver loop: {e}")
                time.sleep(0.05)

    def _keepalive_loop(self) -> None:
        while self._is_running:
            time.sleep(2.0)
            now = time.time()
            ping_payload = struct.pack("!d", now)
            ping_pkt = SecureTunnelPacket.pack(
                LoungeMessageType.PING,
                payload=ping_payload,
                secret_key=self.secret_key
            )

            for peer, last_seen in list(self.connected_peers.items()):
                peer_name = self.peer_names.get(peer, f"{peer[0]}:{peer[1]}")
                time_since_seen = now - last_seen
                if time_since_seen > 16.0:
                    logger.warning(f"Peer '{peer_name}' ({peer[0]}:{peer[1]}) dropped after 16s timeout.")
                    self.connected_peers.pop(peer, None)
                    self.peer_rtt.pop(peer, None)
                    self.reconnecting_peers.discard(peer)
                    if self.on_peer_disconnected:
                        self.on_peer_disconnected(peer, peer_name)
                elif time_since_seen > 5.0:
                    if peer not in self.reconnecting_peers:
                        self.reconnecting_peers.add(peer)
                        logger.info(f"Peer '{peer_name}' experiencing network jitter. Buffering reconnecting state...")
                        if self.on_peer_reconnecting:
                            self.on_peer_reconnecting(peer, peer_name)
                    try:
                        self.socket.sendto(ping_pkt, peer)
                    except Exception:
                        pass
                else:
                    self.reconnecting_peers.discard(peer)
                    try:
                        self.socket.sendto(ping_pkt, peer)
                    except Exception:
                        pass
