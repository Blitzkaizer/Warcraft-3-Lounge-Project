"""
Single-Instance Enforcement & Inter-Process Activation for Warcraft III Lounge.
Prevents duplicate application windows from opening. If a user double-clicks the
app while it is already running (e.g. minimized in tray), signals the active instance
to restore to the foreground and terminates the duplicate immediately.
"""

import os
import socket
import sys
import threading
from typing import Callable, Optional
from .config import logger

DEFAULT_INSTANCE_PORT = 49612

class SingleInstanceController:
    """Enforces a single running instance per profile."""

    def __init__(self, port: int = DEFAULT_INSTANCE_PORT):
        profile = os.environ.get("W3L_PROFILE", "").strip()
        # If user is testing with custom profile names, calculate unique port offset
        if profile:
            offset = sum(ord(c) for c in profile) % 500
            self.port = port + 1 + offset
        else:
            self.port = port

        self.server_sock: Optional[socket.socket] = None
        self._is_running = False

    def is_primary_instance(self, on_restore_request: Callable[[], None]) -> bool:
        """
        Returns True if this is the only active instance.
        If an active instance already exists, notifies it to restore and returns False.
        """
        # 1. Probe for existing running instance
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(1.2)
            probe.connect(("127.0.0.1", self.port))
            try:
                probe.sendall(b"RESTORE\n")
            except Exception:
                pass
            probe.close()
            logger.info(f"Duplicate instance detected on port {self.port}. Signaled primary to restore window.")
            return False
        except (ConnectionRefusedError, OSError, socket.timeout):
            pass

        # 2. Become primary instance and listen for secondary activation signals
        try:
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(("127.0.0.1", self.port))
            self.server_sock.listen(5)
            self.server_sock.settimeout(1.0)
            self._is_running = True

            def listen_loop():
                while self._is_running:
                    try:
                        if not self.server_sock:
                            break
                        client, _ = self.server_sock.accept()
                        try:
                            data = client.recv(64)
                        except Exception:
                            data = b""
                        finally:
                            try:
                                client.close()
                            except Exception:
                                pass

                        if b"RESTORE" in data:
                            logger.info("Received RESTORE signal from secondary instance launch.")
                            on_restore_request()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    except Exception:
                        pass

            t = threading.Thread(target=listen_loop, daemon=True, name="SingleInstanceListener")
            t.start()
            return True
        except Exception as e:
            logger.debug(f"SingleInstance listener setup note: {e}")
            return True

    def close(self):
        """Stops listener and frees the port."""
        self._is_running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
