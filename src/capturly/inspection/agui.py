"""AGUI (Agent GUI) protocol SSE chunk combining.

Reconstructs complete messages, tool calls, and run metadata from
streamed AGUI events.  Start/Content/End streaming triads are
concatenated; lifecycle and state events are captured as metadata.
STATE_DELTA patches are counted but not applied (KISS).
"""

import json
from typing import Any, Optional

# Event types that identify an AGUI protocol stream.
AGUI_EVENT_TYPES = frozenset(
    {
        "RUN_STARTED",
        "RUN_FINISHED",
        "RUN_ERROR",
        "STEP_STARTED",
        "STEP_FINISHED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_CHUNK",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_CHUNK",
        "STATE_SNAPSHOT",
        "STATE_DELTA",
        "ACTIVITY_SNAPSHOT",
        "ACTIVITY_DELTA",
        "MESSAGES_SNAPSHOT",
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_END",
        "RAW",
        "CUSTOM",
    }
)


def is_agui_event(chunk: dict) -> bool:
    """Return True if a parsed SSE data payload looks like an AGUI event."""
    return isinstance(chunk.get("type"), str) and chunk["type"] in AGUI_EVENT_TYPES


def new_agui_accumulator() -> dict[str, Any]:
    """Create request-local state for combining streamed AGUI events."""
    return {
        "_protocol": "agui",
        "received": False,
        "stream_outcome": {"aborted": False, "error": None},
        "run": {"run_id": None, "thread_id": None, "status": None, "error": None},
        "messages": {},
        "message_order": [],
        "tool_calls": {},
        "tool_call_order": [],
        "state": None,
        "reasoning": {},
        "reasoning_order": [],
        "event_count": 0,
    }


def _ensure_message(accumulator: dict[str, Any], msg_id: str, role: str = "assistant") -> None:
    """Register a message id if not already tracked."""
    if msg_id and msg_id not in accumulator["messages"]:
        accumulator["messages"][msg_id] = {"role": role, "content": ""}
        accumulator["message_order"].append(msg_id)


def _ensure_tool_call(accumulator: dict[str, Any], tc_id: str, name: str = "") -> None:
    """Register a tool call id if not already tracked."""
    if tc_id and tc_id not in accumulator["tool_calls"]:
        accumulator["tool_calls"][tc_id] = {"name": name, "args": "", "result": None}
        accumulator["tool_call_order"].append(tc_id)


