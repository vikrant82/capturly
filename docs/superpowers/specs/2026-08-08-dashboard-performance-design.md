# Dashboard Performance — Offset Index & Lazy Detail Rendering

**Date:** 2026-08-08
**Status:** Approved
**Scope:** `src/capturly/dashboard.py`, new `src/capturly/traffic_index.py`, `src/capturly/dashboard.html`, related tests

## Problem

The dashboard becomes unusable as traffic logs grow. Measured against a real log
(47 entries / 64 MB, four entries of 12.75 MB each, median entry 203 KB):

1. **Full re-read per request** — `_get_entries()` reads and `json.loads`-parses the
   entire JSONL file on every API call (`_serve_traffic_list`, `_serve_stats`,
   `_serve_traffic_detail`). ~1-2 s per parse in CPython.
2. **Polling amplifies it** — the frontend polls `/api/traffic` + `/api/stats` every
   3 seconds: ~2 full parses of the whole file every 3 s, indefinitely.
3. **Single-threaded server** — `create_dashboard_server` uses plain `HTTPServer`,
   so one slow parse blocks all other requests.
4. **Fat list payload** — `_summary_entry` embeds the complete `ai_insights` (including
   full system-prompt text) into every list row, so list responses carry megabytes.
5. **Detail view renders everything immediately** — opening an entry builds HTML for
   every section, including a full JSON-tree render of multi-MB request/response bodies
   plus a hidden pretty-printed duplicate for copy. One click can inject 30-50 MB of
   HTML into the DOM, freezing the tab.

Primary use case being optimized: **post-hoc inspection** — browse the list, open
specific calls. Logs are never truncated by users and grow unbounded (currently up to
a few hundred MB). Smart truncation in the detail view is acceptable.

## Approach

Offset index + lazy detail rendering (Approach A). Rejected alternatives:

- **B — whole-file parse cache:** smallest diff, but keeps all parsed entries resident
  (3-5x file size; unbounded growth since users never truncate) and re-parses the whole
  file on every restart.
- **C — persistent sidecar index:** Approach A plus persisting the index to disk.
  Deferred as a future upgrade; A's index module is isolated so C can slot in later.

## Components & Architecture

### New module: `src/capturly/traffic_index.py`

```
TrafficIndex
├── state:  path, indexed_size, inode key, entries: list[IndexedEntry], stats counters
│           IndexedEntry = (offset: int, length: int, summary: dict)
├── sync()          → stat file; parse only bytes beyond indexed_size; append entries
├── summaries()     → list of slim summary dicts (for /api/traffic)
├── stats()         → counters maintained incrementally during sync (for /api/stats)
├── load_entry(i)   → seek to offset of entry i, read exactly that line, json.loads
└── reset()         → drop all index state (truncate / file replacement)
```

- Full entry bodies are **never retained**: each new line is parsed once during
  `sync()` to extract its summary, then discarded. Resident memory is offsets +
  summaries only (a few hundred bytes per entry).
- The module is self-contained (no dashboard imports) so a persistent index can
  replace or wrap it later without touching callers.

### Modified: `src/capturly/dashboard.py`

- File mode (`traffic_log_path` set) replaces `_read_traffic_log()` with a
  `TrafficIndex` instance owned by the handler class. Static-entries mode (used by
  tests) is unchanged; the index is file-mode only.
- `HTTPServer` → `ThreadingHTTPServer` (ThreadingMixIn pattern, as in `server.py`),
  so a slow detail parse cannot block list refreshes.
- `_serve_traffic_list` / `_serve_stats` serve from index summaries/counters; no file
  reads, no full-entry parsing at request time.
- `_serve_traffic_detail(index)` → `index.load_entry(index)`: seek to the stored
  offset, read one line, parse one entry.
- `POST /api/truncate` truncates the file **and** resets the index.

### Data flow

```
Poll:  GET /api/traffic → sync() tails only new bytes → cached summaries (KB payload)
Click: GET /api/traffic/N → seek(offset) → parse ONE line → full entry → lazy render
```

## Index Lifecycle & Consistency

`sync()` runs lazily at the start of every list/stats/detail request, under a
`threading.Lock`:

```
stat the file
├─ missing                              → entries=[], indexed_size=0, done
├─ inode changed or size < indexed_size → reset (replaced/truncated), rescan from 0
└─ size > indexed_size                  → seek(indexed_size), parse complete lines only
```

Rules:

1. **Partial line at EOF** — the logger may be mid-write. Only `\n`-terminated lines
   are indexed; a trailing fragment is left un-indexed and `indexed_size` stops at the
   last complete line. The next sync picks the fragment up once the write finishes.
2. **Malformed lines** — skipped as entries (as today), but the cursor advances past
   them, so `_index` values stay stable between list and detail.
3. **`_index` stability** — summaries contain only valid entries; detail fetch by
   `_index` maps to an exact file offset regardless of skipped garbage.
