"""OpenAI protocol detection and extraction."""

import json
from typing import Optional

_OPENAI_ENDPOINTS = ("/v1/chat/completions", "/v1/completions")


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

    insights = {
        "model": request.get("model"),
        "message_count": len(messages),
        "roles": roles,
        "system_prompts": system_prompts,
        "stream": request.get("stream", False),
    }

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
        "usage": response.get("usage"),
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
        "usage": response.get("usage"),
    }
