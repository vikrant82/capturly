# Dashboard Guide

The built-in web dashboard visualizes captured traffic in a browser. It runs alongside the proxy — default `http://localhost:9090`, configurable via `--dashboard-port` — and is most useful in `log` mode, where the full traffic history is persisted.

## Traffic list

The table shows the **newest 200 entries** by default and auto-refreshes every 3 seconds.

- **Pagination** — page 0 is anchored at the newest entry, so live traffic always appears. Use `‹ Newer` / `Older ›` to walk backward through history in 200-entry windows; the status line shows the visible range. The `#` column is the global log index. If the log shrinks past the page you're viewing (e.g. after a truncate), the view jumps back to the newest page.
- **Filters** — method, path substring, status code, and source (one option per backend when multiple proxies share a recordings directory). Filters apply within the loaded window.
- **Tag chips** — OR semantics: an entry matches if it has *any* active chip.

| Chip | Meaning |
|------|---------|
| AI | OpenAI-style chat completion traffic |
| AGUI | Agent GUI protocol traffic |
| SSE | Streaming responses that were combined |
| Tools | The assistant's response issued tool calls |
| Tool Results | The request carried tool output back to the model (`tool` / legacy `function` message roles) |

`Tools` and `Tool Results` are complementary views of an agentic loop: one tags the call where the model asked for tools, the other tags the follow-up call where the results came back. Entries can have both.

## Detail panel

Click a row to open the detail panel. Sections render lazily on first expansion to keep large logs snappy, and each has a copy button.

**AI entries (OpenAI protocol):**

- **Overview** — source, status, timing, sizes
- **AI meta** — model, token usage, finish reasons, stream flag, message count
- **System Prompt** — extracted system prompt(s), including multi-part content
- **Tools** — tool definitions with parsed parameter schemas
- **New Inputs** — the messages added after the last assistant turn: a user message, or one-or-more trailing tool results (parallel tool results stay together)
- **Messages History** — the conversation up to and including that last assistant message; hidden when empty (first-turn requests)
- **Assistant Response** — content and tool calls from the response
- **SSE Info** — combine status and stream duration (combined streams only)
- **Raw Request / Raw Response** — full bodies as collapsible JSON trees; **Headers** — both sides

**AGUI entries:** run metadata, reconstructed messages, tool calls with parsed arguments and results, reasoning, state snapshots.

**Generic entries:** collapsible request/response bodies and headers.

## Truncate

The **Truncate** button clears the traffic log (with a confirmation prompt). Cache files for replay are untouched.

## Performance notes

The list view is served from a lightweight in-memory index that syncs incrementally with `traffic_log.jsonl` — it never reloads whole log entries per poll. The first request after dashboard startup pays a one-time scan of the log (roughly 2s for a 240MB log); subsequent polls take ~1ms. Detail bodies are fetched per entry only when opened.

## API endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/traffic?page=N&limit=N` | Summary window counted from the **newest** entries (page 0 = latest) |
| `GET /api/traffic?limit=N&offset=N` | Summary window counted from the oldest (legacy) |
| `GET /api/traffic?ai=true` | AI traffic only (composable with the above) |
| `GET /api/traffic/{index}` | Full entry: bodies, headers, insights |
| `GET /api/stats` | Total requests, AI requests, tokens, models |
| `POST /api/truncate` | Clear all traffic entries |
