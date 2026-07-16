# Capturly Product Specification

## Vision

**Record, replay, and debug any HTTP API — with built-in AI traffic intelligence**

Capturly is a developer-first API debugging tool that captures, replays, and analyzes HTTP traffic. It's not just another AI proxy — it's a universal API debugger that happens to understand AI protocols exceptionally well.

## Core Positioning

### Primary Identity: API Traffic Debugger
- Works with ANY HTTP/HTTPS API
- Captures complete request/response cycles
- Handles streaming (SSE, chunked transfer)
- Provides human-readable logs with automatic decompression

### Secondary Identity: AI-Aware Inspector
- Understands OpenAI, Anthropic, Gemini, and MCP protocols
- Extracts and surfaces system prompts, tools, and token usage
- Combines streaming chunks into complete responses
- Debugs AI agents, not just endpoints

## Target Use Cases

### 1. API Development & Testing
**Problem:** Tests are flaky when they depend on third-party APIs  
**Solution:** Record real API responses, replay them instantly in tests

```bash
# Record once
capturly --mode record --backend https://api.stripe.com --port 8080
curl http://localhost:8080/v1/charges

# Replay forever (deterministic tests)
capturly --mode replay --port 8080
```

### 2. Debugging Production Issues
**Problem:** Can't reproduce production bugs locally  
**Solution:** Capture production traffic, replay exact failure scenarios

```bash
# In production (or staging)
capturly --mode log --backend https://api.production.com --port 8080

# Download the traffic log, replay locally
capturly --mode replay --port 8080
# Now you can step through the exact production scenario
```

### 3. Performance Testing
**Problem:** Need to test how your app handles slow APIs  
**Solution:** Replay recorded responses with artificial delays

```bash
# Replay with 2-second delay (simulate slow API)
capturly --mode replay --port 8080 --delay 2000

# Or simulate network throttling
capturly --mode replay --port 8080 --throttle 100kbps
```

### 4. AI/LLM Development
**Problem:** Can't see what your AI agent is actually sending/receiving  
**Solution:** Intercept and inspect complete LLM conversations

```bash
capturly --mode log --backend https://api.openai.com --port 8080
# Now see:
# - Full system prompts
# - Tool definitions and calls
# - Token usage
# - Streaming chunks combined into final response
```

### 5. Hybrid Caching (Development Speed)
**Problem:** Development is slow because every request hits the backend  
**Solution:** Cache responses automatically, fall back to backend for new endpoints

```bash
# First request: hits backend, caches response
# Subsequent requests: instant replay from cache
# New endpoints: automatically recorded
capturly --mode hybrid --backend https://api.example.com --port 8080
```

## Technical Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────┐
│                    Capturly Server                       │
├─────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ HTTP Server  │  │ HTTP Server  │  │   Dashboard  │  │
│  │  (listener)  │  │   (admin)    │  │   Web UI     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │          │
│  ┌──────┴──────────────────┴──────────────────┴───────┐ │
│  │              Request Handler                        │ │
│  │  - Mode routing (record/replay/hybrid/log)         │ │
│  │  - SSE detection and streaming                     │ │
│  │  - AI protocol detection                           │ │
│  └──────┬──────────────────┬──────────────────┬───────┘ │
│         │                  │                  │          │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────┴───────┐  │
│  │   Recorder   │  │   Replayer   │  │   Logger     │  │
│  │  (proxy +    │  │ (load from   │  │ (full req/   │  │
│  │   save)      │  │   storage)   │  │  res capture)│  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │          │
│  ┌──────┴──────────────────┴──────────────────┴───────┐ │
│  │              Storage Layer                          │ │
│  │  - Disk-based recordings (JSON)                    │ │
│  │  - Traffic logs (JSON array)                       │ │
│  │  - SSE event streams (JSONL)                       │ │
│  │  - Cache management                                │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │         AI Traffic Intelligence Layer              │ │
│  │  - OpenAI protocol parser                          │ │
│  │  - Anthropic protocol parser                       │ │
│  │  - SSE chunk combiner                              │ │
│  │  - Token usage extraction                          │ │
│  │  - Tool call tracking                              │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Module Structure

