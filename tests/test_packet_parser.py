"""
Unit tests for Warcraft III W3GS packet parsing and serialization.
"""

import unittest
from src.core.packet_parser import PacketParser, W3GS_MAGIC_BYTE, W3GSPacketId

class TestPacketParser(unittest.TestCase):
    def test_synthetic_packet_validation(self):
        pkt_bytes = PacketParser.create_synthetic_w3gs_packet("Test Lobby", port=6112)
        self.assertTrue(PacketParser.is_w3gs_packet(pkt_bytes))

        header = PacketParser.parse_header(pkt_bytes)
        self.assertIsNotNone(header)
        self.assertEqual(header.magic, W3GS_MAGIC_BYTE)
        self.assertEqual(header.packet_id, 0x30)
        self.assertEqual(header.length, len(pkt_bytes))

    def test_game_info_extraction(self):
        pkt_bytes = PacketParser.create_synthetic_w3gs_packet("Epic DotA 5v5", port=6112)
        game_info = PacketParser.parse_game_info(pkt_bytes)
        self.assertIsNotNone(game_info)
        self.assertEqual(game_info.game_name, "Epic DotA 5v5")
        self.assertEqual(game_info.host_port, 6112)
        self.assertEqual(game_info.slots_total, 12)
        self.assertEqual(game_info.slots_open, 11)

    def test_invalid_packet(self):
        self.assertFalse(PacketParser.is_w3gs_packet(b""))
        self.assertFalse(PacketParser.is_w3gs_packet(b"\x00\x01\x02\x03"))
        self.assertIsNone(PacketParser.parse_header(b"hello world"))

if __name__ == "__main__":
    unittest.main()
