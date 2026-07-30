"""Tests for the async traffic logger."""

import json
import os
import tempfile
import time

from capturly import storage
from capturly.handler import MockServiceHandler
from capturly.logger import AsyncTrafficLogger


def test_logger_appends_jsonl_entries():
    """Logger appends entries as JSONL lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        os.makedirs("capturly-recordings", exist_ok=True)
        storage.RECORDINGS_DIR = None  # Reset cached dir

        logger = AsyncTrafficLogger(MockServiceHandler)
        logger.enqueue({"timestamp_ms": 1000, "method": "GET", "path": "/a"})
        logger.enqueue({"timestamp_ms": 2000, "method": "POST", "path": "/b"})
        time.sleep(0.2)
        logger.stop()

        log_file = os.path.join("capturly-recordings", "traffic_log.jsonl")
        with open(log_file) as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["path"] == "/a"
        assert json.loads(lines[1])["path"] == "/b"


def test_traffic_log_truncation_resets_entries():
    """Truncating traffic_log.jsonl means new entries start fresh."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        os.makedirs("capturly-recordings", exist_ok=True)
        storage.RECORDINGS_DIR = None  # Reset cached dir

        logger = AsyncTrafficLogger(MockServiceHandler)
        logger.enqueue({"timestamp_ms": 1000, "method": "GET", "path": "/old"})
        logger.enqueue({"timestamp_ms": 2000, "method": "POST", "path": "/old"})
        time.sleep(0.2)

        # Truncate the file (simulate dashboard "clear")
        log_file = os.path.join("capturly-recordings", "traffic_log.jsonl")
        with open(log_file, "w") as f:
            f.truncate(0)

        # Add new entry
        logger.enqueue({"timestamp_ms": 3000, "method": "GET", "path": "/new"})
        time.sleep(0.2)
        logger.stop()

        with open(log_file) as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) == 1
        assert json.loads(lines[0])["path"] == "/new"
