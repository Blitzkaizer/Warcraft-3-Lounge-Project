"""
Warcraft III Lounge Companion - CLI Entry Point
Supports hosting a Lounge, joining via Lounge Code, STUN testing, and packet sniffing.
"""

import argparse
import sys
import time

from src.auth.auth_manager import AuthManager
from src.core.config import WC3_DEFAULT_PORT, logger
from src.core.packet_parser import PacketParser
from src.core.udp_proxy import UDPProxy
from src.launcher.game_launcher import GameLauncher
from src.network.lounge_session import LoungeSession
from src.network.stun import STUNClient

def run_stun_check():
    """Runs STUN diagnostics to check external IP and NAT mapping."""
    logger.info("Querying public STUN servers...")
    res = STUNClient.get_public_mapping()
    if res:
        logger.info("==========================================")
        logger.info("       STUN NAT Diagnostics Succeeded     ")
        logger.info(f"  Public IP:   {res.public_ip}")
        logger.info(f"  Public Port: {res.public_port}")
        logger.info(f"  Local Port:  {res.local_port}")
        logger.info(f"  Server Used: {res.server_used}")
        logger.info("==========================================")
    else:
        logger.error("STUN resolution failed. Check internet connection or firewall.")

def run_host_mode(player_name: str = "Player", auto_launch: bool = False):
    """Hosts a new Lounge session, displays the Lounge Code, and listens for players."""
    session = LoungeSession(is_host=True, player_name=player_name)

    def on_peer(peer, username):
        logger.info(f"*** Player '{username}' joined the Lounge from {peer[0]}:{peer[1]}! ***")

    def on_match(name, open_s, total_s):
        logger.info(f"*** Host opened match: '{name}' ({open_s}/{total_s} slots) ***")

    session.on_peer_joined = on_peer
    session.on_match_detected = on_match

    code = session.start_host()
    if not code:
        logger.error("Could not host Lounge. Terminating.")
        sys.exit(1)

    print("\n" + "="*50)
    print(f"   WARCRAFT III LOUNGE CREATED! (Host: {player_name})")
    print(f"   Share this Lounge Code with your friends:")
    print(f"   ==>  {code}  <==")
    print("="*50)
    print("   Open Warcraft III (Classic) -> Local Area Network (LAN)")
    print("   and create your game. Connected friends will see it appear!\n")

    if auto_launch:
        logger.info("Auto-launching Warcraft III...")
        session.launch_game()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nClosing Lounge...")
    finally:
        session.shutdown()

def run_join_mode(code: str, player_name: str = "Player", auto_launch: bool = False):
    """Joins an existing Lounge using a provided room code."""
    session = LoungeSession(is_host=False, player_name=player_name)

    def on_peer(peer, username):
        logger.info(f"*** Connected to Lounge Host '{username}' at {peer[0]}:{peer[1]}! ***")
        if auto_launch:
            logger.info("Auto-launching Warcraft III into LAN screen...")
            session.launch_game()

    def on_match(name, open_s, total_s):
        logger.info(f"*** Discovered Hosted Match: '{name}' ({open_s}/{total_s} slots) ***")
        logger.info("Match is now visible in Warcraft III -> LAN menu!")

    session.on_peer_joined = on_peer
    session.on_match_detected = on_match

    if not session.join_lounge(code):
        logger.error("Failed to join Lounge.")
        sys.exit(1)

    print("\n" + "="*50)
    print(f"   CONNECTING TO LOUNGE: {code} as '{player_name}'")
    print("="*50)
    print("   Open Warcraft III (Classic) -> Local Area Network (LAN)")
    print("   Once synced, the hosted game will appear in your LAN lobby!\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nLeaving Lounge...")
    finally:
        session.shutdown()

def main():
    parser = argparse.ArgumentParser(description="Warcraft III Lounge Companion")
    parser.add_argument(
        "--mode",
        choices=["gui", "host", "join", "stun", "launch", "listen", "test"],
        default="gui",
        help="Mode: 'gui' (desktop UI), 'host' (create Lounge), 'join' (join room), 'launch' (start war3.exe), 'stun' (check NAT), 'test' (unit tests)"
    )
    parser.add_argument("--code", type=str, help="Lounge Code to join (required for --mode join)")
    parser.add_argument("--username", type=str, help="Custom in-app username (default: saved session or Player)")
    parser.add_argument("--auto-launch", action="store_true", help="Auto-launch Warcraft III once connected")
    parser.add_argument("--port", type=int, default=WC3_DEFAULT_PORT, help="Port to bind (default: 6112)")
    parser.add_argument("--profile", type=str, help="Isolated profile name for multi-instance testing (e.g. 'arthas')")

    # If no arguments provided in CLI, default to GUI
    if len(sys.argv) == 1:
        from src.ui.app import launch_gui
        launch_gui()
        return

    args = parser.parse_args()

    if args.profile:
        import os
        os.environ["W3L_PROFILE"] = args.profile.strip()

    if args.mode == "gui":
        from src.ui.app import launch_gui
        launch_gui()
        return

    auth = AuthManager()
    player_name = args.username or auth.current_user.username

    if args.mode == "host":
        run_host_mode(player_name=player_name, auto_launch=args.auto_launch)
    elif args.mode == "join":
        if not args.code:
            logger.error("--code is required when running in join mode (e.g. --code W3L-XXXX-XXXX-XXXX)")
            sys.exit(1)
        run_join_mode(args.code, player_name=player_name, auto_launch=args.auto_launch)
    elif args.mode == "launch":
        launcher = GameLauncher()
        if launcher.exe_path:
            logger.info(f"Detected Classic Warcraft III: {launcher.exe_path}")
            launcher.launch()
        else:
            logger.error("Could not find war3.exe. Check registry or installation folder.")
    elif args.mode == "stun":
        run_stun_check()
    elif args.mode == "listen":
        p = UDPProxy(bind_port=args.port)
        p.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            p.stop()
    elif args.mode == "test":
        import unittest
        suite = unittest.defaultTestLoader.discover("tests")
        unittest.TextTestRunner(verbosity=2).run(suite)

if __name__ == "__main__":
    main()
