"""Tests for AGUI protocol SSE chunk combining."""

import json

from capturly import sse
from capturly.inspection import agui


# --- AGUI SSE event helpers ---

def _agui_sse(event_dict):
    """Format a dict as raw SSE data lines (as the handler would receive)."""
    return [f"data: {json.dumps(event_dict)}\n"]


def _parse_sse_lines(raw_chunks):
    """Convert raw SSE text chunks into lists of event lines."""
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


# A realistic AGUI event stream
AGUI_STREAM = [
    'data: {"type": "RUN_STARTED", "runId": "run-1", "threadId": "thread-1"}\n\n',
    'data: {"type": "TEXT_MESSAGE_START", "messageId": "msg-1", "role": "assistant"}\n\n',
    'data: {"type": "TEXT_MESSAGE_CONTENT", "messageId": "msg-1", "delta": "Hello "}\n\n',
    'data: {"type": "TEXT_MESSAGE_CONTENT", "messageId": "msg-1", "delta": "world"}\n\n',
    'data: {"type": "TEXT_MESSAGE_END", "messageId": "msg-1"}\n\n',
    'data: {"type": "TOOL_CALL_START", "toolCallId": "tc-1", "toolCallName": "get_weather"}\n\n',
    'data: {"type": "TOOL_CALL_ARGS", "toolCallId": "tc-1", "delta": "{\\"city\\""}\n\n',
    'data: {"type": "TOOL_CALL_ARGS", "toolCallId": "tc-1", "delta": ": \\"SF\\"}"}\n\n',
    'data: {"type": "TOOL_CALL_END", "toolCallId": "tc-1"}\n\n',
    'data: {"type": "TOOL_CALL_RESULT", "toolCallId": "tc-1", "content": "72F sunny"}\n\n',
    'data: {"type": "STATE_SNAPSHOT", "snapshot": {"mode": "build"}}\n\n',
    'data: {"type": "STATE_DELTA", "delta": [{"op": "add", "path": "/step", "value": 1}]}\n\n',
    'data: {"type": "REASONING_MESSAGE_START", "messageId": "r-1"}\n\n',
    'data: {"type": "REASONING_MESSAGE_CONTENT", "messageId": "r-1", "delta": "Thinking..."}\n\n',
    'data: {"type": "REASONING_MESSAGE_END", "messageId": "r-1"}\n\n',
    'data: {"type": "STEP_STARTED", "stepName": "analyze"}\n\n',
    'data: {"type": "STEP_FINISHED", "stepName": "analyze"}\n\n',
    'data: {"type": "RUN_FINISHED"}\n\n',
]


# --- Detection tests ---


def test_is_agui_event_positive():
    assert agui.is_agui_event({"type": "RUN_STARTED"}) is True
    assert agui.is_agui_event({"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}) is True
    assert agui.is_agui_event({"type": "CUSTOM", "name": "foo"}) is True


def test_is_agui_event_negative():
    assert agui.is_agui_event({"type": "UNKNOWN_EVENT"}) is False
    assert agui.is_agui_event({"choices": []}) is False
    assert agui.is_agui_event({"type": 123}) is False
    assert agui.is_agui_event({}) is False


# --- Accumulator unit tests ---


def test_agui_accumulator_text_messages():
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"})
    agui.accumulate_agui_event(acc, {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Hello "})
    agui.accumulate_agui_event(acc, {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "world"})
    agui.accumulate_agui_event(acc, {"type": "TEXT_MESSAGE_END", "messageId": "m1"})

    result = agui.finalize_agui_chunks(acc)
    assert result is not None
    assert result["object"] == "agui.completion"
    assert len(result["messages"]) == 1
    assert result["messages"][0]["content"] == "Hello world"
    assert result["messages"][0]["role"] == "assistant"


def test_agui_accumulator_tool_calls():
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "TOOL_CALL_START", "toolCallId": "tc1", "toolCallName": "search"})
    agui.accumulate_agui_event(acc, {"type": "TOOL_CALL_ARGS", "toolCallId": "tc1", "delta": '{"q":'})
    agui.accumulate_agui_event(acc, {"type": "TOOL_CALL_ARGS", "toolCallId": "tc1", "delta": '"test"}'})
    agui.accumulate_agui_event(acc, {"type": "TOOL_CALL_END", "toolCallId": "tc1"})
    agui.accumulate_agui_event(acc, {"type": "TOOL_CALL_RESULT", "toolCallId": "tc1", "content": "3 results"})

    result = agui.finalize_agui_chunks(acc)
    assert result is not None
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["name"] == "search"
    assert tc["arguments"] == {"q": "test"}  # parsed JSON
    assert tc["result"] == "3 results"


def test_agui_accumulator_lifecycle():
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "RUN_STARTED", "runId": "r1", "threadId": "t1"})
    agui.accumulate_agui_event(acc, {"type": "RUN_FINISHED"})

    result = agui.finalize_agui_chunks(acc)
    assert result["run"]["run_id"] == "r1"
    assert result["run"]["thread_id"] == "t1"
    assert result["run"]["status"] == "finished"


def test_agui_accumulator_run_error():
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "RUN_STARTED", "runId": "r1"})
    agui.accumulate_agui_event(acc, {"type": "RUN_ERROR", "message": "Something broke"})

    result = agui.finalize_agui_chunks(acc)
    assert result["run"]["status"] == "error"
    assert result["run"]["error"] == "Something broke"


