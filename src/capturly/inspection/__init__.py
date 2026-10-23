"""AI protocol detection and inspection."""

import json
from typing import Optional, Union

from . import bedrock, openai


def build_ai_insights(
    path: str, request_body: bytes, response_body: Union[bytes, dict]
) -> Optional[dict]:
    """Inspect supported AI traffic while preserving the OpenAI inspector API.

    Bedrock Converse is selected only for its native route or a gateway route
    paired with Bedrock-specific request structure. All other traffic falls
    through to the existing OpenAI inspector.
    """
    if bedrock.is_bedrock_request(path, request_body):
        return bedrock.build_ai_insights(path, request_body, response_body)
    return openai.build_ai_insights(path, request_body, response_body)


def ensure_ai_insights(entry: dict) -> Optional[dict]:
    """Backfill and attach ai_insights on a parsed log entry when missing.

    Supports pre-existing log entries (such as OpenAI Responses API calls
    recorded before inspection support or aborted SSE streams) where
    ``ai_insights`` was not persisted at write time.
    """
    if not isinstance(entry, dict):
        return None
    existing = entry.get("ai_insights")
    if isinstance(existing, dict) and existing:
        return existing

    path = entry.get("path") or ""
    if not isinstance(path, str) or not openai._is_openai_endpoint(path):
        return None

    req = entry.get("request_body")
    if isinstance(req, dict):
        req_bytes = json.dumps(req, ensure_ascii=False).encode("utf-8")
    elif isinstance(req, str):
        req_bytes = req.encode("utf-8")
    elif isinstance(req, bytes):
        req_bytes = req
    else:
        req_bytes = b""

    res = entry.get("response_body")
    if isinstance(res, dict):
        res_arg: Union[bytes, dict] = res
    elif isinstance(res, str):
        res_arg = res.encode("utf-8")
    elif isinstance(res, bytes):
        res_arg = res
    else:
        res_arg = b""

    insights = build_ai_insights(path, req_bytes, res_arg)
    if insights:
        entry["ai_insights"] = insights
        return insights
    return None


__all__ = ["build_ai_insights", "ensure_ai_insights", "bedrock", "openai"]
