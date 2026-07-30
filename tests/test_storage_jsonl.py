"""Tests for JSONL traffic log storage."""

import json
import threading
from unittest.mock import Mock

from capturly import storage


def test_append_creates_jsonl_file(temp_recordings_dir):
    """append_traffic_log_entry creates a .jsonl file with one line per entry."""
    entry = {"method": "GET", "path": "/test", "status_code": 200}
    storage.append_traffic_log_entry(entry)

    log_file = temp_recordings_dir / "traffic_log.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry


def test_append_multiple_entries(temp_recordings_dir):
    """Multiple appends produce multiple lines."""
    for i in range(5):
        storage.append_traffic_log_entry({"method": "GET", "path": f"/{i}"})

    entries = storage.read_traffic_log_entries()
    assert len(entries) == 5
    assert entries[3]["path"] == "/3"


def test_read_skips_malformed_lines(temp_recordings_dir):
    """Malformed lines are skipped, valid lines are returned."""
    log_file = temp_recordings_dir / "traffic_log.jsonl"
    log_file.write_text(
        '{"method": "GET", "path": "/ok"}\n'
        "NOT VALID JSON\n"
        '{"method": "POST", "path": "/also-ok"}\n'
    )

    entries = storage.read_traffic_log_entries()
    assert len(entries) == 2
    assert entries[0]["path"] == "/ok"
    assert entries[1]["path"] == "/also-ok"


def test_read_empty_file(temp_recordings_dir):
    """Empty or whitespace-only file returns empty list."""
    log_file = temp_recordings_dir / "traffic_log.jsonl"
    log_file.write_text("")
    assert storage.read_traffic_log_entries() == []

    log_file.write_text("   \n  \n")
    assert storage.read_traffic_log_entries() == []


def test_read_missing_file(temp_recordings_dir):
    """Missing file returns empty list."""
    assert storage.read_traffic_log_entries() == []


def test_enqueue_sync_fallback_appends(temp_recordings_dir):
    """enqueue_traffic_log_entry without async logger appends JSONL."""
    handler = Mock()
    handler.traffic_logger = None
    handler.log_file_lock = threading.Lock()

    storage.enqueue_traffic_log_entry(handler, {"method": "PATCH", "path": "/x"})
    storage.enqueue_traffic_log_entry(handler, {"method": "DELETE", "path": "/y"})

    entries = storage.read_traffic_log_entries()
    assert len(entries) == 2
    assert entries[0]["method"] == "PATCH"
    assert entries[1]["method"] == "DELETE"
