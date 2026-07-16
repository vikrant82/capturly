"""Asynchronous traffic and SSE event persistence."""

import os
import queue
import sys
import threading

from .storage import RECORDINGS_DIR


class AsyncTrafficLogger:
    """Serializes traffic and SSE event file writes off the request path."""

    def __init__(self, handler_cls):
        self.handler_cls = handler_cls
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="traffic-log-writer", daemon=True)
        self.handler = object.__new__(handler_cls)
        self.handler.log_message = lambda *args, **kwargs: None
        self.log_file = os.path.join(RECORDINGS_DIR, "traffic_log.json")
        self.thread.start()

    def enqueue(self, entry):
        self.queue.put(("entry", entry))

    def enqueue_sse_event(self, event_log_file, sequence, event_lines):
        self.queue.put(("sse_event", (event_log_file, sequence, list(event_lines))))

    def stop(self):
        self.queue.put(("stop", None))
        self.thread.join(timeout=5)

    def _run(self):
        entries = self._load_entries()
        if entries is None:
            entries = []

        while True:
            kind, entry = self.queue.get()
            if kind == "stop":
                self.queue.task_done()
                self._drain(entries)
                return

            if kind == "entry":
                entries = self._refresh_entries_from_disk(entries)
                entries.append(entry)
                self._write_entries(entries)
            elif kind == "sse_event":
                event_log_file, sequence, event_lines = entry
                self._write_sse_event(event_log_file, sequence, event_lines)

            self.queue.task_done()

    def _drain(self, entries):
        while True:
            try:
                kind, entry = self.queue.get_nowait()
            except queue.Empty:
                return

            if kind == "entry":
                entries = self._refresh_entries_from_disk(entries)
                entries.append(entry)
                self._write_entries(entries)
            elif kind == "sse_event":
                event_log_file, sequence, event_lines = entry
                self._write_sse_event(event_log_file, sequence, event_lines)
            self.queue.task_done()

    def _load_entries(self):
        try:
            return self.handler._read_traffic_log_entries()
        except (ValueError, OSError):
            return None

    def _refresh_entries_from_disk(self, entries):
        """Honor external cleanup so deleted logs are not resurrected from memory."""
        if os.path.exists(self.log_file):
            return entries
        return []

    def _write_entries(self, entries):
        try:
            self.handler._write_traffic_log_entries(entries)
        except Exception as e:
            sys.stderr.write(f"[LOG] Failed to write traffic_log.json: {e}\n")

    def _write_sse_event(self, event_log_file, sequence, event_lines):
        try:
            self.handler._log_sse_event(event_log_file, sequence, event_lines)
        except Exception as e:
            sys.stderr.write(f"[LOG] Failed to write SSE event log: {e}\n")
