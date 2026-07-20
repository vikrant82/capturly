"""Tests for AI inspection wired into log mode entries."""

import json
from unittest.mock import Mock

from capturly.modes import log
from capturly.inspection import openai


def _make_handler():
    handler = Mock()
    handler.log_message = Mock()
    return handler


def test_build_log_entry_includes_ai_insights():
    """build_log_entry adds ai_insights for OpenAI traffic."""
    handler = _make_handler()
    request_body = json.dumps({
        "model": "gpt-4",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ],
    }).encode()
    response_body = json.dumps({
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-4",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }).encode()
    request_headers = {"Content-Type": "application/json"}
    response_headers = {"Content-Type": "application/json"}

    entry = log.build_log_entry(
        handler, "POST", "/v1/chat/completions",
        request_body, request_headers,
        200, response_headers, response_body,
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
        handler, "GET", "/api/search",
        request_body, request_headers,
        200, response_headers, response_body,
    )

    assert "ai_insights" not in entry


def test_build_combined_sse_log_entry_includes_ai_insights():
    """build_combined_sse_log_entry adds ai_insights from combined response."""
    handler = _make_handler()
    request_body = json.dumps({
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": True,
    }).encode()
    combined_response = {
        "id": "chatcmpl-stream",
        "object": "chat.completion",
        "model": "gpt-4",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hey!"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    request_headers = {"Content-Type": "application/json"}
    response_headers = {"Content-Type": "text/event-stream"}
    stream_outcome = {"aborted": False, "error": None}

    entry = log.build_combined_sse_log_entry(
        handler, "POST", "/v1/chat/completions",
        request_body, request_headers,
        200, response_headers, combined_response,
        1000, stream_outcome,
    )

    assert "ai_insights" in entry
    assert entry["ai_insights"]["request"]["stream"] is True
    assert entry["ai_insights"]["response"]["assistant_content"] == ["Hey!"]


def test_build_ai_insights_helper():
    """build_ai_insights combines request and response insights."""
    request_body = json.dumps({
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "test"}],
    }).encode()
    response_body = json.dumps({
        "id": "x",
        "object": "chat.completion",
        "model": "gpt-4",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }).encode()

    result = openai.build_ai_insights("/v1/chat/completions", request_body, response_body)

    assert result is not None
    assert "request" in result
    assert "response" in result
    assert result["request"]["message_count"] == 1
    assert result["response"]["finish_reasons"] == ["stop"]


def test_build_ai_insights_returns_none_for_non_ai():
    """build_ai_insights returns None when neither request nor response is AI."""
    result = openai.build_ai_insights("/api/users", b'{}', b'{}')
    assert result is None
