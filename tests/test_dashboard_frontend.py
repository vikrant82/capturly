"""Frontend behavior tests for the dashboard HTML/JS asset."""

import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from capturly import dashboard

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "tests" / "frontend" / "dashboard_js_test.js"
HTML_ASSET = REPO_ROOT / "src" / "capturly" / "dashboard.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_dashboard_js_behavior():
    """Run the Node-based assertions over the dashboard's embedded JS."""
    result = subprocess.run(
        ["node", str(HARNESS), str(HTML_ASSET)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Frontend JS tests failed:\n{result.stdout}\n{result.stderr}"
    )


def test_served_html_matches_asset_file():
    """GET / serves exactly the packaged dashboard.html asset."""
    server = dashboard.create_dashboard_server([], host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            served = resp.read().decode("utf-8")
        assert served == HTML_ASSET.read_text(encoding="utf-8")
    finally:
        server.shutdown()
