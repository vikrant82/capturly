"""Tests for AI protocol detection and inspection."""

import json

from capturly.inspection import openai


def test_detect_openai_chat_completion():
    """Detect OpenAI chat completion from path and response."""
    path = "/v1/chat/completions"
    response_body = json.dumps(
        {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    ).encode()

    result = openai.detect_openai_protocol(path, response_body)

    assert result is not None
    assert result["protocol"] == "openai"
    assert result["type"] == "chat_completion"
    assert result["model"] == "gpt-4"


def test_detect_openai_non_ai_endpoint():
    """Non-AI endpoints return None."""
    path = "/v1/users"
    response_body = b'{"id": 123}'

    result = openai.detect_openai_protocol(path, response_body)
    assert result is None


def test_detect_openai_invalid_json():
    """Invalid JSON responses return None gracefully."""
    path = "/v1/chat/completions"
    response_body = b"not json"

    result = openai.detect_openai_protocol(path, response_body)
    assert result is None


def test_detect_openai_text_completion():
    """Legacy completions endpoint detected correctly."""
    path = "/v1/completions"
    response_body = json.dumps(
        {
            "id": "cmpl-123",
            "object": "text_completion",
            "model": "text-davinci-003",
        }
    ).encode()

    result = openai.detect_openai_protocol(path, response_body)

    assert result is not None
    assert result["type"] == "completion"
    assert result["model"] == "text-davinci-003"


# --- Task 1: Request extraction tests ---


def test_extract_request_insights_system_prompt():
    """Extract system prompt from OpenAI chat request body."""
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": "What is 2+2?"},
            ],
        }
    ).encode()

    result = openai.extract_request_insights("/v1/chat/completions", request_body)

    assert result is not None
    assert result["system_prompts"] == ["You are a helpful coding assistant."]
    assert result["message_count"] == 2
    assert result["roles"] == ["system", "user"]
    assert result["model"] == "gpt-4"


def test_extract_request_insights_tool_definitions():
    """Extract tool definitions from OpenAI chat request body."""
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Get weather"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_time",
                        "description": "Get current time",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
        }
    ).encode()

    result = openai.extract_request_insights("/v1/chat/completions", request_body)

    assert result is not None
    assert result["tool_names"] == ["get_weather", "get_time"]
    assert result["tool_count"] == 2


def test_extract_request_insights_multiple_system_prompts():
    """Extract multiple system prompts (some APIs send multiple)."""
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "system", "content": "Always respond in JSON."},
                {"role": "user", "content": "Hello"},
            ],
        }
    ).encode()

    result = openai.extract_request_insights("/v1/chat/completions", request_body)

    assert result is not None
    assert result["system_prompts"] == ["You are helpful.", "Always respond in JSON."]
    assert result["message_count"] == 3


def test_extract_request_insights_non_ai_endpoint():
    """Non-AI endpoints return None."""
    request_body = json.dumps({"query": "SELECT * FROM users"}).encode()

    result = openai.extract_request_insights("/v1/query", request_body)
    assert result is None


def test_extract_request_insights_invalid_json():
    """Invalid JSON request body returns None gracefully."""
    result = openai.extract_request_insights("/v1/chat/completions", b"not json")
    assert result is None


def test_extract_request_insights_no_messages():
    """Request without messages array returns None."""
    request_body = json.dumps({"model": "gpt-4"}).encode()

    result = openai.extract_request_insights("/v1/chat/completions", request_body)
    assert result is None


def test_extract_request_insights_streaming_flag():
    """Detect streaming flag in request."""
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
    ).encode()

    result = openai.extract_request_insights("/v1/chat/completions", request_body)

    assert result is not None
    assert result["stream"] is True


# --- Task 2: Response extraction tests ---


def test_extract_response_insights_basic():
    """Extract usage, finish_reason, and content from a chat completion response."""
    response_body = json.dumps(
        {
            "id": "chatcmpl-abc",
            "object": "chat.completion",
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "The answer is 4."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }
    ).encode()

    result = openai.extract_response_insights("/v1/chat/completions", response_body)

    assert result is not None
    assert result["finish_reasons"] == ["stop"]
    assert result["usage"] == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    assert result["assistant_content"] == ["The answer is 4."]
    assert result["model"] == "gpt-4"


def test_extract_response_insights_tool_calls():
    """Extract tool call names and arguments from response."""
    response_body = json.dumps(
        {
            "id": "chatcmpl-xyz",
            "object": "chat.completion",
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city": "London"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
        }
    ).encode()

    result = openai.extract_response_insights("/v1/chat/completions", response_body)

    assert result is not None
    assert result["finish_reasons"] == ["tool_calls"]
    assert result["tool_call_names"] == ["get_weather"]
    assert result["tool_call_count"] == 1


def test_extract_response_insights_multiple_choices():
    """Handle multiple choices with different finish reasons."""
    response_body = json.dumps(
        {
            "id": "chatcmpl-multi",
            "object": "chat.completion",
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "A"},
                    "finish_reason": "stop",
                },
                {
                    "index": 1,
                    "message": {"role": "assistant", "content": "B"},
                    "finish_reason": "length",
                },
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
    ).encode()

    result = openai.extract_response_insights("/v1/chat/completions", response_body)

    assert result is not None
    assert result["finish_reasons"] == ["stop", "length"]
    assert result["assistant_content"] == ["A", "B"]


def test_extract_response_insights_non_ai_endpoint():
    """Non-AI endpoints return None."""
    result = openai.extract_response_insights("/v1/users", b'{"id": 1}')
    assert result is None


def test_extract_response_insights_invalid_json():
    """Invalid JSON returns None."""
    result = openai.extract_response_insights("/v1/chat/completions", b"not json")
    assert result is None


def test_extract_response_insights_no_choices():
    """Response without choices returns None."""
    response_body = json.dumps({"id": "x", "object": "chat.completion"}).encode()
    result = openai.extract_response_insights("/v1/chat/completions", response_body)
    assert result is None
