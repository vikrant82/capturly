"""Real local HTTP tests for binary streaming, capture, and exact replay."""

import base64
import contextlib
import http.client
import json
import socket
import struct
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import pytest

from capturly import storage
from capturly.handler import MockServiceHandler
from capturly.server import ThreadedHTTPServer
from tests.test_eventstream import converse_bytes, event, frame, string_header

PATH = "/model/amazon.nova/converse-stream"
REQUEST = b'{"messages":[{"role":"user","content":[{"text":"Hello"}]}]}'


@dataclass
class ResponsePlan:
    body: bytes
    status: int = 200
    media_type: str = "Application/Vnd.Amazon.EventStream; charset=binary"
    advertised_length: Optional[int] = None
    gate: Optional[threading.Event] = None
    sent: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    chunked: bool = False
    end_chunk: bool = True


@contextlib.contextmanager
def running_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def upstream():
    """External HTTP boundary with actual sockets and optional barrier gating."""
    plans = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            plan = plans[self.path]
            self.send_response(plan.status)
            self.send_header("Content-Type", plan.media_type)
            self.send_header("Connection", "close, X-Hop")
            self.send_header("X-Hop", "must-not-forward")
            self.send_header("X-Amzn-RequestId", "request-123")
            if plan.chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header(
                    "Content-Length",
                    str(
                        len(plan.body) if plan.advertised_length is None else plan.advertised_length
                    ),
                )
            self.end_headers()
            self.close_connection = True

            def write(chunk):
                if plan.chunked:
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()

            try:
                if plan.gate:
                    write(plan.body[:10])
                    plan.sent.set()
                    if not plan.gate.wait(timeout=5):
                        return
                    write(plan.body[10:])
                else:
                    write(plan.body)
                if plan.chunked and plan.end_chunk:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except OSError:
                pass  # A disconnected proxy is an expected external failure path.
            finally:
                plan.finished.set()

        def log_message(self, format, *args):
            pass

    with running_server(ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)) as port:
        yield f"http://127.0.0.1:{port}", plans
        for plan in plans.values():
            if plan.gate:
                plan.gate.set()


@contextlib.contextmanager
def proxy_server(backend, mode="log", combine=False):
    # Match production server bootstrap before HYBRID/REPLAY access cached paths.
    storage.get_recordings_dir()
    # A fresh class configures real handlers without mutating shared global handler state.
    handler = type(
        "EventStreamTestHandler",
        (MockServiceHandler,),
        {
            "mode": mode,
            "backend_url": backend,
            "combine_chunks": combine,
            "traffic_logger": None,
            "pipe_name": "binary-test",
            "replay_delay_ms": 0,
        },
    )
    with running_server(ThreadedHTTPServer(("127.0.0.1", 0), handler)) as port:
        yield port


def request(port, path=PATH, body=REQUEST):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path, body, {"Content-Type": "application/json"})
    return connection, connection.getresponse()


