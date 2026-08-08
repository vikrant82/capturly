"""Web dashboard server for real-time traffic inspection.

Two serving modes:
  - static entries (tests): full entries held in memory, legacy summaries
  - file mode: a TrafficIndex over traffic_log.jsonl — slim summaries for
    list/stats, single-line offset reads for detail
"""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from socketserver import ThreadingMixIn
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .traffic_index import TrafficIndex

# Dashboard frontend, served from the packaged HTML asset.
_INDEX_HTML = resources.files("capturly").joinpath("dashboard.html").read_text(encoding="utf-8")

_TRAFFIC_DETAIL_RE = re.compile(r"^/api/traffic/(\d+)$")


def _newest_page_window(total: int, page: int, limit: int) -> tuple:
    """Return (start, end) slice bounds for a page counted from the newest entries.

    Page 0 is the window anchored at the newest entry; higher pages walk
    toward older entries. Out-of-range pages collapse to an empty window
    instead of wrapping or erroring. Summaries are held in log (oldest-first)
    order, so the slice is applied directly to that list.
    """
    end = max(0, total - page * limit)
    start = max(0, end - limit)
    return start, end


def _compute_stats(entries: list) -> dict:
    """Compute summary statistics from full traffic log entries (static mode)."""
    total_requests = len(entries)
    ai_requests = 0
    total_tokens = 0
    models: list = []

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


def _summary_entry(entry: dict, index: int) -> dict:
    """Return a lightweight summary of a traffic entry for list views (static mode)."""
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
        roles = (ai.get("request") or {}).get("roles")
        if isinstance(roles, list) and any(role in ("tool", "function") for role in roles):
            summary["tool_results"] = True
    elif isinstance(resp, dict) and resp.get("tool_calls"):
        summary["tools"] = True
    return summary


class _DashboardServer(ThreadingMixIn, HTTPServer):
    """Threaded server so a slow detail parse cannot block list refreshes."""

    daemon_threads = True
    allow_reuse_address = True


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard API and frontend."""

    # Set by create_dashboard_server before serving.
    entries: Optional[list] = []
    traffic_log_path: Optional[str] = None
    index: Optional[TrafficIndex] = None

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def _get_entries(self) -> list:
        """Return static entries (static mode only)."""
        if self.entries is not None:
            return self.entries
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
        if self.index is not None:
            self.index.reset()
        if self.traffic_log_path:
            _truncate_traffic_log(self.traffic_log_path)
        self._send_json({"ok": True, "message": "Traffic log truncated"})

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_INDEX_HTML.encode("utf-8"))

    def _serve_traffic_list(self, params: dict):
        """Serve a summary window: `page` counts from the newest, `offset` from the oldest."""
        ai_only = params.get("ai", [""])[0].lower() == "true"

        if self.index is not None:
            self.index.sync()
            summaries = self.index.summaries()
            if ai_only:
                summaries = [s for s in summaries if "ai" in s]
        else:
            indexed = list(enumerate(self._get_entries()))
            if ai_only:
                indexed = [(i, e) for i, e in indexed if "ai_insights" in e]
            summaries = [_summary_entry(e, i) for i, e in indexed]

        total = len(summaries)

        try:
            limit = int(params.get("limit", ["50"])[0])
        except ValueError:
            limit = 50

        if "page" in params:
            try:
                page = max(0, int(params["page"][0]))
            except ValueError:
                page = 0
            start, end = _newest_page_window(total, page, limit)
            page_entries = summaries[start:end]
        else:
            try:
                offset = int(params.get("offset", ["0"])[0])
            except ValueError:
                offset = 0
            page_entries = summaries[offset : offset + limit]

        self._send_json({"total": total, "entries": page_entries})

    def _serve_traffic_detail(self, index: int):
        if self.index is not None:
            self.index.sync()
            entry = self.index.load_entry(index)
            if entry is None:
                self._send_json({"error": "Entry not found"}, status=404)
                return
            self._send_json(entry)
            return
        entries = self._get_entries()
        if index < 0 or index >= len(entries):
            self._send_json({"error": "Entry not found"}, status=404)
            return
        self._send_json(entries[index])

    def _serve_stats(self):
        if self.index is not None:
            self.index.sync()
            self._send_json(self.index.stats())
            return
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


def create_dashboard_server(
    entries: Optional[list] = None,
    host: str = "127.0.0.1",
    port: int = 9090,
    traffic_log_path: Optional[str] = None,
) -> HTTPServer:
    """Create a dashboard HTTP server.

    Provide either a static entries list or a traffic_log_path for live
    file-based reading. In file mode a TrafficIndex incrementally indexes
    the log; list/stats are served from slim summaries and detail reads
    parse a single line at its recorded offset.

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
    _Handler.index = TrafficIndex(traffic_log_path) if traffic_log_path else None

    return _DashboardServer((host, port), _Handler)
