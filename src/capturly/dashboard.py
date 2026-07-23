"""Web dashboard server for real-time traffic inspection."""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional
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
  .badge.agui { background: #bc8cff33; color: #bc8cff; border: 1px solid #bc8cff; }
  .empty { text-align: center; padding: 48px; color: #8b949e; }
  .overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 99; }
  .overlay.open { display: block; }
  .detail-panel { display: none; position: fixed; top: 0; right: 0; width: 55%; height: 100%; background: #161b22; border-left: 1px solid #30363d; overflow-y: auto; padding: 24px; z-index: 100; }
  .detail-panel.open { display: block; }
  .detail-panel h2 { font-size: 16px; margin-bottom: 16px; color: #58a6ff; word-break: break-all; }
  .detail-panel .close { position: absolute; top: 16px; right: 16px; background: none; border: none; color: #8b949e; font-size: 20px; cursor: pointer; }
  .detail-panel .close:hover { color: #c9d1d9; }
  .detail-section { margin-bottom: 16px; }
  .detail-section h3 { font-size: 13px; color: #8b949e; margin-bottom: 8px; text-transform: uppercase; }
  .detail-section pre, .detail-body pre { background: #0d1117; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  details { margin-bottom: 8px; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }
  details summary { padding: 10px 14px; cursor: pointer; font-weight: 600; font-size: 13px; color: #c9d1d9; background: #21262d; user-select: none; list-style: none; display: flex; align-items: center; gap: 8px; }
  details summary::-webkit-details-marker { display: none; }
  details summary::before { content: '\\25B6'; font-size: 10px; color: #8b949e; transition: transform 0.15s; }
  details[open] summary::before { transform: rotate(90deg); }
  details summary:hover { background: #292e36; }
  details[open] summary { border-bottom: 1px solid #30363d; }
  .detail-body { padding: 12px 14px; }
  .detail-body h4 { font-size: 12px; color: #8b949e; text-transform: uppercase; margin: 12px 0 6px 0; }
  .detail-body h4:first-child { margin-top: 0; }
  .ai-meta { display: flex; gap: 20px; flex-wrap: wrap; padding: 12px 0; margin-bottom: 12px; border-bottom: 1px solid #30363d; }
  .ai-meta .meta-item { font-size: 12px; }
  .ai-meta .meta-key { color: #8b949e; margin-right: 4px; }
  .ai-meta .meta-val { color: #c9d1d9; font-weight: 600; }
  .msg { margin-bottom: 10px; border-left: 3px solid #30363d; padding: 8px 12px; background: #0d1117; border-radius: 0 6px 6px 0; }
  .msg.system { border-left-color: #d29922; }
  .msg.user { border-left-color: #58a6ff; }
  .msg.assistant { border-left-color: #3fb950; }
  .msg.tool { border-left-color: #bc8cff; }
  .msg .msg-role { font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 4px; letter-spacing: 0.5px; }
  .msg.system .msg-role { color: #d29922; }
  .msg.user .msg-role { color: #58a6ff; }
  .msg.assistant .msg-role { color: #3fb950; }
  .msg.tool .msg-role { color: #bc8cff; }
  .msg .msg-content { font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; color: #c9d1d9; }
  .tool-card { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; margin-bottom: 6px; }
  .tool-card .tool-name { color: #bc8cff; font-weight: 600; font-size: 13px; font-family: monospace; }
  .tool-card .tool-desc { color: #8b949e; font-size: 12px; margin-top: 4px; }
  .tool-card pre { margin-top: 6px; font-size: 11px; }
  .loading { text-align: center; padding: 48px; color: #8b949e; font-size: 14px; }
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
  <button onclick="truncate()" style="margin-left:auto;border-color:#f8514966;color:#f85149">&#x1F5D1; Clear Log</button>
</div>
<table class="traffic-table">
  <thead><tr><th>#</th><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>Req Size</th><th>Res Size</th><th>Tags</th></tr></thead>
  <tbody id="traffic-table"></tbody>
</table>
<div class="overlay" id="overlay" onclick="closeDetail()"></div>
<div class="detail-panel" id="detail-panel">
  <button class="close" onclick="closeDetail()">&times;</button>
  <div id="detail-content"></div>
</div>
<script>
var aiOnly = false;
var entries = [];

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escJson(obj) {
  if (obj == null) return '<span style="color:#8b949e">null</span>';
  if (typeof obj === 'string') return esc(obj);
  try { return esc(JSON.stringify(obj, null, 2)); } catch(e) { return esc(String(obj)); }
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

function collapsible(title, bodyHtml, open) {
  return '<details' + (open ? ' open' : '') + '><summary>' + title + '</summary><div class="detail-body">' + bodyHtml + '</div></details>';
}

function metaItem(key, val) {
  return '<span class="meta-item"><span class="meta-key">' + esc(key) + ':</span> <span class="meta-val">' + esc(String(val)) + '</span></span>';
}

function renderHeaders(headers) {
  if (!headers || !Object.keys(headers).length) return '<span style="color:#8b949e">No headers</span>';
  return '<pre>' + esc(JSON.stringify(headers, null, 2)) + '</pre>';
}

function msgContent(content) {
  if (content == null) return '';
  if (typeof content === 'string') return esc(content);
  if (Array.isArray(content)) {
    return content.map(function(part) {
      if (part.type === 'text') return esc(part.text || '');
      if (part.type === 'image_url') return '[Image: ' + esc(String((part.image_url && part.image_url.url) || '').substring(0, 80)) + ']';
      return esc(JSON.stringify(part));
    }).join('\\n');
  }
  return esc(JSON.stringify(content, null, 2));
}

function renderToolCalls(toolCalls) {
  if (!toolCalls || !toolCalls.length) return '';
  return toolCalls.map(function(tc) {
    var func = tc.function || {};
    var args = '';
    try { args = JSON.stringify(JSON.parse(func.arguments || '{}'), null, 2); } catch(e) { args = func.arguments || ''; }
    return '<div class="tool-card"><div class="tool-name">' + esc(func.name || 'unknown') + '</div><pre>' + esc(args) + '</pre></div>';
  }).join('');
}

function renderTools(tools) {
  if (!tools || !tools.length) return '';
  return tools.map(function(tool) {
    var func = tool.function || tool;
    var html = '<div class="tool-card"><div class="tool-name">' + esc(func.name || 'unknown') + '</div>';
    if (func.description) html += '<div class="tool-desc">' + esc(func.description) + '</div>';
    if (func.parameters) html += '<pre>' + esc(JSON.stringify(func.parameters, null, 2)) + '</pre>';
    html += '</div>';
    return html;
  }).join('');
}

function renderMessage(msg) {
  var role = msg.role || 'unknown';
  var roleLabel = esc(role);
  if (role === 'tool' && msg.tool_call_id) {
    roleLabel = 'tool <span style="color:#8b949e;font-weight:400">(' + esc(msg.tool_call_id) + ')</span>';
  }
  var html = '<div class="msg ' + esc(role) + '">';
  html += '<div class="msg-role">' + roleLabel + '</div>';
  html += '<div class="msg-content">' + msgContent(msg.content) + '</div>';
  if (msg.tool_calls && msg.tool_calls.length) {
    html += renderToolCalls(msg.tool_calls);
  }
  html += '</div>';
  return html;
}

function renderOverview(e) {
  var lines = 'Status: ' + e.status_code + '\\nTime: ' + fmtTime(e.timestamp_ms) + '\\nRequest: ' + fmtSize(e.request_body_size) + '  |  Response: ' + fmtSize(e.response_body_size);
  if (e.sse) {
    lines += '\\nSSE: ' + (e.sse_combine_status || 'streaming');
    if (e.sse_duration_ms != null) lines += '  (' + e.sse_duration_ms + 'ms)';
  }
  return '<div class="detail-section"><h3>Overview</h3><pre>' + esc(lines) + '</pre></div>';
}

function renderAIDetail(e) {
  var ai = e.ai_insights || {};
  var aiReq = ai.request || {};
  var aiRes = ai.response || {};
  var req = (e.request_body && typeof e.request_body === 'object') ? e.request_body : {};
  var res = (e.response_body && typeof e.response_body === 'object') ? e.response_body : {};
  var html = '';

  html += '<div class="ai-meta">';
  html += metaItem('Model', aiReq.model || req.model || '-');
  if (aiRes.usage) {
    var u = aiRes.usage;
    html += metaItem('Tokens', (u.prompt_tokens||0) + ' prompt + ' + (u.completion_tokens||0) + ' completion = ' + (u.total_tokens||0) + ' total');
  }
  if (aiRes.finish_reasons && aiRes.finish_reasons.length) {
    html += metaItem('Finish', aiRes.finish_reasons.join(', '));
  }
  html += metaItem('Stream', req.stream ? 'Yes' : 'No');
  if (aiReq.message_count) html += metaItem('Messages', aiReq.message_count);
  html += '</div>';

  var sysPrompts = aiReq.system_prompts || [];
  if (sysPrompts.length) {
    html += collapsible('&#x1F4DC; System Prompt', '<pre>' + esc(sysPrompts.join('\\n---\\n')) + '</pre>', true);
  }

  var tools = req.tools || [];
  if (tools.length) {
    html += collapsible('&#x1F527; Tools (' + tools.length + ')', renderTools(tools), false);
  }

  var messages = (req.messages || []).filter(function(m) { return m.role !== 'system'; });
  if (messages.length) {
    var msgsHtml = messages.map(renderMessage).join('');
    html += collapsible('&#x1F4AC; Messages (' + messages.length + ')', msgsHtml, true);
  }

  var choices = res.choices || [];
  if (choices.length) {
    var respHtml = '';
    choices.forEach(function(choice) {
      var msg = choice.message || {};
      if (msg.content) {
        respHtml += '<div class="msg assistant"><div class="msg-role">assistant</div><div class="msg-content">' + msgContent(msg.content) + '</div>';
        if (msg.tool_calls && msg.tool_calls.length) respHtml += renderToolCalls(msg.tool_calls);
        respHtml += '</div>';
      } else if (msg.tool_calls && msg.tool_calls.length) {
        respHtml += '<div class="msg assistant"><div class="msg-role">assistant (tool calls)</div>' + renderToolCalls(msg.tool_calls) + '</div>';
      }
    });
    if (respHtml) {
      html += collapsible('&#x1F916; Assistant Response', respHtml, true);
    }
  }

  if (e.sse && e.sse_combine_status) {
    var sseInfo = 'Combine status: ' + e.sse_combine_status;
    if (e.sse_duration_ms != null) sseInfo += '\\nDuration: ' + e.sse_duration_ms + 'ms';
    if (e.sse_error) sseInfo += '\\nError: ' + e.sse_error;
    html += collapsible('&#x1F4E1; SSE Info', '<pre>' + esc(sseInfo) + '</pre>', false);
  }

  return html;
}

function renderAGUIDetail(e) {
  var res = (e.response_body && typeof e.response_body === 'object') ? e.response_body : {};
  var run = res.run || {};
  var html = '';

  html += '<div class="ai-meta">';
  html += metaItem('Protocol', 'AGUI');
  if (run.run_id) html += metaItem('Run ID', run.run_id);
  if (run.thread_id) html += metaItem('Thread ID', run.thread_id);
  html += metaItem('Status', run.status || '-');
  html += metaItem('Events', res.event_count || 0);
  html += '</div>';

  if (run.error) {
    html += '<div class="detail-section"><pre style="color:#f85149">' + esc(run.error) + '</pre></div>';
  }

  var messages = res.messages || [];
  if (messages.length) {
    var msgsHtml = messages.map(function(msg) {
      var role = msg.role || 'assistant';
      return '<div class="msg ' + esc(role) + '"><div class="msg-role">' + esc(role) + '</div><div class="msg-content">' + esc(msg.content || '') + '</div></div>';
    }).join('');
    html += collapsible('&#x1F4AC; Messages (' + messages.length + ')', msgsHtml, true);
  }

  var toolCalls = res.tool_calls || [];
  if (toolCalls.length) {
    var tcHtml = toolCalls.map(function(tc) {
      var h = '<div class="tool-card"><div class="tool-name">' + esc(tc.name || 'unknown') + '</div>';
      if (tc.arguments != null) h += '<h4>Arguments</h4><pre>' + escJson(tc.arguments) + '</pre>';
      if (tc.result != null) h += '<h4>Result</h4><pre>' + escJson(tc.result) + '</pre>';
      h += '</div>';
      return h;
    }).join('');
    html += collapsible('&#x1F527; Tool Calls (' + toolCalls.length + ')', tcHtml, true);
  }

  var reasoning = res.reasoning || [];
  if (reasoning.length) {
    var rHtml = reasoning.map(function(r) { return '<pre>' + esc(r.content || '') + '</pre>'; }).join('');
    html += collapsible('&#x1F9E0; Reasoning', rHtml, false);
  }

  if (res.state != null) {
    html += collapsible('&#x1F4E6; State', '<pre>' + escJson(res.state) + '</pre>', false);
  }

  return html;
}

function renderGenericDetail(e) {
  var html = '';
  html += collapsible('&#x2B06;&#xFE0F; Request Body', '<pre>' + escJson(e.request_body) + '</pre>', true);
  html += collapsible('&#x2B07;&#xFE0F; Response Body', '<pre>' + escJson(e.response_body) + '</pre>', true);
  return html;
}

function renderDetail(e) {
  var html = '<h2>' + esc(e.method) + ' ' + esc(e.path) + '</h2>';
  html += renderOverview(e);

  if (e.ai_insights) {
    html += renderAIDetail(e);
    html += collapsible('Raw Request', '<pre>' + escJson(e.request_body) + '</pre>', false);
    html += collapsible('Raw Response', '<pre>' + escJson(e.response_body) + '</pre>', false);
  } else if (e.response_body && typeof e.response_body === 'object' && e.response_body.object === 'agui.completion') {
    html += renderAGUIDetail(e);
    html += collapsible('Raw Request', '<pre>' + escJson(e.request_body) + '</pre>', false);
  } else {
    html += renderGenericDetail(e);
  }

  html += collapsible('Request Headers', renderHeaders(e.request_headers), false);
  html += collapsible('Response Headers', renderHeaders(e.response_headers), false);

  return html;
}

function renderStats(stats) {
  var bar = document.getElementById('stats-bar');
  bar.innerHTML = '<div class="stat"><div class="value">' + stats.total_requests + '</div><div class="label">Requests</div></div>'
    + '<div class="stat"><div class="value">' + stats.ai_requests + '</div><div class="label">AI Requests</div></div>'
    + '<div class="stat"><div class="value">' + stats.total_tokens.toLocaleString() + '</div><div class="label">Tokens</div></div>'
    + '<div class="stat"><div class="value">' + (stats.models.join(', ') || '-') + '</div><div class="label">Models</div></div>';
}

function renderTable(data) {
  var tbody = document.getElementById('traffic-table');
  if (!data.entries.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No traffic recorded yet</td></tr>';
    return;
  }
  tbody.innerHTML = data.entries.map(function(e, i) {
    var tags = [];
    if (e.ai_insights) tags.push('<span class="badge ai">AI</span>');
    if (e.agui) tags.push('<span class="badge agui">AGUI</span>');
    if (e.sse) tags.push('<span class="badge sse">SSE</span>');
    return '<tr class="clickable" onclick="showDetail(' + i + ')">'
      + '<td>' + i + '</td><td>' + fmtTime(e.timestamp_ms) + '</td>'
      + '<td><span class="method ' + e.method + '">' + e.method + '</span></td>'
      + '<td>' + esc(e.path) + '</td>'
      + '<td><span class="status ' + statusClass(e.status_code) + '">' + e.status_code + '</span></td>'
      + '<td>' + fmtSize(e.request_body_size) + '</td><td>' + fmtSize(e.response_body_size) + '</td>'
      + '<td>' + tags.join(' ') + '</td></tr>';
  }).join('');
}

function setFilter(ai) {
  aiOnly = ai;
  document.getElementById('btn-all').className = ai ? '' : 'active';
  document.getElementById('btn-ai').className = ai ? 'active' : '';
  refresh();
}

function showDetail(idx) {
  var summary = entries[idx];
  if (!summary) return;
  var panel = document.getElementById('detail-panel');
  var overlay = document.getElementById('overlay');
  var content = document.getElementById('detail-content');
  content.innerHTML = '<div class="loading">Loading detail...</div>';
  panel.className = 'detail-panel open';
  overlay.className = 'overlay open';
  fetch('/api/traffic/' + summary._index)
    .then(function(res) { return res.json(); })
    .then(function(e) {
      if (e.error) { content.innerHTML = '<div class="loading">Error: ' + esc(e.error) + '</div>'; return; }
      content.innerHTML = renderDetail(e);
    })
    .catch(function() {
      content.innerHTML = '<div class="loading">Failed to load detail</div>';
    });
}

function truncate() {
  if (!confirm('Clear all traffic log entries?')) return;
  fetch('/api/truncate', {method: 'POST'})
    .then(function() { closeDetail(); refresh(); })
    .catch(function(e) { console.error('Truncate failed:', e); });
}

function closeDetail() {
  document.getElementById('detail-panel').className = 'detail-panel';
  document.getElementById('overlay').className = 'overlay';
}

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeDetail();
});

function refresh() {
  Promise.all([
    fetch('/api/traffic?limit=200' + (aiOnly ? '&ai=true' : '')),
    fetch('/api/stats')
  ]).then(function(responses) {
    return Promise.all(responses.map(function(r) { return r.json(); }));
  }).then(function(results) {
    var traffic = results[0];
    var stats = results[1];
    entries = traffic.entries;
    renderStats(stats);
    renderTable(traffic);
    document.getElementById('last-refresh').textContent = 'Updated: ' + new Date().toLocaleTimeString();
  }).catch(function(e) {
    console.error('Refresh failed:', e);
  });
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


def _summary_entry(entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Return a lightweight summary of a traffic entry for list views."""
    summary = {
        "_index": index,
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
    resp = entry.get("response_body")
    if isinstance(resp, dict) and resp.get("object") == "agui.completion":
        summary["agui"] = True
    return summary


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard API and frontend."""

    # Set by create_dashboard_server before serving.
    entries: Optional[List[Dict[str, Any]]] = []
    traffic_log_path: Optional[str] = None

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def _get_entries(self) -> List[Dict[str, Any]]:
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

    def _serve_traffic_list(self, params: Dict[str, List[str]]):
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
    """Overwrite the traffic log file with an empty list."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
    except OSError:
        pass


def _read_traffic_log(path: str) -> List[Dict[str, Any]]:
    """Read traffic log entries from a JSON file. Returns [] on any error."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def create_dashboard_server(
    entries: Optional[List[Dict[str, Any]]] = None,
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
        traffic_log_path: Path to traffic_log.json for live reading.

    Returns:
        An HTTPServer instance ready to serve_forever().
    """

    class _Handler(DashboardHandler):
        pass

    _Handler.entries = entries
    _Handler.traffic_log_path = traffic_log_path

    server = HTTPServer((host, port), _Handler)
    return server
