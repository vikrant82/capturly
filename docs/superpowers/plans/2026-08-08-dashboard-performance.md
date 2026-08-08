# Dashboard Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Capturly dashboard usable with large traffic logs (64 MB+ / multi-MB entries) via an incremental offset index on the server and lazy, truncated rendering on the frontend.

**Architecture:** A new `TrafficIndex` scans the JSONL log once, storing each entry's byte offset + a slim summary; subsequent syncs parse only appended bytes. The dashboard serves list/stats from summaries, and detail requests seek to a single offset and parse one line. The frontend renders detail sections only on expand, truncates long text/arrays with progressive reveal, and pauses polling while the panel is open.

**Tech Stack:** Python 3.9+ stdlib (`json`, `os`, `threading`, `http.server`, `socketserver`), vanilla JS in the packaged `dashboard.html`, pytest, Node-based JS harness.

**Spec:** `docs/superpowers/specs/2026-08-08-dashboard-performance-design.md`

## Global Constraints

- Python >= 3.9; no new dependencies (stdlib only).
- Line length 100 (black + ruff; ruff selects `E,F,I,N,W,UP`, ignores `E501`).
- Run tests with `python3 -m pytest` (NOT `python` — Homebrew 3.14 has no pytest).
- Tests comparing `tempfile` paths must apply `os.path.realpath()` (macOS `/var` → `/private/var`).
- Conventional commits; one commit per task as specified below.
- Work on the existing branch `feat/dashboard-performance` (already checked out; spec committed as `d14f21b`).
- Do NOT change static-entries dashboard mode behavior (used by `tests/test_dashboard.py`).
- After implementation tasks: run full suite `python3 -m pytest tests/ -v` and `ruff check src/ tests/` before each commit.

---

### Task 1: TrafficIndex — incremental offset index over the JSONL log

**Files:**
- Create: `src/capturly/traffic_index.py`
- Test: `tests/test_traffic_index.py`

**Interfaces:**
- Consumes: nothing (self-contained module)
- Produces: `TrafficIndex(path: str)` with methods
  - `sync() -> None` — bring index up to date with the file
  - `summaries() -> list[dict]` — slim summary dicts (each has `_index`)
  - `stats() -> dict` — `{total_requests, ai_requests, total_tokens, models}`
  - `count() -> int` — number of indexed entries
  - `load_entry(index: int) -> Optional[dict]` — parse the single entry at position `index`; `None` if out of range or unreadable
  - `reset() -> None` — drop all index state

- [ ] **Step 1: Write the failing tests**

Create `tests/test_traffic_index.py`:

```python
"""Tests for the JSONL traffic log offset index."""

import json
import os

from capturly.traffic_index import TrafficIndex


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _entry(**overrides):
    base = {
        "timestamp_ms": 1000,
        "method": "POST",
        "path": "/v1/chat/completions",
        "status_code": 200,
        "request_body_size": 10,
        "response_body_size": 20,
        "source_name": "pipe-a",
        "backend_url": "http://localhost:8000",
        "duration_ms": 5,
    }
    base.update(overrides)
    return base


def test_offset_round_trip(tmp_path):
    """Every _index loads back the exact original entry dict."""
    log = str(tmp_path / "traffic_log.jsonl")
    entries = [_entry(path=f"/{i}") for i in range(5)]
    _write_jsonl(log, entries)
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 5
    for i, original in enumerate(entries):
        assert idx.load_entry(i) == original


def test_summaries_are_slim(tmp_path):
    """Summaries carry small ai fields and never large content."""
    log = str(tmp_path / "traffic_log.jsonl")
    entry = _entry(
        ai_insights={
            "request": {
                "model": "gpt-4o",
                "message_count": 42,
                "system_prompts": ["S" * 100000],
                "tool_names": ["search", "edit"],
                "tool_count": 2,
            },
            "response": {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "tool_call_names": ["search"],
            },
        },
        sse=True,
    )
    _write_jsonl(log, [entry])
    idx = TrafficIndex(log)
    idx.sync()
    summary = idx.summaries()[0]
    assert summary["ai"] == {
        "model": "gpt-4o",
        "message_count": 42,
        "tool_names": ["search", "edit"],
        "tool_count": 2,
        "total_tokens": 15,
    }
    assert summary["sse"] is True
    assert summary["tools"] is True
    assert summary["_index"] == 0
    assert summary["method"] == "POST"
    assert summary["source_name"] == "pipe-a"
    # No large fields leak into summaries
    text = json.dumps(idx.summaries())
    assert "S" * 100 not in text
    assert "system_prompts" not in text


def test_agui_flag_in_summary(tmp_path):
    """Entries whose response_body is an AGUI completion get the agui flag."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(response_body={"object": "agui.completion", "run": {}})])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.summaries()[0]["agui"] is True


def test_incremental_append(tmp_path):
    """Appending new lines indexes only the new entries."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a")])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 1
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(_entry(path="/b")) + "\n")
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(0)["path"] == "/a"
    assert idx.load_entry(1)["path"] == "/b"


def test_partial_line_deferred(tmp_path):
    """A line without a trailing newline is not indexed until complete."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a")])
    with open(log, "a", encoding="utf-8") as f:
        f.write('{"timestamp_ms": 2000, "path": "/par')
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 1
    with open(log, "a", encoding="utf-8") as f:
        f.write('tial"}\n')
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(1) == {"timestamp_ms": 2000, "path": "/partial"}


def test_malformed_line_skipped(tmp_path):
    """Malformed lines are skipped without disturbing entry numbering."""
    log = str(tmp_path / "traffic_log.jsonl")
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(path="/a")) + "\n")
        f.write("not json at all\n")
        f.write(json.dumps(_entry(path="/b")) + "\n")
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(0)["path"] == "/a"
    assert idx.load_entry(1)["path"] == "/b"


def test_truncate_resets(tmp_path):
    """Shrinking the file resets the index; new lines are re-indexed."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a"), _entry(path="/b")])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 2
    with open(log, "w", encoding="utf-8") as f:
        f.truncate(0)
    idx.sync()
    assert idx.count() == 0
    assert idx.stats()["total_requests"] == 0
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(_entry(path="/c")) + "\n")
    idx.sync()
    assert idx.count() == 1
    assert idx.load_entry(0)["path"] == "/c"


def test_replaced_file_resets(tmp_path):
    """Replacing the file (new inode) resets the index."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a")])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 1
    os.remove(log)
    _write_jsonl(log, [_entry(path="/x"), _entry(path="/y")])
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(0)["path"] == "/x"


def test_missing_file(tmp_path):
    """A missing log file yields an empty index, not an error."""
    idx = TrafficIndex(str(tmp_path / "nope.jsonl"))
    idx.sync()
    assert idx.count() == 0
    assert idx.summaries() == []
    assert idx.stats() == {
        "total_requests": 0,
        "ai_requests": 0,
        "total_tokens": 0,
        "models": [],
    }


def test_load_entry_out_of_range(tmp_path):
    """Out-of-range indices return None."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry()])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.load_entry(1) is None
    assert idx.load_entry(-1) is None


def test_stats(tmp_path):
    """Stats counters aggregate model, AI request, and token totals."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(
        log,
        [
            _entry(
                ai_insights={
                    "request": {"model": "gpt-4o", "message_count": 2},
                    "response": {"usage": {"total_tokens": 15}},
                }
            ),
            _entry(),
            _entry(
                ai_insights={
                    "request": {"model": "claude", "message_count": 3},
                    "response": {"usage": {"total_tokens": 30}},
                }
            ),
        ],
    )
    idx = TrafficIndex(log)
    idx.sync()
    stats = idx.stats()
    assert stats["total_requests"] == 3
    assert stats["ai_requests"] == 2
    assert stats["total_tokens"] == 45
    assert stats["models"] == ["claude", "gpt-4o"]


def test_stats_incremental_across_syncs(tmp_path):
    """Stats stay correct when entries arrive in later syncs."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(
        log,
        [
            _entry(
                ai_insights={
                    "request": {"model": "gpt-4o", "message_count": 2},
                    "response": {"usage": {"total_tokens": 15}},
                }
            )
        ],
    )
    idx = TrafficIndex(log)
    idx.sync()
    with open(log, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                _entry(
                    ai_insights={
                        "request": {"model": "claude", "message_count": 3},
                        "response": {"usage": {"total_tokens": 30}},
                    }
                )
            )
            + "\n"
        )
    idx.sync()
    stats = idx.stats()
    assert stats["total_requests"] == 2
    assert stats["ai_requests"] == 2
    assert stats["total_tokens"] == 45
    assert stats["models"] == ["claude", "gpt-4o"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_traffic_index.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'capturly.traffic_index'`

