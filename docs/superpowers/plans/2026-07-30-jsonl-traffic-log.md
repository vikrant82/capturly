# JSONL Traffic Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON-array traffic log with an append-only JSONL file, eliminating the cross-process read-modify-write race that silently loses entries when multiple capturly proxies share a recordings directory.

**Architecture:** Switch `traffic_log.json` (whole-file JSON array, rewritten on every entry) to `traffic_log.jsonl` (one JSON object per line, append-only). Writers use `O_APPEND` single-line writes — atomic on POSIX for lines < 4 KB. Readers parse line-by-line, skipping malformed lines. The `AsyncTrafficLogger` drops its in-memory entries list and `_refresh_entries_from_disk` truncation detection; it simply appends. Truncation (dashboard "clear") empties the file; new appends start fresh naturally.

**Tech Stack:** Python stdlib only (`json`, `os`). No new dependencies.

## Global Constraints

- Python stdlib only — no new packages
- POSIX atomic append guarantee: each entry must be written as a single `f.write()` call to a file opened with mode `"a"` (`O_APPEND`)
- Traffic log entries are typically 200–2000 bytes; well under the 4 KB `PIPE_BUF` threshold
- Old `traffic_log.json` files are ignored (clean break, no migration)
- All existing tests must pass after each task
- Filename: `traffic_log.jsonl`

---

### Task 1: JSONL storage primitives

**Files:**
- Modify: `src/capturly/storage.py`
- Test: `tests/test_storage_jsonl.py` (create)

**Interfaces:**
- Produces: `append_traffic_log_entry(entry: dict) -> None` — appends one JSONL line
- Produces: `read_traffic_log_entries() -> list[dict]` — reads JSONL, skips bad lines
- Removes: `write_traffic_log_entries(entries)` — no longer needed
- Modifies: `enqueue_traffic_log_entry(handler, entry)` — sync fallback uses append

- [ ] **Step 1: Write failing tests for JSONL read/append**

```python
# tests/test_storage_jsonl.py
"""Tests for JSONL traffic log storage."""

import json
import os
import tempfile

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
    import threading
    from unittest.mock import Mock

    handler = Mock()
    handler.traffic_logger = None
    handler.log_file_lock = threading.Lock()

    storage.enqueue_traffic_log_entry(handler, {"method": "PATCH", "path": "/x"})
    storage.enqueue_traffic_log_entry(handler, {"method": "DELETE", "path": "/y"})

    entries = storage.read_traffic_log_entries()
    assert len(entries) == 2
    assert entries[0]["method"] == "PATCH"
    assert entries[1]["method"] == "DELETE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage_jsonl.py -v`
Expected: FAIL — `append_traffic_log_entry` not defined

- [ ] **Step 3: Implement JSONL storage functions**

In `src/capturly/storage.py`, replace the traffic log section (lines 87–119) with:

```python
TRAFFIC_LOG_FILENAME = "traffic_log.jsonl"


def append_traffic_log_entry(entry):
    """Append a single entry as one JSONL line. Safe for concurrent processes."""
    log_file = os.path.join(get_recordings_dir(), TRAFFIC_LOG_FILENAME)
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)


def read_traffic_log_entries():
    """Read traffic-log entries from JSONL file, skipping malformed lines."""
    log_file = os.path.join(get_recordings_dir(), TRAFFIC_LOG_FILENAME)
    if not os.path.exists(log_file):
        return []

    entries = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def enqueue_traffic_log_entry(handler, entry):
    """Queue a traffic-log write, falling back to synchronous append."""
    logger = handler.traffic_logger
    if logger:
        logger.enqueue(entry)
        return

    with handler.log_file_lock:
        append_traffic_log_entry(entry)
```

