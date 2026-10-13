"""Amazon Bedrock Converse request and response inspection."""

import json
import re
from typing import Optional, Union
from urllib.parse import unquote, urlsplit

_CONVERSE_SUFFIX = re.compile(r"/converse(?P<stream>-stream)?/?$", re.IGNORECASE)
_NATIVE_MODEL_PATH = re.compile(r"(?:^|/)model/(.+)/converse(?P<stream>-stream)?/?$", re.IGNORECASE)


def _json_object(value) -> Optional[dict]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, (bytes, bytearray, str)):
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _path_parts(path: str) -> str:
    try:
        parsed = urlsplit(path)
        return parsed.path or path.split("?", 1)[0]
    except (TypeError, ValueError):
        return ""


def _native_model(path: str):
    match = _NATIVE_MODEL_PATH.search(_path_parts(path))
    if not match:
        return None
    model = unquote(match.group(1)).strip("/")
    return model or None


def _is_content_block(block) -> bool:
    # Converse ContentBlock is an untagged union; tagged OpenAI/Anthropic
    # blocks must not make an otherwise generic gateway request look native.
    if not isinstance(block, dict) or not block or "type" in block:
        return False
    return any(
        key
        in {
            "text",
            "image",
            "document",
            "video",
            "audio",
            "toolUse",
            "toolResult",
            "reasoningContent",
            "guardContent",
            "cachePoint",
            "searchResult",
            "citationsContent",
        }
        for key in block
    )


def _valid_messages(request: dict) -> bool:
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            return False
        content = message.get("content")
        if (
            not isinstance(content, list)
            or not content
            or not all(_is_content_block(block) for block in content)
        ):
            return False
    return True


def _has_bedrock_marker(request: dict) -> bool:
    system = request.get("system")
    has_system = isinstance(system, list) and any(
        isinstance(item, dict) and isinstance(item.get("text"), str) for item in system
    )
    tools = request.get("toolConfig")
    has_tools = isinstance(tools, dict) and isinstance(tools.get("tools"), list)
    return has_system or has_tools


def is_bedrock_request(path: str, request_body: bytes) -> bool:
    """Return whether path and body identify native or gateway Converse traffic."""
    if _native_model(path):
        return True
    request = _json_object(request_body)
    if request is None or not _CONVERSE_SUFFIX.search(_path_parts(path)):
        return False
    return _valid_messages(request) or _has_bedrock_marker(request)


def _request_insights(path: str, request: dict) -> dict:
    messages = request.get("messages")
    messages = messages if isinstance(messages, list) else []
    roles = [
        m["role"]
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    system = request.get("system")
    system_prompts = []
    if isinstance(system, list):
        for item in system:
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]:
                system_prompts.append(item["text"])

    tool_results = any(
        isinstance(message, dict)
        and message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and any(
            isinstance(block, dict) and isinstance(block.get("toolResult"), dict)
            for block in message["content"]
        )
        for message in messages
    )

    tool_config = request.get("toolConfig")
    tools = tool_config.get("tools") if isinstance(tool_config, dict) else None
    tool_names = []
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                spec = tool.get("toolSpec")
                if isinstance(spec, dict) and isinstance(spec.get("name"), str):
                    tool_names.append(spec["name"])

    path_info = _NATIVE_MODEL_PATH.search(_path_parts(path))
    model = request.get("modelId")
    if not isinstance(model, str) or not model:
        model = unquote(path_info.group(1)).strip("/") if path_info else None
    endpoint_info = _CONVERSE_SUFFIX.search(_path_parts(path))
    stream = bool(endpoint_info and endpoint_info.group("stream"))
    return {
        "model": model,
        "message_count": len(messages),
        "roles": roles,
        "system_prompts": system_prompts,
        "stream": stream,
        "has_tool_results": tool_results,
        "tool_names": tool_names,
        "tool_count": len(tools) if isinstance(tools, list) else 0,
    }


def _response_insights(response: dict) -> Optional[dict]:
    output = response.get("output")
    message = output.get("message") if isinstance(output, dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    content = content if isinstance(content, list) else []
    assistant_content = [
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]
    ]
    tool_names = []
    for block in content:
        if not isinstance(block, dict):
            continue
        use = block.get("toolUse")
        if isinstance(use, dict) and isinstance(use.get("name"), str):
            tool_names.append(use["name"])

    usage = response.get("usage")
    mapped_usage = dict(usage) if isinstance(usage, dict) else {}
    token_mapping = {
        "inputTokens": "prompt_tokens",
        "outputTokens": "completion_tokens",
        "totalTokens": "total_tokens",
    }
    for source, target in token_mapping.items():
        value = mapped_usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool):
            mapped_usage[target] = value

    result = {
        "model": response.get("modelId"),
        "usage": mapped_usage,
        "finish_reasons": (
            [response["stopReason"]] if isinstance(response.get("stopReason"), str) else []
        ),
        "assistant_content": assistant_content,
        "message": message,
    }
    if tool_names:
        result["tool_call_names"] = tool_names
        result["tool_call_count"] = len(tool_names)
    return result


def build_ai_insights(
    path: str, request_body: bytes, response_body: Union[bytes, dict]
) -> Optional[dict]:
    """Build standard insight fields from Bedrock Converse JSON or combined output.

    Request and response bodies remain untouched outside this summary. Unknown
    native content blocks (including reasoning and signatures) are therefore
    retained in the captured bodies and exposed through ``response.message``.
    """
    if not is_bedrock_request(path, request_body):
        return None

    request = _json_object(request_body)
    native_model = _native_model(path)
    if request is None:
        if not native_model:
            return None
        request = {}

    request_insights = _request_insights(path, request)
    response = _json_object(response_body)
    response_insights = _response_insights(response) if response is not None else None
    if response_insights is not None and not response_insights.get("model"):
        response_insights["model"] = request_insights.get("model")
    result = {"protocol": "bedrock_converse", "request": request_insights}
    if response_insights is not None:
        result["response"] = response_insights
    return result
