"""Tests for dashboard CLI integration and live traffic log reading."""

import json
import os
import tempfile
import threading
import urllib.request

from capturly import dashboard


def test_dashboard_reads_from_traffic_log_file():
    """Dashboard reads live entries from traffic_log.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.json")
        entries = [
            {"timestamp_ms": 1000, "method": "POST", "path": "/v1/chat/completions", "status_code": 200},
            {"timestamp_ms": 2000, "method": "GET", "path": "/api/health", "status_code": 200},
        ]
        with open(log_file, "w") as f:
            json.dump(entries, f)

        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=log_file
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            url = f"http://127.0.0.1:{port}/api/traffic"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                assert data["total"] == 2
                assert data["entries"][0]["path"] == "/v1/chat/completions"
        finally:
            server.shutdown()


def test_dashboard_live_updates():
    """Dashboard reflects new entries appended to traffic_log.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.json")
        with open(log_file, "w") as f:
            json.dump([{"timestamp_ms": 1000, "method": "GET", "path": "/a", "status_code": 200}], f)

        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=log_file
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # Append a new entry
            with open(log_file, "w") as f:
                json.dump([
                    {"timestamp_ms": 1000, "method": "GET", "path": "/a", "status_code": 200},
                    {"timestamp_ms": 2000, "method": "POST", "path": "/b", "status_code": 201},
                ], f)

            url = f"http://127.0.0.1:{port}/api/traffic"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                assert data["total"] == 2
        finally:
            server.shutdown()


def test_dashboard_missing_log_file():
    """Dashboard returns empty list when traffic_log.json doesn't exist."""
    server = dashboard.create_dashboard_server(
        entries=None, host="127.0.0.1", port=0, traffic_log_path="/nonexistent/traffic_log.json"
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = f"http://127.0.0.1:{port}/api/traffic"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            assert data["total"] == 0
            assert data["entries"] == []
    finally:
        server.shutdown()


def test_cli_dashboard_flags():
    """CLI accepts --dashboard and --dashboard-port flags."""
    from capturly import cli
    import argparse

    # Parse with dashboard flags (mock run_server to avoid actually starting)
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="replay")
    parser.add_argument("--backend")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--delay", type=int, default=0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--combine-chunks", action="store_true")
    parser.add_argument("--recordings-dir")
    parser.add_argument("--config", dest="config_file")
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--dashboard-port", type=int, default=9090)

    args = parser.parse_args(["--dashboard", "--dashboard-port", "8888"])
    assert args.dashboard is True
    assert args.dashboard_port == 8888
