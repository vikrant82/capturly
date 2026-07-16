"""Replay mode: return saved responses without backend I/O."""

import base64
import json
import os
import time

from .. import proxy, storage


def replay_saved_response(handler, method, path, body):
    """Return the response saved for a request, if one exists."""
    cache_key = "jwks_static" if "/oauth2/jwks" in path else storage.get_cache_key(method, path, body)
    recording_file = os.path.join(storage.RECORDINGS_DIR, f"{cache_key}.json")
    sse_recording_file = os.path.join(storage.RECORDINGS_DIR, f"{cache_key}.sse")

    if not os.path.exists(recording_file) and not os.path.exists(sse_recording_file):
        handler.log_message(f"✗ No recording found for {method} {path}")
        handler._respond_json(
            {"error": "No recording found", "hint": "Run in RECORD mode first"}, 404
        )
        return

    if os.path.exists(sse_recording_file):
        handler.log_message(
            "✓ SSE endpoint marker found (replay cannot emit recorded events yet)"
        )
        sse_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        proxy.respond_raw(handler, b"", 200, sse_headers)
        return

    try:
        with open(recording_file) as f:
            recording = json.load(f)

        if handler.replay_delay_ms > 0:
            time.sleep(handler.replay_delay_ms / 1000)

        response_body = recording["response_body"].encode("utf-8")
        if recording.get("body_encoding") == "base64":
            response_body = base64.b64decode(recording["response_body"])

        handler.log_message(
            f"✓ Replaying: {recording['method']} {recording['path'][:50]} (res: {len(response_body)} bytes)"
        )
        proxy.respond_raw(
            handler, response_body, recording["status_code"], recording["response_headers"]
        )

    except Exception as e:
        handler.log_message(f"✗ Replay error: {e}")
        handler._respond_error(f"Failed to replay: {e}", 500)


_replay_saved_response = replay_saved_response
