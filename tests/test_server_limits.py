"""The public listener must reject excess slow requests without blocking staff."""
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler

from server import OfficeHTTPServer


class LimitedServer(OfficeHTTPServer):
    max_active_requests = 1


class SlowHandler(BaseHTTPRequestHandler):
    entered = threading.Event()
    get_calls = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.entered.set()
        self.rfile.read(1)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        type(self).get_calls += 1
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class ServerLimitTests(unittest.TestCase):
    def test_a_slow_request_cannot_occupy_an_unbounded_number_of_workers(self):
        SlowHandler.entered.clear()
        SlowHandler.get_calls = 0
        server = LimitedServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, 3) as first:
                first.settimeout(3)
                first.sendall(b"POST / HTTP/1.0\r\nHost: localhost\r\nContent-Length: 1\r\n\r\n")
                self.assertTrue(SlowHandler.entered.wait(3))
                with socket.create_connection(server.server_address, 3) as excess:
                    excess.settimeout(3)
                    excess.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                    try:
                        reply = excess.recv(256)
                    except (ConnectionAbortedError, ConnectionResetError):
                        # Windows may reset when rejecting a socket with unread request bytes.
                        reply = b""
                    if reply:
                        self.assertIn(b"503 Service Unavailable", reply)
                    self.assertEqual(SlowHandler.get_calls, 0)
                first.sendall(b"x")
                self.assertIn(b"200 OK", first.recv(256))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