@pytest.mark.parametrize(
    "mode,combine", [("log", False), ("log", True), ("record", False), ("hybrid", False)]
)
@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("status", [200, 400])
def test_first_binary_fragment_arrives_before_upstream_finishes(
    temp_recordings_dir, upstream, mode, combine, chunked, status
):
    backend, plans = upstream
    gate = threading.Event()
    plan = ResponsePlan(converse_bytes(), gate=gate, chunked=chunked, status=status)
    plans[PATH] = plan
    with proxy_server(backend, mode, combine) as port:
        connection, response = request(port)
        try:
            assert response.status == status
            assert plan.sent.wait(timeout=5)
            assert response.read(10) == plan.body[:10]
            assert not plan.finished.is_set()
            assert response.getheader("Content-Length") is None
            assert response.getheader("Transfer-Encoding") is None
            assert response.getheader("Connection") == "close"
            assert response.getheader("X-Hop") is None
            assert response.getheader("X-Amzn-RequestId") == "request-123"
            gate.set()
            assert response.read() == plan.body[10:]
        finally:
            gate.set()
            connection.close()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["source_name"] == "binary-test"
        assert entry["backend_url"] == backend
        assert entry["eventstream"] is True
        assert entry["eventstream_status"] == "completed"
        assert entry["response_body_encoding"] == "base64"
        assert base64.b64decode(entry["response_body"]) == plan.body
        assert entry["response_body_size"] == len(plan.body)
        assert entry["duration_ms"] >= 0
        assert entry["eventstream_response"] == {
            "output": {"message": {"role": "assistant", "content": [{"text": "Hello world"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
            "metrics": {"latencyMs": 123},
        }
        assert entry["ai_insights"]["protocol"] == "bedrock_converse"
        assert entry["ai_insights"]["response"]["usage"]["total_tokens"] == 10
    else:
        cache_key = storage.get_cache_key("POST", PATH, REQUEST)
        saved = json.loads((temp_recordings_dir / f"{cache_key}.json").read_text())
        assert saved["body_encoding"] == "base64"
        assert base64.b64decode(saved["response_body"]) == plan.body
        assert not (temp_recordings_dir / f"{cache_key}.sse").exists()


@pytest.mark.parametrize("mode", ["record", "hybrid"])
def test_recording_replays_exact_bytes_without_backend(temp_recordings_dir, upstream, mode):
    backend, plans = upstream
    raw = converse_bytes("café\n\x00")
    plans[PATH] = ResponsePlan(raw)
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == 200
        assert response.read() == raw
        connection.close()
    with proxy_server(None, "replay") as port:
        connection, response = request(port)
        assert response.status == 200
        assert response.getheader("Content-Type") == plans[PATH].media_type
        assert response.read() == raw
        connection.close()


def corrupt_stream():
    raw = bytearray(converse_bytes())
    raw[-1] ^= 1
    return bytes(raw)


def malformed_start_stream():
    return frame(
        b"not-json",
        string_header(":message-type", "event")
        + string_header(":event-type", "messageStart")
        + string_header(":content-type", "application/json"),
    )


@pytest.mark.parametrize(
    "raw,outcome",
    [
        pytest.param(b"", "aborted", id="empty"),
        pytest.param(malformed_start_stream(), "error", id="malformed-start"),
        pytest.param(event("messageStop", {"stopReason": "end_turn"}), "error", id="stop-only"),
        pytest.param(
            malformed_start_stream() + event("messageStop", {"stopReason": "end_turn"}),
            "error",
            id="malformed-start-then-stop",
        ),
    ],
)
@pytest.mark.parametrize("path", [PATH, "/proxy/converse-stream"])
@pytest.mark.parametrize("mode", ["log", "record"])
def test_known_bedrock_invalid_lifecycle_never_completes_or_publishes_cache(
    temp_recordings_dir, upstream, raw, outcome, path, mode
):
    backend, plans = upstream
    plans[path] = ResponsePlan(raw)
    with proxy_server(backend, mode) as port:
        connection, response = request(port, path)
        assert response.status == 200
        assert response.read() == raw
        connection.close()
    key = storage.get_cache_key("POST", path, REQUEST)
    assert not (temp_recordings_dir / f"{key}.json").exists()
    assert not (temp_recordings_dir / f"{key}.sse").exists()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["eventstream_status"] == outcome
        assert entry["response_body_encoding"] == "base64"
        assert base64.b64decode(entry["response_body"]) == raw
        assert entry["response_body_size"] == len(raw)
        assert "eventstream_error" in entry
        assert "eventstream_response" not in entry
        assert entry["ai_insights"]["protocol"] == "bedrock_converse"
        assert entry["ai_insights"]["request"]["roles"] == ["user"]
        assert "response" not in entry["ai_insights"]


@pytest.mark.parametrize(
    "raw,outcome",
    [
        pytest.param(
            event("modelStreamErrorException", {"message": "failure"}, "exception"),
            "error",
            id="model-exception",
        ),
        pytest.param(corrupt_stream(), "error", id="corrupt-frame"),
        pytest.param(converse_bytes()[:-3], "aborted", id="truncated-frame"),
    ],
)
@pytest.mark.parametrize("mode", ["log", "record"])
def test_non2xx_eventstream_failure_keeps_status_bytes_and_never_publishes_cache(
    temp_recordings_dir, upstream, raw, outcome, mode
):
    backend, plans = upstream
    plans[PATH] = ResponsePlan(raw, status=400)
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == 400
        assert response.read() == raw
        connection.close()
    key = storage.get_cache_key("POST", PATH, REQUEST)
    assert not (temp_recordings_dir / f"{key}.json").exists()
    assert not (temp_recordings_dir / f"{key}.sse").exists()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["status_code"] == 400
        assert entry["eventstream"] is True
        assert entry["eventstream_status"] == outcome
        assert entry["response_body_encoding"] == "base64"
        assert base64.b64decode(entry["response_body"]) == raw
        assert entry["response_body_size"] == len(raw)
        assert "eventstream_error" in entry
        assert entry["ai_insights"]["protocol"] == "bedrock_converse"
        assert entry["ai_insights"]["request"]["roles"] == ["user"]


@pytest.mark.parametrize(
    "raw,extra_length,outcome",
    [
        (converse_bytes()[:-3], 0, "aborted"),
        (converse_bytes(), 100, "aborted"),
        (corrupt_stream(), 0, "error"),
        (
            event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "partial"}}),
            0,
            "aborted",
        ),
        (
            converse_bytes()
            + event("modelStreamErrorException", {"message": "failure"}, "exception"),
            0,
            "error",
        ),
        (converse_bytes() + frame(headers=b"\x01x\x0a"), 0, "error"),
    ],
)
@pytest.mark.parametrize("mode", ["log", "record"])
@pytest.mark.parametrize("status", [200, 400])
def test_failed_observation_or_transport_never_changes_bytes_or_publishes_cache(
    temp_recordings_dir, upstream, raw, extra_length, outcome, mode, status
):
    backend, plans = upstream
    plans[PATH] = ResponsePlan(raw, advertised_length=len(raw) + extra_length, status=status)
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == status
        assert response.read() == raw  # No synthesized 502 after the native response.
        connection.close()
    key = storage.get_cache_key("POST", PATH, REQUEST)
    assert not (temp_recordings_dir / f"{key}.json").exists()
    assert not (temp_recordings_dir / f"{key}.sse").exists()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["eventstream_status"] == outcome
        assert base64.b64decode(entry["response_body"]) == raw
        assert entry["response_body_size"] == len(raw)
        assert "eventstream_error" in entry
        assert entry["eventstream_response"]["output"]["message"]["content"][0]["text"] in (
            "Hello world",
            "partial",
        )


@pytest.mark.parametrize("combine", [False, True])
def test_failed_request_does_not_contaminate_next_decoder(temp_recordings_dir, upstream, combine):
    backend, plans = upstream
    plans[PATH] = ResponsePlan(corrupt_stream())
    with proxy_server(backend, combine=combine) as port:
        connection, response = request(port)
        assert response.read() == corrupt_stream()
        connection.close()
        plans[PATH] = ResponsePlan(converse_bytes("Fresh"))
        connection, response = request(port)
        assert response.read() == converse_bytes("Fresh")
        connection.close()
    entries = storage.read_traffic_log_entries()
    assert [entry["eventstream_status"] for entry in entries] == ["error", "completed"]
    assert entries[1]["eventstream_response"]["output"]["message"]["content"] == [{"text": "Fresh"}]


@pytest.mark.parametrize("mode", ["log", "record"])
def test_missing_http_chunk_terminator_is_not_clean_eof(temp_recordings_dir, upstream, mode):
    backend, plans = upstream
    raw = converse_bytes()
    plans[PATH] = ResponsePlan(raw, chunked=True, end_chunk=False)
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == 200
        assert response.read() == raw
        connection.close()
    key = storage.get_cache_key("POST", PATH, REQUEST)
    assert not (temp_recordings_dir / f"{key}.json").exists()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["eventstream_status"] == "aborted"
        assert base64.b64decode(entry["response_body"]) == raw


@pytest.mark.parametrize("mode", ["log", "record"])
@pytest.mark.parametrize("status", [200, 400])
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        frame(
            b"\xff\x00\n",
            string_header(":message-type", "event") + string_header(":event-type", "Records"),
        ),
    ],
)
def test_unrelated_aws_stream_is_forwarded_and_captured_without_bedrock_insights(
    temp_recordings_dir, upstream, mode, status, raw
):
    backend, plans = upstream
    path = "/any-vendor/records"
    plans[path] = ResponsePlan(raw, status=status)
    with proxy_server(backend, mode) as port:
        connection, response = request(port, path, b"{}")
        assert response.status == status
        assert response.read() == raw
        connection.close()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["eventstream_status"] == "completed"
        assert "ai_insights" not in entry
        assert "eventstream_response" not in entry
        assert base64.b64decode(entry["response_body"]) == raw
    else:
        with proxy_server(None, "replay") as port:
            connection, response = request(port, path, b"{}")
            assert response.status == status
            assert response.read() == raw
            connection.close()


