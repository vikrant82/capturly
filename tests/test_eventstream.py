"""Binary wire fixtures and behavior tests for the public EventStream observer."""

import base64
import json
import struct
import uuid
import zlib

import pytest

from capturly.eventstream import (
    BedrockConverseAccumulator,
    EventStreamDecoder,
    EventStreamError,
    EventStreamModelError,
    EventStreamTruncatedError,
)
from capturly.utils import is_eventstream_response


def wire_header(name, kind, value=b""):
    encoded = name.encode()
    return bytes([len(encoded)]) + encoded + bytes([kind]) + value


def string_header(name, value):
    encoded = value.encode()
    return wire_header(name, 7, struct.pack(">H", len(encoded)) + encoded)


def frame(payload=b"", headers=b""):
    """Encode independent Smithy fixture bytes (including both wire CRCs)."""
    prelude = struct.pack(">II", 16 + len(headers) + len(payload), len(headers))
    data = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + payload
    return data + struct.pack(">I", zlib.crc32(data))


def event(name, payload, message_type="event"):
    type_header = ":exception-type" if message_type == "exception" else ":event-type"
    return frame(
        json.dumps(payload, ensure_ascii=False).encode(),
        string_header(":message-type", message_type)
        + string_header(type_header, name)
        + string_header(":content-type", "application/json"),
    )


def converse_bytes(text="Hello world"):
    return b"".join(
        [
            event("messageStart", {"role": "assistant"}),
            event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": text}}),
            event("contentBlockStop", {"contentBlockIndex": 0}),
            event("messageStop", {"stopReason": "end_turn"}),
            event(
                "metadata",
                {
                    "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
                    "metrics": {"latencyMs": 123},
                },
            ),
        ]
    )


@pytest.mark.parametrize("split", [1, 2, 7, 11, 12, 15, 16, 31, 4096])
def test_split_and_coalesced_frames_preserve_exact_payloads(split):
    raw = frame(b"\xff\x00\r\n") + frame(b"second")
    decoder = EventStreamDecoder()
    messages = []
    for offset in range(0, len(raw), split):
        messages.extend(decoder.feed(raw[offset : offset + split]))
    decoder.finish()
    assert [message.payload for message in messages] == [b"\xff\x00\r\n", b"second"]


def test_all_typed_headers_and_unknown_names_are_decoded():
    headers = b"".join(
        [
            wire_header("true", 0),
            wire_header("false", 1),
            wire_header("byte", 2, struct.pack(">b", -5)),
            wire_header("short", 3, struct.pack(">h", -257)),
            wire_header("int", 4, struct.pack(">i", -65537)),
            wire_header("long", 5, struct.pack(">q", -4294967297)),
            wire_header("blob", 6, b"\x00\x02\xff\x00"),
            string_header("unrecognized-name", "café"),
            wire_header("time", 8, struct.pack(">q", 1700000000123)),
            wire_header("uuid", 9, bytes.fromhex("00112233445566778899aabbccddeeff")),
        ]
    )
    (message,) = EventStreamDecoder().feed(frame(headers=headers))
    assert message.headers == {
        "true": True,
        "false": False,
        "byte": -5,
        "short": -257,
        "int": -65537,
        "long": -4294967297,
        "blob": b"\xff\x00",
        "unrecognized-name": "café",
        "time": 1700000000123,
        "uuid": uuid.UUID("00112233-4455-6677-8899-aabbccddeeff"),
    }


@pytest.mark.parametrize("position", [8, -1])
def test_either_crc_failure_rejects_corrupt_frame(position):
    raw = bytearray(frame(b"payload"))
    raw[position] ^= 1
    with pytest.raises(EventStreamError):
        list(EventStreamDecoder().feed(bytes(raw)))


def test_valid_prefix_is_observable_before_coalesced_crc_failure():
    corrupt = bytearray(frame(b"bad"))
    corrupt[-1] ^= 1
    messages = EventStreamDecoder().feed(frame(b"valid") + corrupt)
    assert next(messages).payload == b"valid"
    with pytest.raises(EventStreamError):
        next(messages)


@pytest.mark.parametrize("total,headers", [(15, 0), (16, 1), (65, 0), (32, 17)])
def test_invalid_or_over_budget_lengths_fail_at_prelude(total, headers):
    prelude = struct.pack(">II", total, headers)
    raw = prelude + struct.pack(">I", zlib.crc32(prelude))
    with pytest.raises(EventStreamError):
        list(EventStreamDecoder(max_message_size=64, max_headers_size=16).feed(raw))


