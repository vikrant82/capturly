"""Tests for the proxy request/response helpers."""

import json
from unittest.mock import Mock

from capturly import proxy


def test_build_request_preserves_headers():
    """build_request preserves client headers except Host and Connection."""
    handler = Mock()
    handler.backend_url = "https://api.example.com"
    handler.headers = {
        "Authorization": "Bearer token123",
        "Content-Type": "application/json",
        "Host": "localhost:9999",
        "Connection": "keep-alive",
    }

    request = proxy.build_request(handler, "POST", "/v1/chat", b'{"test": true}')

    assert request.get_full_url() == "https://api.example.com/v1/chat"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer token123"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Host") is None  # Should be filtered
    assert request.get_header("Connection") is None  # Should be filtered


def test_respond_json():
    """respond_json sends proper JSON response with headers."""
    handler = Mock()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.wfile = Mock()

    data = {"status": "ok", "count": 42}
    proxy.respond_json(handler, data, status=201)

    handler.send_response.assert_called_once_with(201)
    assert handler.send_header.call_count == 2
    handler.end_headers.assert_called_once()
    handler.wfile.write.assert_called_once()

    # Verify JSON was written
    written_data = handler.wfile.write.call_args[0][0]
    parsed = json.loads(written_data.decode())
    assert parsed == data
