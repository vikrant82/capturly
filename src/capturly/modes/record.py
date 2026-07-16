"""Record mode: proxy requests and save backend responses."""

import os
import urllib.error

from .. import proxy, sse, storage


def record_and_proxy(handler, method, path, body):
    """Proxy a request to the backend and save its response."""
    if not handler.backend_url:
        handler._respond_error("Backend URL not configured for RECORD mode", 500)
        return

    try:
        with proxy.forward_request(handler, method, path, body) as response:
            response_headers = dict(response.headers)
            status_code = response.status

            if handler._is_sse_response(response_headers):
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
                handler.log_message(
                    f"✓ Proxied: {status_code} (res: {len(response_body)} bytes)"
                )
                storage.save_recording(
                    handler, method, path, body, status_code, response_headers, response_body
                )
                proxy.respond_raw(handler, response_body, status_code, response_headers)

    except urllib.error.HTTPError as e:
        error_body = e.read()
        handler.log_message(f"✗ Backend error: {e.code} (res: {len(error_body)} bytes)")
        storage.save_recording(handler, method, path, body, e.code, dict(e.headers), error_body)
        proxy.respond_raw(handler, error_body, e.code, dict(e.headers))

    except Exception as e:
        handler.log_message(f"✗ Proxy error: {e}")
        handler._respond_error(f"Proxy failed: {e}", 502)


_record_and_proxy = record_and_proxy
