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

    def test_start_glow_box_lifecycle(self):
        import tkinter as tk
        from src.ui.lounge_frame import StartGlowBox

        root = tk.Tk()
        root.withdraw()
        box = StartGlowBox(root)
        assert not box.is_glowing

        box.start_glow()
        assert box.is_glowing
        assert len(box.sparkles) > 0

        box.stop_glow()
        assert not box.is_glowing
        root.destroy()

    def test_in_game_event_dispatch(self):
        session = LoungeSession(is_host=True, player_name="HostPlayer")
        mock_in_game_cb = MagicMock()
        session.on_player_in_game_changed = mock_in_game_cb

        # Handle incoming in_game signal 1 and 0
        session._handle_event("CMD:IN_GAME:1:ClientBob")
        mock_in_game_cb.assert_called_once_with("ClientBob", True)

        mock_in_game_cb.reset_mock()
        session._handle_event("CMD:IN_GAME:0:ClientBob")
        mock_in_game_cb.assert_called_once_with("ClientBob", False)

    def test_host_closed_game_event_dispatch(self):
        session = LoungeSession(is_host=False, player_name="ClientBob")
        session.launcher = MagicMock()
        session.launcher.is_running.return_value = False
        session.puncher = MagicMock()
        mock_host_closed_cb = MagicMock()
        mock_in_game_cb = MagicMock()
        session.on_host_closed_game = mock_host_closed_cb
        session.on_player_in_game_changed = mock_in_game_cb

        session._handle_event("CMD:HOST_CLOSED_GAME")

        session.launcher.terminate_game.assert_called_once()
        session.puncher.send_event_to_peers.assert_called_with("CMD:IN_GAME:0:ClientBob")
        mock_host_closed_cb.assert_called_once()
        mock_in_game_cb.assert_called_once_with("ClientBob", False)

    def test_smart_map_launch_flow(self):
        session = LoungeSession(is_host=False, player_name="ClientBob")
        session.launch_game = MagicMock()
        session._notify_status = MagicMock()

        # 1. Host is preparing map -> Client does NOT launch yet
        session._handle_event("CMD:HOST_PREPARING_MAP")
        session.launch_game.assert_not_called()

        # 2. Match is created by Host -> Client auto-launches!
        session._handle_event("CMD:MATCH_CREATED:DotA 5v5")
        session.launch_game.assert_called_once()

    def test_host_notify_match_created(self):
        host_session = LoungeSession(is_host=True, player_name="HostPlayer")
        host_session.puncher = MagicMock()

        host_session.notify_match_created("Footmen Frenzy")
        host_session.puncher.send_event_to_peers.assert_called_once_with("CMD:MATCH_CREATED:Footmen Frenzy")
