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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Capturly Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; background: #0d1117; color: #c9d1d9; }
  .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 18px; color: #58a6ff; }
  .header .refresh { font-size: 12px; color: #8b949e; }
  .stats-bar { display: flex; gap: 24px; padding: 12px 24px; background: #161b22; border-bottom: 1px solid #30363d; flex-wrap: wrap; }
  .stat { text-align: center; }
  .stat .value { font-size: 24px; font-weight: 700; color: #58a6ff; }
  .stat .label { font-size: 11px; color: #8b949e; text-transform: uppercase; }
  .controls { padding: 12px 24px; display: flex; gap: 12px; align-items: center; }
  .controls button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .controls button.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }
  .controls button:hover { border-color: #58a6ff; }
  .traffic-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .traffic-table th { text-align: left; padding: 8px 12px; background: #161b22; color: #8b949e; font-weight: 600; border-bottom: 1px solid #30363d; position: sticky; top: 0; }
  .traffic-table td { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  .traffic-table tr:hover td { background: #161b22; }
  .traffic-table tr.clickable { cursor: pointer; }
  .method { font-weight: 700; }
  .method.POST { color: #3fb950; }
  .method.GET { color: #58a6ff; }
  .method.PUT { color: #d29922; }
  .method.DELETE { color: #f85149; }
  .status { font-weight: 600; }
  .status.s2xx { color: #3fb950; }
  .status.s4xx { color: #d29922; }
  .status.s5xx { color: #f85149; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .badge.ai { background: #1f6feb33; color: #58a6ff; border: 1px solid #1f6feb; }
  .badge.sse { background: #3fb95033; color: #3fb950; border: 1px solid #3fb950; }
  .detail-panel { display: none; position: fixed; top: 0; right: 0; width: 50%; height: 100%; background: #161b22; border-left: 1px solid #30363d; overflow-y: auto; padding: 24px; z-index: 100; }
  .detail-panel.open { display: block; }
  .detail-panel h2 { font-size: 16px; margin-bottom: 16px; color: #58a6ff; }
  .detail-panel .close { position: absolute; top: 16px; right: 16px; background: none; border: none; color: #8b949e; font-size: 20px; cursor: pointer; }
  .detail-section { margin-bottom: 16px; }
  .detail-section h3 { font-size: 13px; color: #8b949e; margin-bottom: 8px; text-transform: uppercase; }
  .detail-section pre { background: #0d1117; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .insight-item { margin-bottom: 8px; }
  .insight-item .key { color: #8b949e; font-size: 12px; }
  .insight-item .val { color: #c9d1d9; font-size: 13px; }
  .empty { text-align: center; padding: 48px; color: #8b949e; }
</style>
</head>
<body>
<div class="header">
  <h1>&#x1F4E1; Capturly Traffic Dashboard</h1>
  <span class="refresh" id="last-refresh"></span>
</div>
<div class="stats-bar" id="stats-bar"></div>
<div class="controls">
  <button id="btn-all" class="active" onclick="setFilter(false)">All Traffic</button>
  <button id="btn-ai" onclick="setFilter(true)">&#x1F916; AI Only</button>
  <button onclick="refresh()">&#x21BB; Refresh</button>
</div>
<table class="traffic-table">
  <thead><tr><th>#</th><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>Req Size</th><th>Res Size</th><th>Tags</th></tr></thead>
  <tbody id="traffic-table"></tbody>
</table>
<div class="detail-panel" id="detail-panel">
  <button class="close" onclick="closeDetail()">&times;</button>
  <div id="detail-content"></div>
</div>
<script>
let aiOnly = false;
let entries = [];

function setFilter(ai) {
  aiOnly = ai;
  document.getElementById('btn-all').className = ai ? '' : 'active';
  document.getElementById('btn-ai').className = ai ? 'active' : '';
  refresh();
}

function fmtTime(ms) {
  if (!ms) return '-';
  return new Date(ms).toLocaleTimeString();
}

function fmtSize(b) {
  if (b == null) return '-';
  if (b < 1024) return b + ' B';
  return (b / 1024).toFixed(1) + ' KB';
}

function statusClass(code) {
  if (code >= 200 && code < 300) return 's2xx';
  if (code >= 400 && code < 500) return 's4xx';
  return 's5xx';
}

function renderStats(stats) {
  const bar = document.getElementById('stats-bar');
  bar.innerHTML = `
    <div class="stat"><div class="value">${stats.total_requests}</div><div class="label">Requests</div></div>
    <div class="stat"><div class="value">${stats.ai_requests}</div><div class="label">AI Requests</div></div>
    <div class="stat"><div class="value">${stats.total_tokens.toLocaleString()}</div><div class="label">Tokens</div></div>
    <div class="stat"><div class="value">${stats.models.join(', ') || '-'}</div><div class="label">Models</div></div>
  `;
}

function renderTable(data) {
  const tbody = document.getElementById('traffic-table');
  if (!data.entries.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No traffic recorded yet</td></tr>';
    return;
  }
  tbody.innerHTML = data.entries.map((e, i) => {
    const tags = [];
    if (e.ai_insights) tags.push('<span class="badge ai">AI</span>');
    if (e.sse) tags.push('<span class="badge sse">SSE</span>');
    return `<tr class="clickable" onclick="showDetail(${i})">
      <td>${i}</td><td>${fmtTime(e.timestamp_ms)}</td>
      <td><span class="method ${e.method}">${e.method}</span></td>
      <td>${e.path}</td>
      <td><span class="status ${statusClass(e.status_code)}">${e.status_code}</span></td>
      <td>${fmtSize(e.request_body_size)}</td><td>${fmtSize(e.response_body_size)}</td>
      <td>${tags.join(' ')}</td>
    </tr>`;
  }).join('');
}

function showDetail(idx) {
  const e = entries[idx];
  if (!e) return;
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');
  let html = `<h2>${e.method} ${e.path}</h2>`;
  html += `<div class="detail-section"><h3>Overview</h3><pre>Status: ${e.status_code}\\nTime: ${fmtTime(e.timestamp_ms)}\\nReq: ${fmtSize(e.request_body_size)} | Res: ${fmtSize(e.response_body_size)}</pre></div>`;

  if (e.ai_insights) {
    const ai = e.ai_insights;
    html += '<div class="detail-section"><h3>&#x1F916; AI Insights</h3>';
    if (ai.request) {
      html += '<div class="insight-item"><span class="key">Model:</span> <span class="val">' + (ai.request.model || '-') + '</span></div>';
      html += '<div class="insight-item"><span class="key">Messages:</span> <span class="val">' + (ai.request.message_count || 0) + '</span></div>';
      if (ai.request.system_prompts && ai.request.system_prompts.length) {
        html += '<div class="insight-item"><span class="key">System Prompts:</span><pre>' + ai.request.system_prompts.join('\\n---\\n') + '</pre></div>';
      }
      if (ai.request.tool_names && ai.request.tool_names.length) {
        html += '<div class="insight-item"><span class="key">Tools:</span> <span class="val">' + ai.request.tool_names.join(', ') + '</span></div>';
      }
    }
    if (ai.response) {
      if (ai.response.usage) {
        const u = ai.response.usage;
        html += '<div class="insight-item"><span class="key">Tokens:</span> <span class="val">' + (u.prompt_tokens||0) + ' prompt + ' + (u.completion_tokens||0) + ' completion = ' + (u.total_tokens||0) + ' total</span></div>';
      }
      if (ai.response.finish_reasons) {
        html += '<div class="insight-item"><span class="key">Finish:</span> <span class="val">' + ai.response.finish_reasons.join(', ') + '</span></div>';
      }
      if (ai.response.tool_call_names && ai.response.tool_call_names.length) {
        html += '<div class="insight-item"><span class="key">Tool Calls:</span> <span class="val">' + ai.response.tool_call_names.join(', ') + '</span></div>';
      }
    }
    html += '</div>';
  }

  if (e.request_body) {
    html += '<div class="detail-section"><h3>Request Body</h3><pre>' + JSON.stringify(e.request_body, null, 2).replace(/</g, '&lt;') + '</pre></div>';
  }
  if (e.response_body) {
    html += '<div class="detail-section"><h3>Response Body</h3><pre>' + JSON.stringify(e.response_body, null, 2).replace(/</g, '&lt;') + '</pre></div>';
  }

  content.innerHTML = html;
  panel.className = 'detail-panel open';
}

function closeDetail() {
  document.getElementById('detail-panel').className = 'detail-panel';
}

async function refresh() {
  try {
    const [trafficRes, statsRes] = await Promise.all([
      fetch('/api/traffic?limit=200' + (aiOnly ? '&ai=true' : '')),
      fetch('/api/stats')
    ]);
    const traffic = await trafficRes.json();
    const stats = await statsRes.json();
    entries = traffic.entries;
    renderStats(stats);
    renderTable(traffic);
    document.getElementById('last-refresh').textContent = 'Updated: ' + new Date().toLocaleTimeString();
  } catch (e) {
    console.error('Refresh failed:', e);
  }
}

refresh();
setInterval(refresh, 3000);
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
