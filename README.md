# Capturly

**Record, replay, and debug any HTTP API — with built-in AI traffic intelligence**

Capturly is an HTTP proxy that captures, replays, and inspects API traffic. It works with any REST/GraphQL endpoint but goes deeper on AI protocols — automatically detecting OpenAI and AGUI (Agent GUI) traffic, extracting system prompts, combining streaming chunks, and surfacing tool calls.

## Quick Start

```bash
# Install
pip install capturly

# Point at OpenAI and watch traffic
capturly --mode log --backend https://api.openai.com --combine-chunks

# Open dashboard at http://localhost:9090
# See your system prompts, tool definitions, and complete conversations
```

## Use Cases

### Debug AI Applications

See exactly what your app sends to OpenAI:

```bash
capturly --mode log --backend https://api.openai.com --combine-chunks
# Configure your app to use http://localhost:9999 instead of api.openai.com
```

The dashboard extracts:
- System prompts (including multi-part array content)
- Tool definitions and tool calls
- Token usage
- Complete conversation history (streaming chunks combined)
- AGUI agent runs: messages, tool calls, reasoning, and state

### Create Deterministic Test Fixtures

Record real API responses, replay them in tests:

```bash
# Record once
capturly --mode record --backend https://api.stripe.com
curl http://localhost:9999/v1/charges

# Replay forever (no network, instant, deterministic)
capturly --mode replay
pytest tests/
```

### Performance Testing

Stress test your service with cached responses:

```bash
# Replay with zero delay (simulate fast API)
capturly --mode replay --delay 0

# Or simulate slow upstream
capturly --mode replay --delay 2000
```

### Development Speedup

Cache common endpoints, record new ones automatically:

```bash
capturly --mode hybrid --backend https://api.example.com
# First request: hits backend, caches response
# Subsequent requests: instant replay from cache
```

## Dashboard

The built-in web dashboard (default: `http://localhost:9090`) shows a newest-first traffic list with filters and tag chips (AI, AGUI, SSE, Tools, Tool Results), plus per-request detail views that surface system prompts, new inputs vs. conversation history, tool calls, token usage, and AGUI agent runs. See the [Dashboard Guide](docs/dashboard.md).

## Modes

| Mode | Behavior | Use When |
|------|----------|----------|
| `record` | Proxy to backend, save responses | Capturing test fixtures |
| `replay` | Return saved responses (no backend) | Running tests, offline dev |
| `hybrid` | Replay if cached, record if new | Day-to-day development |
| `log` | Proxy and persist full traffic | Debugging, AI inspection |

## Configuration

**CLI arguments:**
```bash
capturly --mode log --backend https://api.openai.com --port 8080
```

**Environment variables:**
```bash
export CAPTURLY_RECORDINGS_DIR=./recordings
capturly --mode record --backend https://api.example.com
```

## Installation

```bash
pip install capturly
```

**Requirements:** Python 3.9+

## Documentation

- [Getting Started](docs/getting-started.md) — 5-minute tutorial
- [Mode Reference](docs/modes.md) — Detailed behavior of each mode
- [Dashboard Guide](docs/dashboard.md) — Traffic list, filters, detail views, API
- [Configuration](docs/configuration.md) — All options, all sources

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) file.
