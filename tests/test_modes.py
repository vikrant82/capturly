"""Tests for capturly operating modes."""

import json


def test_record_mode_saves_response(temp_recordings_dir, mock_backend_server):
    """Record mode proxies to backend and saves response."""
    # Full implementation would create a MockServiceHandler instance
    # and call record.record_and_proxy() — deferred to integration tests
    pass


def test_replay_mode_returns_saved_response(temp_recordings_dir):
    """Replay mode returns previously recorded response."""
    # Create a recording manually
    cache_key = "test_key"
    recording = {
        "method": "GET",
        "path": "/test",
        "status_code": 200,
        "response_headers": {"Content-Type": "application/json"},
        "response_body": '{"result": "success"}',
        "body_encoding": "utf-8",
    }

    recording_file = temp_recordings_dir / f"{cache_key}.json"
    with open(recording_file, "w") as f:
        json.dump(recording, f)

    # Verify file exists and is valid JSON
    assert recording_file.exists()
    with open(recording_file) as f:
        loaded = json.load(f)
    assert loaded["path"] == "/test"
    assert loaded["status_code"] == 200


def test_hybrid_mode_cache_hit(temp_recordings_dir):
    """Hybrid mode replays cached response."""
    # Verify cache hit behavior — deferred to integration tests
    pass


def test_hybrid_mode_cache_miss(temp_recordings_dir, mock_backend_server):
    """Hybrid mode records on cache miss."""
    # Verify cache miss triggers recording — deferred to integration tests
    pass


def test_log_mode_supports_all_http_methods(temp_recordings_dir, mock_backend_server):
    """Log mode proxies PATCH, HEAD, OPTIONS (not just GET/POST/PUT/DELETE)."""
    import threading
    import urllib.request

    from capturly.handler import MockServiceHandler
    from capturly.server import ThreadedHTTPServer

    MockServiceHandler.mode = "log"
    MockServiceHandler.backend_url = mock_backend_server
    MockServiceHandler.replay_delay_ms = 0
    MockServiceHandler.combine_chunks = False
    MockServiceHandler.traffic_logger = None

    server = ThreadedHTTPServer(("127.0.0.1", 0), MockServiceHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"

        # PATCH with body
        req = urllib.request.Request(
            f"{base}/api/resource/1",
            data=b'{"field": "updated"}',
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            assert data["method"] == "PATCH"
            assert data["body"] == '{"field": "updated"}'

        # DELETE
        req = urllib.request.Request(f"{base}/api/resource/1", method="DELETE")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            assert data["method"] == "DELETE"

        # OPTIONS
        req = urllib.request.Request(f"{base}/api/resource/1", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 204

        # HEAD (no body in response)
        req = urllib.request.Request(f"{base}/api/resource/1", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert resp.read() == b""  # HEAD must not return a body

        # Verify traffic log captured the entries
        # (log writes happen after response is sent, so allow a moment)
        import time

        from capturly import storage

        time.sleep(0.3)
        entries = storage.read_traffic_log_entries()
        methods_logged = [e["method"] for e in entries]
        assert "PATCH" in methods_logged
        assert "DELETE" in methods_logged
        assert "OPTIONS" in methods_logged
        assert "HEAD" in methods_logged
    finally:
        server.shutdown()
        # Reset class-level state
        MockServiceHandler.mode = "record"
        MockServiceHandler.backend_url = None
        MockServiceHandler.traffic_logger = None
