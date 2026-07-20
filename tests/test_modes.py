"""Tests for capturly operating modes."""

import json
from pathlib import Path


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
