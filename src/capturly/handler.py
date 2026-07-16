"""HTTP request handler that delegates behavior to the configured mode."""

import sys
import threading
from http.server import BaseHTTPRequestHandler
from typing import Optional

from . import modes, proxy, sse, storage, utils


class MockServiceHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests in record, replay, hybrid, or log mode."""

    protocol_version = "HTTP/1.1"
    mode = "replay"
    backend_url = None
    replay_delay_ms = 0
    combine_chunks = False
    log_file_lock = threading.Lock()
    traffic_logger: Optional[object] = None

    def log_message(self, format, *args):
        """Log with a mode prefix and timestamp."""
        message = format % args if args else format
        sys.stderr.write(
            f"[{self.mode.upper()}] {self.log_date_time_string()} {message}\n"
        )

    def do_GET(self):
        self._handle_request("GET")

    def do_POST(self):
        self._handle_request("POST")

    def do_PUT(self):
        self._handle_request("PUT")

    def do_DELETE(self):
        self._handle_request("DELETE")

    def _handle_request(self, method):
        """Read the request body and delegate it to the configured mode."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        path = self.path
        self.log_message(f"{method} {path} ({len(body)} bytes)")

        if self.mode == "record":
            self._record_and_proxy(method, path, body)
        elif self.mode == "log":
            self._log_and_proxy(method, path, body)
        elif self.mode == "hybrid":
            self._hybrid_request(method, path, body)
        else:
            self._replay_saved_response(method, path, body)

    def _decode_or_base64(self, raw_bytes):
        return utils.decode_or_base64(raw_bytes)

    def _decode_text_lossy(self, raw_bytes):
        return utils.decode_text_lossy(raw_bytes)

    def _get_header_value(self, headers, name):
        return utils.get_header_value(headers, name)

    def _decode_body_for_log(self, raw_bytes, headers):
        return utils.decode_body_for_log(raw_bytes, headers)

    def _body_for_log_entry(self, raw_bytes, headers):
        return utils.body_for_log_entry(raw_bytes, headers)

    def _is_sse_response(self, headers):
        return utils.is_sse_response(headers)

    def _is_sse_request(self, headers):
        return utils.is_sse_request(headers)

    def _backend_timeout_seconds(self):
        return utils.backend_timeout_seconds(self.headers)

    def _maybe_parse_json_text(self, text):
        return sse.maybe_parse_json_text(text)

    def _append_jsonl_entry(self, file_path, entry):
        return sse.append_jsonl_entry(file_path, entry)

    def _log_sse_event(self, event_log_file, sequence, event_lines):
        return sse.log_sse_event(self, event_log_file, sequence, event_lines)

    def _build_sse_log_entry(
        self,
        method,
        path,
        request_body,
        request_headers,
        status_code,
        response_headers,
        sse_event_log_name,
        timestamp_ms=None,
    ):
        return modes.log.build_sse_log_entry(
            self,
            method,
            path,
            request_body,
            request_headers,
            status_code,
            response_headers,
            sse_event_log_name,
            timestamp_ms,
        )

    def _build_log_entry(
        self,
        method,
        path,
        request_body,
        request_headers,
        status_code,
        response_headers,
        response_body,
    ):
        return modes.log.build_log_entry(
            self,
            method,
            path,
            request_body,
            request_headers,
            status_code,
            response_headers,
            response_body,
        )

    def _build_combined_sse_log_entry(
        self,
        method,
        path,
        request_body,
        request_headers,
        status_code,
        response_headers,
        response_body,
        started_timestamp_ms,
        stream_outcome,
    ):
        return modes.log.build_combined_sse_log_entry(
            self,
            method,
            path,
            request_body,
            request_headers,
            status_code,
            response_headers,
            response_body,
            started_timestamp_ms,
            stream_outcome,
        )

    def _new_sse_chunk_accumulator(self):
        return sse.new_chunk_accumulator()

    def _append_chunk_value(self, message, field, value):
        return sse.append_chunk_value(message, field, value)

    def _merge_sse_function_call(self, message, function_call):
        return sse.merge_function_call(message, function_call)

    def _merge_sse_tool_call(self, choice_state, tool_call):
        return sse.merge_tool_call(choice_state, tool_call)

    def _merge_sse_choice(self, accumulator, choice):
        return sse.merge_choice(accumulator, choice)

    def _accumulate_sse_event(self, accumulator, event_lines):
        return sse.accumulate_sse_event(accumulator, event_lines)

    def _finalize_sse_chunks(self, accumulator):
        return sse.finalize_sse_chunks(accumulator)

    def _read_traffic_log_entries(self):
        return storage.read_traffic_log_entries()

    def _write_traffic_log_entries(self, entries):
        return storage.write_traffic_log_entries(entries)

    def _enqueue_traffic_log_entry(self, entry):
        return storage.enqueue_traffic_log_entry(self, entry)

    def _enqueue_sse_event_log(self, event_log_file, sequence, event_lines):
        return storage.enqueue_sse_event_log(self, event_log_file, sequence, event_lines)

    def _get_cache_key(self, method, path, body):
        return storage.get_cache_key(method, path, body)

    def _record_and_proxy(self, method, path, body):
        return modes.record.record_and_proxy(self, method, path, body)

    def _log_and_proxy(self, method, path, body):
        return modes.log.log_and_proxy(self, method, path, body)

    def _hybrid_request(self, method, path, body):
        return modes.hybrid.hybrid_request(self, method, path, body)

    def _replay_saved_response(self, method, path, body):
        return modes.replay.replay_saved_response(self, method, path, body)

    def _save_recording(self, method, path, body, status_code, headers, response_body):
        return storage.save_recording(
            self, method, path, body, status_code, headers, response_body
        )

    def _respond_raw(self, body, status, headers=None):
        return proxy.respond_raw(self, body, status, headers)

    def _respond_sse_stream(
        self, response, status, headers=None, event_log_file=None, accumulator=None
    ):
        return sse.respond_sse_stream(
            self, response, status, headers, event_log_file, accumulator
        )

    def _respond_json(self, data, status=200):
        return proxy.respond_json(self, data, status)

    def _respond_error(self, message, status):
        return proxy.respond_error(self, message, status)
