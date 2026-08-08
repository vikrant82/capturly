"""Incremental offset index over the JSONL traffic log.

The dashboard needs fast list/stats/detail access to a traffic log that grows
without bound. TrafficIndex scans the file once, remembering each entry's byte
offset and a slim summary; subsequent syncs parse only newly appended bytes.
Full entry bodies are never retained — only offsets and summaries stay resident.
"""

import json
import os
import threading
from typing import Optional


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
                self._entries.append({"offset": cursor, "length": line_len, "summary": summary})
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