Remove `write_traffic_log_entries` and its private alias `_write_traffic_log_entries`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage_jsonl.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/capturly/storage.py tests/test_storage_jsonl.py
git commit -m "feat(storage): add JSONL append/read for traffic log"
```

---

### Task 2: AsyncTrafficLogger — append-only writer

**Files:**
- Modify: `src/capturly/logger.py`
- Modify: `tests/test_logger.py`

**Interfaces:**
- Consumes: `storage.append_traffic_log_entry(entry)` from Task 1
- Consumes: `storage.TRAFFIC_LOG_FILENAME` from Task 1
- Removes: `_load_entries`, `_refresh_entries_from_disk`, `_write_entries` (in-memory entries tracking)
- The logger no longer holds an entries list; it appends each entry directly

- [ ] **Step 1: Update test_logger.py for JSONL**

```python
# tests/test_logger.py
"""Tests for the async traffic logger."""

import json
import os
import tempfile
import time

from capturly.handler import MockServiceHandler
from capturly.logger import AsyncTrafficLogger


def test_logger_appends_jsonl_entries():
    """Logger appends entries as JSONL lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        os.makedirs("capturly-recordings", exist_ok=True)

        logger = AsyncTrafficLogger(MockServiceHandler)
        logger.enqueue({"timestamp_ms": 1000, "method": "GET", "path": "/a"})
        logger.enqueue({"timestamp_ms": 2000, "method": "POST", "path": "/b"})
        time.sleep(0.2)
        logger.stop()

        log_file = os.path.join("capturly-recordings", "traffic_log.jsonl")
        with open(log_file) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["path"] == "/a"
        assert json.loads(lines[1])["path"] == "/b"


def test_traffic_log_truncation_resets_entries():
    """Truncating traffic_log.jsonl means new entries start fresh."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        os.makedirs("capturly-recordings", exist_ok=True)

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
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 1
        assert json.loads(lines[0])["path"] == "/new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_logger.py -v`
Expected: FAIL — logger still writes JSON array format

- [ ] **Step 3: Rewrite AsyncTrafficLogger for append-only**

Replace `src/capturly/logger.py` with:

```python
"""Asynchronous traffic and SSE event persistence."""

import os
import queue
import sys
import threading

from . import storage


class AsyncTrafficLogger:
    """Serializes traffic and SSE event file writes off the request path."""

    def __init__(self, handler_cls):
        self.handler_cls = handler_cls
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="traffic-log-writer", daemon=True)
        self.handler = object.__new__(handler_cls)
        self.handler.log_message = lambda *args, **kwargs: None
        self.log_file = os.path.join(storage.get_recordings_dir(), storage.TRAFFIC_LOG_FILENAME)
        self.thread.start()

    def enqueue(self, entry):
        self.queue.put(("entry", entry))

    def enqueue_sse_event(self, event_log_file, sequence, event_lines):
        self.queue.put(("sse_event", (event_log_file, sequence, list(event_lines))))

    def stop(self):
        self.queue.put(("stop", None))
        self.thread.join(timeout=5)

    def _run(self):
        while True:
            kind, entry = self.queue.get()
            if kind == "stop":
                self.queue.task_done()
                self._drain()
                return

            if kind == "entry":
                self._append_entry(entry)
            elif kind == "sse_event":
                event_log_file, sequence, event_lines = entry
                self._write_sse_event(event_log_file, sequence, event_lines)

            self.queue.task_done()

    def _drain(self):
        while True:
            try:
                kind, entry = self.queue.get_nowait()
            except queue.Empty:
                return

            if kind == "entry":
                self._append_entry(entry)
            elif kind == "sse_event":
                event_log_file, sequence, event_lines = entry
                self._write_sse_event(event_log_file, sequence, event_lines)
            self.queue.task_done()

    def _append_entry(self, entry):
        try:
            storage.append_traffic_log_entry(entry)
        except Exception as e:
            sys.stderr.write(f"[LOG] Failed to append traffic log entry: {e}\n")

    def _write_sse_event(self, event_log_file, sequence, event_lines):
        try:
            self.handler._log_sse_event(event_log_file, sequence, event_lines)
        except Exception as e:
            sys.stderr.write(f"[LOG] Failed to write SSE event log: {e}\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_logger.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/capturly/logger.py tests/test_logger.py
git commit -m "feat(logger): switch AsyncTrafficLogger to JSONL append-only"
```

