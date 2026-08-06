"""Web dashboard server for real-time traffic inspection."""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

# Dashboard frontend, served from the packaged HTML asset.
_INDEX_HTML = resources.files("capturly").joinpath("dashboard.html").read_text(encoding="utf-8")

_TRAFFIC_DETAIL_RE = re.compile(r"^/api/traffic/(\d+)$")


def _compute_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from traffic log entries."""
    total_requests = len(entries)
    ai_requests = 0
    total_tokens = 0
    models: list[str] = []

    for entry in entries:
        insights = entry.get("ai_insights")
        if not insights:
            continue
        ai_requests += 1

        req = insights.get("request", {})
        model = req.get("model")
        if model and model not in models:
            models.append(model)

        resp = insights.get("response", {})
        usage = resp.get("usage")
        if isinstance(usage, dict):
            total_tokens += usage.get("total_tokens", 0)

    return {
        "total_requests": total_requests,
        "ai_requests": ai_requests,
        "total_tokens": total_tokens,
        "models": sorted(models),
    }


def _summary_entry(entry: dict[str, Any], index: int) -> dict[str, Any]:
    """Return a lightweight summary of a traffic entry for list views."""
    summary = {
        "_index": index,
        "timestamp_ms": entry.get("timestamp_ms"),
        "method": entry.get("method"),
        "path": entry.get("path"),
        "status_code": entry.get("status_code"),
        "request_body_size": entry.get("request_body_size"),
        "response_body_size": entry.get("response_body_size"),
        "source_name": entry.get("source_name"),
        "backend_url": entry.get("backend_url"),
        "duration_ms": entry.get("duration_ms"),
    }
    if "ai_insights" in entry:
        summary["ai_insights"] = entry["ai_insights"]
    if entry.get("sse"):
        summary["sse"] = True
    resp = entry.get("response_body")
    if isinstance(resp, dict) and resp.get("object") == "agui.completion":
        summary["agui"] = True
    # Detect tool calls for badge
    ai = entry.get("ai_insights")
    if ai and isinstance(ai, dict):
        ai_resp = ai.get("response")
        if isinstance(ai_resp, dict) and ai_resp.get("tool_call_names"):
            summary["tools"] = True
    elif isinstance(resp, dict) and resp.get("tool_calls"):
        summary["tools"] = True
    return summary


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard API and frontend."""

    # Set by create_dashboard_server before serving.
    entries: Optional[list[dict[str, Any]]] = []
    traffic_log_path: Optional[str] = None

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def _get_entries(self) -> list[dict[str, Any]]:
        """Return entries from memory or by reading the traffic log file."""
        if self.entries is not None:
            return self.entries
        if self.traffic_log_path:
            return _read_traffic_log(self.traffic_log_path)
        return []

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/traffic":
            self._serve_traffic_list(params)
        elif path == "/api/stats":
            self._serve_stats()
        else:
            match = _TRAFFIC_DETAIL_RE.match(path)
            if match:
                self._serve_traffic_detail(int(match.group(1)))
            else:
                self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/truncate":
            self._handle_truncate()
        else:
            self._send_json({"error": "Not found"}, status=404)

    def _handle_truncate(self):
        """Clear all traffic entries from memory and/or the log file."""
        if self.entries is not None:
            self.entries.clear()
        if self.traffic_log_path:
            _truncate_traffic_log(self.traffic_log_path)
        self._send_json({"ok": True, "message": "Traffic log truncated"})

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_INDEX_HTML.encode("utf-8"))

    def _serve_traffic_list(self, params: dict[str, list[str]]):
        all_entries = self._get_entries()

        # Build indexed list to preserve original indices through filtering
        indexed = list(enumerate(all_entries))

        # Filter by AI traffic
        if params.get("ai", [""])[0].lower() == "true":
            indexed = [(i, e) for i, e in indexed if "ai_insights" in e]

        total = len(indexed)

        # Pagination
        try:
            limit = int(params.get("limit", ["50"])[0])
        except ValueError:
            limit = 50
        try:
            offset = int(params.get("offset", ["0"])[0])
        except ValueError:
            offset = 0

        page = indexed[offset : offset + limit]
        summaries = [_summary_entry(e, i) for i, e in page]

        self._send_json({"total": total, "entries": summaries})

    def _serve_traffic_detail(self, index: int):
        entries = self._get_entries()
        if index < 0 or index >= len(entries):
            self._send_json({"error": "Entry not found"}, status=404)
            return
        self._send_json(entries[index])

    def _serve_stats(self):
        self._send_json(_compute_stats(self._get_entries()))

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _truncate_traffic_log(path: str) -> None:
    """Truncate the traffic log file to empty."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.truncate(0)
    except OSError:
        pass


def _read_traffic_log(path: str) -> list[dict[str, Any]]:
    """Read traffic log entries from a JSONL file. Returns [] on any error."""
    if not os.path.isfile(path):
        return []
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        entries.append(obj)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def create_dashboard_server(
    entries: Optional[list[dict[str, Any]]] = None,
    host: str = "127.0.0.1",
    port: int = 9090,
    traffic_log_path: Optional[str] = None,
) -> HTTPServer:
    """Create a dashboard HTTP server.

    Provide either a static entries list or a traffic_log_path for live
    file-based reading. When traffic_log_path is set, the dashboard reads
    the file on each request, reflecting live updates from the logger.

    Args:
        entries: Static list of traffic log entry dicts (or None for file mode).
        host: Bind address.
        port: Bind port (0 for random available port in tests).
        traffic_log_path: Path to traffic_log.jsonl for live reading.

    Returns:
        An HTTPServer instance ready to serve_forever().
    """

    class _Handler(DashboardHandler):
        pass

    _Handler.entries = entries
    _Handler.traffic_log_path = traffic_log_path

    server = HTTPServer((host, port), _Handler)
    return server
