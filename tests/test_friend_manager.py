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

    def test_profile_isolation(self):
        os.environ["W3L_PROFILE"] = "arthas_test"
        fm_isolated = FriendManager("arthas_test")
        self.assertTrue("profile_arthas_test" in str(fm_isolated.config_dir))
        del os.environ["W3L_PROFILE"]

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

if __name__ == '__main__':
    unittest.main()
