"""Tests for the async traffic logger."""

import json
import os
import tempfile
import time

from capturly.handler import MockServiceHandler
from capturly.logger import AsyncTrafficLogger


def test_traffic_log_truncation_resets_entries():
    """Truncating traffic_log.json should reset entries, not resurrect old ones."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        os.makedirs("capturly-recordings", exist_ok=True)

        # Create logger and add entries
        logger = AsyncTrafficLogger(MockServiceHandler)
        logger.enqueue({"timestamp_ms": 1000, "method": "GET", "path": "/old"})
        logger.enqueue({"timestamp_ms": 2000, "method": "POST", "path": "/old"})

        # Wait for writes
        time.sleep(0.1)

        # Truncate the file (simulate user action)
        log_file = os.path.join("capturly-recordings", "traffic_log.json")
        with open(log_file, "w") as f:
            f.write("")  # Truncate to empty

        # Add new entry
        logger.enqueue({"timestamp_ms": 3000, "method": "GET", "path": "/new"})
        time.sleep(0.1)

        # Stop logger
        logger.stop()

        # Verify: only new entry exists, old ones not resurrected
        with open(log_file) as f:
            entries = json.load(f)

        assert len(entries) == 1
        assert entries[0]["path"] == "/new"
        assert entries[0]["timestamp_ms"] == 3000
