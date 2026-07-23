"""Server-Sent Events forwarding, logging, and chunk combining.

Supports OpenAI chat-completion chunks and AGUI (Agent GUI) protocol
events.  The protocol is auto-detected from the first parseable SSE
data payload.
"""

import json
import time

from .inspection import agui as agui_inspection


def maybe_parse_json_text(text):
    """Parse JSON-looking SSE data, otherwise leave it as plain text."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None

    try:
        return json.loads(stripped)
    except Exception:
        return None


def append_jsonl_entry(file_path, entry):
    """Append one JSON value as a line to an SSE event log."""
    with open(file_path, "a", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False)
        f.write("\n")


def log_sse_event(handler, event_log_file, sequence, event_lines):
    """Write JSON payloads directly; otherwise keep a structured SSE entry."""
    data_lines = []
    comments = []
    extra_fields = {}
    event_entry = {
        "timestamp_ms": int(time.time() * 1000),
        "sequence": sequence,
    }

    for raw_line in event_lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            continue

        if line.startswith(":"):
            comments.append(line[1:].lstrip())
            continue

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]

        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event_entry["event"] = value
        elif field == "id":
            event_entry["id"] = value
        elif field == "retry":
            try:
                event_entry["retry_ms"] = int(value)
            except ValueError:
                event_entry["retry_ms"] = value
        else:
            extra_fields[field] = value

    if comments:
        event_entry["comments"] = comments
    if extra_fields:
        event_entry["fields"] = extra_fields

    if data_lines:
        data_text = "\n".join(data_lines)
        data_json = maybe_parse_json_text(data_text)
        if data_json is not None:
            append_jsonl_entry(event_log_file, data_json)
            return
        event_entry["data"] = data_text

    append_jsonl_entry(event_log_file, event_entry)


def new_chunk_accumulator():
    """Create request-local state for combining streamed chat-completion chunks."""
    return {
        "received": False,
        "top_level": {},
        "choices": {},
        "choice_order": [],
        "stream_outcome": {"aborted": False, "error": None},
    }


def append_chunk_value(message, field, value):
    """Append streamed text fields while retaining non-text values."""
    if value is None:
        return
    if field not in message or message[field] is None:
        message[field] = value
    elif isinstance(message[field], str) and isinstance(value, str):
        message[field] += value
    elif isinstance(message[field], list) and isinstance(value, list):
        message[field].extend(value)
    else:
        message[field] = value


def merge_function_call(message, function_call):
    """Merge legacy function-call name and argument fragments."""
    if not isinstance(function_call, dict):
        return
    accumulated = message.setdefault("function_call", {})
    name = function_call.get("name")
    if name and not accumulated.get("name"):
        accumulated["name"] = name
    arguments = function_call.get("arguments")
    if isinstance(arguments, str):
        append_chunk_value(accumulated, "arguments", arguments)
    for field, value in function_call.items():
        if field not in ("name", "arguments") and value is not None:
            accumulated.setdefault(field, value)


def merge_tool_call(choice_state, tool_call):
    """Merge one streamed tool-call delta using its per-choice index."""
    index = tool_call.get("index", 0)
    tool_calls = choice_state["tool_calls"]
    if index not in tool_calls:
        tool_calls[index] = {"_index": index, "function": {}}
        choice_state["tool_call_order"].append(index)

    accumulated = tool_calls[index]
    for field in ("id", "type"):
        value = tool_call.get(field)
        if value is not None and (field not in accumulated or not accumulated[field]):
            accumulated[field] = value

    function = tool_call.get("function") or {}
    for field, value in function.items():
        if field == "arguments" and isinstance(value, str):
            append_chunk_value(accumulated["function"], field, value)
        elif field == "name" and isinstance(value, str):
            accumulated["function"].setdefault("name", value)
        elif value is not None and field not in accumulated["function"]:
            accumulated["function"][field] = value

    for field, value in tool_call.items():
        if field not in ("index", "id", "type", "function") and value is not None:
            accumulated.setdefault(field, value)


def merge_choice(accumulator, choice):
    """Merge one choice delta and terminal fields into request-local state."""
    index = choice.get("index", 0)
    choices = accumulator["choices"]
    if index not in choices:
        choices[index] = {
            "index": index,
            "message": {},
            "fields": {},
            "tool_calls": {},
            "tool_call_order": [],
        }
        accumulator["choice_order"].append(index)

    state = choices[index]
    fields = state["fields"]
    for field, value in choice.items():
        if field in ("index", "delta", "message"):
            continue
        if field == "finish_reason" and value is not None:
            fields[field] = value
        elif value is not None and (field not in fields or fields[field] is None):
            fields[field] = value

    delta = choice.get("delta") or {}
    message = state["message"]
    if isinstance(delta, dict):
        _merge_message_fields(state, message, delta)

    incoming_message = choice.get("message")
    if isinstance(incoming_message, dict):
        _merge_message_fields(state, message, incoming_message)


def _merge_message_fields(state, message, incoming):
    """Merge message fields shared by delta and non-streaming choice payloads."""
    for field, value in incoming.items():
        if field == "tool_calls" and isinstance(value, list):
            for tool_call in value:
                if isinstance(tool_call, dict):
                    merge_tool_call(state, tool_call)
        elif field == "role":
            if value is not None and not message.get("role"):
                message["role"] = value
        elif field == "function_call":
            merge_function_call(message, value)
        elif field in ("content", "reasoning_content", "refusal"):
            append_chunk_value(message, field, value)
        elif value is not None and (field not in message or message[field] is None):
            message[field] = value


def accumulate_sse_event(accumulator, event_lines):
    """Parse one SSE event and merge it into request state.

    Auto-detects the protocol from the first parseable JSON payload:
    OpenAI chat-completion chunks (``choices`` list) are merged via the
    existing OpenAI combiner; AGUI events (``type`` field) are merged
    via :mod:`capturly.inspection.agui`.  Subsequent events follow the
    detected protocol.
    """
    data_lines = []
    for raw_line in event_lines:
        line = raw_line.rstrip("\r\n")
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)

    if not data_lines:
        return
    data_text = "\n".join(data_lines).strip()
    if not data_text or data_text == "[DONE]":
        return
    try:
        chunk = json.loads(data_text)
    except (TypeError, ValueError):
        return
    if not isinstance(chunk, dict):
        return

    # --- Protocol detection on first parseable event ---
    if accumulator.get("_protocol") is None:
        if isinstance(chunk.get("choices"), list):
            accumulator["_protocol"] = "openai"
        elif agui_inspection.is_agui_event(chunk):
            # Switch accumulator to AGUI mode, preserving stream_outcome ref
            outcome = accumulator["stream_outcome"]
            accumulator.clear()
            accumulator.update(agui_inspection.new_agui_accumulator())
            accumulator["stream_outcome"] = outcome
        else:
            return

    # --- AGUI path ---
    if accumulator.get("_protocol") == "agui":
        agui_inspection.accumulate_agui_event(accumulator, chunk)
        return

    # --- OpenAI path ---
    if not isinstance(chunk.get("choices"), list):
        return

    valid_choices = [choice for choice in chunk["choices"] if isinstance(choice, dict)]
    if not valid_choices:
        return

    accumulator["received"] = True
    for field, value in chunk.items():
        if field not in ("choices", "object") and value is not None:
            if field not in accumulator["top_level"] or accumulator["top_level"][field] is None:
                accumulator["top_level"][field] = value
    for choice in valid_choices:
        merge_choice(accumulator, choice)


def finalize_sse_chunks(accumulator):
    """Return a combined response body or None without valid chunks.

    Dispatches to the AGUI or OpenAI finalizer based on the detected
    protocol.  Returns None when no valid events were received.
    """
    if not accumulator.get("received"):
        return None

    if accumulator.get("_protocol") == "agui":
        return agui_inspection.finalize_agui_chunks(accumulator)

    response_body = dict(accumulator["top_level"])
    response_body["object"] = "chat.completion"
    choices = []
    for index in accumulator["choice_order"]:
        state = accumulator["choices"][index]
        choice = {"index": state["index"]}
        choice.update(state["fields"])
        message = dict(state["message"])
        if state["tool_call_order"]:
            tool_calls = []
            for tool_index in state["tool_call_order"]:
                tool_call = dict(state["tool_calls"][tool_index])
                tool_call.pop("_index", None)
                tool_calls.append(tool_call)
            message["tool_calls"] = tool_calls
        choice["message"] = message
        choices.append(choice)
    response_body["choices"] = choices
    return response_body


def respond_sse_stream(
    handler, response, status, headers=None, event_log_file=None, accumulator=None
):
    """Forward an SSE response incrementally as lines arrive from the backend."""
    handler.close_connection = True
    handler.send_response(status)
    if headers:
        for name, value in headers.items():
            if name.lower() not in ["content-length", "transfer-encoding", "connection"]:
                handler.send_header(name, value)
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.flush()

    event_lines = []
    event_count = 0
    stream_outcome = (
        accumulator["stream_outcome"]
        if accumulator is not None
        else {"aborted": False, "error": None}
    )

    try:
        while True:
            chunk = response.readline()
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()

            line = chunk.decode("utf-8", errors="replace")
            if not event_log_file and accumulator is None:
                continue

            if line in ["\n", "\r\n"]:
                if event_lines:
                    if accumulator is not None:
                        accumulate_sse_event(accumulator, event_lines)
                    if event_log_file:
                        event_count += 1
                        handler._enqueue_sse_event_log(event_log_file, event_count, event_lines)
                    event_lines = []
            else:
                event_lines.append(line)
    except (BrokenPipeError, ConnectionResetError):
        handler.log_message("⚠ SSE client disconnected")
        stream_outcome["aborted"] = True
        stream_outcome["error"] = "client_disconnected"
    except Exception as e:
        handler.log_message(f"✗ SSE stream error: {e}")
        stream_outcome["aborted"] = True
        stream_outcome["error"] = str(e)
    finally:
        if event_log_file and event_lines:
            event_count += 1
            handler._enqueue_sse_event_log(event_log_file, event_count, event_lines)
        if accumulator is not None and event_lines:
            accumulate_sse_event(accumulator, event_lines)

        if event_log_file:
            handler.log_message(f"🧾 SSE events captured: {event_count}")


# Private names retained from the POC while the implementation lives in this module.
_maybe_parse_json_text = maybe_parse_json_text
_append_jsonl_entry = append_jsonl_entry
_log_sse_event = log_sse_event
_new_sse_chunk_accumulator = new_chunk_accumulator
_append_chunk_value = append_chunk_value
_merge_sse_function_call = merge_function_call
_merge_sse_tool_call = merge_tool_call
_merge_sse_choice = merge_choice
_accumulate_sse_event = accumulate_sse_event
_finalize_sse_chunks = finalize_sse_chunks
_respond_sse_stream = respond_sse_stream