- [ ] **Step 3: Implement `src/capturly/traffic_index.py`**

```python
"""Incremental offset index over the JSONL traffic log.

The dashboard needs fast list/stats/detail access to a traffic log that grows
without bound. TrafficIndex scans the file once, remembering each entry's byte
offset and a slim summary; subsequent syncs parse only newly appended bytes.
Full entry bodies are never retained — only offsets and summaries stay resident.
"""

import json
import os
import threading
from typing import Any, Optional


def _build_summary(entry: dict, index: int) -> dict:
    """Build the slim list-view summary for a traffic entry.

    Contains everything the dashboard table, filters, badges, and stats need
    and nothing larger: no system prompts, no message bodies, no raw bodies.
    Flag derivation mirrors the dashboard's legacy _summary_entry rules.
    """
    summary: dict = {
        "_index": index,
        "timestamp_ms": entry.get("timestamp_ms"),
        "method": entry.get("method"),
        "path": entry.get("path"),
        "status_code": entry.get("status_code"),
        "request_body_size": entry.get("request_body_size"),
        "response_body_size": entry.get("response_body_size"),
        "source_name": entry.get("source_name"),
        "backend_url": entry.get("backend_url"),
        "duration_ms": entry.get("duration_ms"),
    }
    resp = entry.get("response_body")
    if entry.get("sse"):
        summary["sse"] = True
    if isinstance(resp, dict) and resp.get("object") == "agui.completion":
        summary["agui"] = True
    ai = entry.get("ai_insights")
    if ai and isinstance(ai, dict):
        ai_req = ai.get("request") or {}
        ai_resp = ai.get("response") or {}
        slim_ai: dict = {
            "model": ai_req.get("model"),
            "message_count": ai_req.get("message_count"),
        }
        if ai_req.get("tool_names"):
            slim_ai["tool_names"] = ai_req["tool_names"]
            slim_ai["tool_count"] = ai_req.get("tool_count", len(ai_req["tool_names"]))
        usage = ai_resp.get("usage")
        if isinstance(usage, dict):
            slim_ai["total_tokens"] = usage.get("total_tokens", 0)
        summary["ai"] = slim_ai
        if isinstance(ai_resp, dict) and ai_resp.get("tool_call_names"):
            summary["tools"] = True
    elif isinstance(resp, dict) and resp.get("tool_calls"):
        summary["tools"] = True
    return summary


class TrafficIndex:
    """Append-aware index over a JSONL traffic log.

    Invariants:
      - All mutation happens under an internal lock via sync()/reset().
      - summaries()/stats()/count() are safe from any thread.
      - load_entry() performs read-only file access at fixed offsets and
        needs no lock beyond reading the offset pair.
      - Only newline-terminated lines are indexed; a trailing fragment
        (writer mid-append) is deferred to the next sync.
      - Malformed lines are skipped but the byte cursor advances past them,
        keeping _index values stable between list and detail.

    Error behavior: missing or unreadable files yield an empty index;
    load_entry returns None on any read/parse failure.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._entries: list = []  # each: {"offset": int, "length": int, "summary": dict}
        self._indexed_size = 0
        self._file_id: Optional[tuple] = None  # (st_dev, st_ino)
        self._stats = {"total_requests": 0, "ai_requests": 0, "total_tokens": 0}
        self._models: list = []

    def sync(self) -> None:
        """Bring the index up to date with the file on disk."""
        with self._lock:
            try:
                st = os.stat(self.path)
            except OSError:
                self._reset_locked()
                return
            file_id = (st.st_dev, st.st_ino)
            if file_id != self._file_id or st.st_size < self._indexed_size:
                self._reset_locked()
                self._file_id = file_id
            if st.st_size == self._indexed_size:
                return
            self._scan_from(self._indexed_size)

    def reset(self) -> None:
        """Drop all index state (used on truncate)."""
        with self._lock:
            self._reset_locked()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def summaries(self) -> list:
        """Return slim summary dicts in log order (each carries _index)."""
        with self._lock:
            return [e["summary"] for e in self._entries]

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_requests": self._stats["total_requests"],
                "ai_requests": self._stats["ai_requests"],
                "total_tokens": self._stats["total_tokens"],
                "models": sorted(self._models),
            }

    def load_entry(self, index: int) -> Optional[dict]:
        """Read and parse the single entry at position `index`."""
        with self._lock:
            if index < 0 or index >= len(self._entries):
                return None
            offset = self._entries[index]["offset"]
            length = self._entries[index]["length"]
        try:
            with open(self.path, "rb") as f:
                f.seek(offset)
                raw = f.read(length)
            obj = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return obj if isinstance(obj, dict) else None

    def _reset_locked(self) -> None:
        self._entries = []
        self._indexed_size = 0
        self._file_id = None
        self._stats = {"total_requests": 0, "ai_requests": 0, "total_tokens": 0}
        self._models = []

    def _scan_from(self, start: int) -> None:
        try:
            with open(self.path, "rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return
        # Only index complete lines; a trailing fragment means a writer is
        # mid-append and the remainder is picked up on the next sync.
        end = data.rfind(b"\n")
        if end == -1:
            return
        cursor = start
        for raw in data[: end + 1].split(b"\n")[:-1]:
            line_len = len(raw) + 1
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                obj = None
            if isinstance(obj, dict):
                summary = _build_summary(obj, len(self._entries))
                self._entries.append(
                    {"offset": cursor, "length": line_len, "summary": summary}
                )
                self._update_stats(summary)
            cursor += line_len
        self._indexed_size = start + end + 1

    def _update_stats(self, summary: dict) -> None:
        self._stats["total_requests"] += 1
        ai = summary.get("ai")
        if ai:
            self._stats["ai_requests"] += 1
            tokens = ai.get("total_tokens")
            if isinstance(tokens, int):
                self._stats["total_tokens"] += tokens
            model = ai.get("model")
            if model and model not in self._models:
                self._models.append(model)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_traffic_index.py -v`