```
src/capturly/
├── __init__.py          # Package initialization, version
├── __main__.py          # Entry point for python -m capturly
├── cli.py               # Command-line interface
├── server.py            # HTTP server setup, TLS handling
├── handler.py           # Main request handler, mode routing
├── proxy.py             # HTTP proxy logic, request forwarding
├── storage.py           # Disk-based storage, cache keys
├── logger.py            # Traffic logging, async writer
├── sse.py               # SSE stream handling, chunk combining
├── utils.py             # Utility functions (atomic writes, etc.)
└── modes/
    ├── __init__.py      # Mode exports
    ├── record.py        # Record mode implementation
    ├── replay.py        # Replay mode implementation
    ├── hybrid.py        # Hybrid mode implementation
    └── log.py           # Log mode implementation
```

## Feature Specifications

### 1. Core Modes

#### Record Mode
- **Purpose:** Capture real API responses for later replay
- **Behavior:**
  - Proxy requests to backend
  - Save complete response (headers, body, status)
  - Handle binary files (base64 encoding)
  - Detect SSE streams (save metadata only, stream live)
  - Skip non-cacheable status codes (504, etc.)
- **Storage:** `<recordings-dir>/<cache-key>.json`

#### Replay Mode
- **Purpose:** Serve cached responses without hitting backend
- **Behavior:**
  - Load response from disk based on cache key
  - Apply configured delay (simulate slow APIs)
  - Return exact recorded response
  - Return 404 with helpful message if not found
- **Options:**
  - `--delay <ms>`: Artificial delay before response
  - `--throttle <rate>`: Bandwidth throttling (future)

#### Hybrid Mode
- **Purpose:** Best of both worlds - replay if cached, record if new
- **Behavior:**
  - Check cache for matching request
  - If found: replay (with optional delay)
  - If not found: proxy to backend and record
- **Use case:** Development speedup - common endpoints cached, new ones auto-recorded

#### Log Mode
- **Purpose:** Full traffic inspection for debugging
- **Behavior:**
  - Proxy requests to backend
  - Log complete request and response
  - Capture headers, body, timestamps, metadata
  - Handle SSE streams with detailed event logging
  - Optional: combine OpenAI streaming chunks
- **Storage:**
  - `traffic_log.json`: Array of all request/response entries
  - `sse-events/*.jsonl`: Individual SSE event streams

### 2. Cache Key Generation

```python
def generate_cache_key(method: str, path: str, body: bytes) -> str:
    """Generate deterministic cache key from request."""
    content = f"{method}:{path}:{hashlib.md5(body).hexdigest()}"
    return hashlib.sha256(content.encode()).hexdigest()
```

**Rationale:** Includes method, path, and body hash to ensure uniqueness.

### 3. HTTPS Support

#### Backend HTTPS (Already Works)
- Use `urllib.request.urlopen()` with HTTPS URLs
- Automatic TLS verification
- No configuration needed

#### Listener HTTPS (Phase 1 Feature)
- Support `--tls-cert` and `--tls-key` for custom certificates
- Auto-generate self-signed certificates for localhost with `--tls-auto`
- Use `ssl.SSLContext` to wrap the HTTP server

```bash
# Auto-generate self-signed cert
capturly --mode log --backend https://api.openai.com \
         --listen https://localhost:8080 --tls-auto

# Use existing certificates
capturly --mode log --backend https://api.openai.com \
         --listen https://localhost:8080 \
         --tls-cert ./cert.pem --tls-key ./key.pem
```

### 4. SSE (Server-Sent Events) Handling

#### Detection
- Check `Content-Type: text/event-stream` header
- Check `Accept: text/event-stream` in request

#### Recording
- Save metadata only (no body caching)
- Stream live to client
- Create `.sse` marker file

#### Logging
- Parse SSE events line by line
- Extract `data`, `event`, `id`, `retry` fields
- Save to separate JSONL file per request
- Optional: combine OpenAI-style chunks into final completion

#### Chunk Combining (OpenAI-specific)
- Accumulate `choices[].delta` fields
- Merge `content`, `tool_calls`, `function_call` across chunks
- Reconstruct complete `chat.completion` object
- Track tool call order and indices

