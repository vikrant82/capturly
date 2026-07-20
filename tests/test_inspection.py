"""Tests for AI protocol detection and inspection."""

import json

from capturly.inspection import openai


def test_detect_openai_chat_completion():
    """Detect OpenAI chat completion from path and response."""
    path = "/v1/chat/completions"
    response_body = json.dumps({
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "gpt-4",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello"},
                "finish_reason": "stop"
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    }).encode()

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
    response_body = json.dumps({
        "id": "cmpl-123",
        "object": "text_completion",
        "model": "text-davinci-003",
    }).encode()

    result = openai.detect_openai_protocol(path, response_body)

    assert result is not None
    assert result["type"] == "completion"
    assert result["model"] == "text-davinci-003"
