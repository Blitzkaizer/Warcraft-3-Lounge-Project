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

if __name__ == '__main__':
    unittest.main()
