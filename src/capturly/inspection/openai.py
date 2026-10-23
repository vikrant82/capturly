"""OpenAI protocol detection and extraction."""

import json
from typing import Any, Optional

_OPENAI_ENDPOINTS = ("/v1/chat/completions", "/v1/completions")

_CACHE_READ_CANDIDATES = (
    "cache_read_tokens",
    "cached_tokens",
    ("prompt_tokens_details", "cached_tokens"),
    ("input_tokens_details", "cached_tokens"),
    "cacheReadInputTokens",
    "cache_read_input_tokens",
    "prompt_cache_hit_tokens",
)

_CACHE_WRITE_CANDIDATES = (
    "cache_write_tokens",
    "cacheWriteInputTokens",
    "cache_creation_input_tokens",
    ("prompt_tokens_details", "cache_creation_tokens"),
    ("prompt_tokens_details", "cache_write_tokens"),
    ("input_tokens_details", "cache_creation_tokens"),
    ("input_tokens_details", "cache_write_tokens"),
)

_THINKING_CANDIDATES = (
    "thinking_tokens",
    "reasoning_tokens",
    ("completion_tokens_details", "reasoning_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
    "thinkingTokens",
    "reasoningTokens",
)


def _is_int(value: Any) -> bool:
    """Return True when value is a non-boolean integer."""
    return isinstance(value, int) and not isinstance(value, bool)


def _first_int(data: dict, candidates: tuple) -> Optional[int]:
    """Return the first non-boolean integer found across top-level or nested keys."""
    for candidate in candidates:
        if isinstance(candidate, tuple):
            cur: Any = data
            for key in candidate:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(key)
            val = cur
        else:
            val = data.get(candidate)
        if _is_int(val):
            return int(val)
    return None


def _normalize_usage(usage: Any) -> Optional[dict]:
    """Preserve raw usage keys and populate normalized token counts when present."""
    if not isinstance(usage, dict):
        return None

    normalized = dict(usage)
    if not _is_int(normalized.get("prompt_tokens")):
        input_tokens = _first_int(normalized, ("input_tokens", "inputTokens"))
        if input_tokens is not None:
            normalized["prompt_tokens"] = input_tokens

    if not _is_int(normalized.get("completion_tokens")):
        output_tokens = _first_int(normalized, ("output_tokens", "outputTokens"))
        if output_tokens is not None:
            normalized["completion_tokens"] = output_tokens

    if not _is_int(normalized.get("total_tokens")):
        if _is_int(normalized.get("prompt_tokens")) and _is_int(
            normalized.get("completion_tokens")
        ):
            normalized["total_tokens"] = (
                normalized["prompt_tokens"] + normalized["completion_tokens"]
            )

    cache_read = _first_int(normalized, _CACHE_READ_CANDIDATES)
    if cache_read is not None:
        normalized["cache_read_tokens"] = cache_read

    cache_write = _first_int(normalized, _CACHE_WRITE_CANDIDATES)
    if cache_write is not None:
        normalized["cache_write_tokens"] = cache_write

    thinking = _first_int(normalized, _THINKING_CANDIDATES)
    if thinking is not None:
        normalized["thinking_tokens"] = thinking

    return normalized


def _is_openai_endpoint(path: str) -> bool:
    """Check if a request path targets a known OpenAI endpoint."""
    return any(endpoint in path for endpoint in _OPENAI_ENDPOINTS)


def extract_request_insights(path: str, request_body: bytes) -> Optional[dict]:
    """Extract AI-relevant insights from an OpenAI request body.

    Parses the request to surface system prompts, tool definitions, message
    structure, and streaming configuration. Returns None for non-AI endpoints,
    invalid JSON, or requests without a messages array.

    Args:
        path: Request path (e.g., "/v1/chat/completions")
        request_body: Raw request body bytes

    Returns:
        Dict with extracted insights, or None if not applicable.
    """
    if not _is_openai_endpoint(path):
        return None

    try:
        request = json.loads(request_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(request, dict):
        return None

    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    system_prompts = []
    roles = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role:
            roles.append(role)
        if role == "system":
            content = msg.get("content")
            if isinstance(content, str) and content:
                system_prompts.append(content)
            elif isinstance(content, list):
                texts = [
                    part["text"]
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ]
                if texts:
                    system_prompts.append("\n".join(texts))

    insights = {
        "model": request.get("model"),
        "message_count": len(messages),
        "roles": roles,
        "system_prompts": system_prompts,
        "stream": request.get("stream", False),
    }

    max_tokens = _first_int(request, ("max_completion_tokens", "max_tokens"))
    if max_tokens is not None:
        insights["max_tokens"] = max_tokens

    tools = request.get("tools")
    if isinstance(tools, list) and tools:
        tool_names = []
        for tool in tools:
            if isinstance(tool, dict):
                func = tool.get("function")
                if isinstance(func, dict) and func.get("name"):
                    tool_names.append(func["name"])
        insights["tool_names"] = tool_names
        insights["tool_count"] = len(tools)

    return insights


def extract_response_insights(path: str, response_body: bytes) -> Optional[dict]:
    """Extract AI-relevant insights from an OpenAI response body.

    Parses the response to surface token usage, finish reasons, assistant
    content, and tool calls. Returns None for non-AI endpoints, invalid JSON,
    or responses without a choices array.

    Args:
        path: Request path (e.g., "/v1/chat/completions")
        response_body: Raw response body bytes

    Returns:
        Dict with extracted insights, or None if not applicable.
    """
    if not _is_openai_endpoint(path):
        return None

    try:
        response = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(response, dict):
        return None

    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    finish_reasons = []
    assistant_content = []
    tool_call_names = []

    for choice in choices:
        if not isinstance(choice, dict):
            continue

        reason = choice.get("finish_reason")
        if reason:
            finish_reasons.append(reason)

        message = choice.get("message")
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        if isinstance(content, str) and content:
            assistant_content.append(content)

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function")
                    if isinstance(func, dict) and func.get("name"):
                        tool_call_names.append(func["name"])

    insights = {
        "model": response.get("model"),
        "usage": _normalize_usage(response.get("usage")),
        "finish_reasons": finish_reasons,
        "assistant_content": assistant_content,
    }

    if tool_call_names:
        insights["tool_call_names"] = tool_call_names
        insights["tool_call_count"] = len(tool_call_names)

    return insights


def detect_openai_protocol(path: str, response_body: bytes) -> Optional[dict]:
    """Detect OpenAI API traffic and return protocol metadata.

    Args:
        path: Request path (e.g., "/v1/chat/completions")
        response_body: Response body as bytes

    Returns:
        Dict with protocol metadata if OpenAI traffic detected, None otherwise.
        Returns None for non-AI endpoints, invalid JSON, or unrecognized response shapes.
    """
    if not _is_openai_endpoint(path):
        return None

    try:
        response = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(response, dict):
        return None

    obj_type = response.get("object")
    if obj_type not in ["chat.completion", "text_completion"]:
        return None

    return {
        "protocol": "openai",
        "type": "chat_completion" if obj_type == "chat.completion" else "completion",
        "model": response.get("model"),
        "id": response.get("id"),
        "usage": _normalize_usage(response.get("usage")),
    }


def build_ai_insights(path: str, request_body: bytes, response_body) -> Optional[dict]:
    """Combine request and response insights into a single ai_insights dict.

    Accepts response_body as bytes or a pre-parsed dict (for combined SSE
    entries where the response is already structured). Returns None when
    neither request nor response yields AI insights.

    Args:
        path: Request path
        request_body: Raw request body bytes
        response_body: Raw response bytes or a dict (combined SSE completion)

    Returns:
        Dict with "request" and/or "response" insight sub-dicts, or None.
    """
    request_insights = extract_request_insights(path, request_body)

    if isinstance(response_body, dict):
        response_bytes = json.dumps(response_body, ensure_ascii=False).encode("utf-8")
    elif isinstance(response_body, bytes):
        response_bytes = response_body
    else:
        response_bytes = b""

    response_insights = extract_response_insights(path, response_bytes)

    if request_insights is None and response_insights is None:
        return None

    result = {}
    if request_insights is not None:
        result["request"] = request_insights
    if response_insights is not None:
        result["response"] = response_insights
    return result
