"""OpenAI protocol detection and extraction."""

import json
from typing import Optional


def detect_openai_protocol(path: str, response_body: bytes) -> Optional[dict]:
    """Detect OpenAI API traffic and return protocol metadata.

    Args:
        path: Request path (e.g., "/v1/chat/completions")
        response_body: Response body as bytes

    Returns:
        Dict with protocol metadata if OpenAI traffic detected, None otherwise.
        Returns None for non-AI endpoints, invalid JSON, or unrecognized response shapes.
    """
    if not any(
        endpoint in path for endpoint in ["/v1/chat/completions", "/v1/completions"]
    ):
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
