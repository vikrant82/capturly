"""End-to-end integration test for SSE chunk combining with AI insights."""

import json

from capturly import sse
from capturly.modes import log as log_mode

# Realistic OpenAI streaming chunks for a chat completion with tool calls
SSE_CHUNKS = [
    'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
    'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
    'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n',
    'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
    "data: [DONE]\n\n",
]

SSE_TOOL_CALL_CHUNKS = [
    'data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}\n\n',
    'data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"loc"}}]},"finish_reason":null}]}\n\n',
    'data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ation\\": \\"SF\\"}"}}]},"finish_reason":null}]}\n\n',
    'data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","model":"gpt-4","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":20,"completion_tokens":8,"total_tokens":28}}\n\n',
    "data: [DONE]\n\n",
]


def _parse_sse_lines(raw_chunks):
    """Convert raw SSE text chunks into lists of event lines (as the handler would see them)."""
    events = []
    current = []
    for chunk in raw_chunks:
        for line in chunk.split("\n"):
            if line == "":
                if current:
                    events.append(current)
                    current = []
            else:
                current.append(line + "\n")
    if current:
        events.append(current)
    return events


def test_sse_content_combining_end_to_end():
    """SSE content chunks combine into a complete response with AI insights in the log entry."""
    accumulator = sse.new_chunk_accumulator()
    events = _parse_sse_lines(SSE_CHUNKS)

    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    combined = sse.finalize_sse_chunks(accumulator)
    assert combined is not None
    assert combined["object"] == "chat.completion"
    assert combined["model"] == "gpt-4"
    assert combined["choices"][0]["message"]["content"] == "Hello world"
    assert combined["choices"][0]["finish_reason"] == "stop"
    assert combined["usage"]["total_tokens"] == 15

    # Now build the log entry and verify AI insights are present
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Say hello"},
            ],
            "stream": True,
        }
    ).encode()

    entry = log_mode.build_combined_sse_log_entry(
        handler=None,
        method="POST",
        path="/v1/chat/completions",
        request_body=request_body,
        request_headers={"Content-Type": "application/json"},
        status_code=200,
        response_headers={"Content-Type": "text/event-stream"},
        response_body=combined,
        started_timestamp_ms=1000,
        stream_outcome=accumulator["stream_outcome"],
    )

    assert entry["sse"] is True
    assert entry["ai_insights"] is not None
    assert entry["ai_insights"]["request"]["model"] == "gpt-4"
    assert entry["ai_insights"]["request"]["system_prompts"] == ["You are helpful."]
    assert entry["ai_insights"]["request"]["message_count"] == 2
    assert entry["ai_insights"]["response"]["usage"]["total_tokens"] == 15
    assert entry["ai_insights"]["response"]["finish_reasons"] == ["stop"]


def test_sse_tool_call_combining_end_to_end():
    """SSE tool-call chunks combine correctly with AI insights showing tool calls."""
    accumulator = sse.new_chunk_accumulator()
    events = _parse_sse_lines(SSE_TOOL_CALL_CHUNKS)

    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    combined = sse.finalize_sse_chunks(accumulator)
    assert combined is not None
    assert combined["choices"][0]["finish_reason"] == "tool_calls"

    tool_calls = combined["choices"][0]["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {"location": "SF"}

    # Build log entry
    request_body = json.dumps(
        {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Weather in SF?"}],
            "tools": [{"type": "function", "function": {"name": "get_weather"}}],
            "stream": True,
        }
    ).encode()

    entry = log_mode.build_combined_sse_log_entry(
        handler=None,
        method="POST",
        path="/v1/chat/completions",
        request_body=request_body,
        request_headers={"Content-Type": "application/json"},
        status_code=200,
        response_headers={"Content-Type": "text/event-stream"},
        response_body=combined,
        started_timestamp_ms=2000,
        stream_outcome=accumulator["stream_outcome"],
    )

    assert entry["ai_insights"]["request"]["tool_names"] == ["get_weather"]
    assert entry["ai_insights"]["response"]["tool_call_names"] == ["get_weather"]
    assert entry["ai_insights"]["response"]["finish_reasons"] == ["tool_calls"]
    assert entry["ai_insights"]["response"]["usage"]["total_tokens"] == 28


def test_sse_aborted_stream():
    """Aborted SSE stream produces a log entry with sse_error."""
    accumulator = sse.new_chunk_accumulator()
    # Only feed partial chunks (no [DONE])
    events = _parse_sse_lines(SSE_CHUNKS[:2])
    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    # Simulate abort
    accumulator["stream_outcome"]["aborted"] = True
    accumulator["stream_outcome"]["error"] = "client_disconnected"

    combined = sse.finalize_sse_chunks(accumulator)
    assert combined is not None  # Partial content still combined

    entry = log_mode.build_combined_sse_log_entry(
        handler=None,
        method="POST",
        path="/v1/chat/completions",
        request_body=b'{"model":"gpt-4","messages":[]}',
        request_headers={},
        status_code=200,
        response_headers={},
        response_body=combined,
        started_timestamp_ms=3000,
        stream_outcome=accumulator["stream_outcome"],
    )

    assert entry["sse"] is True
    assert entry["sse_error"] == "client_disconnected"
