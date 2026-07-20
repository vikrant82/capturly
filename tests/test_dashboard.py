"""Tests for the dashboard backend API."""

import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request

from capturly import dashboard


def _start_dashboard(entries, port=0):
    """Start a dashboard server with given entries, return (server, port)."""
    server = dashboard.create_dashboard_server(entries, host="127.0.0.1", port=port)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


def _get_json(port, path):
    """Fetch a JSON response from the dashboard."""
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


SAMPLE_ENTRIES = [
    {
        "timestamp_ms": 1000,
        "method": "POST",
        "path": "/v1/chat/completions",
        "status_code": 200,
        "request_body_size": 100,
        "response_body_size": 200,
        "ai_insights": {
            "request": {"model": "gpt-4", "system_prompts": ["Be helpful"], "message_count": 2},
            "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        },
    },
    {
        "timestamp_ms": 2000,
        "method": "GET",
        "path": "/api/users",
        "status_code": 200,
        "request_body_size": 0,
        "response_body_size": 500,
    },
    {
        "timestamp_ms": 3000,
        "method": "POST",
        "path": "/v1/chat/completions",
        "status_code": 200,
        "request_body_size": 150,
        "response_body_size": 300,
        "ai_insights": {
            "request": {"model": "gpt-4", "system_prompts": [], "message_count": 3},
            "response": {"usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}},
        },
    },
]


def test_api_traffic_list():
    """GET /api/traffic returns all entries with metadata."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        data = _get_json(port, "/api/traffic")
        assert data["total"] == 3
        assert len(data["entries"]) == 3
        assert data["entries"][0]["path"] == "/v1/chat/completions"
        assert data["entries"][0]["method"] == "POST"
        assert data["entries"][1]["path"] == "/api/users"
    finally:
        server.shutdown()


def test_api_traffic_list_pagination():
    """GET /api/traffic?limit=2&offset=1 returns paginated results."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        data = _get_json(port, "/api/traffic?limit=2&offset=1")
        assert data["total"] == 3
        assert len(data["entries"]) == 2
        assert data["entries"][0]["path"] == "/api/users"
    finally:
        server.shutdown()


def test_api_traffic_detail():
    """GET /api/traffic/0 returns full entry detail."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        data = _get_json(port, "/api/traffic/0")
        assert data["path"] == "/v1/chat/completions"
        assert data["ai_insights"]["request"]["model"] == "gpt-4"
    finally:
        server.shutdown()


def test_api_traffic_detail_not_found():
    """GET /api/traffic/99 returns 404."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        url = f"http://127.0.0.1:{port}/api/traffic/99"
        try:
            urllib.request.urlopen(url, timeout=5)
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


def test_api_stats():
    """GET /api/stats returns summary statistics."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        data = _get_json(port, "/api/stats")
        assert data["total_requests"] == 3
        assert data["ai_requests"] == 2
        assert data["total_tokens"] == 45  # 15 + 30
        assert data["models"] == ["gpt-4"]
    finally:
        server.shutdown()


def test_api_traffic_filter_ai():
    """GET /api/traffic?ai=true returns only AI traffic."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        data = _get_json(port, "/api/traffic?ai=true")
        assert data["total"] == 2
        assert all("ai_insights" in e for e in data["entries"])
    finally:
        server.shutdown()


def test_dashboard_serves_html():
    """GET / returns HTML content."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
            assert "<!DOCTYPE html>" in content or "<html" in content
            assert resp.headers.get("Content-Type", "").startswith("text/html")
    finally:
        server.shutdown()
