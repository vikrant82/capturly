"""Amazon EventStream byte transport and request-local, optional observation.

Forwarding never depends on decoding: corrupt or unfamiliar streams still reach
the client byte-for-byte. Observer budgets bound retained *frame* data, not the
wire protocol's service-side size limits. Captured response bodies remain in
memory, as with the existing recording API.
"""

import base64
import copy
import http.client
import json
import struct
import uuid
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_MAX_MESSAGE_SIZE = 32 * 1024 * 1024
DEFAULT_MAX_HEADERS_SIZE = 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024


class EventStreamError(ValueError):
    """Observation failed; callers must not treat the stream as complete."""


class EventStreamTruncatedError(EventStreamError):
    """EOF arrived before a complete frame or observed Bedrock message."""


class EventStreamModelError(EventStreamError):
    """An AWS modeled exception or unmodeled error was received."""


@dataclass
class EventStreamMessage:
    """A CRC-validated message with typed headers and unmodified payload bytes."""

    headers: dict[str, Any]
    payload: bytes


def _decode_headers(raw):
    headers = {}
    offset = 0

    def take(size):
        nonlocal offset
        if offset + size > len(raw):
            raise EventStreamError("header exceeds advertised headers length")
        value = raw[offset : offset + size]
        offset += size
        return value

    while offset < len(raw):
        name_size = take(1)[0]
        if not name_size:
            raise EventStreamError("empty header name")
        name = take(name_size).decode("utf-8")
        if name in headers:
            raise EventStreamError("duplicate header name")
        kind = take(1)[0]
        if kind in (0, 1):
            value = kind == 0
        elif kind in (2, 3, 4, 5, 8):
            fmt = {2: ">b", 3: ">h", 4: ">i", 5: ">q", 8: ">q"}[kind]
            value = struct.unpack(fmt, take(struct.calcsize(fmt)))[0]
        elif kind in (6, 7):
            size = struct.unpack(">H", take(2))[0]
            value = take(size)
            if kind == 7:
                value = value.decode("utf-8")
        elif kind == 9:
            value = uuid.UUID(bytes=take(16))
        else:
            # Type indicators are a closed enum; an unknown width cannot be skipped safely.
            raise EventStreamError(f"unknown header type: {kind}")
        headers[name] = value
    return headers


class EventStreamDecoder:
    """Incremental, single-consumer Smithy binary decoder (not thread-safe).

    ``feed`` yields validated messages across arbitrary byte boundaries. Exhaust
    each iterator before feeding again; a valid prefix is yielded even when a
    later coalesced frame fails. CRC, length and header failures latch permanently
    and raise ``EventStreamError``. ``finish`` checks final truncation. Configurable
    budgets protect observation memory; they do not constrain byte forwarding.
    """

    def __init__(
        self,
        max_message_size=DEFAULT_MAX_MESSAGE_SIZE,
        max_headers_size=DEFAULT_MAX_HEADERS_SIZE,
    ):
        """Set local observation budgets; raise ValueError for invalid budgets."""
        if max_message_size < 16 or max_headers_size < 0:
            raise ValueError("invalid EventStream observation budgets")
        self.max_message_size = max_message_size
        self.max_headers_size = max_headers_size
        self._buffer = bytearray()
        self._total = None
        self._headers_size = 0
        self._error = None

    def feed(self, data: bytes) -> Iterator[EventStreamMessage]:
        """Yield intact messages, raising on corruption without resynchronizing.

        Only one advertised frame is buffered, even for large coalesced inputs.
        The caller must exhaust the iterator to consume all supplied bytes.
        """
        if self._error is not None:
            raise self._error
        view = memoryview(data)
        offset = 0
        try:
            while offset < len(view):
                target = self._total if self._total is not None else 12
                size = min(target - len(self._buffer), len(view) - offset)
                self._buffer.extend(view[offset : offset + size])
                offset += size
                if len(self._buffer) < target:
                    continue
                if self._total is None:
                    total, headers_size, prelude_crc = struct.unpack(">III", self._buffer)
                    if zlib.crc32(self._buffer[:8]) != prelude_crc:
                        raise EventStreamError("prelude CRC mismatch")
                    if total < 16 or headers_size > total - 16:
                        raise EventStreamError("invalid advertised message lengths")
                    if total > self.max_message_size or headers_size > self.max_headers_size:
                        raise EventStreamError("EventStream observation budget exceeded")
                    self._total = total
                    self._headers_size = headers_size
                    continue
                raw = bytes(self._buffer)
                expected_crc = struct.unpack(">I", raw[-4:])[0]
                if zlib.crc32(raw[:-4]) != expected_crc:
                    raise EventStreamError("message CRC mismatch")
                headers_end = 12 + self._headers_size
                message = EventStreamMessage(
                    _decode_headers(raw[12:headers_end]), raw[headers_end:-4]
                )
                self._buffer.clear()
                self._total = None
                yield message
        except (EventStreamError, UnicodeError) as error:
            self._error = (
                error if isinstance(error, EventStreamError) else EventStreamError(str(error))
            )
            self._buffer.clear()
            raise self._error from None

    def finish(self) -> None:
        """Validate EOF; raise the latched failure or EventStreamTruncatedError."""
        if self._error is not None:
            raise self._error
        if self._buffer:
            self._error = EventStreamTruncatedError("truncated EventStream frame")
            self._buffer.clear()
            raise self._error


