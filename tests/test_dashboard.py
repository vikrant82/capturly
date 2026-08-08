"""Tests for the dashboard backend API."""

import json
import os
import threading
import urllib.error
import urllib.request

from capturly import dashboard, storage


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
            "response": {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            },
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
            "response": {
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
            },
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


def test_api_traffic_newest_first_pages():
    """GET /api/traffic?page=N windows backward from the newest entries."""
    entries = [
        {"timestamp_ms": 1000 * (i + 1), "method": "GET", "path": f"/{i}", "status_code": 200}
        for i in range(5)
    ]
    server, port = _start_dashboard(entries)
    try:
        data = _get_json(port, "/api/traffic?limit=2&page=0")
        assert data["total"] == 5
        assert [e["path"] for e in data["entries"]] == ["/3", "/4"]
        data = _get_json(port, "/api/traffic?limit=2&page=1")
        assert [e["path"] for e in data["entries"]] == ["/1", "/2"]
        data = _get_json(port, "/api/traffic?limit=2&page=2")
        assert [e["path"] for e in data["entries"]] == ["/0"]
        data = _get_json(port, "/api/traffic?limit=2&page=3")
        assert data["total"] == 5
        assert data["entries"] == []
    finally:
        server.shutdown()


def test_file_mode_newest_first_pages(temp_recordings_dir):
    """File-mode (TrafficIndex) dashboard pages from the newest entries too."""
    for i in range(5):
        storage.append_traffic_log_entry(
            {
                "timestamp_ms": 1000 * (i + 1),
                "method": "GET",
                "path": f"/{i}",
                "status_code": 200,
            }
        )
    log_path = os.path.join(str(temp_recordings_dir), storage.TRAFFIC_LOG_FILENAME)
    server = dashboard.create_dashboard_server(
        entries=None, host="127.0.0.1", port=0, traffic_log_path=log_path
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data = _get_json(port, "/api/traffic?limit=2&page=0")
        assert data["total"] == 5
        assert [e["path"] for e in data["entries"]] == ["/3", "/4"]
        data = _get_json(port, "/api/traffic?limit=2&page=1")
        assert [e["path"] for e in data["entries"]] == ["/1", "/2"]
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
    """GET / returns HTML content with traffic viewer UI."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
            assert "<!DOCTYPE html>" in content
            assert resp.headers.get("Content-Type", "").startswith("text/html")
            # Key UI elements
            assert "traffic-table" in content
            assert "stats" in content.lower()
            assert "/api/traffic" in content
            assert "/api/stats" in content
    finally:
        server.shutdown()


def test_dashboard_html_has_pagination_ui():
    """The served HTML includes the newest-first pagination controls."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
            assert 'id="page-newer"' in content
            assert 'id="page-older"' in content
            assert 'id="page-info"' in content
            assert "pageWindow" in content
            assert "&page=" in content
    finally:
        server.shutdown()


def test_summary_entry_includes_source_and_duration():
    """_summary_entry surfaces source_name, backend_url, and duration_ms."""
    entry = {
        "timestamp_ms": 1000,
        "method": "GET",
        "path": "/a",
        "status_code": 200,
        "source_name": "adoption",
        "backend_url": "https://a.example.com",
        "duration_ms": 42,
    }
    summary = dashboard._summary_entry(entry, 0)
    assert summary["source_name"] == "adoption"
    assert summary["backend_url"] == "https://a.example.com"
    assert summary["duration_ms"] == 42


def test_summary_entry_tool_results_from_roles():
    """_summary_entry tags entries whose request carried tool/function results."""
    base = {
        "timestamp_ms": 1000,
        "method": "POST",
        "path": "/v1/chat/completions",
        "status_code": 200,
    }
    with_tool = {
        **base,
        "ai_insights": {"request": {"roles": ["system", "user", "assistant", "tool"]}},
    }
    assert dashboard._summary_entry(with_tool, 0)["tool_results"] is True

    legacy_function = {
        **base,
        "ai_insights": {"request": {"roles": ["system", "user", "function"]}},
    }
    assert dashboard._summary_entry(legacy_function, 0)["tool_results"] is True

    plain = {**base, "ai_insights": {"request": {"roles": ["system", "user"]}}}
    assert "tool_results" not in dashboard._summary_entry(plain, 0)
    assert "tool_results" not in dashboard._summary_entry(base, 0)


def test_api_traffic_tool_results_flag(temp_recordings_dir):
    """File-mode summaries expose tool_results for requests carrying tool messages."""
    storage.append_traffic_log_entry(
        {
            "timestamp_ms": 1000,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 200,
            "ai_insights": {
                "request": {"roles": ["system", "user", "assistant", "tool", "tool"]},
                "response": {"tool_call_names": ["search"]},
            },
        }
    )
    storage.append_traffic_log_entry(
        {
            "timestamp_ms": 2000,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 200,
            "ai_insights": {"request": {"roles": ["system", "user"]}},
        }
    )
    log_path = os.path.join(str(temp_recordings_dir), storage.TRAFFIC_LOG_FILENAME)
    server = dashboard.create_dashboard_server(
        entries=None, host="127.0.0.1", port=0, traffic_log_path=log_path
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data = _get_json(port, "/api/traffic")
        by_ts = {e["timestamp_ms"]: e for e in data["entries"]}
        assert by_ts[1000]["tool_results"] is True
        assert by_ts[1000]["tools"] is True  # response made tool calls; request returns results
        assert "tool_results" not in by_ts[2000]
    finally:
        server.shutdown()


def test_dashboard_html_has_tool_results_filter():
    """The served HTML includes the Tool Results chip and badge styling."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
            assert 'data-tag="tool_results"' in content
            assert ">Tool Results</span>" in content
            assert "badge tool_results" in content
    finally:
        server.shutdown()


def test_api_traffic_summary_carries_source_fields():
    """GET /api/traffic summaries include source and duration fields."""
    entries = [
        {
            "timestamp_ms": 1000,
            "method": "GET",
            "path": "/a",
            "status_code": 200,
            "source_name": "adoption",
            "backend_url": "https://a.example.com",
            "duration_ms": 10,
        }
    ]
    server, port = _start_dashboard(entries)
    try:
        data = _get_json(port, "/api/traffic")
        e = data["entries"][0]
        assert e["source_name"] == "adoption"
        assert e["backend_url"] == "https://a.example.com"
        assert e["duration_ms"] == 10
    finally:
        server.shutdown()


def test_dashboard_html_has_source_and_duration_ui():
    """The served HTML includes the Source/Duration columns and source filter."""
    server, port = _start_dashboard(SAMPLE_ENTRIES)
    try:
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
            assert "filter-source" in content
            assert "<th>Source</th>" in content
            assert "<th>Duration</th>" in content
            assert "fmtDuration" in content
            assert "badge source" in content
            assert "sourceLabel" in content
    finally:
        server.shutdown()


def test_dashboard_aggregates_multiple_sources_from_shared_log(temp_recordings_dir):
    """A dashboard reading a shared log shows traffic from every pipe."""
    storage.append_traffic_log_entry(
        {
            "timestamp_ms": 1000,
            "method": "GET",
            "path": "/a",
            "status_code": 200,
            "source_name": "adoption",
            "backend_url": "https://a.example.com",
            "duration_ms": 10,
        }
    )
    storage.append_traffic_log_entry(
        {
            "timestamp_ms": 2000,
            "method": "POST",
            "path": "/b",
            "status_code": 201,
            "source_name": "billing",
            "backend_url": "https://b.example.com",
            "duration_ms": 20,
        }
    )
    log_path = os.path.join(str(temp_recordings_dir), storage.TRAFFIC_LOG_FILENAME)
    server = dashboard.create_dashboard_server(
        entries=None, host="127.0.0.1", port=0, traffic_log_path=log_path
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data = _get_json(port, "/api/traffic")
        by_source = {e["source_name"]: e for e in data["entries"]}
        assert set(by_source) == {"adoption", "billing"}
        assert by_source["adoption"]["backend_url"] == "https://a.example.com"
        assert by_source["billing"]["duration_ms"] == 20
    finally:
        server.shutdown()