### 5. AI Traffic Intelligence

#### OpenAI Protocol Support

**Detection:**
- Path contains `/v1/chat/completions` or `/v1/completions`
- Response has `object: chat.completion` or `chat.completion.chunk`

**Extraction:**
```python
{
    "model": "gpt-4",
    "system_prompt": "You are a helpful assistant...",
    "messages": [...],
    "tools": [...],
    "tool_calls": [...],
    "token_usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150
    },
    "streamed": True,
    "chunks_combined": 42
}
```

#### Anthropic Protocol Support (Phase 2)
- Path contains `/v1/messages`
- Extract system prompt, messages, tool_use blocks

#### Generic JSON Inspection
- Pretty-print JSON bodies in logs
- Decode base64-encoded content
- Decompress gzip responses

### 6. CLI Interface

```bash
capturly [OPTIONS]

Options:
  --mode [record|replay|hybrid|log]  Operating mode (default: replay)
  --backend URL                      Backend URL for proxy modes
  --port INT                         Listen port (default: 9999)
  --host HOST                        Listen host (default: 0.0.0.0)
  --delay INT                        Replay delay in milliseconds
  --recordings-dir PATH              Directory for recordings
  --tls-cert PATH                    TLS certificate file
  --tls-key PATH                     TLS private key file
  --tls-auto                         Auto-generate self-signed cert
  --combine-chunks                   Combine OpenAI SSE chunks (log mode)
  --dashboard-port INT               Dashboard web UI port
  --verbose                          Verbose logging
```

### 7. Web Dashboard (Phase 1 Feature)

**Purpose:** Visualize captured traffic in a browser

**Features:**
- List all captured requests
- Filter by method, path, status code, timestamp
- View complete request/response details
- Expand/collapse JSON bodies
- Download recordings
- Search across all traffic

**Implementation:**
- Simple Flask or FastAPI app
- Reads from `traffic_log.json`
- Serves on `--dashboard-port` (default: 9090)
- Auto-opens browser on startup (optional)

### 8. Storage Format

#### Recording File (`<cache-key>.json`)
```json
{
  "method": "POST",
  "path": "/v1/chat/completions",
  "request_body_size": 1024,
  "status_code": 200,
  "response_headers": {
    "Content-Type": "application/json",
    "X-Request-ID": "abc123"
  },
  "response_body": "{...}",
  "body_encoding": "utf-8",
  "cache_key": "sha256:...",
  "recorded_at": "2026-01-16T23:30:00Z"
}
```

#### Traffic Log Entry (in `traffic_log.json`)
```json
{
  "timestamp_ms": 1705450200000,
  "method": "POST",
  "path": "/v1/chat/completions",
  "cache_key": "sha256:...",
  "request_headers": {...},
  "request_body": {...},
  "request_body_size": 1024,
  "status_code": 200,
  "response_headers": {...},
  "response_body": {...},
  "response_body_size": 2048,
  "ai_intelligence": {
    "protocol": "openai",
    "model": "gpt-4",
    "system_prompt": "...",
    "token_usage": {...}
  }
}
```

## Implementation Phases

### Phase 1: Foundation (MVP)
- [x] Project scaffolding
- [x] Core modes (record, replay, hybrid, log)
- [x] Storage layer
- [x] SSE handling
- [x] CLI interface
- [ ] HTTPS listener with auto-cert generation
- [ ] Basic web dashboard
- [ ] OpenAI protocol detection and extraction
- [ ] README and documentation

### Phase 2: Polish
- [ ] Anthropic protocol support
- [ ] Gemini protocol support
- [ ] MCP protocol support
- [ ] Request/response modification rules
- [ ] HAR export format
- [ ] Bandwidth throttling
- [ ] Docker image
- [ ] Configuration file support (YAML/JSON)

### Phase 3: Advanced Features
- [ ] Traffic analysis and statistics
- [ ] Conditional replay (pattern matching)
- [ ] Request rewriting rules
- [ ] Team collaboration (share recordings)
- [ ] Prometheus metrics endpoint
- [ ] Grafana dashboard integration