def _merge_native(target, incoming):
    for key, value in incoming.items():
        if isinstance(value, dict):
            existing = target.setdefault(key, {})
            if not isinstance(existing, dict):
                raise EventStreamError("inconsistent content block type")
            _merge_native(existing, value)
        elif isinstance(value, str) and isinstance(target.get(key), str):
            target[key] += value
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key].extend(copy.deepcopy(value))
        else:
            target[key] = copy.deepcopy(value)


class BedrockConverseAccumulator:
    """Accumulate native ConverseStream events, without OpenAI conversion.

    State is request-local and single-consumer, not thread-safe. Unknown events
    are ignored. Content list positions retain wire block indexes (sparse gaps
    are empty dictionaries). Model/shape errors raise EventStreamError and leave
    preceding output available through ``result``; only ``finish`` validates
    completion, after EOF so metadata following messageStop is retained.
    ``expect_bedrock=True`` uses caller-known request identity to require a valid
    messageStart/messageStop lifecycle, even before any native event is recognized.
    """

    def __init__(self, expect_bedrock=False):
        """Create request-local state; known Bedrock streams require native completion."""
        self._expect_bedrock = expect_bedrock
        self._received = False
        self._started = False
        self._role = None
        self._blocks = {}
        self._open_blocks = set()
        self._stopped = False
        self._fields = {}
        self._error = None

    def accept(self, message: EventStreamMessage) -> None:
        """Observe a validated message; latch model, JSON and shape failures."""
        if self._error is not None:
            raise self._error
        try:
            self._accept(message)
        except (EventStreamError, ValueError, TypeError, KeyError) as error:
            self._error = (
                error if isinstance(error, EventStreamError) else EventStreamError(str(error))
            )
            raise self._error from None

    def _accept(self, message):
        headers = message.headers
        message_type = headers.get(":message-type")
        name = headers.get(":event-type", "")
        if message_type in ("exception", "error") or (
            isinstance(name, str) and name.endswith("Exception")
        ):
            error_type = headers.get(":exception-type") or headers.get(":error-code") or name
            detail = headers.get(":error-message")
            if not detail and message.payload:
                try:
                    payload = json.loads(message.payload)
                    if isinstance(payload, dict):
                        detail = payload.get("message") or payload.get("Message")
                except (ValueError, UnicodeError):
                    pass
            raise EventStreamModelError(
                f"{error_type or 'stream_error'}: {detail or 'model error'}"
            )
        if name not in (
            "messageStart",
            "contentBlockStart",
            "contentBlockDelta",
            "contentBlockStop",
            "messageStop",
            "metadata",
        ):
            return
        try:
            payload = json.loads(message.payload)
        except (ValueError, UnicodeError):
            if not self._received and not self._expect_bedrock:
                return
            raise
        if not isinstance(payload, dict):
            if not self._received and not self._expect_bedrock:
                return
            raise EventStreamError("Bedrock event payload must be an object")
        # Smithy event payloads are unwrapped; also accept SDK-style tagged JSON.
        if name in payload and isinstance(payload[name], dict):
            payload = payload[name]
        if not self._received and not self._expect_bedrock:
            # Event names are not globally unique across AWS services. Require a
            # native shape before imposing Bedrock's completion requirements.
            identifying_key = {
                "messageStart": "role",
                "messageStop": "stopReason",
                "contentBlockStart": "contentBlockIndex",
                "contentBlockDelta": "contentBlockIndex",
                "contentBlockStop": "contentBlockIndex",
            }.get(name)
            if identifying_key is None or identifying_key not in payload:
                return
        if self._expect_bedrock:
            if name == "messageStart":
                if self._received or self._started or self._stopped:
                    raise EventStreamError("messageStart is not the first native event")
                if payload.get("role") not in ("assistant", "user"):
                    raise EventStreamError("invalid messageStart role")
            elif name == "messageStop":
                if not self._started or self._stopped:
                    raise EventStreamError("messageStop without an active messageStart")
                if not isinstance(payload.get("stopReason"), str) or not payload["stopReason"]:
                    raise EventStreamError("invalid messageStop reason")
                if self._open_blocks:
                    raise EventStreamError("messageStop with incomplete content blocks")
            elif name == "metadata":
                if not self._started:
                    raise EventStreamError("metadata without messageStart")
            elif self._stopped:
                raise EventStreamError("content received after messageStop")
        self._received = True
        if name == "messageStart":
            self._role = payload["role"]
            self._started = True
        elif name == "messageStop":
            self._fields.update(copy.deepcopy(payload))
            self._stopped = True
        elif name == "metadata":
            self._fields.update(copy.deepcopy(payload))
        else:
            index = payload["contentBlockIndex"]
            if type(index) is not int or not 0 <= index < 10000:
                raise EventStreamError("invalid or over-budget content block index")
            block = self._blocks.setdefault(index, {})
            if name == "contentBlockStop":
                tool = block.get("toolUse")
                if isinstance(tool, dict) and isinstance(tool.get("input"), str):
                    tool["input"] = json.loads(tool["input"])
                self._open_blocks.discard(index)
                return
            if self._stopped:
                raise EventStreamError("content received after messageStop")
            self._open_blocks.add(index)
            incoming = payload["start" if name == "contentBlockStart" else "delta"]
            if not isinstance(incoming, dict):
                raise EventStreamError("content block must be an object")
            incoming = copy.deepcopy(incoming)
            reasoning = incoming.pop("reasoningContent", None)
            if reasoning is not None:
                if not isinstance(reasoning, dict):
                    raise EventStreamError("reasoning content must be an object")
                accumulated = block.setdefault("reasoningContent", {})
                redacted = reasoning.pop("redactedContent", None)
                if redacted is not None:
                    previous = base64.b64decode(
                        accumulated.get("redactedContent", ""), validate=True
                    )
                    combined = previous + base64.b64decode(redacted, validate=True)
                    accumulated["redactedContent"] = base64.b64encode(combined).decode("ascii")
                if reasoning:
                    _merge_native(accumulated.setdefault("reasoningText", {}), reasoning)
            _merge_native(block, incoming)

    def finish(self) -> None:
        """Validate native completion at EOF, without discarding partial output."""
        if self._error is not None:
            raise self._error
        if self._expect_bedrock and not self._started:
            raise EventStreamTruncatedError("missing Bedrock messageStart")
        if (self._received or self._expect_bedrock) and (not self._stopped or self._open_blocks):
            raise EventStreamTruncatedError("incomplete Bedrock message")

    def result(self) -> Optional[dict]:
        """Return a detached native response snapshot, or None for unrelated streams.

        A snapshot is not a completion claim; callers must separately check EOF,
        decoder integrity, transport outcome and ``finish``.
        """
        if not self._received:
            return None
        message = {
            "content": [self._blocks.get(i, {}) for i in range(max(self._blocks, default=-1) + 1)]
        }
        if self._role is not None:
            message["role"] = self._role
        return copy.deepcopy({"output": {"message": message}, **self._fields})