@pytest.mark.parametrize("mode", ["log", "record"])
def test_native_json_http_errors_remain_normal_responses(temp_recordings_dir, upstream, mode):
    backend, plans = upstream
    raw = b'{"message":"Access denied"}'
    plans[PATH] = ResponsePlan(raw, status=403, media_type="application/json")
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == 403
        assert response.getheader("Content-Type") == "application/json"
        assert response.read() == raw
        connection.close()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["status_code"] == 403
        assert entry["response_body"] == {"message": "Access denied"}
        assert "eventstream" not in entry
    else:
        key = storage.get_cache_key("POST", PATH, REQUEST)
        recording = json.loads((temp_recordings_dir / f"{key}.json").read_text())
        assert recording["status_code"] == 403
        assert recording["response_body"] == raw.decode()


@pytest.mark.parametrize("mode", ["log", "record"])
@pytest.mark.parametrize("status", [200, 400])
def test_capture_storage_failure_does_not_append_another_http_response(
    temp_recordings_dir, upstream, mode, status
):
    backend, plans = upstream
    raw = converse_bytes()
    plans[PATH] = ResponsePlan(raw, status=status)
    filename = (
        "traffic_log.jsonl"
        if mode == "log"
        else f"{storage.get_cache_key('POST', PATH, REQUEST)}.json"
    )
    (temp_recordings_dir / filename).mkdir()
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == status
        assert response.read() == raw
        connection.close()


@pytest.mark.parametrize("mode", ["log", "record"])
@pytest.mark.parametrize("status", [200, 400])
def test_downstream_disconnect_cannot_publish_completed_capture(
    temp_recordings_dir, upstream, mode, status
):
    backend, plans = upstream
    raw = converse_bytes() + frame(b"x" * (1024 * 1024))
    gate = threading.Event()
    plan = ResponsePlan(raw, gate=gate, status=status)
    plans[PATH] = plan
    with proxy_server(backend, mode) as port:
        connection, response = request(port)
        assert response.status == status
        assert response.read(10) == raw[:10]
        sock = getattr(response.fp.raw, "_sock")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        response.close()
        connection.close()
        gate.set()
        assert plan.finished.wait(timeout=5)
    key = storage.get_cache_key("POST", PATH, REQUEST)
    assert not (temp_recordings_dir / f"{key}.json").exists()
    if mode == "log":
        (entry,) = storage.read_traffic_log_entries()
        assert entry["eventstream_status"] == "aborted"
        assert entry["response_body_size"] < len(raw)
