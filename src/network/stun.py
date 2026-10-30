"""
STUN (RFC 5389 / RFC 3489) Client for discovering public IP and NAT mapped port.
Pure Python standard library implementation - no external dependencies.
"""

from dataclasses import dataclass
import os
import socket
import struct
from typing import List, Optional, Tuple
from ..core.config import logger

# Public reliable STUN servers
DEFAULT_STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
]

STUN_MAGIC_COOKIE = 0x2112A442

@dataclass
class STUNResult:
    public_ip: str
    public_port: int
    local_port: int
    server_used: str
    local_ip: str = "127.0.0.1"

class STUNClient:
    """Discovers external public IP and mapped NAT port using STUN."""

    @staticmethod
    def get_lan_ip() -> str:
        """Determines the local network (LAN) IP of this machine."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def get_public_mapping(
        sock: Optional[socket.socket] = None,
        servers: Optional[List[Tuple[str, int]]] = None,
        timeout: float = 3.0
    ) -> Optional[STUNResult]:
        """
        Queries STUN server(s) to discover external IP and port.
        If `sock` is provided, discovers the external mapping for that specific socket.
        """
        servers = servers or DEFAULT_STUN_SERVERS
        owns_socket = sock is None

        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", 0))

        old_timeout = sock.gettimeout()
        sock.settimeout(timeout)
        local_port = sock.getsockname()[1]

        try:
            for host, port in servers:
                try:
                    # Construct STUN Binding Request (RFC 5389)
                    trans_id = os.urandom(12)
                    req = struct.pack("!HHI12s", 0x0001, 0, STUN_MAGIC_COOKIE, trans_id)
                    sock.sendto(req, (host, port))

                    data, _ = sock.recvfrom(2048)
                    if len(data) < 20:
                        continue

                    msg_type, msg_len, magic = struct.unpack_from("!HHI", data, 0)
                    # 0x0101 is Binding Success Response
                    if msg_type != 0x0101:
                        continue

                    offset = 20
                    endpoint: Optional[Tuple[str, int]] = None

                    # Parse STUN attributes
                    while offset + 4 <= len(data):
                        attr_type, attr_len = struct.unpack_from("!HH", data, offset)
                        offset += 4

                        # XOR-MAPPED-ADDRESS (RFC 5389: 0x0020)
                        if attr_type == 0x0020 and attr_len >= 8:
                            _, family, x_port = struct.unpack_from("!BBH", data, offset)
                            if family == 0x01:  # IPv4
                                x_ip = struct.unpack_from("!I", data, offset + 4)[0]
                                ext_port = x_port ^ (STUN_MAGIC_COOKIE >> 16)
                                ext_ip = socket.inet_ntoa(struct.pack("!I", x_ip ^ STUN_MAGIC_COOKIE))
                                endpoint = (ext_ip, ext_port)
                                break

                        # Fallback: MAPPED-ADDRESS (RFC 3489: 0x0001)
                        elif attr_type == 0x0001 and attr_len >= 8:
                            _, family, ext_port = struct.unpack_from("!BBH", data, offset)
                            if family == 0x01:
                                ext_ip = socket.inet_ntoa(data[offset + 4 : offset + 8])
                                endpoint = (ext_ip, ext_port)
                                break

                        # Attribute lengths in STUN are padded to multiples of 4
                        offset += (attr_len + 3) & ~3

                    if endpoint:
                        pub_ip, pub_port = endpoint
                        lan_ip = cls.get_lan_ip() if hasattr(cls, "get_lan_ip") else STUNClient.get_lan_ip()
                        logger.info(f"STUN discovery via {host}:{port} -> Public: {pub_ip}:{pub_port} (LAN: {lan_ip}, Local: {local_port})")
                        return STUNResult(
                            public_ip=pub_ip,
                            public_port=pub_port,
                            local_port=local_port,
                            server_used=f"{host}:{port}",
                            local_ip=lan_ip
                        )

                except (socket.timeout, OSError) as e:
                    logger.debug(f"STUN query failed for {host}:{port}: {e}")
                    continue

        finally:
            if owns_socket:
                try:
                    sock.close()
                except Exception:
                    pass
            else:
                sock.settimeout(old_timeout)

        logger.error("STUN resolution failed across all configured servers.")
        return None
