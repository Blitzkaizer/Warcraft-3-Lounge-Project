"""
TCP-over-UDP Stream Multiplexer for Warcraft III.
Enables Warcraft III TCP lobby and gameplay sync to travel through the
UDP hole-punched P2P socket without requiring manual TCP port forwarding.
"""

import socket
import threading
import time
from typing import Callable, Dict, Optional, Tuple
from .config import BUFFER_SIZE, WC3_DEFAULT_PORT, logger

class ClientTCPBridge:
    """
    Runs on Client. Listens on 127.0.0.1:6112 (TCP).
    When client WC3 connects, reads TCP stream and forwards chunks via UDP callback.
    Receives incoming UDP chunks and pumps them into client WC3.
    """

    def __init__(
        self,
        local_bind_port: int = WC3_DEFAULT_PORT,
        on_send_udp_chunk: Optional[Callable[[int, bytes], None]] = None,
        on_stream_close: Optional[Callable[[int], None]] = None
    ):
        self.local_bind_port = local_bind_port
        self.on_send_udp_chunk = on_send_udp_chunk
        self.on_stream_close = on_stream_close

        self._server_sock: Optional[socket.socket] = None
        self._is_running = False
        self._accept_thread: Optional[threading.Thread] = None

        self.active_streams: Dict[int, socket.socket] = {}
        self._next_stream_id = 1
        self.last_error: Optional[str] = None

    @staticmethod
    def is_port_in_use(port: int = WC3_DEFAULT_PORT) -> bool:
        """Checks if local TCP port is already bound by Warcraft III or another program."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Do not use SO_REUSEADDR for testing exclusive availability
            s.bind(("127.0.0.1", port))
            s.close()
            return False
        except OSError:
            return True

    def start(self) -> bool:
        if self._is_running:
            return True

        if self.is_port_in_use(self.local_bind_port):
            self.last_error = f"Port {self.local_bind_port} is already in use by Warcraft III or another program."
            logger.error(self.last_error)
            return False

        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(("127.0.0.1", self.local_bind_port))
            self._server_sock.listen(5)
            self._server_sock.settimeout(1.0)

            self._is_running = True
            self._accept_thread = threading.Thread(
                target=self._accept_loop,
                name="ClientTCPBridge-Listener",
                daemon=True
            )
            self._accept_thread.start()
            logger.info(f"Client TCP-over-UDP Bridge listening on 127.0.0.1:{self.local_bind_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start Client TCP Bridge on port {self.local_bind_port}: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        self._is_running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        for stream_id, sock in list(self.active_streams.items()):
            try:
                sock.close()
            except Exception:
                pass
        self.active_streams.clear()

    def receive_udp_chunk(self, stream_id: int, data: bytes) -> None:
        """Pumps data received from UDP tunnel into local WC3 socket."""
        sock = self.active_streams.get(stream_id)
        if sock:
            try:
                sock.sendall(data)
            except Exception as e:
                logger.debug(f"Failed to write to local WC3 stream {stream_id}: {e}")
                self.close_stream(stream_id)

    def close_stream(self, stream_id: int) -> None:
        sock = self.active_streams.pop(stream_id, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        if self.on_stream_close:
            self.on_stream_close(stream_id)

    def _accept_loop(self) -> None:
        while self._is_running:
            try:
                if not self._server_sock:
                    break
                client_sock, client_addr = self._server_sock.accept()
                stream_id = self._next_stream_id
                self._next_stream_id += 1

                self.active_streams[stream_id] = client_sock
                logger.info(f"Local WC3 connected to bridge (Stream {stream_id}) from {client_addr}")

                # Start reader thread to pump WC3 bytes out to UDP tunnel
                t = threading.Thread(
                    target=self._read_client_loop,
                    args=(stream_id, client_sock),
                    daemon=True
                )
                t.start()

            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if self._is_running:
                    logger.debug(f"Error in ClientTCPBridge accept: {e}")
                time.sleep(0.1)

    def _read_client_loop(self, stream_id: int, sock: socket.socket) -> None:
        sock.settimeout(1.0)
        try:
            while self._is_running and stream_id in self.active_streams:
                try:
                    data = sock.recv(1400)
                    if not data:
                        break
                    if self.on_send_udp_chunk:
                        self.on_send_udp_chunk(stream_id, data)
                except socket.timeout:
                    continue
        except Exception:
            pass
        finally:
            self.close_stream(stream_id)


class HostTCPBridge:
    """
    Runs on Host. When receiving TCP_DATA via UDP from a client,
    maintains a local TCP connection to Host's WC3 on 127.0.0.1:6112.
    Pumps Host WC3 response bytes back to client over UDP tunnel.
    """

    def __init__(
        self,
        target_port: int = WC3_DEFAULT_PORT,
        on_send_udp_chunk: Optional[Callable[[Tuple[str, int], int, bytes], None]] = None,
        on_stream_close: Optional[Callable[[Tuple[str, int], int], None]] = None
    ):
        self.target_port = target_port
        self.on_send_udp_chunk = on_send_udp_chunk
        self.on_stream_close = on_stream_close

        self._is_running = False
        # Key: (peer_addr, stream_id) -> socket connected to local Host WC3
        self.active_streams: Dict[Tuple[Tuple[str, int], int], socket.socket] = {}

    def start(self):
        self._is_running = True

    def stop(self):
        self._is_running = False
        for key, sock in list(self.active_streams.items()):
            try:
                sock.close()
            except Exception:
                pass
        self.active_streams.clear()

    def handle_incoming_chunk(self, peer: Tuple[str, int], stream_id: int, data: bytes):
        key = (peer, stream_id)
        sock = self.active_streams.get(key)

        if not sock:
            # Enforce max 4 streams per peer to prevent socket exhaustion
            peer_stream_count = sum(1 for p, _ in self.active_streams.keys() if p == peer)
            if peer_stream_count >= 4:
                logger.warning(f"Peer {peer} exceeded max concurrent streams (4). Dropping stream {stream_id}")
                return

            # Connect to local Host WC3
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", self.target_port))
                sock.settimeout(1.0)
                self.active_streams[key] = sock
                logger.info(f"Host bridge connected to local WC3 on port {self.target_port} for peer {peer} (Stream {stream_id})")

                # Spawn reader thread to forward WC3 responses back to peer over UDP
                t = threading.Thread(
                    target=self._read_host_loop,
                    args=(peer, stream_id, sock),
                    daemon=True
                )
                t.start()
            except Exception as e:
                logger.error(f"Host bridge could not connect to local WC3 on 127.0.0.1:{self.target_port}: {e}")
                return

        try:
            sock.sendall(data)
        except Exception as e:
            logger.debug(f"Failed to forward data to Host WC3: {e}")
            self.close_stream(peer, stream_id)

    def close_stream(self, peer: Tuple[str, int], stream_id: int):
        key = (peer, stream_id)
        sock = self.active_streams.pop(key, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        if self.on_stream_close:
            self.on_stream_close(peer, stream_id)

    def _read_host_loop(self, peer: Tuple[str, int], stream_id: int, sock: socket.socket):
        key = (peer, stream_id)
        try:
            while self._is_running and key in self.active_streams:
                try:
                    data = sock.recv(1400)
                    if not data:
                        break
                    if self.on_send_udp_chunk:
                        self.on_send_udp_chunk(peer, stream_id, data)
                except socket.timeout:
                    continue
        except Exception:
            pass
        finally:
            self.close_stream(peer, stream_id)