def accumulate_agui_event(accumulator: dict[str, Any], chunk: dict) -> None:
    """Process one parsed AGUI event and merge it into the accumulator.

    Handles the Start/Content/End streaming triads for text messages,
    tool calls, and reasoning.  Lifecycle events update run metadata.
    STATE_SNAPSHOT is kept; STATE_DELTA is counted only.  All other
    event types (STEP_*, ACTIVITY_*, CUSTOM, RAW, etc.) increment the
    event counter but are not stored individually.
    """
    accumulator["received"] = True
    accumulator["event_count"] += 1

    event_type = chunk.get("type", "")

    # --- Lifecycle ---
    if event_type == "RUN_STARTED":
        accumulator["run"]["run_id"] = chunk.get("runId") or chunk.get("run_id")
        accumulator["run"]["thread_id"] = chunk.get("threadId") or chunk.get("thread_id")
        accumulator["run"]["status"] = "running"

    elif event_type == "RUN_FINISHED":
        accumulator["run"]["status"] = "finished"

    elif event_type == "RUN_ERROR":
        accumulator["run"]["status"] = "error"
        accumulator["run"]["error"] = chunk.get("message") or chunk.get("error")

    # --- Text Messages ---
    elif event_type == "TEXT_MESSAGE_START":
        _ensure_message(accumulator, chunk.get("messageId", ""), chunk.get("role", "assistant"))

    elif event_type == "TEXT_MESSAGE_CONTENT":
        msg_id = chunk.get("messageId", "")
        _ensure_message(accumulator, msg_id)
        if msg_id:
            accumulator["messages"][msg_id]["content"] += chunk.get("delta", "")

    elif event_type == "TEXT_MESSAGE_CHUNK":
        msg_id = chunk.get("messageId", "")
        _ensure_message(accumulator, msg_id, chunk.get("role", "assistant"))
        if msg_id and chunk.get("delta"):
            accumulator["messages"][msg_id]["content"] += chunk["delta"]

    # TEXT_MESSAGE_END — message already tracked, nothing to do

    # --- Tool Calls ---
    elif event_type == "TOOL_CALL_START":
        _ensure_tool_call(accumulator, chunk.get("toolCallId", ""), chunk.get("toolCallName", ""))

    elif event_type == "TOOL_CALL_ARGS":
        tc_id = chunk.get("toolCallId", "")
        _ensure_tool_call(accumulator, tc_id)
        if tc_id:
            accumulator["tool_calls"][tc_id]["args"] += chunk.get("delta", "")

    elif event_type == "TOOL_CALL_RESULT":
        tc_id = chunk.get("toolCallId", "")
        _ensure_tool_call(accumulator, tc_id)
        if tc_id:
            accumulator["tool_calls"][tc_id]["result"] = chunk.get("content")

    elif event_type == "TOOL_CALL_CHUNK":
        tc_id = chunk.get("toolCallId", "")
        _ensure_tool_call(accumulator, tc_id, chunk.get("toolCallName", ""))
        if tc_id and chunk.get("delta"):
            accumulator["tool_calls"][tc_id]["args"] += chunk["delta"]

    # TOOL_CALL_END — tool call already tracked, nothing to do

    # --- State ---
    elif event_type == "STATE_SNAPSHOT":
        accumulator["state"] = chunk.get("snapshot")

    # STATE_DELTA — counted only (KISS)

    # --- Reasoning ---
    elif event_type == "REASONING_MESSAGE_START":
        msg_id = chunk.get("messageId", "")
        if msg_id and msg_id not in accumulator["reasoning"]:
            accumulator["reasoning"][msg_id] = ""
            accumulator["reasoning_order"].append(msg_id)

    elif event_type == "REASONING_MESSAGE_CONTENT":
        msg_id = chunk.get("messageId", "")
        if msg_id:
            if msg_id not in accumulator["reasoning"]:
                accumulator["reasoning"][msg_id] = ""
                accumulator["reasoning_order"].append(msg_id)
            accumulator["reasoning"][msg_id] += chunk.get("delta", "")

    # Everything else (STEP_*, ACTIVITY_*, CUSTOM, RAW, etc.) — counted only


def finalize_agui_chunks(accumulator: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return a structured AGUI completion object, or None without valid events.

    The returned dict has ``object: "agui.completion"`` and contains
    reconstructed messages, tool calls, reasoning, run metadata, and
    the latest state snapshot (if any).
    """
    if not accumulator.get("received"):
        return None

    messages = []
    for msg_id in accumulator["message_order"]:
        msg = accumulator["messages"][msg_id]
        messages.append({"message_id": msg_id, "role": msg["role"], "content": msg["content"]})

    tool_calls = []
    for tc_id in accumulator["tool_call_order"]:
        tc = accumulator["tool_calls"][tc_id]
        args: Any = tc["args"]
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        tool_calls.append(
            {
                "tool_call_id": tc_id,
                "name": tc["name"],
                "arguments": args,
                "result": tc["result"],
            }
        )

    reasoning = []
    for msg_id in accumulator["reasoning_order"]:
        reasoning.append({"message_id": msg_id, "content": accumulator["reasoning"][msg_id]})

    result: dict[str, Any] = {
        "object": "agui.completion",
        "run": accumulator["run"],
        "event_count": accumulator["event_count"],
    }
    if messages:
        result["messages"] = messages
    if tool_calls:
        result["tool_calls"] = tool_calls
    if accumulator["state"] is not None:
        result["state"] = accumulator["state"]
    if reasoning:
        result["reasoning"] = reasoning

    return result
