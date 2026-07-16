"""Threaded HTTP server and Capturly server lifecycle."""

import os
from http.server import HTTPServer
from socketserver import ThreadingMixIn

from .handler import MockServiceHandler
from .logger import AsyncTrafficLogger
from .storage import RECORDINGS_DIR


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server tuned for concurrent local load-test traffic."""

    daemon_threads = False
    allow_reuse_address = True
    request_queue_size = 256

    def server_close(self):
        """Wait for request threads before closing the process-owned log writer."""
        super().server_close()


def _recordings_count():
    """Count JSON recordings already available to replay or hybrid mode."""
    if not os.path.exists(RECORDINGS_DIR):
        return 0
    return len([f for f in os.listdir(RECORDINGS_DIR) if f.endswith(".json")])


def run_server(args):
    """Configure the handler, run the server, and shut down its logger."""
    MockServiceHandler.mode = args.mode
    MockServiceHandler.backend_url = args.backend
    MockServiceHandler.replay_delay_ms = args.delay
    MockServiceHandler.combine_chunks = args.combine_chunks if args.mode == "log" else False
    MockServiceHandler.traffic_logger = (
        AsyncTrafficLogger(MockServiceHandler) if args.mode == "log" else None
    )

    server = ThreadedHTTPServer((args.host, args.port), MockServiceHandler)

    print(
        f"🚀 Mock server running in {args.mode.upper()} mode on http://{args.host}:{args.port}"
    )
    if args.mode == "record":
        print(f"📡 Proxying to: {args.backend}")
        print(f"💾 Recordings will be saved to: {RECORDINGS_DIR}")
        if args.delay > 0:
            print(f"⏱️ Replay delay configured ({args.delay}ms) but ignored in RECORD mode")
    elif args.mode == "log":
        print(f"📡 Proxying to: {args.backend}")
        print(f"📝 Full request/response logs will be saved to: {RECORDINGS_DIR}")
        print(f"🧩 SSE chunk combining: {'enabled' if args.combine_chunks else 'disabled'}")
        if args.delay > 0:
            print(f"⏱️ Replay delay configured ({args.delay}ms) but ignored in LOG mode")
    elif args.mode == "hybrid":
        print("🔄 Hybrid mode: cache-through with backend fallback")
        print(f"📡 Backend: {args.backend}")
        print(f"💾 Cache directory: {RECORDINGS_DIR}")
        print(f"⏱️ Replay delay for cache hits: {args.delay}ms")
        print(f"   Pre-cached responses: {_recordings_count()}")
    else:
        print(f"📂 Reading recordings from: {RECORDINGS_DIR}")
        print(f"⏱️ Replay delay: {args.delay}ms")
        print(f"   Found {_recordings_count()} recorded responses")

    print("\n🔍 Watching requests...\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down mock server...")
        server.shutdown()
    finally:
        server.server_close()
        if MockServiceHandler.traffic_logger:
            MockServiceHandler.traffic_logger.stop()
