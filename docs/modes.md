# Mode Reference

Capturly has four operating modes, each designed for a specific workflow.

## Record Mode

**Purpose:** Capture real API responses for later replay.

**When to use:**
- Setting up test fixtures
- Capturing production traffic for offline debugging
- Creating a baseline of API responses

**Command:**
```bash
capturly --mode record --backend https://api.example.com
```

**Behavior:**
1. Receives HTTP request from client
2. Forwards request to backend (preserving headers, body)
3. Receives response from backend
4. Saves complete response to disk
5. Returns response to client

**What's saved:**
- Request method, path, body hash
- Response status code, headers, body
- Timestamp
- Cache key (for replay lookup)

**Storage format:**
```json
{
  "method": "POST",
  "path": "/v1/chat/completions",
  "request_body_size": 1024,
  "status_code": 200,
  "response_headers": {
    "Content-Type": "application/json"
  },
  "response_body": "{\"id\": \"chatcmpl-...\", ...}",
  "body_encoding": "utf-8",
  "cache_key": "sha256:a1b2c3d4..."
}
```

**SSE streams:**
- Metadata saved (marker file)
- Stream forwarded live to client
- Body not cached (too large)

**Error handling:**
- Backend errors (4xx, 5xx) are recorded and returned to client
- Network errors return 502 to client

## Replay Mode

**Purpose:** Serve cached responses without hitting the backend.

**When to use:**
- Running test suites
- Offline development
- Stress testing with zero latency

**Command:**
```bash
capturly --mode replay
```

**Behavior:**
1. Receives HTTP request from client
2. Computes cache key (method + path + body hash)
3. Looks for matching recording on disk
4. If found: returns recorded response
5. If not found: returns 404 with helpful message

**Options:**
- `--delay <ms>`: Add artificial delay before response (simulate slow API)

**Missing recording response:**
```json
{
  "error": "No recording found",
  "hint": "Run in RECORD mode first"
}
```

## Hybrid Mode

**Purpose:** Cache-through — replay if cached, record if new.

**When to use:**
- Day-to-day development
- Automatic caching of common endpoints
- Recording new endpoints without mode switching

**Command:**
```bash
capturly --mode hybrid --backend https://api.example.com
```

**Behavior:**
1. Receives HTTP request from client
2. Computes cache key
3. Checks for existing recording
4. **Cache hit:** Replay recorded response (with optional delay)
5. **Cache miss:** Proxy to backend and record response

## Log Mode

**Purpose:** Full traffic inspection for debugging.

**When to use:**
- Debugging production issues
- Inspecting AI traffic
- Analyzing API behavior
- Creating reproducible bug scenarios

**Command:**
```bash
capturly --mode log --backend https://api.openai.com
```

**Behavior:**
1. Receives HTTP request from client
2. Forwards request to backend
3. Receives response from backend
4. Logs complete request and response
5. Returns response to client

**What's logged:**
```json
{
  "timestamp_ms": 1705450200000,
  "method": "POST",
  "path": "/v1/chat/completions",
  "cache_key": "sha256:...",
  "request_headers": {},
  "request_body": {},
  "request_body_size": 1024,
  "status_code": 200,
  "response_headers": {},
  "response_body": {},
  "response_body_size": 2048
}
```

**Storage:**
- `traffic_log.json`: Array of all request/response entries
- `sse-events/*.jsonl`: Individual SSE event streams

**Options:**
- `--combine-chunks`: Combine streaming chunks into final response (OpenAI and AGUI protocols auto-detected)

## Mode Comparison

| Feature | Record | Replay | Hybrid | Log |
|---------|--------|--------|--------|-----|
| Backend required | Yes | No | Yes | Yes |
| Saves responses | Yes | No | Yes | No |
| Full traffic log | No | No | No | Yes |
| AI inspection | No | No | No | Yes |
| AGUI inspection | No | No | No | Yes |
| Use case | Test fixtures | Offline tests | Dev speedup | Debugging |

## SSE Chunk Combining

When `--combine-chunks` is enabled in log mode, Capturly auto-detects the streaming protocol from the first SSE event and combines chunks accordingly:

**OpenAI protocol** (detected by `choices` array in event data):
- Merges `delta.content`, `delta.tool_calls`, `delta.function_call` across chunks
- Produces a complete `chat.completion` object with usage stats

**AGUI protocol** (detected by `type` field matching known AGUI event types):
- Combines `TEXT_MESSAGE_START/CONTENT/END` into full messages
- Combines `TOOL_CALL_START/ARGS/END/RESULT` into full tool calls with parsed JSON arguments
- Captures `RUN_STARTED/FINISHED/ERROR` lifecycle metadata
- Keeps latest `STATE_SNAPSHOT`; counts `STATE_DELTA` patches (not applied)
- Combines `REASONING_MESSAGE_*` into reasoning content
- Produces an `agui.completion` object

**Unknown protocols** are ignored (entry gets `no_valid_chunks` fallback).
