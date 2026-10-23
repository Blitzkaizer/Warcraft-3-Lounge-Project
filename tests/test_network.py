"""
Unit tests for Phase 2 & Security Layer 1: LoungeCode, STUN framing, and SecureTunnelPacket.
"""

import struct
import unittest
from src.network.lounge_code import LoungeCode
from src.network.protocol import LoungeMessageType, SecureTunnelPacket

class TestNetworkModules(unittest.TestCase):
    def test_lounge_code_roundtrip(self):
        test_ip = "198.51.100.42"
        test_port = 6113
        test_secret = b"12345678"

        code = LoungeCode.encode(test_ip, test_port, test_secret)
        self.assertTrue(code.startswith("W3L-"))

        decoded = LoungeCode.decode(code)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded[0], test_ip)
        self.assertEqual(decoded[1], test_port)
        self.assertEqual(decoded[2], test_secret)

    def test_lounge_code_invalid(self):
        self.assertIsNone(LoungeCode.decode("INVALID-CODE-XYZ"))
        self.assertIsNone(LoungeCode.decode(""))

    def test_secure_tunnel_packet_signature_valid(self):
        payload = b"Hello Warcraft III Peers"
        stream_id = 7
        key = b"SuperSecretRoomKey123"

        packed = SecureTunnelPacket.pack(
            LoungeMessageType.W3GS_BROADCAST,
            payload,
            stream_id=stream_id,
            secret_key=key
        )

        unpacked = SecureTunnelPacket.unpack(packed, secret_key=key)
        self.assertIsNotNone(unpacked)
        msg_type, unpacked_stream_id, unpacked_payload = unpacked
        self.assertEqual(msg_type, LoungeMessageType.W3GS_BROADCAST)
        self.assertEqual(unpacked_stream_id, stream_id)
        self.assertEqual(unpacked_payload, payload)

    def test_secure_tunnel_packet_signature_tampered_drops(self):
        payload = b"Authorized Data"
        key_valid = b"KeyValid"
        key_attacker = b"KeyAttacker"

        packed = SecureTunnelPacket.pack(
            LoungeMessageType.W3GS_BROADCAST,
            payload,
            secret_key=key_valid
        )

        # Attacker tries to unpack with wrong key -> must drop (return None)
        unpacked_wrong_key = SecureTunnelPacket.unpack(packed, secret_key=key_attacker)
        self.assertIsNone(unpacked_wrong_key)

        # Attacker tampers with 1 byte of the packet -> must drop (return None)
        tampered_bytes = bytearray(packed)
        tampered_bytes[25] ^= 0xFF
        unpacked_tampered = SecureTunnelPacket.unpack(bytes(tampered_bytes), secret_key=key_valid)
        self.assertIsNone(unpacked_tampered)

    def test_secure_tunnel_packet_payload_is_encrypted_on_wire(self):
        payload = b"Secret In-Game Chat Message"
        key = b"EncryptionKey456"

        packed = SecureTunnelPacket.pack(
            LoungeMessageType.TCP_DATA,
            payload,
            secret_key=key
        )

        # Verify that the plaintext payload does NOT appear in raw wire bytes (true confidentiality)
        self.assertNotIn(payload, packed)

    def test_anti_replay_protection(self):
        import time
        payload = b"Replay Me"
        key = b"Key123"

        packed = SecureTunnelPacket.pack(LoungeMessageType.W3GS_BROADCAST, payload, secret_key=key)

        # Unpack immediately succeeds
        self.assertIsNotNone(SecureTunnelPacket.unpack(packed, secret_key=key))

        # Tampered stale timestamp (30 seconds in the past) -> must be rejected by anti-replay
        stale_time = int(time.time() - 40)
        stale_header = struct.pack("!HI2BH", 0x5733, stale_time, 0x05, 0, len(payload))
        stale_body = stale_header + payload
        stale_tag = SecureTunnelPacket.compute_tag(key, stale_body)
        stale_packet = stale_tag + stale_body

        unpacked_stale = SecureTunnelPacket.unpack(stale_packet, secret_key=key, verify_timestamp=True)
        self.assertIsNone(unpacked_stale)

    def test_nat_puncher_rate_limiter(self):
        from src.network.nat_puncher import NATPuncher, MAX_PACKETS_PER_SEC_PER_IP
        puncher = NATPuncher()
        test_ip = "203.0.113.50"

        # Under the threshold, packets are accepted
        for _ in range(MAX_PACKETS_PER_SEC_PER_IP):
            self.assertFalse(puncher._is_rate_limited(test_ip))

        # Once threshold is reached, excess packets are blocked
        self.assertTrue(puncher._is_rate_limited(test_ip))
        self.assertTrue(puncher._is_rate_limited(test_ip))
        self.assertEqual(puncher.rate_limited_dropped, 2)

    def test_ping_pong_rtt_payload(self):
        import time
        now = time.time()
        key = b"KeyPing"
        ping_payload = struct.pack("!d", now)

        # Pack PING
        ping_pkt = SecureTunnelPacket.pack(LoungeMessageType.PING, payload=ping_payload, secret_key=key)
        unpacked_ping = SecureTunnelPacket.unpack(ping_pkt, secret_key=key)
        self.assertIsNotNone(unpacked_ping)
        msg_type, _, rx_payload = unpacked_ping
        self.assertEqual(msg_type, LoungeMessageType.PING)
        self.assertEqual(rx_payload, ping_payload)

        # Pack PONG mirroring payload
        pong_pkt = SecureTunnelPacket.pack(LoungeMessageType.PONG, payload=rx_payload, secret_key=key)
        unpacked_pong = SecureTunnelPacket.unpack(pong_pkt, secret_key=key)
        self.assertIsNotNone(unpacked_pong)
        pong_type, _, pong_payload = unpacked_pong
        self.assertEqual(pong_type, LoungeMessageType.PONG)

        # Unpack sent timestamp and calculate RTT
        sent_time = struct.unpack("!d", pong_payload[:8])[0]
        self.assertAlmostEqual(sent_time, now, places=3)

    def test_peer_leave_packet(self):
        key = b"KeyLeave"
        user_bytes = b"Arthas"
        leave_pkt = SecureTunnelPacket.pack(LoungeMessageType.PEER_LEAVE, payload=user_bytes, secret_key=key)
        unpacked = SecureTunnelPacket.unpack(leave_pkt, secret_key=key)
        self.assertIsNotNone(unpacked)
        msg_type, _, rx_payload = unpacked
        self.assertEqual(msg_type, LoungeMessageType.PEER_LEAVE)
        self.assertEqual(rx_payload.decode("utf-8"), "Arthas")

if __name__ == "__main__":
    unittest.main()
