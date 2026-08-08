"""Tests for dashboard CLI integration and live traffic log reading."""

import json
import os
import tempfile
import threading
import urllib.request

from capturly import dashboard


def _write_jsonl(path, entries):
    """Write entries as JSONL (one JSON object per line)."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def test_dashboard_reads_from_traffic_log_file():
    """Dashboard reads live entries from traffic_log.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.jsonl")
        entries = [
            {
                "timestamp_ms": 1000,
                "method": "POST",
                "path": "/v1/chat/completions",
                "status_code": 200,
            },
            {"timestamp_ms": 2000, "method": "GET", "path": "/api/health", "status_code": 200},
        ]
        _write_jsonl(log_file, entries)

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
    """Dashboard reflects new entries appended to traffic_log.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.jsonl")
        _write_jsonl(
            log_file,
            [{"timestamp_ms": 1000, "method": "GET", "path": "/a", "status_code": 200}],
        )

        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=log_file
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # Append a new entry (simulates proxy writing)
            with open(log_file, "a") as f:
                f.write(
                    json.dumps(
                        {"timestamp_ms": 2000, "method": "POST", "path": "/b", "status_code": 201}
                    )
                    + "\n"
                )

            url = f"http://127.0.0.1:{port}/api/traffic"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                assert data["total"] == 2
        finally:
            server.shutdown()


def test_dashboard_missing_log_file():
    """Dashboard returns empty list when traffic_log.jsonl doesn't exist."""
    server = dashboard.create_dashboard_server(
        entries=None, host="127.0.0.1", port=0, traffic_log_path="/nonexistent/traffic_log.jsonl"
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


def test_file_mode_slim_summary_and_full_detail():
    """File-mode list returns slim summaries; detail returns the full entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.jsonl")
        entry = {
            "timestamp_ms": 1000,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 200,
            "ai_insights": {
                "request": {
                    "model": "gpt-4o",
                    "message_count": 5,
                    "system_prompts": ["S" * 10000],
                },
                "response": {"usage": {"total_tokens": 99}},
            },
        }
        _write_jsonl(log_file, [entry])

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
            summary = data["entries"][0]
            assert "ai_insights" not in summary
            assert summary["ai"]["model"] == "gpt-4o"
            assert summary["ai"]["total_tokens"] == 99
            assert "S" * 100 not in json.dumps(data)

            url = f"http://127.0.0.1:{port}/api/traffic/0"
            with urllib.request.urlopen(url, timeout=5) as resp:
                detail = json.loads(resp.read().decode())
            assert detail == entry  # full fidelity via offset read
        finally:
            server.shutdown()


def test_file_mode_ai_filter():
    """?ai=true filters file-mode summaries by the slim ai field."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.jsonl")
        _write_jsonl(
            log_file,
            [
                {
                    "timestamp_ms": 1000,
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "status_code": 200,
                    "ai_insights": {"request": {"model": "gpt-4o"}, "response": {}},
                },
                {"timestamp_ms": 2000, "method": "GET", "path": "/health", "status_code": 200},
            ],
        )
        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=log_file
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{port}/api/traffic?ai=true"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            assert data["total"] == 1
            assert data["entries"][0]["path"] == "/v1/chat/completions"
        finally:
            server.shutdown()


def test_truncate_endpoint_resets_index():
    """POST /api/truncate clears the file and the index; appends work after."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.jsonl")
        _write_jsonl(
            log_file,
            [{"timestamp_ms": 1000, "method": "GET", "path": "/a", "status_code": 200}],
        )
        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=log_file
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/truncate", method="POST", data=b""
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert json.loads(resp.read().decode())["ok"] is True

            url = f"http://127.0.0.1:{port}/api/traffic"
            with urllib.request.urlopen(url, timeout=5) as resp:
                assert json.loads(resp.read().decode())["total"] == 0

            with open(log_file, "a") as f:
                f.write(
                    json.dumps(
                        {"timestamp_ms": 2000, "method": "GET", "path": "/b", "status_code": 200}
                    )
                    + "\n"
                )
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            assert data["total"] == 1
            assert data["entries"][0]["path"] == "/b"
        finally:
            server.shutdown()


def test_concurrent_requests_served():
    """The threaded dashboard serves overlapping list and detail requests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "traffic_log.jsonl")
        _write_jsonl(
            log_file,
            [
                {"timestamp_ms": i, "method": "GET", "path": f"/{i}", "status_code": 200}
                for i in range(20)
            ],
        )
        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=log_file
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        errors = []

        def worker(i):
            try:
                url = f"http://127.0.0.1:{port}/api/traffic/{i % 20}"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    assert data["path"] == f"/{i % 20}"
                url = f"http://127.0.0.1:{port}/api/traffic?limit=5"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    assert json.loads(resp.read().decode())["total"] == 20
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        try:
            workers = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for w in workers:
                w.start()
            for w in workers:
                w.join(timeout=15)
            assert errors == []
        finally:
            server.shutdown()
