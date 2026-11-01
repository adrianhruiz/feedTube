import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from feedtube.notify import refresh_jellyfin


@pytest.fixture
def jellyfin():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        status = 204

        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self.send_response(Handler.status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/", calls, Handler
    server.shutdown()


def test_refresh_jellyfin_posts_library_refresh_with_token(jellyfin):
    url, calls, _ = jellyfin
    assert refresh_jellyfin(url, "secret") is None
    assert calls == [("/Library/Refresh", 'MediaBrowser Token="secret"')]


def test_refresh_jellyfin_reports_http_error(jellyfin):
    url, _, handler = jellyfin
    handler.status = 401
    assert "401" in refresh_jellyfin(url, "bad")


def test_refresh_jellyfin_reports_unreachable_server():
    assert refresh_jellyfin("http://127.0.0.1:9", "k") is not None
