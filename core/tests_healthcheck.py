import importlib.util
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "healthcheck.py"
_spec = importlib.util.spec_from_file_location("healthcheck", _SCRIPT)
healthcheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(healthcheck)


class HostHeaderTests(SimpleTestCase):
    def test_uses_first_allowed_host(self):
        with mock.patch.dict("os.environ", {"DJANGO_ALLOWED_HOSTS": " s.prosim.ps ,rn.prosim.ps"}):
            self.assertEqual(healthcheck.host_header(), "s.prosim.ps")

    def test_strips_subdomain_wildcard_dot(self):
        with mock.patch.dict("os.environ", {"DJANGO_ALLOWED_HOSTS": ".prosim.ps"}):
            self.assertEqual(healthcheck.host_header(), "prosim.ps")

    def test_falls_back_to_localhost(self):
        for value in ("", "*"):
            with mock.patch.dict("os.environ", {"DJANGO_ALLOWED_HOSTS": value}):
                self.assertEqual(healthcheck.host_header(), "localhost")


class _Handler(BaseHTTPRequestHandler):
    status = 200
    seen_host = None

    def do_GET(self):  # noqa: N802
        type(self).seen_host = self.headers.get("Host")
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class CheckTests(SimpleTestCase):
    def _serve(self, status):
        _Handler.status = status
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_healthy_on_200_with_allowed_host(self):
        port = self._serve(200)
        env = {"PORT": str(port), "DJANGO_ALLOWED_HOSTS": "s.prosim.ps"}
        with mock.patch.dict("os.environ", env):
            self.assertEqual(healthcheck.check(), 0)
        self.assertEqual(_Handler.seen_host, "s.prosim.ps")

    def test_unhealthy_on_error_status(self):
        port = self._serve(400)
        with mock.patch.dict("os.environ", {"PORT": str(port)}):
            self.assertEqual(healthcheck.check(), 1)

    def test_unhealthy_when_nothing_listens(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with mock.patch.dict("os.environ", {"PORT": str(port)}):
            self.assertEqual(healthcheck.check(), 1)
