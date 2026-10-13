"""AI protocol detection and inspection."""

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


__all__ = ["build_ai_insights", "bedrock", "openai"]
