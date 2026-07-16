"""Shared request, response, and traffic-log helpers."""

import base64
import gzip
import json

DEFAULT_BACKEND_TIMEOUT_SECONDS = 60 * 5
SSE_BACKEND_TIMEOUT_SECONDS = 60 * 60 * 24


def decode_or_base64(raw_bytes):
    """Decode bytes as UTF-8; fall back to base64 for binary content."""
    try:
        return raw_bytes.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return base64.b64encode(raw_bytes).decode("utf-8"), "base64"


def decode_text_lossy(raw_bytes):
    """Decode bytes to readable text without base64-encoding binary data."""
    return raw_bytes.decode("utf-8", errors="replace")


def get_header_value(headers, name):
    """Get a header value case-insensitively from a dict-like object."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def decode_body_for_log(raw_bytes, headers):
    """Decode a body for human-readable logs, including gzip payloads."""
    decoded_bytes = raw_bytes
    content_encoding = get_header_value(headers, "Content-Encoding").lower()
    if "gzip" in content_encoding:
        try:
            decoded_bytes = gzip.decompress(raw_bytes)
        except Exception:
            decoded_bytes = raw_bytes

    text = decoded_bytes.decode("utf-8", errors="replace")
    content_type = get_header_value(headers, "Content-Type").lower()
    if "application/json" in content_type:
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except Exception:
            return text

    return text


def body_for_log_entry(raw_bytes, headers):
    """Return structured JSON for JSON payloads, otherwise readable text."""
    body_text = decode_body_for_log(raw_bytes, headers)
    content_type = get_header_value(headers, "Content-Type").lower()

    if "application/json" in content_type:
        try:
            return json.loads(body_text)
        except Exception:
            return body_text

    return body_text


def is_sse_response(headers):
    """Return whether response headers identify a Server-Sent Events stream."""
    content_type = get_header_value(headers, "Content-Type").lower()
    return "text/event-stream" in content_type


def is_sse_request(headers):
    """Return whether request headers ask for a Server-Sent Events stream."""
    accept = get_header_value(headers, "Accept").lower()
    return "text/event-stream" in accept


def backend_timeout_seconds(headers):
    """Use a longer timeout for SSE streams than for normal proxied calls."""
    if is_sse_request(headers):
        return SSE_BACKEND_TIMEOUT_SECONDS
    return DEFAULT_BACKEND_TIMEOUT_SECONDS


# Private names retained from the POC while the implementation lives in this module.
_decode_or_base64 = decode_or_base64
_decode_text_lossy = decode_text_lossy
_get_header_value = get_header_value
_decode_body_for_log = decode_body_for_log
_body_for_log_entry = body_for_log_entry
_is_sse_response = is_sse_response
_is_sse_request = is_sse_request
_backend_timeout_seconds = backend_timeout_seconds
