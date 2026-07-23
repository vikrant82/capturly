"""Backend request construction, forwarding, and raw response writing."""

import json
import urllib.request


def build_request(handler, method, path, body):
    """Build a backend request while preserving applicable client headers."""
    full_url = handler.backend_url + path
    request = urllib.request.Request(full_url, data=body if body else None, method=method)
    for header_name, header_value in handler.headers.items():
        if header_name.lower() not in ["host", "connection"]:
            request.add_header(header_name, header_value)
    return request


def forward_request(handler, method, path, body):
    """Open a backend request using the request's normal or SSE timeout."""
    request = build_request(handler, method, path, body)
    return urllib.request.urlopen(request, timeout=handler._backend_timeout_seconds())


def respond_raw(handler, body, status, headers=None):
    """Send raw response bytes while replacing transport length headers."""
    handler.send_response(status)
    if headers:
        for name, value in headers.items():
            if name.lower() not in ["content-length", "transfer-encoding"]:
                handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def respond_json(handler, data, status=200):
    """Send a JSON response."""
    response = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(response)))
    handler.end_headers()
    handler.wfile.write(response)


def respond_error(handler, message, status):
    """Send a JSON error response."""
    respond_json(handler, {"error": message}, status)


# Private names retained from the POC while the implementation lives in this module.
_build_request = build_request
_forward_request = forward_request
_respond_raw = respond_raw
_respond_json = respond_json
_respond_error = respond_error
