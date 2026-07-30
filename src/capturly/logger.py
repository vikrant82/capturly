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
