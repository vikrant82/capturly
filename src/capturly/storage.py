"""On-disk response recordings and traffic-log persistence."""

import hashlib
import json
import os
import threading
import time

from . import utils

RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "mock-recordings")
NON_CACHEABLE_STATUS_CODES = {504}


def atomic_write_json(file_path, data, **dump_kwargs):
    """Publish a complete JSON file without exposing partial writes to readers."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp_path = f"{file_path}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump_kwargs)
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        finally:
            raise


def get_cache_key(method, path, body):
    """Generate a cache key from the request method, path, and body."""
    content = f"{method}:{path}:{hashlib.md5(body).hexdigest()}"
    return hashlib.sha256(content.encode()).hexdigest()


def save_recording(handler, method, path, body, status_code, headers, response_body):
    """Save a proxied response using the handler's request logging interface."""
    if status_code in NON_CACHEABLE_STATUS_CODES:
        handler.log_message(f"⏭️ Skipping recording for non-cacheable status: {status_code}")
        return

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    cache_key = get_cache_key(method, path, body)
    recording_file = os.path.join(RECORDINGS_DIR, f"{cache_key}.json")
    response_str, body_encoding = utils.decode_or_base64(response_body)

    recording = {
        "method": method,
        "path": path,
        "request_body_size": len(body),
        "status_code": status_code,
        "response_headers": {
            k: v
            for k, v in headers.items()
            if k.lower() not in ["date", "server", "connection"]
        },
        "response_body": response_str,
        "body_encoding": body_encoding,
        "cache_key": cache_key,
    }

    atomic_write_json(recording_file, recording, indent=2)
    handler.log_message(f"💾 Saved recording: {cache_key[:16]}...")


def read_traffic_log_entries():
    """Read current traffic-log entries from disk."""
    log_file = os.path.join(RECORDINGS_DIR, "traffic_log.json")
    if not os.path.exists(log_file):
        return []

    with open(log_file, encoding="utf-8") as f:
        content = f.read()
        if not content.strip():
            return []
        return json.loads(content)


def write_traffic_log_entries(entries):
    """Atomically publish traffic-log JSON entries."""
    log_file = os.path.join(RECORDINGS_DIR, "traffic_log.json")
    atomic_write_json(log_file, entries, ensure_ascii=False, indent=2)


def enqueue_traffic_log_entry(handler, entry):
    """Queue a traffic-log write, falling back to synchronous persistence."""
    logger = handler.traffic_logger
    if logger:
        logger.enqueue(entry)
        return

    with handler.log_file_lock:
        try:
            entries = read_traffic_log_entries()
        except (json.JSONDecodeError, OSError):
            entries = []
        entries.append(entry)
        write_traffic_log_entries(entries)


def enqueue_sse_event_log(handler, event_log_file, sequence, event_lines):
    """Queue an SSE event log write, falling back to synchronous persistence."""
    logger = handler.traffic_logger
    if logger:
        logger.enqueue_sse_event(event_log_file, sequence, event_lines)
        return

    handler._log_sse_event(event_log_file, sequence, event_lines)


# Private names retained from the POC while the implementation lives in this module.
_atomic_write_json = atomic_write_json
_get_cache_key = get_cache_key
_save_recording = save_recording
_read_traffic_log_entries = read_traffic_log_entries
_write_traffic_log_entries = write_traffic_log_entries