def test_agui_accumulator_state_snapshot():
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "STATE_SNAPSHOT", "snapshot": {"mode": "build"}})
    agui.accumulate_agui_event(acc, {"type": "STATE_DELTA", "delta": [{"op": "add"}]})

    result = agui.finalize_agui_chunks(acc)
    assert result["state"] == {"mode": "build"}
    assert result["event_count"] == 2  # both counted


def test_agui_accumulator_reasoning():
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "REASONING_MESSAGE_START", "messageId": "r1"})
    agui.accumulate_agui_event(acc, {"type": "REASONING_MESSAGE_CONTENT", "messageId": "r1", "delta": "Let me "})
    agui.accumulate_agui_event(acc, {"type": "REASONING_MESSAGE_CONTENT", "messageId": "r1", "delta": "think"})
    agui.accumulate_agui_event(acc, {"type": "REASONING_MESSAGE_END", "messageId": "r1"})

    result = agui.finalize_agui_chunks(acc)
    assert len(result["reasoning"]) == 1
    assert result["reasoning"][0]["content"] == "Let me think"


def test_agui_accumulator_empty():
    acc = agui.new_agui_accumulator()
    assert agui.finalize_agui_chunks(acc) is None


def test_agui_text_message_chunk_convenience():
    """TEXT_MESSAGE_CHUNK auto-creates messages."""
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "TEXT_MESSAGE_CHUNK", "messageId": "m1", "role": "assistant", "delta": "Hi"})
    agui.accumulate_agui_event(acc, {"type": "TEXT_MESSAGE_CHUNK", "messageId": "m1", "delta": " there"})

    result = agui.finalize_agui_chunks(acc)
    assert result["messages"][0]["content"] == "Hi there"


def test_agui_tool_call_chunk_convenience():
    """TOOL_CALL_CHUNK auto-creates tool calls."""
    acc = agui.new_agui_accumulator()
    agui.accumulate_agui_event(acc, {"type": "TOOL_CALL_CHUNK", "toolCallId": "tc1", "toolCallName": "run", "delta": "{}"})

    result = agui.finalize_agui_chunks(acc)
    assert result["tool_calls"][0]["name"] == "run"
    assert result["tool_calls"][0]["arguments"] == {}


# --- Integration: protocol detection via accumulate_sse_event ---


def test_protocol_detection_agui():
    """accumulate_sse_event auto-detects AGUI protocol from first event."""
    accumulator = sse.new_chunk_accumulator()
    events = _parse_sse_lines(AGUI_STREAM)

    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    assert accumulator.get("_protocol") == "agui"
    combined = sse.finalize_sse_chunks(accumulator)

    assert combined is not None
    assert combined["object"] == "agui.completion"
    assert combined["run"]["run_id"] == "run-1"
    assert combined["run"]["thread_id"] == "thread-1"
    assert combined["run"]["status"] == "finished"

    # Text message combined
    assert len(combined["messages"]) == 1
    assert combined["messages"][0]["content"] == "Hello world"
    assert combined["messages"][0]["role"] == "assistant"

    # Tool call combined with parsed JSON args
    assert len(combined["tool_calls"]) == 1
    tc = combined["tool_calls"][0]
    assert tc["name"] == "get_weather"
    assert tc["arguments"] == {"city": "SF"}
    assert tc["result"] == "72F sunny"

    # State snapshot kept
    assert combined["state"] == {"mode": "build"}

    # Reasoning combined
    assert len(combined["reasoning"]) == 1
    assert combined["reasoning"][0]["content"] == "Thinking..."

    # All 18 events counted
    assert combined["event_count"] == 18


def test_protocol_detection_openai_still_works():
    """Existing OpenAI detection is not broken by AGUI support."""
    accumulator = sse.new_chunk_accumulator()
    openai_chunks = [
        'data: {"choices": [{"index": 0, "delta": {"role": "assistant"}}], "model": "gpt-4"}\n\n',
        'data: {"choices": [{"index": 0, "delta": {"content": "Hi"}}]}\n\n',
        'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n\n',
    ]
    events = _parse_sse_lines(openai_chunks)
    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    assert accumulator.get("_protocol") == "openai"
    combined = sse.finalize_sse_chunks(accumulator)
    assert combined is not None
    assert combined["object"] == "chat.completion"
    assert combined["choices"][0]["message"]["content"] == "Hi"


def test_unknown_protocol_ignored():
    """Events that match neither OpenAI nor AGUI are ignored."""
    accumulator = sse.new_chunk_accumulator()
    events = _parse_sse_lines([
        'data: {"foo": "bar"}\n\n',
        'data: {"baz": 123}\n\n',
    ])
    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    assert accumulator.get("_protocol") is None
    assert sse.finalize_sse_chunks(accumulator) is None


def test_stream_outcome_preserved_on_protocol_switch():
    """The stream_outcome dict reference survives the AGUI accumulator swap."""
    accumulator = sse.new_chunk_accumulator()
    original_outcome = accumulator["stream_outcome"]

    events = _parse_sse_lines([
        'data: {"type": "RUN_STARTED", "runId": "r1"}\n\n',
    ])
    for event_lines in events:
        sse.accumulate_sse_event(accumulator, event_lines)

    # The stream_outcome reference should be the same object
    assert accumulator["stream_outcome"] is original_outcome
    assert accumulator.get("_protocol") == "agui"