def test_exact_observation_budget_boundary_is_accepted():
    raw = frame(b"x" * 48)
    (message,) = EventStreamDecoder(max_message_size=64).feed(raw)
    assert message.payload == b"x" * 48


@pytest.mark.parametrize(
    "headers",
    [
        b"\x00\x00",
        b"\x05name",
        wire_header("x", 7, b"\x00\x03a"),
        wire_header("x", 10),
        string_header("x", "a") + string_header("x", "b"),
        wire_header("x", 4, b"\x00"),
        wire_header("x", 7, b"\x00\x01\xff"),
    ],
)
def test_invalid_headers_are_observer_errors(headers):
    with pytest.raises(EventStreamError):
        list(EventStreamDecoder().feed(frame(headers=headers)))


@pytest.mark.parametrize("cut", [1, 7, 11, 12, 16, 20])
def test_final_partial_frame_is_not_complete(cut):
    decoder = EventStreamDecoder()
    assert list(decoder.feed(frame(b"payload")[:cut])) == []
    with pytest.raises(EventStreamTruncatedError):
        decoder.finish()


def test_tool_reasoning_and_text_keep_indexes_and_metadata_after_message_stop():
    raw = b"".join(
        [
            event("messageStart", {"role": "assistant"}),
            event(
                "contentBlockStart",
                {
                    "contentBlockIndex": 2,
                    "start": {"toolUse": {"toolUseId": "call-1", "name": "weather"}},
                },
            ),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 2, "delta": {"toolUse": {"input": '{"city":"'}}},
            ),
            event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Hello "}}),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 1, "delta": {"reasoningContent": {"text": "Think "}}},
            ),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 2, "delta": {"toolUse": {"input": 'Paris"}'}}},
            ),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 1, "delta": {"reasoningContent": {"text": "carefully"}}},
            ),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 1, "delta": {"reasoningContent": {"signature": "sig-"}}},
            ),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 1, "delta": {"reasoningContent": {"signature": "exact"}}},
            ),
            event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "world"}}),
            event("contentBlockStop", {"contentBlockIndex": 2}),
            event("contentBlockStop", {"contentBlockIndex": 1}),
            event("contentBlockStop", {"contentBlockIndex": 0}),
            event("futureEvent", {"opaque": "ignored"}),
            event(
                "messageStop", {"stopReason": "tool_use", "additionalModelResponseFields": {"x": 1}}
            ),
            event(
                "metadata",
                {
                    "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
                    "metrics": {"latencyMs": 123},
                },
            ),
        ]
    )
    accumulator = BedrockConverseAccumulator()
    for message in EventStreamDecoder().feed(raw):
        accumulator.accept(message)
    accumulator.finish()
    assert accumulator.result() == {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Hello world"},
                    {
                        "reasoningContent": {
                            "reasoningText": {"text": "Think carefully", "signature": "sig-exact"}
                        }
                    },
                    {
                        "toolUse": {
                            "toolUseId": "call-1",
                            "name": "weather",
                            "input": {"city": "Paris"},
                        }
                    },
                ],
            }
        },
        "stopReason": "tool_use",
        "additionalModelResponseFields": {"x": 1},
        "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
        "metrics": {"latencyMs": 123},
    }


def test_sparse_indexes_and_binary_reasoning_fragments_are_preserved():
    accumulator = BedrockConverseAccumulator()
    for message in EventStreamDecoder().feed(
        b"".join(
            [
                event("messageStart", {"role": "assistant"}),
                event(
                    "contentBlockDelta",
                    {
                        "contentBlockIndex": 2,
                        "delta": {
                            "reasoningContent": {
                                "redactedContent": base64.b64encode(b"\xff").decode()
                            }
                        },
                    },
                ),
                event(
                    "contentBlockDelta",
                    {
                        "contentBlockIndex": 2,
                        "delta": {
                            "reasoningContent": {
                                "redactedContent": base64.b64encode(b"\x00").decode()
                            }
                        },
                    },
                ),
            ]
        )
    ):
        accumulator.accept(message)
    response = accumulator.result()
    assert response is not None
    assert response["output"]["message"]["content"] == [
        {},
        {},
        {"reasoningContent": {"redactedContent": "/wA="}},
    ]
    with pytest.raises(EventStreamTruncatedError):
        accumulator.finish()


