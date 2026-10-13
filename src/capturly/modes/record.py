"""Record mode: proxy requests and save backend responses."""

import os
import urllib.error

from .. import eventstream, proxy, sse, storage, utils
from ..inspection import bedrock


def _record_eventstream(handler, response, method, path, body):
    """Publish only complete binary captures, regardless of the native HTTP status."""
    response_headers = dict(response.headers)
    status_code = response.status
    capture = eventstream.respond_eventstream(
        handler,
        response,
        status_code,
        response_headers,
        expect_bedrock=bedrock.is_bedrock_request(path, body),
    )
    if capture.status == "completed":
        storage.save_recording(
            handler, method, path, body, status_code, response_headers, capture.body
        )
    handler.log_message(
        f"✓ EventStream: {status_code} ({capture.status}, {len(capture.body)} bytes)"
    )


def record_and_proxy(handler, method, path, body):
    """Proxy a request to the backend and save its response."""
    if not handler.backend_url:
        handler._respond_error("Backend URL not configured for RECORD mode", 500)
        return

    eventstream_started = False
    try:
        with proxy.forward_request(handler, method, path, body) as response:
            response_headers = dict(response.headers)
            status_code = response.status

            if utils.is_eventstream_response(response_headers):
                eventstream_started = True
                _record_eventstream(handler, response, method, path, body)
            elif handler._is_sse_response(response_headers):
                cache_key = storage.get_cache_key(method, path, body)
                recording_file = os.path.join(storage.RECORDINGS_DIR, f"{cache_key}.sse")
                storage.atomic_write_json(
                    recording_file,
                    {
                        "method": method,
                        "path": path,
                        "request_body_size": len(body),
                        "sse": True,
                    },
                )
                handler.log_message(
                    f"✓ SSE stream: {status_code} (request recorded, streaming live, no body cached)"
                )
                sse.respond_sse_stream(handler, response, status_code, response_headers)
            else:
                response_body = response.read()
                handler.log_message(f"✓ Proxied: {status_code} (res: {len(response_body)} bytes)")
                storage.save_recording(
                    handler, method, path, body, status_code, response_headers, response_body
                )
                proxy.respond_raw(handler, response_body, status_code, response_headers)

    except urllib.error.HTTPError as e:
        if eventstream_started:
            e.close()
            handler.log_message(f"✗ EventStream recording error after response started: {e}")
            return
        if utils.is_eventstream_response(dict(e.headers)):
            eventstream_started = True
            try:
                with e:
                    _record_eventstream(handler, e, method, path, body)
            except Exception as stream_error:
                handler.log_message(
                    f"✗ EventStream recording error after response started: {stream_error}"
                )
            return
        error_body = e.read()
        handler.log_message(f"✗ Backend error: {e.code} (res: {len(error_body)} bytes)")
        storage.save_recording(handler, method, path, body, e.code, dict(e.headers), error_body)
        proxy.respond_raw(handler, error_body, e.code, dict(e.headers))

    except Exception as e:
        handler.log_message(f"✗ Proxy error: {e}")
        if not eventstream_started:
            handler._respond_error(f"Proxy failed: {e}", 502)


_record_and_proxy = record_and_proxy
