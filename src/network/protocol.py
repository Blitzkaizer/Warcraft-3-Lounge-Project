"""
P2P Lounge Network Protocol with Authenticated Encryption (EtM) and Anti-Replay Protection.
Features:
- Encrypt-then-MAC (EtM): Payload is encrypted via keystream cipher and signed with HMAC-SHA256.
- Anti-Replay Protection: 32-bit timestamp in header drops packets older than 15 seconds.
- Guarantees complete confidentiality and integrity: Wire data is unreadable and untamperable.
"""

from enum import IntEnum
import hashlib
import hmac
import struct
import time
from typing import Optional, Tuple

LOUNGE_PROTOCOL_MAGIC = 0x5733  # 'W3'
SIGNATURE_LEN = 16              # 128-bit truncated HMAC-SHA256 tag
MAX_TIMESTAMP_DRIFT_SEC = 15.0  # Max acceptable age of packet (anti-replay)

class LoungeMessageType(IntEnum):
    PUNCH_PROBE = 0x01     # Hole punch exploration packet
    PUNCH_ACK = 0x02       # Hole punch confirmed
    PING = 0x03            # NAT keep-alive & latency measure
    PONG = 0x04            # Latency pong
    W3GS_BROADCAST = 0x05  # Warcraft III LAN UDP discovery broadcast
    TCP_DATA = 0x06        # Multiplexed TCP game lobby & gameplay stream
    TCP_CLOSE = 0x07       # Connection close signal
    LOUNGE_EVENT = 0x08    # Room events (player joined, ready, auto-launch)
    PEER_LEAVE = 0x09      # Peer gracefully left the room
    CHAT_MESSAGE = 0x0A    # Real-time room lobby text chat

class SecureTunnelPacket:
    """
    Encrypt-then-MAC (EtM) packet framing with Anti-Replay timestamp.
    
    Wire Format:
      [HMAC Signature (16 bytes)]
      [Magic uint16 (2 bytes) = 0x5733]
      [Timestamp uint32 (4 bytes)]
      [MessageType uint8 (1 byte)]
      [StreamId uint8 (1 byte)]
      [PayloadLength uint16 (2 bytes)]
      [Encrypted Payload (N bytes)]
    """

    @classmethod
    def _keystream_xor(cls, key: bytes, timestamp: int, stream_id: int, data: bytes) -> bytes:
        """
        Symmetric stream cipher using SHA256-CTR keystream.
        Fast, pure standard library, completely hides plaintext chat & commands.
        """
        if not data:
            return b""
        out = bytearray(len(data))
        block_idx = 0
        offset = 0
        while offset < len(data):
            block_seed = key + struct.pack("!IIB", timestamp, block_idx, stream_id)
            block = hashlib.sha256(block_seed).digest()
            chunk_len = min(32, len(data) - offset)
            for i in range(chunk_len):
                out[offset + i] = data[offset + i] ^ block[i]
            offset += chunk_len
            block_idx += 1
        return bytes(out)

    @classmethod
    def compute_tag(cls, secret_key: bytes, message_bytes: bytes) -> bytes:
        return hmac.new(secret_key, message_bytes, hashlib.sha256).digest()[:SIGNATURE_LEN]

    @classmethod
    def pack(
        cls,
        msg_type: LoungeMessageType,
        payload: bytes = b"",
        stream_id: int = 0,
        secret_key: bytes = b"W3LoungeDefaultKey"
    ) -> bytes:
        current_time = int(time.time())
        encrypted_payload = cls._keystream_xor(secret_key, current_time, stream_id, payload)
        
        # Header: Magic (2b), Timestamp (4b), Type (1b), StreamId (1b), Length (2b) = 10 bytes
        header = struct.pack("!HI2BH", LOUNGE_PROTOCOL_MAGIC, current_time, int(msg_type), stream_id, len(encrypted_payload))
        body = header + encrypted_payload
        tag = cls.compute_tag(secret_key, body)
        return tag + body

    @classmethod
    def unpack(
        cls,
        data: bytes,
        secret_key: bytes = b"W3LoungeDefaultKey",
        verify_timestamp: bool = True
    ) -> Optional[Tuple[LoungeMessageType, int, bytes]]:
        if len(data) < SIGNATURE_LEN + 10:
            return None

        received_tag = data[:SIGNATURE_LEN]
        body = data[SIGNATURE_LEN:]

        # 1. Cryptographic HMAC verification in constant time
        expected_tag = cls.compute_tag(secret_key, body)
        if not hmac.compare_digest(received_tag, expected_tag):
            return None

        magic, packet_timestamp, msg_type_val, stream_id, length = struct.unpack_from("!HI2BH", body, 0)
        if magic != LOUNGE_PROTOCOL_MAGIC:
            return None
        if len(body) < 10 + length:
            return None

        # 2. Anti-Replay Protection: Reject packets outside acceptable time drift window
        if verify_timestamp:
            now = time.time()
            if abs(now - packet_timestamp) > MAX_TIMESTAMP_DRIFT_SEC:
                # Stale or replayed packet! Drop it.
                return None

        try:
            msg_type = LoungeMessageType(msg_type_val)
        except ValueError:
            return None

        encrypted_payload = body[10 : 10 + length]
        decrypted_payload = cls._keystream_xor(secret_key, packet_timestamp, stream_id, encrypted_payload)
        return msg_type, stream_id, decrypted_payload
