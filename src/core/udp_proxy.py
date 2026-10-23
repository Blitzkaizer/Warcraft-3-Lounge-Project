"""
Core UDP Proxy and LAN Packet Interceptor/Injector for Warcraft III.
Includes UDPProxy for capturing broadcasts and UDPInjector for forwarding
packets without binding to port 6112, avoiding WSAEADDRINUSE (10048) conflicts.
"""

import socket
import struct
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

from .config import BUFFER_SIZE, WC3_DEFAULT_PORT, logger
from .packet_parser import PacketParser

# Windows Winsock IOCTL constant to suppress WSAECONNRESET (10054) on UDP sockets
SIO_UDP_CONNRESET = 0x9800000C

class UDPInjector:
    """
    Lightweight sender socket that sends packets to Warcraft III on 127.0.0.1:6112
    WITHOUT binding to port 6112 itself. This prevents port conflicts with war3.exe.
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._init_socket()

    def _init_socket(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if sys.platform == "win32":
            try:
                self._sock.ioctl(SIO_UDP_CONNRESET, struct.pack("I", 0))
            except Exception:
                pass

    def inject(self, data: bytes, target_ip: str = "127.0.0.1", target_port: int = WC3_DEFAULT_PORT) -> bool:
        """Sends packet to local WC3 or broadcast address."""
        if not self._sock:
            self._init_socket()
        try:
            self._sock.sendto(data, (target_ip, target_port))
            return True
        except Exception as e:
            logger.error(f"UDPInjector failed to send to {target_ip}:{target_port}: {e}")
            return False

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

class UDPProxy:
    """
    UDP listener and injector for Warcraft III LAN packets.
    Captures broadcasts on port 6112 and facilitates forwarding.
    """

    def __init__(
        self,
        bind_ip: str = "0.0.0.0",
        bind_port: int = WC3_DEFAULT_PORT,
        on_packet_captured: Optional[Callable[[bytes, Tuple[str, int]], None]] = None
    ):
        self.bind_ip = bind_ip
        self.bind_port = bind_port
        self.on_packet_captured = on_packet_captured

        self._socket: Optional[socket.socket] = None
        self._sender = UDPInjector()
        self._is_running = False
        self._listen_thread: Optional[threading.Thread] = None

        self.packets_captured = 0
        self.packets_injected = 0
        self.peer_targets: List[Tuple[str, int]] = []

    def _create_listen_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        if sys.platform == "win32":
            try:
                sock.ioctl(SIO_UDP_CONNRESET, struct.pack("I", 0))
            except Exception:
                pass

        sock.settimeout(1.0)
        sock.bind((self.bind_ip, self.bind_port))
        return sock

    def start(self) -> bool:
        if self._is_running:
            return True

        try:
            self._socket = self._create_listen_socket()
            self._is_running = True

            self._listen_thread = threading.Thread(
                target=self._listen_loop,
                name="UDPProxy-Listener",
                daemon=True
            )
            self._listen_thread.start()
            logger.info(f"UDPProxy listening on {self.bind_ip}:{self.bind_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start UDPProxy on {self.bind_ip}:{self.bind_port}: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        self._is_running = False
        if self._listen_thread and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=2.0)

        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self._sender.close()
        logger.info("UDPProxy stopped.")

    def add_peer(self, ip: str, port: int) -> None:
        target = (ip, port)
        if target not in self.peer_targets:
            self.peer_targets.append(target)
            logger.info(f"Added peer target: {ip}:{port}")

    def remove_peer(self, ip: str, port: int) -> None:
        target = (ip, port)
        if target in self.peer_targets:
            self.peer_targets.remove(target)
            logger.info(f"Removed peer target: {ip}:{port}")

    def inject_packet(self, data: bytes, target_ip: str = "127.0.0.1", target_port: int = WC3_DEFAULT_PORT) -> bool:
        success = self._sender.inject(data, target_ip, target_port)
        if success:
            self.packets_injected += 1
        return success

    def broadcast_locally(self, data: bytes, port: int = WC3_DEFAULT_PORT) -> bool:
        success = self.inject_packet(data, "127.0.0.1", port)
        try:
            self.inject_packet(data, "255.255.255.255", port)
        except Exception:
            pass
        return success

    def _listen_loop(self) -> None:
        while self._is_running:
            try:
                if not self._socket:
                    break
                data, addr = self._socket.recvfrom(BUFFER_SIZE)
                if not data:
                    continue

                self.packets_captured += 1

                if PacketParser.is_w3gs_packet(data):
                    header = PacketParser.parse_header(data)
                    pkt_desc = header.packet_name if header else "W3GS"
                    logger.debug(f"Captured {pkt_desc} ({len(data)} bytes) from {addr[0]}:{addr[1]}")

                    game_info = PacketParser.parse_game_info(data)
                    if game_info:
                        logger.info(
                            f"[WC3 LAN GAME DETECTED] Name: '{game_info.game_name}' | "
                            f"Port: {game_info.host_port} | Slots: {game_info.slots_open}/{game_info.slots_total}"
                        )

                    if self.on_packet_captured:
                        try:
                            self.on_packet_captured(data, addr)
                        except Exception as cb_err:
                            logger.error(f"Error in on_packet_captured callback: {cb_err}")

                    for peer_ip, peer_port in self.peer_targets:
                        try:
                            self._sender.inject(data, peer_ip, peer_port)
                        except Exception as send_err:
                            logger.warning(f"Failed to relay packet to peer {peer_ip}:{peer_port}: {send_err}")

            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if self._is_running:
                    logger.error(f"Unexpected error in UDPProxy loop: {e}")
                time.sleep(0.1)
