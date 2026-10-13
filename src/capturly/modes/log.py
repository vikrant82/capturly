"""Log mode: proxy requests and persist complete traffic metadata."""

import base64
import json
import os
import time
import urllib.error

from .. import eventstream, proxy, sse, storage, utils
from ..inspection import bedrock, build_ai_insights


def _source_meta(handler):
    """Return source-identification fields stamped onto every traffic entry.

    ``source_name`` is the friendly pipe name (``--pipe``); ``backend_url`` is
    the backend this instance proxies. Either may be None. The dashboard
    resolves a display label as ``source_name or backend_url or "default"``.
    """
    return {
        "source_name": getattr(handler, "pipe_name", None),
        "backend_url": getattr(handler, "backend_url", None),
    }


def build_sse_log_entry(
    handler,
    method,
    path,
    request_body,
    request_headers,
    status_code,
    response_headers,
    sse_event_log_name,
    timestamp_ms=None,
):
    """Build SSE request metadata without writing the shared traffic log."""
    return {
        **_source_meta(handler),
        "timestamp_ms": timestamp_ms or int(time.time() * 1000),
        "method": method,
        "path": path,
        "cache_key": storage.get_cache_key(method, path, request_body),
        "request_headers": request_headers,
        "request_body": utils.body_for_log_entry(request_body, request_headers),
        "request_body_size": len(request_body),
        "status_code": status_code,
        "response_headers": response_headers,
        "sse": True,
        "sse_event_log": f"sse-events/{sse_event_log_name}",
    }


def build_log_entry(
    handler,
    method,
    path,
    request_body,
    request_headers,
    status_code,
    response_headers,
    response_body,
    started_timestamp_ms=None,
):
    """Build full request/response metadata without writing the shared log.

    When ``started_timestamp_ms`` is provided, the entry includes a
    ``duration_ms`` field measuring wall-clock time from request start to
    completion.
    """
    completed_timestamp_ms = int(time.time() * 1000)
    entry = {
        **_source_meta(handler),
        "timestamp_ms": completed_timestamp_ms,
        "method": method,
        "path": path,
        "cache_key": storage.get_cache_key(method, path, request_body),
        "request_headers": request_headers,
        "request_body": utils.body_for_log_entry(request_body, request_headers),
        "request_body_size": len(request_body),
        "status_code": status_code,
        "response_headers": response_headers,
        "response_body": utils.body_for_log_entry(response_body, response_headers),
        "response_body_size": len(response_body),
    }
    if started_timestamp_ms is not None:
        entry["duration_ms"] = max(0, completed_timestamp_ms - started_timestamp_ms)
    ai_insights = build_ai_insights(path, request_body, response_body)
    if ai_insights is not None:
        entry["ai_insights"] = ai_insights
    return entry


