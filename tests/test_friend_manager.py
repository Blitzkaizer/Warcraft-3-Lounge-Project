import unittest
import tempfile
import os
from pathlib import Path
from src.auth.friend_manager import FriendManager, GameInvite

class TestFriendManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.fm = FriendManager("test_user")
        self.fm.config_dir = Path(self.tmp_dir.name)
        self.fm.friends_file = self.fm.config_dir / "friends.json"

    def tearDown(self):
        self.fm.stop_listening()
        self.tmp_dir.cleanup()

    def test_add_and_get_friends(self):
        self.assertEqual(self.fm.get_friends(), [])
        
        # Add friend
        success, msg = self.fm.add_friend("Arthas")
        self.assertTrue(success)
        self.assertIn("Arthas", self.fm.get_friends())

        # Cannot add duplicate
        dup_success, _ = self.fm.add_friend("arthas")
        self.assertFalse(dup_success)

        # Cannot add self
        self_success, _ = self.fm.add_friend("test_user")
        self.assertFalse(self_success)

    def test_remove_friend(self):
        self.fm.add_friend("Jaina")
        self.assertEqual(len(self.fm.get_friends()), 1)

        removed = self.fm.remove_friend("Jaina")
        self.assertTrue(removed)
        self.assertEqual(len(self.fm.get_friends()), 0)

    def test_send_and_accept_friend_request(self):
        # User 1 sends request to User 2
        user2_fm = FriendManager("arthas_peer")
        user2_fm.config_dir = Path(self.tmp_dir.name)
        user2_fm.friends_file = self.fm.config_dir / "friends.json"

        # User 1 dispatches request
        success, msg = self.fm.send_friend_request("arthas_peer")
        self.assertTrue(success)
        self.assertIn("arthas_peer", self.fm.get_friends())

        # User 2 accepts request
        user2_fm.accept_friend_request("test_user")
        self.assertIn("test_user", user2_fm.get_friends())

    def test_direct_message_handling(self):
        received = []
        self.fm.on_direct_message_received = lambda sender, text: received.append((sender, text))
        
        # Simulate incoming DM
        data = {
            "type": "DIRECT_MESSAGE",
            "from": "Thrall",
            "text": "For the Horde!",
            "ts": 1893456000.0
        }
        self.fm._process_incoming_dict(data)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], ("Thrall", "For the Horde!"))

    def test_game_invite_candidates(self):
        invite = GameInvite(
            host_username="blitz",
            recipient_username="arthas",
            room_code="W3L-TEST",
            timestamp=12345.0,
            local_port=54321,
            local_ip="192.168.1.50"
        )
        self.assertEqual(invite.local_port, 54321)
        self.assertEqual(invite.local_ip, "192.168.1.50")

    def test_nat_traversal_syn_signal(self):
        syn_events = []
        self.fm.on_nat_traversal_syn = lambda sender, pub_ip, pub_port, lan_ip, l_port, ver: syn_events.append({
            "sender": sender,
            "pub_ip": pub_ip,
            "pub_port": pub_port,
            "lan_ip": lan_ip,
            "local_port": l_port,
            "version": ver
        })

        import time
        data = {
            "type": "NAT_TRAVERSAL_SYN",
            "from": "VegetaSama",
            "client_pub_ip": "203.0.113.10",
            "client_pub_port": 58921,
            "client_lan_ip": "192.168.1.15",
            "client_local_port": 58921,
            "version": "1.0.1",
            "ts": time.time()
        }
        self.fm._process_incoming_dict(data)
        self.assertEqual(len(syn_events), 1)
        self.assertEqual(syn_events[0]["sender"], "VegetaSama")
        self.assertEqual(syn_events[0]["pub_ip"], "203.0.113.10")
        self.assertEqual(syn_events[0]["pub_port"], 58921)
        self.assertEqual(syn_events[0]["version"], "1.0.1")

if __name__ == '__main__':
    unittest.main()
