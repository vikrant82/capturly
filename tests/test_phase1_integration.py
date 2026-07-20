"""Phase 1 capstone integration test.

Exercises the full pipeline: config → log mode → AI inspection → dashboard API.
"""

import json
import os
import tempfile
import threading
import urllib.request

from capturly import config, dashboard
from capturly.modes import log as log_mode


def test_phase1_full_pipeline():
    """End-to-end: config loads, log entry built with AI insights, dashboard serves it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Config file with env var interpolation
        os.environ["TEST_BACKEND_URL"] = "https://api.example.com"
        config_path = os.path.join(tmpdir, "capturly.yaml")
        with open(config_path, "w") as f:
            f.write(
                "mode: log\n"
                "backend: ${TEST_BACKEND_URL}\n"
                "dashboard:\n"
                "  enabled: true\n"
                "  port: 9090\n"
                "log:\n"
                "  combine_chunks: true\n"
            )

        cfg = config.load_config(config_path)
        assert cfg["mode"] == "log"
        assert cfg["backend"] == "https://api.example.com"
        assert cfg["dashboard.enabled"] is True
        assert cfg["dashboard.port"] == 9090
        assert cfg["log.combine_chunks"] is True

        # 2. Build a log entry with AI insights (simulating log mode)
        request_body = json.dumps({
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a code review assistant."},
                {"role": "user", "content": "Review this PR"},
            ],
            "tools": [
                {"type": "function", "function": {"name": "read_file", "parameters": {}}},
                {"type": "function", "function": {"name": "comment_on_pr", "parameters": {}}},
            ],
        }).encode()

        response_body = json.dumps({
            "id": "chatcmpl-phase1",
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "I'll review the PR now.",
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "src/main.py"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }).encode()

        entry = log_mode.build_log_entry(
            handler=None,
            method="POST",
            path="/v1/chat/completions",
            request_body=request_body,
            request_headers={"Content-Type": "application/json"},
            status_code=200,
            response_headers={"Content-Type": "application/json"},
            response_body=response_body,
        )

        # Verify AI insights
        assert entry["ai_insights"] is not None
        assert entry["ai_insights"]["request"]["model"] == "gpt-4o"
        assert entry["ai_insights"]["request"]["system_prompts"] == ["You are a code review assistant."]
        assert entry["ai_insights"]["request"]["tool_names"] == ["read_file", "comment_on_pr"]
        assert entry["ai_insights"]["request"]["message_count"] == 2
        assert entry["ai_insights"]["response"]["finish_reasons"] == ["tool_calls"]
        assert entry["ai_insights"]["response"]["tool_call_names"] == ["read_file"]
        assert entry["ai_insights"]["response"]["usage"]["total_tokens"] == 70

        # 3. Write entry to traffic_log.json and serve via dashboard
        traffic_log_path = os.path.join(tmpdir, "traffic_log.json")
        with open(traffic_log_path, "w") as f:
            json.dump([entry], f)

        server = dashboard.create_dashboard_server(
            entries=None, host="127.0.0.1", port=0, traffic_log_path=traffic_log_path
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # 4. Verify dashboard API returns the entry with insights
            url = f"http://127.0.0.1:{port}/api/traffic"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                assert data["total"] == 1
                assert data["entries"][0]["ai_insights"]["request"]["model"] == "gpt-4o"

            # 5. Verify stats endpoint
            url = f"http://127.0.0.1:{port}/api/stats"
            with urllib.request.urlopen(url, timeout=5) as resp:
                stats = json.loads(resp.read().decode())
                assert stats["total_requests"] == 1
                assert stats["ai_requests"] == 1
                assert stats["total_tokens"] == 70
                assert stats["models"] == ["gpt-4o"]

            # 6. Verify detail endpoint
            url = f"http://127.0.0.1:{port}/api/traffic/0"
            with urllib.request.urlopen(url, timeout=5) as resp:
                detail = json.loads(resp.read().decode())
                assert detail["path"] == "/v1/chat/completions"
                assert detail["ai_insights"]["response"]["tool_call_names"] == ["read_file"]

            # 7. Verify AI filter
            url = f"http://127.0.0.1:{port}/api/traffic?ai=true"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                assert data["total"] == 1

            # 8. Verify HTML frontend is served
            url = f"http://127.0.0.1:{port}/"
            with urllib.request.urlopen(url, timeout=5) as resp:
                html = resp.read().decode()
                assert "traffic-table" in html
                assert "/api/traffic" in html
                assert "/api/stats" in html
        finally:
            server.shutdown()

        del os.environ["TEST_BACKEND_URL"]


def test_phase1_config_merge_with_cli():
    """Config file values merge correctly with CLI args."""
    import argparse

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "capturly.yaml")
        with open(config_path, "w") as f:
            f.write(
                "mode: log\n"
                "port: 8888\n"
                "dashboard:\n"
                "  enabled: true\n"
                "  port: 7777\n"
            )

        cfg = config.load_config(config_path)

        # Simulate CLI args where user only set --mode explicitly
        args = argparse.Namespace(
            mode="record",  # CLI explicit (non-default)
            port=9999,  # default
            backend=None,
            delay=0,
            host="0.0.0.0",
            combine_chunks=False,
            recordings_dir=None,
            dashboard=False,  # default
            dashboard_port=9090,  # default
        )

        merged = config.merge_config_with_args(cfg, args)

        # CLI wins for mode (non-default)
        assert merged.mode == "record"
        # Config fills port (was at default)
        assert merged.port == 8888
        # Config fills dashboard settings
        assert merged.dashboard is True
        assert merged.dashboard_port == 7777
