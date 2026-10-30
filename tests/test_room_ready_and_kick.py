import pytest
from unittest.mock import MagicMock
from src.network.lounge_session import LoungeSession

class TestRoomReadyAndKick:
    def test_ready_event_dispatch(self):
        session = LoungeSession(is_host=True, player_name="HostPlayer")
        mock_ready_cb = MagicMock()
        session.on_player_ready_changed = mock_ready_cb

        # Handle incoming ready signal
        session._handle_event("CMD:READY:1:ClientBob")
        mock_ready_cb.assert_called_once_with("ClientBob", True)

        mock_ready_cb.reset_mock()
        session._handle_event("CMD:READY:0:ClientBob")
        mock_ready_cb.assert_called_once_with("ClientBob", False)

    def test_kick_event_dispatch_to_target(self):
        session = LoungeSession(is_host=False, player_name="TargetBob")
        mock_kicked_cb = MagicMock()
        mock_other_kicked_cb = MagicMock()
        session.on_kicked_by_host = mock_kicked_cb
        session.on_player_kicked = mock_other_kicked_cb

        session._handle_event("CMD:KICK:TargetBob")
        mock_kicked_cb.assert_called_once()
        mock_other_kicked_cb.assert_not_called()

    def test_kick_event_dispatch_to_other_peers(self):
        session = LoungeSession(is_host=False, player_name="ObserverAlice")
        mock_kicked_cb = MagicMock()
        mock_other_kicked_cb = MagicMock()
        session.on_kicked_by_host = mock_kicked_cb
        session.on_player_kicked = mock_other_kicked_cb

        session._handle_event("CMD:KICK:TargetBob")
        mock_kicked_cb.assert_not_called()
        mock_other_kicked_cb.assert_called_once_with("TargetBob")

    def test_session_set_ready_broadcasts_command(self):
        session = LoungeSession(is_host=False, player_name="ClientBob")
        session.puncher = MagicMock()

        session.set_ready(True)
        session.puncher.send_event_to_peers.assert_called_with("CMD:READY:1:ClientBob")

        session.set_ready(False)
        session.puncher.send_event_to_peers.assert_called_with("CMD:READY:0:ClientBob")

    def test_session_kick_player_broadcasts_and_disconnects_peer(self):
        session = LoungeSession(is_host=True, player_name="HostPlayer")
        session.puncher = MagicMock()
        peer_addr = ("192.168.1.100", 55555)
        session.connected_peers[peer_addr] = "BadActor"
        session.puncher.connected_peers = {peer_addr: 1234567.8}
        session.puncher.peer_names = {peer_addr: "BadActor"}

        session.kick_player("BadActor")

        session.puncher.send_event_to_peers.assert_called_once_with("CMD:KICK:BadActor")
        assert peer_addr not in session.connected_peers
        assert peer_addr not in session.puncher.connected_peers
        assert peer_addr not in session.puncher.peer_names
