"""Hybrid mode: replay cached responses and proxy cache misses."""

import os

from .. import storage


def hybrid_request(handler, method, path, body):
    """Replay a cached response or record and proxy a cache miss."""
    cache_key = "jwks_static" if "/oauth2/jwks" in path else storage.get_cache_key(method, path, body)
    recording_file = os.path.join(storage.RECORDINGS_DIR, f"{cache_key}.json")
    sse_recording_file = os.path.join(storage.RECORDINGS_DIR, f"{cache_key}.sse")

    if os.path.exists(sse_recording_file):
        handler.log_message("⚡ SSE cache hit - proxying to backend")
        handler._record_and_proxy(method, path, body)
    elif os.path.exists(recording_file):
        handler.log_message("⚡ Cache HIT - replaying")
        handler._replay_saved_response(method, path, body)
    else:
        handler.log_message("📡 Cache MISS - proxying to backend")
        handler._record_and_proxy(method, path, body)


_hybrid_request = hybrid_request