---

### Task 3: Dashboard — JSONL reader and truncation

**Files:**
- Modify: `src/capturly/dashboard.py`
- Modify: `src/capturly/server.py`
- Modify: `tests/test_dashboard_integration.py`
- Modify: `tests/test_phase1_integration.py`

**Interfaces:**
- Consumes: `storage.TRAFFIC_LOG_FILENAME` from Task 1
- `_read_traffic_log(path)` now parses JSONL
- `_truncate_traffic_log(path)` truncates to empty file

- [ ] **Step 1: Update dashboard integration tests for JSONL**

In `tests/test_dashboard_integration.py`, change all `json.dump(entries, f)` writes to JSONL format:

```python
def _write_jsonl(path, entries):
    """Helper: write entries as JSONL."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
```

Replace `json.dump(entries, f)` calls with `_write_jsonl(log_file, entries)`.
Change filenames from `traffic_log.json` to `traffic_log.jsonl`.

In `tests/test_phase1_integration.py`, same change for the traffic log write (line 107–109):

```python
traffic_log_path = os.path.join(tmpdir, "traffic_log.jsonl")
with open(traffic_log_path, "w") as f:
    f.write(json.dumps(entry) + "\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_integration.py tests/test_phase1_integration.py -v`
Expected: FAIL — dashboard still reads JSON array

- [ ] **Step 3: Update dashboard.py**

Replace `_read_traffic_log`:

```python
def _read_traffic_log(path: str) -> list[dict[str, Any]]:
    """Read traffic log entries from a JSONL file. Returns [] on any error."""
    if not os.path.isfile(path):
        return []
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        entries.append(obj)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries
```

Replace `_truncate_traffic_log`:

```python
def _truncate_traffic_log(path: str) -> None:
    """Truncate the traffic log file to empty."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.truncate(0)
    except OSError:
        pass
```

- [ ] **Step 4: Update server.py filename**

In `src/capturly/server.py` line 60, change:

```python
traffic_log_path = os.path.join(storage.get_recordings_dir(), "traffic_log.json")
```

to:

```python
traffic_log_path = os.path.join(storage.get_recordings_dir(), storage.TRAFFIC_LOG_FILENAME)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_integration.py tests/test_phase1_integration.py tests/test_dashboard.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/capturly/dashboard.py src/capturly/server.py tests/test_dashboard_integration.py tests/test_phase1_integration.py
git commit -m "feat(dashboard): read JSONL traffic log, update server filename"
```

---

### Task 4: Handler cleanup and full verification

**Files:**
- Modify: `src/capturly/handler.py`
- Modify: `tests/test_modes.py`

**Interfaces:**
- Removes: `MockServiceHandler._write_traffic_log_entries` (no longer called by anything)
- Keeps: `_read_traffic_log_entries`, `_enqueue_traffic_log_entry` (still used)

- [ ] **Step 1: Remove dead handler method**

In `src/capturly/handler.py`, remove `_write_traffic_log_entries`:

```python
# DELETE these two lines:
    def _write_traffic_log_entries(self, entries):
        return storage.write_traffic_log_entries(entries)
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: all PASS (78+ tests)

- [ ] **Step 3: Verify no remaining references to old format**

Run: `grep -r "traffic_log\.json" src/ tests/ --include="*.py" | grep -v ".jsonl"`
Expected: no matches (all references should be `.jsonl` or use `TRAFFIC_LOG_FILENAME`)

- [ ] **Step 4: Commit**

```bash
git add src/capturly/handler.py
git commit -m "refactor(handler): remove dead _write_traffic_log_entries method"
```