@dataclass
class EventStreamCapture:
    """Exact captured bytes and observer outcome; only completed is cacheable."""

    body: bytes
    status: str
    error: Optional[str]
    response: Optional[dict]


def respond_eventstream(
    handler, response, status, headers=None, expect_bedrock=False
) -> EventStreamCapture:
    """Forward bytes promptly and tee them to a separate, fallible observer.

    Uses bounded ``read1`` when available and flushes every fragment. The caller
    owns the upstream response context; this function forces downstream close
    framing and contains all ordinary setup/read/write/observer failures, never
    sending a second HTTP response. Decoder failures disable observation only.
    A completed result requires clean HTTP EOF, intact frames and, when detected,
    a complete Bedrock message. ``expect_bedrock`` additionally requires a valid
    native start/stop lifecycle for caller-identified Bedrock requests. Generic
    EventStreams retain permissive observation. No state is shared between requests.
    """
    decoder = EventStreamDecoder()
    accumulator = BedrockConverseAccumulator(expect_bedrock=expect_bedrock)
    captured = bytearray()
    decoder_error = None
    model_error = None
    transport_error = None
    headers = headers or {}
    expected_length = None
    for name, value in headers.items():
        if name.lower() == "content-length":
            try:
                expected_length = int(value)
            except ValueError:
                pass

    def observe(chunk):
        nonlocal decoder_error, model_error
        if decoder_error is not None:
            return
        try:
            for message in decoder.feed(chunk):
                if model_error is None:
                    try:
                        accumulator.accept(message)
                    except Exception as error:
                        model_error = error
        except Exception as error:
            decoder_error = error

    def forward(chunk):
        captured.extend(chunk)
        try:
            handler.wfile.write(chunk)
            handler.wfile.flush()
        finally:
            observe(chunk)

    handler.close_connection = True
    try:
        handler.send_response(status)
        connection_tokens = {
            token.strip().lower()
            for name, value in headers.items()
            if name.lower() == "connection"
            for token in value.split(",")
        }
        excluded = {
            "content-length",
            "transfer-encoding",
            "connection",
            "keep-alive",
            "trailer",
            "upgrade",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
        } | connection_tokens
        for name, value in headers.items():
            if name.lower() not in excluded:
                handler.send_header(name, value)
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.flush()
        read = getattr(response, "read1", response.read)
        while True:
            try:
                chunk = read(READ_CHUNK_SIZE)
            except http.client.IncompleteRead as error:
                if error.partial:
                    forward(error.partial)
                raise
            if not chunk:
                break
            forward(chunk)
        if expected_length is not None and len(captured) != expected_length:
            raise EventStreamTruncatedError("upstream Content-Length was not satisfied")
    except Exception as error:
        transport_error = error

    if decoder_error is None:
        try:
            decoder.finish()
        except Exception as error:
            decoder_error = error
    if model_error is None:
        try:
            accumulator.finish()
        except Exception as error:
            model_error = error
    error = transport_error or decoder_error or model_error
    outcome = "completed"
    if error is not None:
        outcome = (
            "aborted"
            if transport_error or isinstance(error, EventStreamTruncatedError)
            else "error"
        )
    return EventStreamCapture(
        bytes(captured), outcome, str(error) if error else None, accumulator.result()
    )
