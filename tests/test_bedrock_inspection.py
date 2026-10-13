"""Public inspector tests for standard Bedrock Converse traffic."""

import json

import pytest

from capturly.inspection import bedrock, build_ai_insights


def _bytes(value):
    return json.dumps(value).encode("utf-8")


def test_native_converse_maps_standard_request_and_response_fields():
    request = {
        "messages": [{"role": "user", "content": [{"text": "Hi"}]}],
        "system": [{"text": "Be concise"}],
        "toolConfig": {"tools": [{"toolSpec": {"name": "lookup"}}]},
    }
    response = {
        "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 7, "outputTokens": 2, "totalTokens": 9},
        "metrics": {"latencyMs": 12},
    }

    insights = build_ai_insights(
        "/model/arn%3Aaws%3Abedrock%3Aus-east-1%3A123%3Ainference-profile%2Fsample/converse?trace=1",
        _bytes(request),
        _bytes(response),
    )

    assert insights is not None
    assert insights["protocol"] == "bedrock_converse"
    assert insights["request"] == {
        "model": "arn:aws:bedrock:us-east-1:123:inference-profile/sample",
        "message_count": 1,
        "roles": ["user"],
        "system_prompts": ["Be concise"],
        "stream": False,
        "has_tool_results": False,
        "tool_names": ["lookup"],
        "tool_count": 1,
    }
    assert insights["response"]["finish_reasons"] == ["end_turn"]
    assert insights["response"]["model"] == insights["request"]["model"]
    assert insights["response"]["assistant_content"] == ["Hello"]
    assert insights["response"]["usage"] == {
        "inputTokens": 7,
        "outputTokens": 2,
        "totalTokens": 9,
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
    }


def test_prompt_resource_stream_route_needs_no_messages_and_body_model_wins():
    insights = build_ai_insights(
        "/model/path-model/converse-stream?foo=bar",
        _bytes({"modelId": "body-model", "promptVariables": {"name": {"text": "Ada"}}}),
        {"output": {"message": {"role": "assistant", "content": []}}, "stopReason": "end_turn"},
    )

    assert insights is not None
    assert insights["request"]["model"] == "body-model"
    assert insights["request"]["message_count"] == 0
    assert insights["request"]["roles"] == []
    assert insights["request"]["stream"] is True
    assert insights["response"]["finish_reasons"] == ["end_turn"]


def test_native_stream_path_uses_encoded_path_model_as_fallback():
    insights = build_ai_insights("/model/vendor%2Fmodel-v2/converse-stream", b"{}", {})

    assert insights is not None
    assert insights["request"]["model"] == "vendor/model-v2"
    assert insights["request"]["stream"] is True
    assert insights["request"]["message_count"] == 0
    assert "response" not in insights


def test_gateway_converse_route_requires_bedrock_content_or_distinctive_fields():
    request = {
        "modelId": "gateway-model",
        "messages": [{"role": "user", "content": [{"text": "Call a tool"}]}],
        "system": [{"text": "Use tools when useful"}],
        "toolConfig": {"tools": [{"toolSpec": {"name": "search"}}]},
    }
    insights = build_ai_insights(
        "/anthropic/deployments/dpl-4/converse-stream?api-version=1", _bytes(request), {}
    )

    assert insights is not None
    assert insights["protocol"] == "bedrock_converse"
    assert insights["request"]["model"] == "gateway-model"
    assert insights["request"]["stream"] is True
    assert insights["request"]["roles"] == ["user"]
    assert insights["request"]["tool_names"] == ["search"]


def test_gateway_system_only_prompt_resource_is_recognized():
    insights = build_ai_insights(
        "/proxy/converse", _bytes({"system": [{"text": "Context"}]}), b"not json"
    )

    assert insights is not None
    assert insights["request"]["system_prompts"] == ["Context"]
    assert insights["request"]["message_count"] == 0
    assert "response" not in insights


def test_tool_result_blocks_keep_user_role_and_explicit_result_flag():
    request = {
        "messages": [
            {"role": "assistant", "content": [{"toolUse": {"name": "search", "input": {}}}]},
            {
                "role": "user",
                "content": [{"toolResult": {"toolUseId": "t-1", "content": [{"text": "found"}]}}],
            },
        ]
    }
    insights = build_ai_insights("/model/m/converse", _bytes(request), b"{}")

    assert insights is not None
    assert insights["request"]["roles"] == ["assistant", "user"]
    assert insights["request"]["has_tool_results"] is True


def test_response_preserves_native_tool_reasoning_cache_and_signatures():
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Searching"},
                    {"toolUse": {"toolUseId": "t-1", "name": "search", "input": {"q": "x"}}},
                    {
                        "reasoningContent": {
                            "reasoningText": {"text": "because", "signature": "sig-1"}
                        }
                    },
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": 12,
            "outputTokens": 4,
            "totalTokens": 16,
            "cacheReadInputTokens": 5,
        },
    }

    insights = build_ai_insights("/model/m/converse", b"{}", response)

    assert insights is not None
    assert insights["response"]["tool_call_names"] == ["search"]
    assert insights["response"]["tool_call_count"] == 1
    assert insights["response"]["assistant_content"] == ["Searching"]
    assert insights["response"]["usage"]["cacheReadInputTokens"] == 5
    assert (
        insights["response"]["message"]["content"][2]["reasoningContent"]["reasoningText"][
            "signature"
        ]
        == "sig-1"
    )