### Phase 4: Ecosystem
- [ ] Python SDK for programmatic usage
- [ ] CI/CD integration helpers
- [ ] Kubernetes operator
- [ ] VS Code extension
- [ ] Plugin system

## Configuration System

### Environment Variables
```bash
CAPTURLY_MODE=log
CAPTURLY_BACKEND=https://api.openai.com
CAPTURLY_PORT=8080
CAPTURLY_RECORDINGS_DIR=./recordings
CAPTURLY_TLS_AUTO=true
```

### Configuration File (`capturly.yaml`)
```yaml
mode: log
backend: https://api.openai.com
port: 8080
recordings_dir: ./recordings
tls:
  auto: true
  cert: ./cert.pem
  key: ./key.pem
dashboard:
  enabled: true
  port: 9090
ai_intelligence:
  openai: true
  anthropic: true
  combine_chunks: true
logging:
  verbose: false
  file: ./capturly.log
```

## Security Considerations

1. **API Keys in Traffic**
   - Redact `Authorization` headers by default in logs
   - Configurable redaction patterns
   - Warning when sensitive data is captured

2. **TLS Certificates**
   - Auto-generated certs are for localhost only
   - Clear warnings about self-signed certs in production
   - Support for ACME/Let's Encrypt (future)

3. **Access Control**
   - Bind to localhost by default
   - Optional authentication for dashboard (future)

## Performance Considerations

1. **Async Logging**
   - Traffic logging happens in background thread
   - No blocking of request/response flow
   - Atomic file writes prevent corruption

2. **Storage Efficiency**
   - Compress large responses (optional)
   - Deduplicate identical responses (future)
   - Automatic cleanup of old recordings (optional)

3. **Memory Usage**
   - Stream SSE events, don't buffer entire response
   - Lazy load recordings in replay mode
   - Configurable memory limits (future)

## Success Metrics

### For Developers
- Time to debug API issues reduced by 50%
- Test suite reliability improved (no flaky API dependencies)
- Development speed increased (hybrid caching)

### For AI Developers
- Visibility into AI agent behavior
- Ability to reproduce AI issues locally
- Understanding of token usage and costs

### Adoption Metrics
- GitHub stars and forks
- PyPI downloads
- Community contributions
- Use case diversity (not just AI)

## Competitive Landscape

### vs. mitmproxy
- **Capturly advantage:** AI protocol understanding, simpler UX, replay with delays
- **mitmproxy advantage:** More mature, more features, scripting support

### vs. Charles Proxy / Proxyman
- **Capturly advantage:** Open source, AI-specific features, replay mode
- **Charles/Proxyman advantage:** GUI, more protocols, mobile support

### vs. AI-specific proxies (Portkey, Helicone)
- **Capturly advantage:** Universal API debugger, not just AI
- **AI proxies advantage:** Production features (rate limiting, caching, analytics)

### Positioning
Capturly fills the gap between general-purpose proxies and AI-specific tools. It's the debugger that understands AI, not just the AI tool that debugs.

## Open Questions

1. **Should we support WebSocket interception?**
   - Pro: More complete traffic capture
   - Con: More complex, less common for APIs
   - Decision: Phase 2 or 3

2. **Should we support gRPC?**
   - Pro: Growing protocol
   - Con: Requires different approach
   - Decision: Out of scope for now

3. **Should we add a plugin system?**
   - Pro: Extensibility, community contributions
   - Con: Complexity, maintenance burden
   - Decision: Phase 4

4. **Should we support multiple storage backends?**
   - Pro: PostgreSQL, S3, etc.
   - Con: Complexity, disk is usually fine
   - Decision: Start with disk, add if needed

## Conclusion

Capturly is positioned to become the go-to tool for API debugging and testing, with special strength in AI development. By focusing on developer experience, universal API support, and intelligent traffic inspection, we can build a successful open-source project that fills a real gap in the developer toolkit.

The key is to maintain the "debugger first, AI-aware second" positioning while delivering exceptional value for AI use cases. This differentiates us from both general proxies and AI-specific tools.