def build_combined_sse_log_entry(
    handler,
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
    """Build one traffic entry whose response body is the combined SSE completion."""
    completed_timestamp_ms = int(time.time() * 1000)
    has_combined_completion = response_body is not None
    if stream_outcome["aborted"]:
        combine_status = "aborted"
    elif not has_combined_completion:
        combine_status = "no_valid_chunks"
    else:
        combine_status = "completed"
    if response_body is None:
        response_body = {
            "combined_completion": None,
            "reason": (
                "stream_aborted_without_valid_chunks"
                if stream_outcome["aborted"]
                else "no_valid_openai_chunks"
            ),
        }
    entry = {
        **_source_meta(handler),
        "timestamp_ms": completed_timestamp_ms,
        "method": method,
        "path": path,
        "cache_key": storage.get_cache_key(method, path, request_body),
        "request_headers": request_headers,
        "request_body": utils.body_for_log_entry(request_body, request_headers),
        "request_body_size": len(request_body),
        "status_code": status_code,
        "response_headers": response_headers,
        "response_body": response_body,
        "response_body_size": len(json.dumps(response_body, ensure_ascii=False).encode("utf-8")),
        "sse": True,
        "sse_chunks_combined": True,
        "sse_combine_status": combine_status,
        "sse_aborted": stream_outcome["aborted"],
        "sse_started_timestamp_ms": started_timestamp_ms,
        "sse_duration_ms": max(0, completed_timestamp_ms - started_timestamp_ms),
        "duration_ms": max(0, completed_timestamp_ms - started_timestamp_ms),
    }
    if stream_outcome["error"]:
        entry["sse_error"] = stream_outcome["error"]
    ai_insights = build_ai_insights(path, request_body, response_body)
    if ai_insights is not None:
        entry["ai_insights"] = ai_insights
    return entry


def build_eventstream_log_entry(
    handler,
    method,
    path,
    request_body,
    request_headers,
    status_code,
    response_headers,
    capture,
    started_timestamp_ms,
):
    """Build exact binary capture metadata and optional native Bedrock insights.

    ``capture`` is an EventStreamCapture. Partial native responses are retained
    separately from the base64 body; only eventstream_status declares completion.
    EventStream observation is always enabled; combine_chunks applies to SSE only.
    This builder performs no I/O and assumes request-local capture state.
    """
    completed_timestamp_ms = int(time.time() * 1000)
    entry = {
        **_source_meta(handler),
        "timestamp_ms": completed_timestamp_ms,
        "method": method,
        "path": path,
        "cache_key": storage.get_cache_key(method, path, request_body),
        "request_headers": request_headers,
        "request_body": utils.body_for_log_entry(request_body, request_headers),
        "request_body_size": len(request_body),
        "status_code": status_code,
        "response_headers": response_headers,
        "response_body": base64.b64encode(capture.body).decode("ascii"),
        "response_body_encoding": "base64",
        "response_body_size": len(capture.body),
        "duration_ms": max(0, completed_timestamp_ms - started_timestamp_ms),
        "eventstream": True,
        "eventstream_status": capture.status,
    }
    if capture.error:
        entry["eventstream_error"] = capture.error
    if capture.response is not None:
        entry["eventstream_response"] = capture.response
    response_for_inspection = capture.response
    if response_for_inspection is None and bedrock.is_bedrock_request(path, request_body):
        # The captured EventStream bytes are binary, not a JSON response body.
        # An empty response preserves request insights without inventing a
        # response or changing response-less OpenAI behavior.
        response_for_inspection = b""
    if response_for_inspection is not None:
        insights = build_ai_insights(path, request_body, response_for_inspection)
        if insights is not None:
            entry["ai_insights"] = insights
    return entry


def _log_eventstream(handler, response, method, path, body, request_headers, started_timestamp_ms):
    """Use the same binary capture/log path for successful and HTTPError responses."""
    response_headers = dict(response.headers)
    status_code = response.status
    capture = eventstream.respond_eventstream(
        handler,
        response,
        status_code,
        response_headers,
        expect_bedrock=bedrock.is_bedrock_request(path, body),
    )
    handler._enqueue_traffic_log_entry(
        build_eventstream_log_entry(
            handler,
            method,
            path,
            body,
            request_headers,
            status_code,
            response_headers,
            capture,
            started_timestamp_ms,
        )
    )
    handler.log_message(
        f"📝 EventStream: {status_code} ({capture.status}, {len(capture.body)} bytes)"
    )


def log_and_proxy(handler, method, path, body):
    """Proxy a request and persist its complete request/response log entry."""
    if not handler.backend_url:
        handler._respond_error("Backend URL not configured for LOG mode", 500)
        return

    request_headers = {k: v for k, v in handler.headers.items()}
    started_timestamp_ms = int(time.time() * 1000)
    eventstream_started = False

    try:
        with proxy.forward_request(handler, method, path, body) as response:
            response_headers = dict(response.headers)
            status_code = response.status

            if utils.is_eventstream_response(response_headers):
                eventstream_started = True
                _log_eventstream(
                    handler, response, method, path, body, request_headers, started_timestamp_ms
                )
            elif handler._is_sse_response(response_headers):
                started_timestamp_ms = int(time.time() * 1000)
                if handler.combine_chunks:
                    accumulator = sse.new_chunk_accumulator()
                    handler.log_message(
                        f"📝 SSE stream: {status_code} (combining chunks, streaming live)"
                    )
                    try:
                        sse.respond_sse_stream(
                            handler,
                            response,
                            status_code,
                            response_headers,
                            accumulator=accumulator,
                        )
                    except Exception as e:
                        accumulator["stream_outcome"]["aborted"] = True
                        accumulator["stream_outcome"]["error"] = str(e)
                        handler.log_message(f"✗ SSE stream setup error: {e}")
                    finally:
                        try:
                            combined_body = sse.finalize_sse_chunks(accumulator)
                        except Exception as e:
                            combined_body = None
                            accumulator["stream_outcome"]["aborted"] = True
                            accumulator["stream_outcome"]["error"] = str(e)
                            handler.log_message(f"✗ SSE combine error: {e}")
                        handler._enqueue_traffic_log_entry(
                            build_combined_sse_log_entry(
                                handler,
                                method,
                                path,
                                body,
                                request_headers,
                                status_code,
                                response_headers,
                                combined_body,
                                started_timestamp_ms,
                                accumulator["stream_outcome"],
                            )
                        )
                else:
                    cache_key = storage.get_cache_key(method, path, body)
                    sse_events_dir = os.path.join(storage.RECORDINGS_DIR, "sse-events")
                    os.makedirs(sse_events_dir, exist_ok=True)
                    sse_event_log_name = f"{started_timestamp_ms}-{cache_key[:16]}.jsonl"
                    sse_event_log_path = os.path.join(sse_events_dir, sse_event_log_name)
                    with open(sse_event_log_path, "a", encoding="utf-8"):
                        pass

                    handler._enqueue_traffic_log_entry(
                        build_sse_log_entry(
                            handler,
                            method,
                            path,
                            body,
                            request_headers,
                            status_code,
                            response_headers,
                            sse_event_log_name,
                            timestamp_ms=started_timestamp_ms,
                        )
                    )
                    handler.log_message(
                        f"📝 SSE stream: {status_code} (request queued for logging, streaming live)"
                    )
                    sse.respond_sse_stream(
                        handler,
                        response,
                        status_code,
                        response_headers,
                        event_log_file=sse_event_log_path,
                    )
            else:
                response_body = response.read()
                proxy.respond_raw(handler, response_body, status_code, response_headers)
                handler._enqueue_traffic_log_entry(
                    build_log_entry(
                        handler,
                        method,
                        path,
                        body,
                        request_headers,
                        status_code,
                        response_headers,
                        response_body,
                        started_timestamp_ms,
                    )
                )
                handler.log_message(
                    f"📝 Logged and proxied: {status_code} (res: {len(response_body)} bytes)"
                )

    except urllib.error.HTTPError as e:
        if eventstream_started:
            e.close()
            handler.log_message(f"✗ EventStream log error after response started: {e}")
            return
        if utils.is_eventstream_response(dict(e.headers)):
            eventstream_started = True
            try:
                with e:
                    _log_eventstream(
                        handler, e, method, path, body, request_headers, started_timestamp_ms
                    )
            except Exception as stream_error:
                handler.log_message(
                    f"✗ EventStream log error after response started: {stream_error}"
                )
            return
        error_body = e.read()
        proxy.respond_raw(handler, error_body, e.code, dict(e.headers))
        handler._enqueue_traffic_log_entry(
            build_log_entry(
                handler,
                method,
                path,
                body,
                request_headers,
                e.code,
                dict(e.headers),
                error_body,
                started_timestamp_ms,
            )
        )
        handler.log_message(f"📝 Logged backend error: {e.code} (res: {len(error_body)} bytes)")

    except Exception as e:
        handler.log_message(f"✗ Proxy/log error: {e}")
        if not eventstream_started:
            handler._respond_error(f"Proxy failed: {e}", 502)


_log_and_proxy = log_and_proxy