def test_gateway_requires_bedrock_shape_and_does_not_match_path_substrings():
    openai_like = {"model": "gpt", "messages": [{"role": "user", "content": "Hi"}]}
    assert build_ai_insights("/v1/converse/users", _bytes(openai_like), b"{}") is None
    assert build_ai_insights("/proxy/converse", _bytes(openai_like), b"{}") is None
    assert build_ai_insights("/proxy/converse", _bytes({"messages": []}), b"{}") is None
    assert build_ai_insights("/v1/messages", _bytes({"system": [{"text": "x"}]}), b"{}") is None


def test_type_tagged_gateway_messages_are_not_classified_as_bedrock():
    request = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
    }
    path = "/proxy/converse"

    assert bedrock.is_bedrock_request(path, _bytes(request)) is False
    assert bedrock.build_ai_insights(path, _bytes(request), b"{}") is None
    assert build_ai_insights(path, _bytes(request), b"{}") is None


def test_openai_completion_insights_survive_converse_suffix_dispatch():
    request = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
    }
    response = {
        "id": "chatcmpl-probe",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    insights = build_ai_insights("/v1/chat/completions/converse", _bytes(request), _bytes(response))

    assert insights == {
        "request": {
            "model": "gpt-4o",
            "message_count": 1,
            "roles": ["user"],
            "system_prompts": [],
            "stream": False,
        },
        "response": {
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "finish_reasons": ["stop"],
            "assistant_content": ["Hi"],
        },
    }


def test_gateway_untagged_text_and_tool_unions_remain_bedrock():
    text_request = {"messages": [{"role": "user", "content": [{"text": "Hello"}]}]}
    tool_request = {
        "messages": [
            {"role": "assistant", "content": [{"toolUse": {"name": "lookup", "input": {}}}]},
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tool-1", "content": [{"text": "found"}]}}
                ],
            },
        ]
    }

    text_insights = build_ai_insights("/proxy/converse", _bytes(text_request), b"{}")
    tool_insights = build_ai_insights("/proxy/converse", _bytes(tool_request), b"{}")

    assert text_insights is not None
    assert tool_insights is not None
    assert text_insights["protocol"] == "bedrock_converse"
    assert text_insights["request"]["roles"] == ["user"]
    assert tool_insights["protocol"] == "bedrock_converse"
    assert tool_insights["request"]["roles"] == ["assistant", "user"]
    assert tool_insights["request"]["has_tool_results"] is True


@pytest.mark.parametrize(
    "marker",
    [
        {"system": [{"text": "Use Bedrock context"}]},
        {"toolConfig": {"tools": [{"toolSpec": {"name": "lookup"}}]}},
    ],
)
def test_explicit_bedrock_markers_still_classify_type_tagged_gateway_messages(marker):
    request = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
        **marker,
    }

    assert bedrock.is_bedrock_request("/proxy/converse", _bytes(request)) is True
    insights = build_ai_insights("/proxy/converse", _bytes(request), b"{}")
    assert insights is not None
    assert insights["protocol"] == "bedrock_converse"
    assert insights["request"]["system_prompts"] == (
        ["Use Bedrock context"] if "system" in marker else []
    )
    assert insights["request"]["tool_names"] == (["lookup"] if "toolConfig" in marker else [])


def test_bedrock_inspector_rejects_valid_messages_on_unrelated_path():
    request = {"messages": [{"role": "user", "content": [{"text": "hello"}]}]}

    assert bedrock.build_ai_insights("/unrelated", _bytes(request), b"{}") is None


def test_malformed_optional_data_is_safe_and_does_not_classify_generic_messages():
    insights = build_ai_insights(
        "/model/m/converse",
        _bytes({"messages": "broken", "system": "broken", "toolConfig": []}),
        {
            "output": {"message": {"role": "assistant", "content": [None, "bad", {"toolUse": []}]}},
            "usage": [],
        },
    )

    assert insights is not None
    assert insights["request"]["message_count"] == 0
    assert insights["request"]["roles"] == []
    assert insights["request"]["system_prompts"] == []
    assert insights["request"]["has_tool_results"] is False
    assert insights["response"]["assistant_content"] == []
    assert insights["response"]["usage"] == {}


def test_openai_dispatch_fallback_keeps_existing_insight_shape():
    request = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]}
    response = {
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [
            {"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
        ],
    }

    insights = build_ai_insights("/v1/chat/completions", _bytes(request), _bytes(response))

    assert insights == {
        "request": {
            "model": "gpt-4o",
            "message_count": 1,
            "roles": ["user"],
            "system_prompts": [],
            "stream": False,
        },
        "response": {
            "model": "gpt-4o",
            "usage": None,
            "finish_reasons": ["stop"],
            "assistant_content": ["Hello"],
        },
    }
