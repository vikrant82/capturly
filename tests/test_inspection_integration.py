"""Tests for AI inspection wired into log mode entries."""

import json
from unittest.mock import Mock

from capturly.inspection import openai
from capturly.modes import log


def _make_handler():
    handler = Mock()
    handler.log_message = Mock()
    return handler


def test_build_log_entry_includes_ai_insights():
    """build_log_entry adds ai_insights for OpenAI traffic."""
    handler = _make_handler()
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ],
        }
    ).encode()
    response_body = json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
    ).encode()
    request_headers = {"Content-Type": "application/json"}
    response_headers = {"Content-Type": "application/json"}

    entry = log.build_log_entry(
        handler,
        "POST",
        "/v1/chat/completions",
        request_body,
        request_headers,
        200,
        response_headers,
        response_body,
    )

    assert "ai_insights" in entry
    insights = entry["ai_insights"]
    assert insights["request"]["system_prompts"] == ["You are helpful."]
    assert insights["request"]["model"] == "gpt-4"
    assert insights["response"]["finish_reasons"] == ["stop"]
    assert insights["response"]["usage"]["total_tokens"] == 13


def test_build_log_entry_no_ai_insights_for_non_ai():
    """build_log_entry omits ai_insights for non-AI traffic."""
    handler = _make_handler()
    request_body = json.dumps({"query": "test"}).encode()
    response_body = json.dumps({"results": []}).encode()
    request_headers = {"Content-Type": "application/json"}
    response_headers = {"Content-Type": "application/json"}

    entry = log.build_log_entry(
        handler,
        "GET",
        "/api/search",
        request_body,
        request_headers,
        200,
        response_headers,
        response_body,
    )

    assert "ai_insights" not in entry


def test_build_combined_sse_log_entry_includes_ai_insights():
    """build_combined_sse_log_entry adds ai_insights from combined response."""
    handler = _make_handler()
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
    ).encode()
    combined_response = {
        "id": "chatcmpl-stream",
        "object": "chat.completion",
        "model": "gpt-4",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hey!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    request_headers = {"Content-Type": "application/json"}
    response_headers = {"Content-Type": "text/event-stream"}
    stream_outcome = {"aborted": False, "error": None}

    entry = log.build_combined_sse_log_entry(
        handler,
        "POST",
        "/v1/chat/completions",
        request_body,
        request_headers,
        200,
        response_headers,
        combined_response,
        1000,
        stream_outcome,
    )

    assert "ai_insights" in entry
    assert entry["ai_insights"]["request"]["stream"] is True
    assert entry["ai_insights"]["response"]["assistant_content"] == ["Hey!"]


def test_build_ai_insights_helper():
    """build_ai_insights combines request and response insights."""
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "test"}],
        }
    ).encode()
    response_body = json.dumps(
        {
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()

    result = openai.build_ai_insights("/v1/chat/completions", request_body, response_body)

    assert result is not None
    assert "request" in result
    assert "response" in result
    assert result["request"]["message_count"] == 1
    assert result["response"]["finish_reasons"] == ["stop"]


def test_build_ai_insights_returns_none_for_non_ai():
    """build_ai_insights returns None when neither request nor response is AI."""
    result = openai.build_ai_insights("/api/users", b"{}", b"{}")
    assert result is None


def _make_source_handler(pipe_name=None, backend_url=None):
    handler = Mock()
    handler.log_message = Mock()
    handler.pipe_name = pipe_name
    handler.backend_url = backend_url
    return handler


def test_build_log_entry_includes_source_and_duration():
    """build_log_entry stamps source fields and computes duration_ms."""
    handler = _make_source_handler("adoption", "https://api.example.com")
    started = 1000
    entry = log.build_log_entry(
        handler, "GET", "/x", b"{}", {}, 200, {}, b"{}", started_timestamp_ms=started
    )
    assert entry["source_name"] == "adoption"
    assert entry["backend_url"] == "https://api.example.com"
    assert entry["duration_ms"] == entry["timestamp_ms"] - started
    assert entry["duration_ms"] >= 0


def test_build_log_entry_omits_duration_without_start():
    """build_log_entry leaves out duration_ms when no start time is given."""
    handler = _make_source_handler("adoption", "https://api.example.com")
    entry = log.build_log_entry(handler, "GET", "/x", b"{}", {}, 200, {}, b"{}")
    assert "duration_ms" not in entry
    assert entry["source_name"] == "adoption"


def test_build_log_entry_source_defaults_to_none():
    """Unnamed pipes stamp a None source_name; backend_url is still recorded."""
    handler = _make_source_handler(None, "https://api.example.com")
    entry = log.build_log_entry(handler, "GET", "/x", b"{}", {}, 200, {}, b"{}")
    assert entry["source_name"] is None
    assert entry["backend_url"] == "https://api.example.com"


def test_build_combined_sse_duration_matches_sse_duration():
    """Combined-SSE entries expose duration_ms equal to sse_duration_ms."""
    handler = _make_source_handler("billing", "https://b.example.com")
    stream_outcome = {"aborted": False, "error": None}
    entry = log.build_combined_sse_log_entry(
        handler,
        "POST",
        "/sse",
        b"{}",
        {},
        200,
        {},
        {"combined_completion": "ok"},
        1000,
        stream_outcome,
    )
    assert entry["source_name"] == "billing"
    assert entry["duration_ms"] == entry["sse_duration_ms"]


def test_build_sse_log_entry_has_source_no_duration():
    """Non-combined SSE entries carry source fields but no duration_ms."""
    handler = _make_source_handler("billing", "https://b.example.com")
    entry = log.build_sse_log_entry(
        handler, "POST", "/sse", b"{}", {}, 200, {}, "events.jsonl", timestamp_ms=1000
    )
    assert entry["source_name"] == "billing"
    assert entry["backend_url"] == "https://b.example.com"
    assert "duration_ms" not in entry
