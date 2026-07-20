"""Shared pytest fixtures for capturly tests."""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from capturly import storage


@pytest.fixture
def temp_recordings_dir():
    """Create a temporary directory for recordings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recordings_dir = Path(tmpdir) / "capturly-recordings"
        recordings_dir.mkdir()
        os.environ["CAPTURLY_RECORDINGS_DIR"] = str(recordings_dir)
        storage.RECORDINGS_DIR = None  # Reset cached value
        yield recordings_dir
        if "CAPTURLY_RECORDINGS_DIR" in os.environ:
            del os.environ["CAPTURLY_RECORDINGS_DIR"]
        storage.RECORDINGS_DIR = None


@pytest.fixture
def mock_backend_server():
    """Start a simple HTTP server for testing backend responses."""

    class MockHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"path": self.path, "method": "GET"}
            self.wfile.write(json.dumps(response).encode())

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"path": self.path, "method": "POST", "body": body.decode()}
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, format, *args):
            pass  # Suppress logs during tests

    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
