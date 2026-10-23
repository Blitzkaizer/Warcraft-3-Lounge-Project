"""
Lounge Code encoding and decoding with 64-bit Cryptographic Entropy.
Converts Host Public IP, Port, and a 64-bit Cryptographic Room Secret
into a formatted, copy-pasteable Lounge Code (e.g. W3L-XXXX-XXXX-XXXX-XXXX).
64 bits of entropy (1.8 x 10^19 combinations) makes brute-forcing mathematically impossible.
"""

import base64
import os
import secrets
import socket
import struct
from typing import Optional, Tuple
from ..core.config import logger

LOUNGE_CODE_PREFIX = "W3L-"

class LoungeCode:
    """Handles generation and resolution of high-entropy Lounge Invite Codes."""

    @staticmethod
    def generate_secret() -> bytes:
        """Generates 8 bytes (64 bits) of cryptographically secure random bytes."""
        return secrets.token_bytes(8)

    @classmethod
    def encode(cls, ip: str, port: int, room_secret: Optional[bytes] = None) -> str:
        """
        Encodes (IP 4b, Port 2b, Secret 8b) -> 14 bytes total -> 23 Base32 chars.
        Formatted as: W3L-XXXX-XXXX-XXXX-XXXX-XXXX
        """
        secret = room_secret if (room_secret and len(room_secret) == 8) else cls.generate_secret()
        try:
            ip_packed = socket.inet_aton(ip)
            data = ip_packed + struct.pack("!H", port) + secret
            b32 = base64.b32encode(data).decode("ascii").rstrip("=")
            # Format cleanly into 4-character chunks
            chunks = [b32[i:i+4] for i in range(0, len(b32), 4)]
            return f"{LOUNGE_CODE_PREFIX}{'-'.join(chunks)}"
        except Exception as e:
            logger.error(f"Failed to encode Lounge Code: {e}")
            raise

    @staticmethod
    def decode(code: str) -> Optional[Tuple[str, int, bytes]]:
        """
        Decodes a formatted Lounge Code back into (IP, Port, 64-bit Secret).
        Returns None if code is invalid or corrupt.
        """
        clean = code.strip().upper().replace(" ", "").replace("-", "")
        if clean.startswith("W3L"):
            clean = clean[3:]

        pad_len = (8 - (len(clean) % 8)) % 8
        clean += "=" * pad_len

        try:
            raw = base64.b32decode(clean)
            if len(raw) != 14:
                return None

            ip_bytes = raw[0:4]
            port = struct.unpack("!H", raw[4:6])[0]
            room_secret = raw[6:14]
            ip = socket.inet_ntoa(ip_bytes)
            return ip, port, room_secret
        except Exception as e:
            logger.debug(f"Failed to decode Lounge Code '{code}': {e}")
            return None
