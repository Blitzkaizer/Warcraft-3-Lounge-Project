import unittest
import time
from src.network.lobby_service import LobbyService, LobbyRoom

class TestLobbyService(unittest.TestCase):
    def test_lobby_room_creation_and_expiration(self):
        svc = LobbyService(username="TestHost")
        room = svc.start_hosting_room("DotA 5v5 AP", "W3L-CODE-1234", max_players=10)
        self.assertEqual(room.title, "DotA 5v5 AP")
        self.assertEqual(room.host_username, "TestHost")
        self.assertEqual(room.current_players, 1)

        svc.update_player_count(3)
        self.assertEqual(room.current_players, 3)

        svc.stop_hosting_room()
        self.assertFalse(svc._is_announcing)

    def test_lobby_room_password_protection(self):
        svc = LobbyService(username="SecretHost")
        room = svc.start_hosting_room("Private Clan Match", "W3L-SECRET", max_players=6, password="clanwar2026")
        self.assertTrue(room.is_locked)
        self.assertTrue(len(room.password_hash) > 0)
        svc.stop_hosting_room()

if __name__ == '__main__':
    unittest.main()
