"""
Warcraft III W3GS / LAN Discovery packet parser and serializer.
Interprets header format (0xF7) and extracts game announcement details.
"""

from dataclasses import dataclass
import struct
from typing import Optional, Tuple
from .config import W3GS_MAGIC_BYTE, W3GSPacketId, logger

@dataclass
class W3GSPacket:
    magic: int
    packet_id: int
    length: int
    payload: bytes

    @property
    def packet_name(self) -> str:
        try:
            return W3GSPacketId(self.packet_id).name
        except ValueError:
            return f"CUSTOM_0x{self.packet_id:02X}"

@dataclass
class GameInfo:
    game_name: str
    map_name: str
    host_port: int
    slots_total: int
    slots_open: int
    product: str
    raw_packet: bytes

class PacketParser:
    """Parses and validates Warcraft 3 LAN UDP packets."""

    @staticmethod
    def is_w3gs_packet(data: bytes) -> bool:
        """Check if packet matches WC3 magic header (0xF7) and has valid length."""
        if len(data) < 4:
            return False
        if data[0] != W3GS_MAGIC_BYTE:
            return False
        pkt_len = struct.unpack_from("<H", data, 2)[0]
        return pkt_len == len(data)

    @classmethod
    def parse_header(cls, data: bytes) -> Optional[W3GSPacket]:
        """Extract header fields from raw UDP packet."""
        if not cls.is_w3gs_packet(data):
            return None
        magic, packet_id, length = struct.unpack_from("<BBH", data, 0)
        payload = data[4:length]
        return W3GSPacket(
            magic=magic,
            packet_id=packet_id,
            length=length,
            payload=payload
        )

    @classmethod
    def parse_game_info(cls, data: bytes) -> Optional[GameInfo]:
        """
        Attempts to parse W3GS GAME_INFO / LAN announcement packet.
        Commonly sent when a match is created or refreshed in LAN lobby.
        """
        pkt = cls.parse_header(data)
        if not pkt:
            return None

        # LAN announcements typically use packet IDs 0x2F, 0x30, 0x31, 0x32
        if pkt.packet_id not in (0x2F, 0x30, 0x31, 0x32):
            return None

        try:
            payload = pkt.payload
            offset = 0
            # Product ID (4 bytes)
            if len(payload) < 16:
                return None

            product = payload[offset:offset+4].decode("ascii", errors="replace")
            offset += 4

            # Version (uint32), Host Counter (uint32), Entry Key (uint32)
            _version, _host_counter, _entry_key = struct.unpack_from("<III", payload, offset)
            offset += 12

            # Game Name (null-terminated string)
            null_idx = payload.find(b"\x00", offset)
            if null_idx == -1:
                return None
            game_name = payload[offset:null_idx].decode("utf-8", errors="replace")[:128]
            offset = null_idx + 1

            # Null byte delimiter
            offset += 1

            # Encoded stat string (contains map name, host name, map hash, etc.)
            null_idx = payload.find(b"\x00", offset)
            stat_string = ""
            if null_idx != -1:
                # Stat string can be parsed/decoded, but let's extract readable text
                raw_stat = payload[offset:null_idx]
                offset = null_idx + 1
                try:
                    stat_string = raw_stat.decode("latin-1", errors="ignore")[:512]
                except Exception:
                    stat_string = ""

            # Slots total, game type, etc.
            slots_total = 0
            slots_open = 0
            host_port = 6112

            if len(payload) >= offset + 16:
                slots_total = struct.unpack_from("<I", payload, offset)[0]
                offset += 4
                _game_type = struct.unpack_from("<I", payload, offset)[0]
                offset += 4
                _param = struct.unpack_from("<I", payload, offset)[0]
                offset += 4
                slots_open = struct.unpack_from("<I", payload, offset)[0]
                offset += 4
                _up_time = struct.unpack_from("<I", payload, offset)[0]
                offset += 4
                if len(payload) >= offset + 2:
                    host_port = struct.unpack_from("<H", payload, offset)[0]

            return GameInfo(
                game_name=game_name,
                map_name=stat_string,
                host_port=host_port,
                slots_total=slots_total,
                slots_open=slots_open,
                product=product,
                raw_packet=data
            )
        except Exception as e:
            logger.debug(f"Could not parse full GameInfo: {e}")
            return None

    @classmethod
    def rewrite_host_port(cls, data: bytes, new_port: int) -> bytes:
        """
        Rewrites the embedded host TCP port in a game announcement packet
        so client WC3 connects to the local proxy port.
        """
        pkt = cls.parse_header(data)
        if not pkt or len(data) < 20:
            return data

        # The host port is commonly the last 2 bytes (or before optional game flags)
        mutable = bytearray(data)
        try:
            pkt_len = struct.unpack_from("<H", data, 2)[0]
            if pkt_len >= 6:
                struct.pack_into("<H", mutable, pkt_len - 2, new_port)
                return bytes(mutable)
        except Exception:
            pass
        return data

    @staticmethod
    def create_synthetic_w3gs_packet(game_name: str = "Lounge Test Match", port: int = 6112) -> bytes:
        """
        Constructs a synthetic W3GS 0x30 game announcement packet for testing.
        """
        game_bytes = game_name.encode("utf-8") + b"\x00"
        stat_bytes = b"Maps\\FrozenThrone\\(4)TurtleRock.w3x\x00"
        pre = struct.pack("<4sIII", b"WAR3", 26, 1, 0)
        post = struct.pack("<IIIIIH", 12, 1, 0, 11, 100, port)
        payload = pre + game_bytes + b"\x00" + stat_bytes + post
        total_len = 4 + len(payload)
        header = struct.pack("<BBH", W3GS_MAGIC_BYTE, 0x30, total_len)
        return header + payload