def test_invalid_tool_json_retains_fragments_and_cannot_complete():
    accumulator = BedrockConverseAccumulator()
    raw = b"".join(
        [
            event(
                "contentBlockStart",
                {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "call-1", "name": "weather"}},
                },
            ),
            event(
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"city":'}}},
            ),
            event("contentBlockStop", {"contentBlockIndex": 0}),
        ]
    )
    with pytest.raises(EventStreamError):
        for message in EventStreamDecoder().feed(raw):
            accumulator.accept(message)
    assert accumulator.result() == {
        "output": {
            "message": {
                "content": [
                    {"toolUse": {"toolUseId": "call-1", "name": "weather", "input": '{"city":'}},
                ]
            }
        },
    }
    with pytest.raises(EventStreamError):
        accumulator.finish()


@pytest.mark.parametrize(
    "raw",
    [
        event("modelStreamErrorException", {"message": "model failed"}, "exception"),
        frame(
            headers=string_header(":message-type", "error")
            + string_header(":error-code", "InternalError")
            + string_header(":error-message", "backend failed")
        ),
        event("validationException", {"message": "invalid"}),
    ],
)
def test_model_errors_preserve_partial_output_without_completing(raw):
    accumulator = BedrockConverseAccumulator()
    prefix = event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "partial"}})
    messages = EventStreamDecoder().feed(prefix + raw)
    accumulator.accept(next(messages))
    with pytest.raises(EventStreamModelError):
        accumulator.accept(next(messages))
    response = accumulator.result()
    assert response is not None
    assert response["output"]["message"]["content"] == [{"text": "partial"}]


@pytest.mark.parametrize(
    "name,payload",
    [
        ("Records", {"records": []}),
        ("metadata", {"progress": 100}),
        ("messageStart", {"applicationState": "ready"}),
    ],
)
def test_unknown_events_do_not_invent_a_bedrock_response(name, payload):
    accumulator = BedrockConverseAccumulator()
    for message in EventStreamDecoder().feed(event(name, payload)):
        accumulator.accept(message)
    accumulator.finish()
    assert accumulator.result() is None


def test_known_bedrock_empty_stream_is_incomplete_not_generic_completion():
    accumulator = BedrockConverseAccumulator(expect_bedrock=True)
    with pytest.raises(EventStreamTruncatedError):
        accumulator.finish()
    assert accumulator.result() is None


@pytest.mark.parametrize(
    "raw",
    [
        event("messageStart", "not an object"),
        event("messageStart", {}),
        event("messageStart", {"role": None}),
        event("messageStop", {"stopReason": "end_turn"}),
        event("messageStart", {"role": "assistant"}) + event("messageStart", {"role": "assistant"}),
        event("messageStart", {"role": "assistant"}) + event("messageStop", {}),
        event("messageStart", {"role": "assistant"}) + event("messageStop", {"stopReason": None}),
        event("messageStart", {"role": "assistant"})
        + event("messageStop", {"stopReason": "end_turn"})
        + event("messageStop", {"stopReason": "end_turn"}),
        converse_bytes() + event("contentBlockStop", {"contentBlockIndex": 0}),
    ],
)
def test_known_bedrock_invalid_start_stop_lifecycle_latches_an_error(raw):
    accumulator = BedrockConverseAccumulator(expect_bedrock=True)
    with pytest.raises(EventStreamError):
        for message in EventStreamDecoder().feed(raw):
            accumulator.accept(message)
    with pytest.raises(EventStreamError):
        accumulator.finish()


def test_known_bedrock_stop_with_open_block_preserves_partial_text_without_completion():
    accumulator = BedrockConverseAccumulator(expect_bedrock=True)
    raw = (
        event("messageStart", {"role": "assistant"})
        + event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "partial"}})
        + event("messageStop", {"stopReason": "end_turn"})
    )
    with pytest.raises(EventStreamError):
        for message in EventStreamDecoder().feed(raw):
            accumulator.accept(message)
    assert accumulator.result() == {
        "output": {"message": {"role": "assistant", "content": [{"text": "partial"}]}},
    }
    with pytest.raises(EventStreamError):
        accumulator.finish()


def test_known_bedrock_valid_lifecycle_reads_metadata_after_stop():
    accumulator = BedrockConverseAccumulator(expect_bedrock=True)
    for message in EventStreamDecoder().feed(converse_bytes()):
        accumulator.accept(message)
    accumulator.finish()
    assert accumulator.result() == {
        "output": {"message": {"role": "assistant", "content": [{"text": "Hello world"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
        "metrics": {"latencyMs": 123},
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Application/Vnd.Amazon.EventStream; charset=binary", True),
        ("application/vnd.amazon.eventstream", True),
        ("text/event-stream", False),
        ("application/vnd.amazon.eventstream-other", False),
    ],
)
def test_transport_detection_is_exact_media_type_not_vendor_path(value, expected):
    assert is_eventstream_response({"CONTENT-TYPE": value}) is expected