4. **Concurrency** — the lock guards index mutation; `sync()` is idempotent and costs
   one `stat()` when nothing is new. Detail reads (seek+read at fixed offsets) are
   read-only and need no lock.
5. **Stats counters** (total_requests, ai_requests, total_tokens, models set) are
   maintained incrementally during sync from summary fields; `/api/stats` never
   iterates entries.

### Slim summary shape

Returned per entry by `/api/traffic` — everything needed for the table, filters,
badges, and stats; nothing larger:

```json
{
  "_index": 12,
  "timestamp_ms": 1723100000000,
  "method": "POST",
  "path": "/v1/chat/completions",
  "status_code": 200,
  "request_body_size": 1234,
  "response_body_size": 5678,
  "source_name": "pipe-a",
  "backend_url": "http://localhost:8000",
  "duration_ms": 420,
  "sse": true,
  "agui": false,
  "tools": true,
  "ai": {
    "model": "gpt-4o",
    "message_count": 42,
    "tool_count": 8,
    "tool_names": ["search", "edit"],
    "total_tokens": 12345
  }
}
```

- `ai` is present only when the entry has `ai_insights`; it replaces the full
  `ai_insights` blob in list responses. No `system_prompts`, no `roles`, no usage
  breakdown beyond `total_tokens`.
- Flags (`sse`, `agui`, `tools`) are derived with the same rules as today's
  `_summary_entry`.

## Frontend: Lazy Rendering & Smart Truncation

**Nothing renders until opened.**

1. **Lazy sections** — `renderDetail()` renders only the small parts immediately
   (overview, AI meta bar). Every collapsible section (System Prompt, Tools, Messages,
   Assistant Response, Raw Request/Response, Headers) is emitted as
   `<details data-section="..." data-rendered="false">` with a placeholder body. A
   delegated `toggle` listener renders a section's content from the in-memory entry
   object the first time it is expanded. Opening a 12 MB entry produces a ~1 KB DOM.
2. **Text truncation** — message content, system prompts, reasoning blocks render the
   first **2,000 characters** plus a "Show more (+N chars)" button that progressively
   reveals the rest (doubling per click). The full string is already in memory; no
   extra fetches.
3. **JSON tree caps** — containers render their first **100 children**, then a
   "… N more (load)" row revealing the next 100 per click; string values inside the
   tree follow the same 2,000-char truncation with inline expansion.
4. **Copy semantics** — the copy button copies what is currently rendered (existing
   behavior). Expand / reveal first to copy more.
5. **Memory hygiene** — the fetched full entry lives in `currentDetail` only while the
   panel is open; released on close.
6. **Polling** — the 3 s interval stays (it is now nearly free) but **pauses while the
   detail panel is open**; resumes on close.
7. **Shape adaptation** — badges/filters switch from `e.ai_insights` presence to the
   slim `e.ai` object; list `limit` stays 200; sort order and source filter unchanged.

## Error Handling

| Situation | Behavior |
|---|---|
| Log file missing | Empty list, zeroed stats (unchanged) |
| Malformed JSON line | Skipped; cursor advances; `_index` unaffected |
| Partial line at EOF | Deferred to next sync |
| File externally truncated/replaced | Inode/size detection → index reset + rescan |
| Detail read/parse fails at offset | `404 {"error": ...}`; no crash |
| Concurrent requests | Lock-serialized `sync()`; detail reads lock-free |

The list shape change (`ai_insights` → slim `ai`) affects only the dashboard's own
frontend; there are no external API consumers.

## Testing

- **New `tests/test_traffic_index.py`**
  - Offset round-trip: every `_index` loads back the exact original dict
  - Incremental append: new entries added, existing ones unchanged
  - Partial line at EOF deferred until completed
  - Malformed line skipped with consistent numbering
  - Truncate/shrink and inode-change both reset and rescan
  - Slim summary content: `ai` fields present; **no `system_prompts` or other large
    fields leak into summaries**
  - Stats counters correct across syncs
- **Updated dashboard tests** (`test_dashboard.py`, `test_dashboard_integration.py`)
  - File-mode list returns slim shape; detail returns the full entry via offsets
  - Live-update and truncate tests pass against the index
  - `ai=true` filter works on the slim shape
  - Concurrent-request sanity check on the threaded server
  - Existing static-entries tests pass unchanged
- **Frontend Node harness** (`tests/frontend/dashboard_js_test.js`)
  - Sections carry placeholders until toggled (lazy rendering)
  - Truncation helpers cap text at the limit and cap JSON tree children
  - Badge/filter logic works on the slim `ai` shape

## Performance Goals

Measured against the reference 64 MB / 47-entry log:

- First dashboard open (one-time index pass): **< 3 s**
- Every subsequent list/stats refresh: **< 100 ms**, payload < ~50 KB
- Detail click on a 12 MB entry: panel opens instantly; any section expands in **< 1 s**
- No browser jank; behavior holds as the file grows further

## Out of Scope

- Persistent/sidecar index (Approach C) — future upgrade path; index module is isolated
- Log rotation/retention policies
- Full-text search over entry bodies
