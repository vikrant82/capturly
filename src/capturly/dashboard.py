"""Web dashboard server for real-time traffic inspection."""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Minimal HTML placeholder — replaced by full frontend in Task 6.
_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Capturly Dashboard</title>
</head>
<body>
<h1>Capturly Traffic Dashboard</h1>
<p>Loading...</p>
<script>
fetch('/api/traffic').then(r=>r.json()).then(d=>{
  document.body.innerHTML += '<pre>'+JSON.stringify(d,null,2)+'</pre>';
});
</script>
</body>
</html>
"""

_TRAFFIC_DETAIL_RE = re.compile(r"^/api/traffic/(\d+)$")


def _compute_stats(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary statistics from traffic log entries."""
    total_requests = len(entries)
    ai_requests = 0
    total_tokens = 0
    models: List[str] = []

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


def _summary_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return a lightweight summary of a traffic entry for list views."""
    summary = {
        "timestamp_ms": entry.get("timestamp_ms"),
        "method": entry.get("method"),
        "path": entry.get("path"),
        "status_code": entry.get("status_code"),
        "request_body_size": entry.get("request_body_size"),
        "response_body_size": entry.get("response_body_size"),
    }
    if "ai_insights" in entry:
        summary["ai_insights"] = entry["ai_insights"]
    if entry.get("sse"):
        summary["sse"] = True
    return summary


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard API and frontend."""

    # Set by create_dashboard_server before serving.
    entries: List[Dict[str, Any]] = []

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

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

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_INDEX_HTML.encode("utf-8"))

    def _serve_traffic_list(self, params: Dict[str, List[str]]):
        entries = self.entries

        # Filter by AI traffic
        if params.get("ai", [""])[0].lower() == "true":
            entries = [e for e in entries if "ai_insights" in e]

        total = len(entries)

        # Pagination
        try:
            limit = int(params.get("limit", ["50"])[0])
        except ValueError:
            limit = 50
        try:
            offset = int(params.get("offset", ["0"])[0])
        except ValueError:
            offset = 0

        page = entries[offset : offset + limit]
        summaries = [_summary_entry(e) for e in page]

        self._send_json({"total": total, "entries": summaries})

    def _serve_traffic_detail(self, index: int):
        if index < 0 or index >= len(self.entries):
            self._send_json({"error": "Entry not found"}, status=404)
            return
        self._send_json(self.entries[index])

    def _serve_stats(self):
        self._send_json(_compute_stats(self.entries))

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_dashboard_server(
    entries: List[Dict[str, Any]],
    host: str = "127.0.0.1",
    port: int = 9090,
) -> HTTPServer:
    """Create a dashboard HTTP server bound to the given entries.

    The server reads from the provided entries list. For live updates,
    pass a shared list that the traffic logger appends to.

    Args:
        entries: List of traffic log entry dicts.
        host: Bind address.
        port: Bind port (0 for random available port in tests).

    Returns:
        An HTTPServer instance ready to serve_forever().
    """

    class _Handler(DashboardHandler):
        pass

    _Handler.entries = entries

    server = HTTPServer((host, port), _Handler)
    return server
