"""On-disk response recordings and traffic-log persistence."""

import hashlib
import json
import os
import threading
import time

from . import utils

RECORDINGS_DIR = None


def get_recordings_dir():
    """Get the recordings directory, creating it if needed.

    Priority:
    1. CAPTURLY_RECORDINGS_DIR environment variable
    2. ./capturly-recordings in current working directory
    """
    global RECORDINGS_DIR

    if RECORDINGS_DIR is None:
        env_dir = os.environ.get("CAPTURLY_RECORDINGS_DIR")
        if env_dir:
            RECORDINGS_DIR = env_dir
        else:
            RECORDINGS_DIR = os.path.join(os.getcwd(), "capturly-recordings")

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    return RECORDINGS_DIR


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

    recordings_dir = get_recordings_dir()
    cache_key = get_cache_key(method, path, body)
    recording_file = os.path.join(recordings_dir, f"{cache_key}.json")
    response_str, body_encoding = utils.decode_or_base64(response_body)

    recording = {
        "method": method,
        "path": path,
        "request_body_size": len(body),
        "status_code": status_code,
        "response_headers": {
            k: v for k, v in headers.items() if k.lower() not in ["date", "server", "connection"]
        },
        "response_body": response_str,
        "body_encoding": body_encoding,
        "cache_key": cache_key,
    }

    atomic_write_json(recording_file, recording, indent=2)
    handler.log_message(f"💾 Saved recording: {cache_key[:16]}...")


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

