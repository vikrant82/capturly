"""End-to-end integration tests for capturly."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def test_full_record_replay_workflow():
    """End-to-end test: record traffic, then replay it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recordings_dir = Path(tmpdir) / "recordings"

        # Start a simple backend
        backend_script = Path(tmpdir) / "backend.py"
        backend_script.write_text("""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())

    def log_message(self, format, *args):
        pass

HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
""")

        backend_proc = subprocess.Popen(
            [sys.executable, str(backend_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)  # Wait for backend to start

        try:
            # Record mode
            record_proc = subprocess.Popen(
                [
                    sys.executable, "-m", "capturly",
                    "--mode", "record",
                    "--backend", "http://127.0.0.1:8765",
                    "--port", "9998",
                    "--recordings-dir", str(recordings_dir),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)

            # Make a request
            result = subprocess.run(
                ["curl", "-s", "http://127.0.0.1:9998/test"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert json.loads(result.stdout) == {"status": "ok"}

            record_proc.terminate()
            record_proc.wait()

            # Verify recording exists
            json_files = list(recordings_dir.glob("*.json"))
            assert len(json_files) == 1

            # Replay mode
            replay_proc = subprocess.Popen(
                [
                    sys.executable, "-m", "capturly",
                    "--mode", "replay",
                    "--port", "9997",
                    "--recordings-dir", str(recordings_dir),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)

            # Make same request, should get recorded response
            result = subprocess.run(
                ["curl", "-s", "http://127.0.0.1:9997/test"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert json.loads(result.stdout) == {"status": "ok"}

            replay_proc.terminate()
            replay_proc.wait()

        finally:
            backend_proc.terminate()
            backend_proc.wait()