Expected: all PASS (12 tests)

- [ ] **Step 5: Lint and format**

Run: `python3 -m ruff check src/capturly/traffic_index.py tests/test_traffic_index.py && python3 -m black src/capturly/traffic_index.py tests/test_traffic_index.py`
Expected: no errors (black may reformat; re-run tests if it does)

- [ ] **Step 6: Commit**

```bash
git add src/capturly/traffic_index.py tests/test_traffic_index.py
git commit -m "feat: incremental offset index for the JSONL traffic log"
```

---

### Task 2: Dashboard server — serve from the index, threaded server

**Files:**
- Modify: `src/capturly/dashboard.py` (full rewrite below; removes now-unused `_read_traffic_log`)
- Test: `tests/test_dashboard_integration.py` (append new file-mode tests)

**Interfaces:**
- Consumes: `TrafficIndex` from Task 1 (`sync`, `summaries`, `stats`, `count`, `load_entry`, `reset`)
- Produces:
  - `/api/traffic` in file mode returns slim summaries (no `ai_insights`; `ai` object instead)
  - `/api/traffic/N` in file mode returns the full entry via offset read
  - `/api/stats` in file mode returns index-maintained counters
  - `create_dashboard_server(entries=None, host="127.0.0.1", port=9090, traffic_log_path=None) -> HTTPServer` (signature unchanged)
  - Static-entries mode behavior unchanged

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_integration.py` (keep existing imports; add `threading`/`urllib.error` where needed — `threading` and `urllib.request` are already imported):

```python
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
                    json.dumps({"timestamp_ms": 2000, "method": "GET", "path": "/b", "status_code": 200})
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
            [{"timestamp_ms": i, "method": "GET", "path": f"/{i}", "status_code": 200}
             for i in range(20)],
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
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_integration.py -v`
Expected: `test_file_mode_slim_summary_and_full_detail`, `test_file_mode_ai_filter`, `test_truncate_endpoint_resets_index` FAIL (list still returns full `ai_insights`; truncate does not reset index state — note `test_concurrent_requests_served` may pass even before the change; that is fine). Existing tests still PASS.

- [ ] **Step 3: Rewrite `src/capturly/dashboard.py`**

Replace the entire file with:

```python
"""Web dashboard server for real-time traffic inspection.

Two serving modes:
  - static entries (tests): full entries held in memory, legacy summaries
  - file mode: a TrafficIndex over traffic_log.jsonl — slim summaries for
    list/stats, single-line offset reads for detail
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from socketserver import ThreadingMixIn
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .traffic_index import TrafficIndex

# Dashboard frontend, served from the packaged HTML asset.
_INDEX_HTML = resources.files("capturly").joinpath("dashboard.html").read_text(encoding="utf-8")

_TRAFFIC_DETAIL_RE = re.compile(r"^/api/traffic/(\d+)$")


def _compute_stats(entries: list) -> dict:
    """Compute summary statistics from full traffic log entries (static mode)."""
    total_requests = len(entries)
    ai_requests = 0
    total_tokens = 0
    models: list = []

    for entry in entries:
        insights = entry.get("ai_insights")
        if not insights:
            continue
        ai_requests += 1

        req = insights.get("request", {})
        model = req.get("model")
        if model and model not in models:
            models.append(model)

        resp = insights.get("response", {})
        usage = resp.get("usage")
        if isinstance(usage, dict):
            total_tokens += usage.get("total_tokens", 0)

    return {
        "total_requests": total_requests,
        "ai_requests": ai_requests,
        "total_tokens": total_tokens,
        "models": sorted(models),
    }


def _summary_entry(entry: dict, index: int) -> dict:
    """Return a lightweight summary of a traffic entry for list views (static mode)."""
    summary = {
        "_index": index,
        "timestamp_ms": entry.get("timestamp_ms"),
        "method": entry.get("method"),
        "path": entry.get("path"),
        "status_code": entry.get("status_code"),
        "request_body_size": entry.get("request_body_size"),
        "response_body_size": entry.get("response_body_size"),
        "source_name": entry.get("source_name"),
        "backend_url": entry.get("backend_url"),
        "duration_ms": entry.get("duration_ms"),
    }
    if "ai_insights" in entry:
        summary["ai_insights"] = entry["ai_insights"]
    if entry.get("sse"):
        summary["sse"] = True
    resp = entry.get("response_body")
    if isinstance(resp, dict) and resp.get("object") == "agui.completion":
        summary["agui"] = True
    # Detect tool calls for badge
    ai = entry.get("ai_insights")
    if ai and isinstance(ai, dict):
        ai_resp = ai.get("response")
        if isinstance(ai_resp, dict) and ai_resp.get("tool_call_names"):
            summary["tools"] = True
    elif isinstance(resp, dict) and resp.get("tool_calls"):
        summary["tools"] = True
    return summary


class _DashboardServer(ThreadingMixIn, HTTPServer):
    """Threaded server so a slow detail parse cannot block list refreshes."""

    daemon_threads = True
    allow_reuse_address = True


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard API and frontend."""

    # Set by create_dashboard_server before serving.
    entries: Optional[list] = []
    traffic_log_path: Optional[str] = None
    index: Optional[TrafficIndex] = None

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def _get_entries(self) -> list:
        """Return static entries (static mode only)."""
        if self.entries is not None:
            return self.entries
        return []

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/traffic":
            self._serve_traffic_list(params)
        elif path == "/api/stats":
            self._serve_stats()
        else:
            match = _TRAFFIC_DETAIL_RE.match(path)
            if match:
                self._serve_traffic_detail(int(match.group(1)))
            else:
                self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/truncate":
            self._handle_truncate()
        else:
            self._send_json({"error": "Not found"}, status=404)

    def _handle_truncate(self):
        """Clear all traffic entries from memory and/or the log file."""
        if self.entries is not None:
            self.entries.clear()
        if self.index is not None:
            self.index.reset()
        if self.traffic_log_path:
            _truncate_traffic_log(self.traffic_log_path)
        self._send_json({"ok": True, "message": "Traffic log truncated"})

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_INDEX_HTML.encode("utf-8"))

    def _serve_traffic_list(self, params: dict):
        ai_only = params.get("ai", [""])[0].lower() == "true"

        if self.index is not None:
            self.index.sync()
            summaries = self.index.summaries()
            if ai_only:
                summaries = [s for s in summaries if "ai" in s]
        else:
            indexed = list(enumerate(self._get_entries()))
            if ai_only:
                indexed = [(i, e) for i, e in indexed if "ai_insights" in e]
            summaries = [_summary_entry(e, i) for i, e in indexed]

        total = len(summaries)

        try:
            limit = int(params.get("limit", ["50"])[0])
        except ValueError:
            limit = 50
        try:
            offset = int(params.get("offset", ["0"])[0])
        except ValueError:
            offset = 0

        page = summaries[offset : offset + limit]
        self._send_json({"total": total, "entries": page})

    def _serve_traffic_detail(self, index: int):
        if self.index is not None:
            self.index.sync()
            entry = self.index.load_entry(index)
            if entry is None:
                self._send_json({"error": "Entry not found"}, status=404)
                return
            self._send_json(entry)
            return
        entries = self._get_entries()
        if index < 0 or index >= len(entries):
            self._send_json({"error": "Entry not found"}, status=404)
            return
        self._send_json(entries[index])

    def _serve_stats(self):
        if self.index is not None:
            self.index.sync()
            self._send_json(self.index.stats())
            return
        self._send_json(_compute_stats(self._get_entries()))

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _truncate_traffic_log(path: str) -> None:
    """Truncate the traffic log file to empty."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.truncate(0)
    except OSError:
        pass


def create_dashboard_server(
    entries: Optional[list] = None,
    host: str = "127.0.0.1",
    port: int = 9090,
    traffic_log_path: Optional[str] = None,
) -> HTTPServer:
    """Create a dashboard HTTP server.

    Provide either a static entries list or a traffic_log_path for live
    file-based reading. In file mode a TrafficIndex incrementally indexes
    the log; list/stats are served from slim summaries and detail reads
    parse a single line at its recorded offset.

    Args:
        entries: Static list of traffic log entry dicts (or None for file mode).
        host: Bind address.
        port: Bind port (0 for random available port in tests).
        traffic_log_path: Path to traffic_log.jsonl for live reading.

    Returns:
        An HTTPServer instance ready to serve_forever().
    """

    class _Handler(DashboardHandler):
        pass

    _Handler.entries = entries
    _Handler.traffic_log_path = traffic_log_path
    _Handler.index = TrafficIndex(traffic_log_path) if traffic_log_path else None

    return _DashboardServer((host, port), _Handler)
```

Note: `_read_traffic_log` is intentionally removed — file mode no longer reads whole files.

- [ ] **Step 4: Run the dashboard test suites**

Run: `python3 -m pytest tests/test_dashboard.py tests/test_dashboard_integration.py tests/test_traffic_index.py -v`
Expected: all PASS (new file-mode tests now pass; all pre-existing dashboard tests unchanged and green)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS (guards against anything importing the removed `_read_traffic_log`)

- [ ] **Step 6: Lint and format**

Run: `python3 -m ruff check src/capturly/dashboard.py tests/test_dashboard_integration.py && python3 -m black src/capturly/dashboard.py tests/test_dashboard_integration.py`
Expected: no errors; re-run tests if black reformats

- [ ] **Step 7: Commit**

```bash
git add src/capturly/dashboard.py tests/test_dashboard_integration.py
git commit -m "perf(dashboard): serve list/stats from offset index, threaded server"
```

---

### Task 3: Frontend — lazy detail sections, slim shape, polling pause

**Files:**
- Modify: `src/capturly/dashboard.html` (script block)
- Test: `tests/frontend/dashboard_js_test.js` (append assertions)

**Interfaces:**
- Consumes: slim list shape from Task 2 (`e.ai` instead of `e.ai_insights` in list responses; detail responses unchanged)
- Produces: `renderDetail(e)` emitting `<details data-section="..." data-rendered="false">` placeholders; global `sectionRenderers` map; `currentDetail` lifecycle; `startPolling()`/`stopPolling()`

- [ ] **Step 1: Write the failing frontend assertions**

Append to `tests/frontend/dashboard_js_test.js` (before the final `if (failures > 0)` block):

```js
// --- renderDetail: lazy sections ---
const lazyEntry = {
  method: 'POST', path: '/v1/chat/completions', status_code: 200,
  timestamp_ms: 1000, duration_ms: 5,
  request_body_size: 10, response_body_size: 20,
  ai_insights: {
    request: { model: 'gpt-4o', message_count: 2, system_prompts: ['You are helpful.'] },
    response: { usage: { total_tokens: 15 } },
  },
  request_body: { model: 'gpt-4o', messages: [
    { role: 'system', content: 'You are helpful.' },
    { role: 'user', content: 'UNIQUE_USER_MESSAGE_MARKER' },
  ] },
  response_body: { choices: [{ message: { role: 'assistant', content: 'hi' } }] },
  request_headers: { 'content-type': 'application/json' },
  response_headers: { 'content-type': 'application/json' },
};
const detailHtml = vm.runInContext(
  'renderDetail(' + JSON.stringify(lazyEntry) + ')', sandbox);
assert(detailHtml.includes('data-rendered="false"'), 'sections start unrendered');
assert(detailHtml.includes('data-section="messages"'), 'messages section present');
assert(detailHtml.includes('data-section="system-prompt"'), 'system prompt section present');
assert(detailHtml.includes('data-section="raw-request"'), 'raw request section present');
assert(!detailHtml.includes('UNIQUE_USER_MESSAGE_MARKER'),
  'message bodies are NOT rendered until expanded');

// --- slim list shape: badges/filters use e.ai ---
assert(vm.runInContext(`(function() {
  var e = { ai: { model: 'gpt-4o' }, method: 'POST', path: '/x', status_code: 200 };
  return matchesFilters === undefined ? false : true;
})()`, sandbox), 'matchesFilters exists');
vm.runInContext(`filters = { method: null, path: '', status: null, source: null, tags: ['ai'] };`, sandbox);
assert(vm.runInContext(
  `matchesFilters({ ai: { model: 'x' }, method: 'POST', path: '/a', status_code: 200 })`,
  sandbox) === true, 'ai tag matches entries with slim ai object');
assert(vm.runInContext(
  `matchesFilters({ method: 'POST', path: '/a', status_code: 200 })`,
  sandbox) === false, 'ai tag does not match entries without ai');
vm.runInContext(`filters = { method: null, path: '', status: null, source: null, tags: [] };`, sandbox);
```

Also update the existing copy-source assertion — replace:

```js
assert(tree.includes('json-copy-src'), 'hidden copy source present');
```

with:

```js
assert(tree.includes('json-copy-src'), 'small trees keep hidden copy source');
```

(behavior unchanged for small trees; Task 4 adds the size cap).

- [ ] **Step 2: Run the harness to verify the new assertions fail**

Run: `node tests/frontend/dashboard_js_test.js src/capturly/dashboard.html`
Expected: FAIL — `data-rendered`/`data-section` assertions fail (current `renderDetail` renders eagerly)

- [ ] **Step 3: Modify `src/capturly/dashboard.html`**

Apply these changes to the `<script>` block:

**3a. Add globals after `var filters = ...`:**

```js
var currentDetail = null;      // full entry while the panel is open
var sectionRenderers = {};     // section id -> fn(entry) returning body html
var refreshTimer = null;
```

**3b. Replace `collapsible` with the lazy version:**

```js
function collapsible(title, sectionId, open) {
  return '<details data-section="' + sectionId + '" data-rendered="false"' + (open ? ' open' : '')
    + '><summary><span>' + title + '</span><button class="copy-btn" onclick="event.preventDefault();copySection(this)" title="Copy">&#x2398;</button></summary>'
    + '<div class="detail-body"><span class="loading">Render on expand…</span></div></details>';
}
```

**3c. Split `renderAIDetail` into an immediate meta part and section renderers. Replace the whole `renderAIDetail` function with:**

```js
function renderAIMeta(e) {
  var ai = e.ai_insights || {};
  var aiReq = ai.request || {};
  var aiRes = ai.response || {};
  var req = (e.request_body && typeof e.request_body === 'object') ? e.request_body : {};
  var res = (e.response_body && typeof e.response_body === 'object') ? e.response_body : {};
  var html = '<div class="ai-meta">';
  html += metaItem('Model', aiReq.model || req.model || '-');
  if (aiRes.usage) {
    var u = aiRes.usage;
    html += metaItem('Tokens', (u.prompt_tokens||0) + ' prompt + ' + (u.completion_tokens||0) + ' completion = ' + (u.total_tokens||0) + ' total');
  }
  if (aiRes.finish_reasons && aiRes.finish_reasons.length) {
    html += metaItem('Finish', aiRes.finish_reasons.join(', '));
  }
  html += metaItem('Stream', req.stream ? 'Yes' : 'No');
  if (aiReq.message_count) html += metaItem('Messages', aiReq.message_count);
  html += '</div>';
  return html;
}

function renderChoices(choices) {
  var respHtml = '';
  choices.forEach(function(choice) {
    var msg = choice.message || {};
    if (msg.content) {
      respHtml += '<div class="msg assistant"><div class="msg-role">assistant</div><div class="msg-content">' + msgContent(msg.content) + '</div>';
      if (msg.tool_calls && msg.tool_calls.length) respHtml += renderToolCalls(msg.tool_calls);
      respHtml += '</div>';
    } else if (msg.tool_calls && msg.tool_calls.length) {
      respHtml += '<div class="msg assistant"><div class="msg-role">assistant (tool calls)</div>' + renderToolCalls(msg.tool_calls) + '</div>';
    }
  });
  return respHtml;
}

function registerAISections(e) {
  var aiReq = (e.ai_insights && e.ai_insights.request) || {};
  var req = (e.request_body && typeof e.request_body === 'object') ? e.request_body : {};
  var res = (e.response_body && typeof e.response_body === 'object') ? e.response_body : {};
  var sysPrompts = aiReq.system_prompts || [];
  if (sysPrompts.length) {
    sectionRenderers['system-prompt'] = function() {
      return '<pre>' + esc(sysPrompts.join('\n---\n')) + '</pre>';
    };
  }
  var tools = req.tools || [];
  if (tools.length) {
    sectionRenderers['tools'] = function() { return renderTools(tools); };
  }
  var messages = (req.messages || []).filter(function(m) { return m.role !== 'system'; });
  if (messages.length) {
    sectionRenderers['messages'] = function() { return messages.map(renderMessage).join(''); };
  }
  var choices = res.choices || [];
  if (choices.length) {
    sectionRenderers['assistant-response'] = function() { return renderChoices(choices); };
  }
  if (e.sse && e.sse_combine_status) {
    sectionRenderers['sse-info'] = function() {
      var sseInfo = 'Combine status: ' + e.sse_combine_status;
      if (e.sse_duration_ms != null) sseInfo += '\nDuration: ' + e.sse_duration_ms + 'ms';
      if (e.sse_error) sseInfo += '\nError: ' + e.sse_error;
      return '<pre>' + esc(sseInfo) + '</pre>';
    };
  }
  sectionRenderers['raw-request'] = function() { return renderJsonTree(e.request_body); };
  sectionRenderers['raw-response'] = function() { return renderJsonTree(e.response_body); };
  return { sysPrompts: sysPrompts, tools: tools, messages: messages, choices: choices };
}

function registerAGUISections(e) {
  var res = (e.response_body && typeof e.response_body === 'object') ? e.response_body : {};
  var run = res.run || {};
  var messages = res.messages || [];
  if (messages.length) {
    sectionRenderers['agui-messages'] = function() {
      return messages.map(function(msg) {
        var role = msg.role || 'assistant';
        return '<div class="msg ' + esc(role) + '"><div class="msg-role">' + esc(role) + '</div><div class="msg-content">' + esc(msg.content || '') + '</div></div>';
      }).join('');
    };
  }
  var toolCalls = res.tool_calls || [];
  if (toolCalls.length) {
    sectionRenderers['agui-tool-calls'] = function() {
      return toolCalls.map(function(tc) {
        var h = '<div class="tool-card"><div class="tool-name">' + esc(tc.name || 'unknown') + '</div>';
        if (tc.arguments != null) h += '<h4>Arguments</h4>' + renderJsonTree(tc.arguments);
        if (tc.result != null) h += '<h4>Result</h4>' + renderJsonTree(tc.result);
        h += '</div>';
        return h;
      }).join('');
    };
  }
  var reasoning = res.reasoning || [];
  if (reasoning.length) {
    sectionRenderers['agui-reasoning'] = function() {
      return reasoning.map(function(r) { return '<pre>' + esc(r.content || '') + '</pre>'; }).join('');
    };
  }
  if (res.state != null) {
    sectionRenderers['agui-state'] = function() { return renderJsonTree(res.state); };
  }
  sectionRenderers['raw-request'] = function() { return renderJsonTree(e.request_body); };
  return { run: run, messages: messages, toolCalls: toolCalls, reasoning: reasoning };
}
```

**3d. Replace `renderDetail` with the lazy version:**

```js
function renderDetail(e) {
  sectionRenderers = {};
  var html = '<h2>' + esc(e.method) + ' ' + esc(e.path) + '</h2>';
  html += renderOverview(e);

  if (e.ai_insights) {
    html += renderAIMeta(e);
    var parts = registerAISections(e);
    if (parts.sysPrompts.length) html += collapsible('&#x1F4DC; System Prompt', 'system-prompt');
    if (parts.tools.length) html += collapsible('&#x1F527; Tools (' + parts.tools.length + ')', 'tools');
    if (parts.messages.length) html += collapsible('&#x1F4AC; Messages (' + parts.messages.length + ')', 'messages');
    if (parts.choices.length) html += collapsible('&#x1F916; Assistant Response', 'assistant-response');
    if (e.sse && e.sse_combine_status) html += collapsible('&#x1F4E1; SSE Info', 'sse-info');
    html += collapsible('Raw Request', 'raw-request');
    html += collapsible('Raw Response', 'raw-response');
  } else if (e.response_body && typeof e.response_body === 'object' && e.response_body.object === 'agui.completion') {
    var res = e.response_body;
    var run = res.run || {};
    html += '<div class="ai-meta">';
    html += metaItem('Protocol', 'AGUI');
    if (run.run_id) html += metaItem('Run ID', run.run_id);
    if (run.thread_id) html += metaItem('Thread ID', run.thread_id);
    html += metaItem('Status', run.status || '-');
    html += metaItem('Events', res.event_count || 0);
    html += '</div>';
    if (run.error) {
      html += '<div class="detail-section"><pre style="color:#f85149">' + esc(run.error) + '</pre></div>';
    }
    var aguiParts = registerAGUISections(e);
    if (aguiParts.messages.length) html += collapsible('&#x1F4AC; Messages (' + aguiParts.messages.length + ')', 'agui-messages');
    if (aguiParts.toolCalls.length) html += collapsible('&#x1F557;&#xFE0F; Tool Calls (' + aguiParts.toolCalls.length + ')', 'agui-tool-calls');
    if (aguiParts.reasoning.length) html += collapsible('&#x1F9E0; Reasoning', 'agui-reasoning');
    if (res.state != null) html += collapsible('&#x1F4E6; State', 'agui-state');
    html += collapsible('Raw Request', 'raw-request');
  } else {
    sectionRenderers['raw-request'] = function() { return renderJsonTree(e.request_body); };
    sectionRenderers['raw-response'] = function() { return renderJsonTree(e.response_body); };
    html += collapsible('&#x2B06;&#xFE0F; Request Body', 'raw-request');
    html += collapsible('&#x2B07;&#xFE0F; Response Body', 'raw-response');
  }

  sectionRenderers['req-headers'] = function() { return renderHeaders(e.request_headers); };
  html += collapsible('Request Headers', 'req-headers');
  sectionRenderers['res-headers'] = function() { return renderHeaders(e.response_headers); };
  html += collapsible('Response Headers', 'res-headers');

  return html;
}
```

**3e. Replace `showDetail` and `closeDetail` (currentDetail lifecycle + polling pause):**

```js
function showDetail(idx) {
  var summary = entries[idx];
  if (!summary) return;
  selectedIdx = summary._index;
  var rows = document.querySelectorAll('#traffic-table tr.clickable');
  for (var r = 0; r < rows.length; r++) {
    rows[r].classList.toggle('selected', r === idx);
  }
  var panel = document.getElementById('detail-panel');
  var overlay = document.getElementById('overlay');
  var content = document.getElementById('detail-content');
  content.innerHTML = '<div class="loading">Loading detail...</div>';
  panel.className = 'detail-panel open';
  overlay.className = 'overlay open';
  stopPolling();
  fetch('/api/traffic/' + summary._index)
    .then(function(res) { return res.json(); })
    .then(function(e) {
      if (e.error) { content.innerHTML = '<div class="loading">Error: ' + esc(e.error) + '</div>'; return; }
      currentDetail = e;
      content.innerHTML = renderDetail(e);
    })
    .catch(function() {
      content.innerHTML = '<div class="loading">Failed to load detail</div>';
    });
}

function closeDetail() {
  document.getElementById('detail-panel').className = 'detail-panel';
  document.getElementById('overlay').className = 'overlay';
  currentDetail = null;
  startPolling();
}
```

**3f. Register the delegated lazy-render listener — add immediately after the `document.addEventListener('keydown', ...)` block:**

```js
(function() {
  var panel = document.getElementById('detail-panel');
  if (!panel || !panel.addEventListener) return;
  panel.addEventListener('toggle', function(ev) {
    var d = ev.target;
    if (!d || d.tagName !== 'DETAILS' || d.getAttribute('data-rendered') === 'true') return;
    var fn = sectionRenderers[d.getAttribute('data-section')];
    if (!fn || !currentDetail) return;
    var body = d.querySelector('.detail-body');
    if (body) body.innerHTML = fn(currentDetail);
    d.setAttribute('data-rendered', 'true');
  }, true);
})();
```

**3g. Slim-shape badges/filters — in `renderTable` replace:**

```js
    if (e.ai_insights) tags.push('<span class="badge ai">AI</span>');
```

with:

```js
    if (e.ai || e.ai_insights) tags.push('<span class="badge ai">AI</span>');
```

and in `matchesFilters` replace:

```js
      if (tag === 'ai' && e.ai_insights) hasTag = true;
```

with:

```js
      if (tag === 'ai' && (e.ai || e.ai_insights)) hasTag = true;
```

(The `e.ai_insights` fallback keeps static-mode compatibility.)

**3h. Replace the polling bootstrap at the end of the script — replace:**

```js
refresh();
setInterval(refresh, 3000);
```

with:

```js
function startPolling() {
  if (refreshTimer == null) refreshTimer = setInterval(refresh, 3000);
}

function stopPolling() {
  if (refreshTimer != null) { clearInterval(refreshTimer); refreshTimer = null; }
}

refresh();
startPolling();
```

**3i. Add a CSS class for the placeholder — inside the `<style>` block, after the `.loading` rule:**

```css
  details .loading { padding: 8px 0; display: block; text-align: left; }
```

- [ ] **Step 4: Run the frontend harness**

Run: `node tests/frontend/dashboard_js_test.js src/capturly/dashboard.html`
Expected: `SMOKE OK` (all assertions pass, old and new)

- [ ] **Step 5: Run the Python suites that guard the served HTML**

Run: `python3 -m pytest tests/test_dashboard.py tests/test_dashboard_frontend.py -v`
Expected: all PASS (`test_served_html_matches_asset_file` validates the packaged asset is what gets served; `test_dashboard_html_has_source_and_duration_ui` checks UI markers)

- [ ] **Step 6: Commit**

```bash
git add src/capturly/dashboard.html tests/frontend/dashboard_js_test.js
git commit -m "perf(dashboard): lazy detail sections, slim list shape, pause polling on detail"
```

---

### Task 4: Frontend — smart truncation (text + JSON tree caps)

**Files:**
- Modify: `src/capturly/dashboard.html` (script block)
- Test: `tests/frontend/dashboard_js_test.js` (append assertions)

**Interfaces:**
- Consumes: lazy sections from Task 3 (`sectionRenderers`, `currentDetail`)
- Produces:
  - `TEXT_LIMIT = 2000`, `JSON_CHILD_LIMIT = 100`, `COPY_SRC_LIMIT = 100000`
  - `renderLongText(text) -> html` (truncated with "Show more" reveal)
  - `detailTexts` / `detailValues` stores with `storeText` / `storeValue`
  - `revealMore(btn)`, `loadMoreJson(rowEl)`
  - `msgContent` truncated via `renderLongText`
  - `renderJsonTree` skips the hidden copy source when pretty JSON exceeds `COPY_SRC_LIMIT`

- [ ] **Step 1: Write the failing frontend assertions**

Append to `tests/frontend/dashboard_js_test.js` (before the final `if (failures > 0)` block):

```js
// --- text truncation ---
const longText = 'x'.repeat(5000);
vm.runInContext('detailTexts = []; currentDetail = {};', sandbox);
const trunc = vm.runInContext(
  'renderLongText(' + JSON.stringify(longText) + ')', sandbox);
assert(trunc.includes('x'.repeat(2000)), 'first 2000 chars rendered');
assert(!trunc.includes('x'.repeat(2001)), 'content beyond limit not rendered');
assert(trunc.includes('Show more (+3000 chars)'), 'show-more button reports remaining chars');
const shortText = vm.runInContext(`renderLongText('short')`, sandbox);
assert(shortText === 'short', 'short text renders untouched');

// --- json tree child cap ---
vm.runInContext('detailValues = [];', sandbox);
const bigArr = vm.runInContext(
  'renderJsonTree({ arr: Array.from({length: 250}, function(_, i) { return i; }) })', sandbox);
assert(bigArr.includes('150 more (load)'), 'children beyond 100 are capped with load row');
assert(bigArr.includes('data-val-idx'), 'load row references stored value');

// --- copy source cap ---
const hugeObj = 'renderJsonTree({ big: ' + JSON.stringify('y'.repeat(150000)) + ' })';
const hugeTree = vm.runInContext(hugeObj, sandbox);
assert(!hugeTree.includes('json-copy-src'), 'huge trees skip the hidden copy source');
const smallTree = vm.runInContext(`renderJsonTree({ a: 1 })`, sandbox);
assert(smallTree.includes('json-copy-src'), 'small trees keep the hidden copy source');

// --- msgContent truncation ---
const msgHtml = vm.runInContext(
  'msgContent(' + JSON.stringify('z'.repeat(4000)) + ')', sandbox);
assert(msgHtml.includes('Show more'), 'message content is truncated');
assert(!msgHtml.includes('z'.repeat(2001)), 'message content capped at limit');
```

- [ ] **Step 2: Run the harness to verify the new assertions fail**

Run: `node tests/frontend/dashboard_js_test.js src/capturly/dashboard.html`
Expected: FAIL — `renderLongText is not defined`

- [ ] **Step 3: Modify `src/capturly/dashboard.html`**

**4a. Add truncation globals and helpers — after the globals added in Task 3 (`var refreshTimer = null;`):**

```js
var TEXT_LIMIT = 2000;
var JSON_CHILD_LIMIT = 100;
var COPY_SRC_LIMIT = 100000;
var detailTexts = [];   // full strings behind truncated renderings
var detailValues = [];  // full values behind capped JSON trees

function storeText(t) { detailTexts.push(t); return detailTexts.length - 1; }
function storeValue(v) { detailValues.push(v); return detailValues.length - 1; }

function renderLongText(text) {
  if (text == null) return '';
  var s = String(text);
  if (s.length <= TEXT_LIMIT) return esc(s);
  var idx = storeText(s);
  return '<span class="trunc-wrap"><span class="trunc-text" data-text-idx="' + idx
    + '" data-shown="' + TEXT_LIMIT + '">' + esc(s.substring(0, TEXT_LIMIT)) + '</span>'
    + '<button class="show-more" onclick="revealMore(this)">Show more (+'
    + (s.length - TEXT_LIMIT) + ' chars)</button></span>';
}

function revealMore(btn) {
  var wrap = btn.parentElement;
  var span = wrap ? wrap.querySelector('.trunc-text') : null;
  if (!span) return;
  var idx = parseInt(span.getAttribute('data-text-idx'), 10);
  var shown = parseInt(span.getAttribute('data-shown'), 10) * 2;
  var full = detailTexts[idx] || '';
  if (shown >= full.length) {
    shown = full.length;
    btn.remove();
  } else {
    btn.textContent = 'Show more (+' + (full.length - shown) + ' chars)';
  }
  span.setAttribute('data-shown', String(shown));
  span.innerHTML = esc(full.substring(0, shown));
}
```

**4b. Add CSS for the reveal button — in the `<style>` block after the rule added in Task 3:**

```css
  .show-more { background: #21262d; border: 1px solid #30363d; color: #58a6ff; cursor: pointer; font-size: 11px; padding: 2px 10px; border-radius: 4px; margin: 6px 0; display: inline-block; }
  .show-more:hover { border-color: #58a6ff; }
  .j-more { cursor: pointer; color: #8b949e; font-style: italic; }
  .j-more:hover { color: #58a6ff; }
```

**4c. Truncate message content — replace `msgContent` with:**

```js
function msgPlainText(content) {
  if (content == null) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map(function(part) {
      if (part.type === 'text') return part.text || '';
      if (part.type === 'image_url') return '[Image: ' + String((part.image_url && part.image_url.url) || '').substring(0, 80) + ']';
      return JSON.stringify(part);
    }).join('\n');
  }
  return JSON.stringify(content, null, 2);
}

function msgContent(content) {
  return renderLongText(msgPlainText(content));
}
```

**4d. Cap JSON tree children and truncate tree strings — replace `jsonValueHtml` with:**

```js
function jsonValueHtml(val, depth, keyHtml) {
  if (val === null || typeof val !== 'object') {
    return '<div class="j-line">' + keyHtml + jsonPrimitiveHtml(val) + '</div>';
  }
  var isArr = Array.isArray(val);
  var open = isArr ? '[' : '{';
  var close = isArr ? ']' : '}';
  var keys = isArr ? null : Object.keys(val);
  var count = isArr ? val.length : keys.length;
  if (count === 0) {
    return '<div class="j-line">' + keyHtml + '<span class="j-brace">' + open + close + '</span></div>';
  }
  var label = count + (isArr ? (count === 1 ? ' item' : ' items') : (count === 1 ? ' key' : ' keys'));
  var html = '<div class="j-node' + (depth < JSON_EXPAND_DEPTH ? '' : ' collapsed') + '">';
  html += '<div class="j-row" onclick="toggleJsonNode(this)"><span class="j-toggle"></span>' + keyHtml
    + '<span class="j-brace">' + open + '</span><span class="j-preview"> &#x2026; ' + label + ' ' + close + '</span></div>';
  html += '<div class="j-children">';
  var limit = Math.min(count, JSON_CHILD_LIMIT);
  html += jsonChildrenHtml(val, isArr, keys, 0, limit, depth);
  if (count > limit) {
    var vidx = storeValue(val);
    html += '<div class="j-line j-more" data-val-idx="' + vidx + '" data-offset="' + limit
      + '" data-depth="' + depth + '" onclick="loadMoreJson(this)">'
      + '<span class="j-preview">&#x2026; ' + (count - limit) + ' more (load)</span></div>';
  }
  html += '</div>';
  html += '<div class="j-line j-closeline"><span class="j-brace">' + close + '</span></div>';
  html += '</div>';
  return html;
}

function jsonChildrenHtml(val, isArr, keys, from, to, depth) {
  var html = '';
  for (var i = from; i < to; i++) {
    var k = isArr ? i : keys[i];
    var v = isArr ? val[i] : val[keys[i]];
    var childKey = isArr ? '' : '<span class="j-key">"' + esc(String(k)) + '"</span>: ';
    html += jsonValueHtml(v, depth + 1, childKey);
  }
  return html;
}

function loadMoreJson(rowEl) {
  var idx = parseInt(rowEl.getAttribute('data-val-idx'), 10);
  var offset = parseInt(rowEl.getAttribute('data-offset'), 10);
  var depth = parseInt(rowEl.getAttribute('data-depth'), 10);
  var val = detailValues[idx];
  if (val == null) return;
  var isArr = Array.isArray(val);
  var keys = isArr ? null : Object.keys(val);
  var count = isArr ? val.length : keys.length;
  var to = Math.min(offset + JSON_CHILD_LIMIT, count);
  rowEl.insertAdjacentHTML('beforebegin', jsonChildrenHtml(val, isArr, keys, offset, to, depth));
  if (to >= count) {
    rowEl.remove();
  } else {
    rowEl.setAttribute('data-offset', String(to));
    rowEl.querySelector('.j-preview').innerHTML = '&#x2026; ' + (count - to) + ' more (load)';
  }
}
```

**4e. Truncate long string values inside the tree — replace `jsonPrimitiveHtml` with:**

```js
function jsonPrimitiveHtml(val) {
  if (val === null) return '<span class="j-null">null</span>';
  var t = typeof val;
  if (t === 'string') {
    if (val.length > TEXT_LIMIT) return '<span class="j-str">"' + renderLongText(val) + '"</span>';
    return '<span class="j-str">"' + esc(val) + '"</span>';
  }
  if (t === 'number') return '<span class="j-num">' + String(val) + '</span>';
  if (t === 'boolean') return '<span class="j-bool">' + String(val) + '</span>';
  return esc(String(val));
}
```

**4f. Cap the hidden copy source — replace `renderJsonTree` with:**

```js
function renderJsonTree(obj) {
  if (obj == null || typeof obj !== 'object') {
    return '<pre>' + escJson(obj) + '</pre>';
  }
  var copySrc;
  try { copySrc = JSON.stringify(obj, null, 2); } catch (e) { copySrc = String(obj); }
  // Only small trees carry the hidden full-JSON copy source; for huge bodies
  // copy falls back to whatever is currently rendered.
  var copyHtml = copySrc.length <= COPY_SRC_LIMIT
    ? '<pre class="json-copy-src">' + esc(copySrc) + '</pre>' : '';
  return '<div class="json-tree">' + copyHtml + jsonValueHtml(obj, 0, '') + '</div>';
}
```

**4g. Truncate the System Prompt section — in `registerAISections` (added in Task 3), replace:**

```js
    sectionRenderers['system-prompt'] = function() {
      return '<pre>' + esc(sysPrompts.join('\n---\n')) + '</pre>';
    };
```

with:

```js
    sectionRenderers['system-prompt'] = function() {
      return '<pre>' + renderLongText(sysPrompts.join('\n---\n')) + '</pre>';
    };
```

**4h. Reset per-detail stores when opening a detail — in `showDetail`, inside the fetch `.then`, replace:**

```js
      currentDetail = e;
      content.innerHTML = renderDetail(e);
```

with:

```js
      currentDetail = e;
      detailTexts = [];
      detailValues = [];
      content.innerHTML = renderDetail(e);
```

- [ ] **Step 4: Run the frontend harness**

Run: `node tests/frontend/dashboard_js_test.js src/capturly/dashboard.html`
Expected: `SMOKE OK` (all assertions pass)

- [ ] **Step 5: Run Python suites guarding the HTML**

Run: `python3 -m pytest tests/test_dashboard.py tests/test_dashboard_frontend.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/capturly/dashboard.html tests/frontend/dashboard_js_test.js
git commit -m "perf(dashboard): smart truncation for long text and JSON trees"
```

---

### Task 5: Verify against the real 64 MB log

**Files:**
- None modified (measurement only; throwaway script in /tmp)

**Interfaces:**
- Consumes: everything from Tasks 1-4
- Produces: measured numbers confirming the spec's performance goals

- [ ] **Step 1: Measure server-side index + API latency**

Run (uses the real user log read-only):

```bash
python3 - <<'EOF'
import json, time, urllib.request
from capturly import dashboard
from capturly.traffic_index import TrafficIndex
import threading

LOG = "/Users/chauv/vibe/authoring-service-core/vibecoding-mcp/capturly-recordings/traffic_log.jsonl"

idx = TrafficIndex(LOG)
t0 = time.perf_counter()
idx.sync()
t1 = time.perf_counter()
print(f"initial index pass: {t1 - t0:.2f}s for {idx.count()} entries")

t0 = time.perf_counter()
idx.sync()
t1 = time.perf_counter()
print(f"no-op sync: {(t1 - t0) * 1000:.2f}ms")

server = dashboard.create_dashboard_server(entries=None, host="127.0.0.1", port=0, traffic_log_path=LOG)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

def timed(path):
    t0 = time.perf_counter()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as r:
        body = r.read()
    return (time.perf_counter() - t0) * 1000, len(body)

ms, size = timed("/api/traffic?limit=200")
print(f"first /api/traffic: {ms:.0f}ms, payload {size / 1024:.0f}KB")
ms, size = timed("/api/traffic?limit=200")
print(f"second /api/traffic: {ms:.0f}ms, payload {size / 1024:.0f}KB")
ms, size = timed("/api/stats")
print(f"/api/stats: {ms:.0f}ms")

# Largest entry detail (index 0-4 contain the 12MB ones; find the biggest)
sizes = [(i, s.get("response_body_size") or 0) for i, s in enumerate(idx.summaries())]
biggest = max(sizes, key=lambda x: x[1])[0]
ms, size = timed(f"/api/traffic/{biggest}")
print(f"detail of biggest entry (#{biggest}): {ms:.0f}ms, payload {size / (1024*1024):.1f}MB")
server.shutdown()
EOF
```

Expected (spec goals): initial index pass < 3 s; second `/api/traffic` < 100 ms with payload < ~50 KB; detail of the biggest entry parses in well under 1 s of server time. Record the actual numbers in the commit message of Task 6.

- [ ] **Step 2: Browser smoke check (manual)**

Start the dashboard against the real log:

```bash
python3 -c "
import threading, time
from capturly import dashboard
server = dashboard.create_dashboard_server(entries=None, host='127.0.0.1', port=9191,
    traffic_log_path='/Users/chauv/vibe/authoring-service-core/vibecoding-mcp/capturly-recordings/traffic_log.jsonl')
threading.Thread(target=server.serve_forever, daemon=True).start()
print('dashboard: http://127.0.0.1:9191')
time.sleep(600)
" &
open http://127.0.0.1:9191
```

Verify manually: list loads quickly; filters work; clicking a 12 MB entry opens the panel instantly; expanding Messages/Raw Request renders with truncation and "Show more"/"load" controls; no tab freeze. Then kill the background process.

If anything fails the spec goals, stop and report before continuing.

- [ ] **Step 3: No commit for this task** (measurement only)

---

### Task 6: Version bump, full verification, PR

**Files:**
- Modify: `pyproject.toml` (version)

**Interfaces:**
- Consumes: all previous tasks merged on `feat/dashboard-performance`
- Produces: version 0.7.0, green CI-equivalent checks, open PR against `main`

- [ ] **Step 1: Bump version**

In `pyproject.toml` change:

```toml
version = "0.6.0"
```

to:

```toml
version = "0.7.0"
```

- [ ] **Step 2: Full verification**

Run: `python3 -m pytest tests/ -v && python3 -m ruff check src/ tests/ && python3 -m black --check src/ tests/ && python3 -m mypy src/capturly/ || true`

Expected: all pytest tests pass; ruff clean; black clean. (mypy is advisory in this repo — note failures but do not block unless they are in files this change touched.)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.7.0"
```

- [ ] **Step 4: Push and open the PR**

```bash
gh auth switch --user vikrant82
git push -u origin feat/dashboard-performance
gh pr create --base main --head feat/dashboard-performance \
  --title "perf(dashboard): offset index + lazy rendering for large traffic logs" \
  --body "Fixes dashboard unusability with large traffic logs (64MB+ files, multi-MB entries).

- New TrafficIndex: one-time scan + incremental tail of the JSONL log; list/stats served from slim in-memory summaries; detail reads parse a single line at its byte offset
- Threaded dashboard server
- Frontend: detail sections render only on expand; long text truncated at 2000 chars with progressive reveal; JSON trees capped at 100 children per batch; polling pauses while detail is open
- List payloads no longer carry full ai_insights (system prompts)

Measured against a real 64MB / 47-entry log: <numbers from Task 5>

Spec: docs/superpowers/specs/2026-08-08-dashboard-performance-design.md"
```

Return the PR URL.
