# Capturly

**Record, replay, and debug any HTTP API — with built-in AI traffic intelligence.**

Capturly is a universal HTTP traffic debugger that captures real API responses, replays them instantly or with simulated delays, and provides deep inspection of request/response cycles — including intelligent parsing of AI API traffic (OpenAI, Anthropic, etc.).

## Features

- **Four operating modes**: Record, Replay, Hybrid (cache-through), and Log
- **Universal**: Works with any HTTP API — not just AI providers
- **SSE support**: Full Server-Sent Events streaming with chunk combining
- **Smart AI inspection**: Auto-detects and parses OpenAI-compatible API traffic, extracting system prompts, tool definitions, and token usage
- **Deterministic testing**: Record real responses and replay them in tests — no more flaky third-party dependencies
- **Delay simulation**: Add configurable delays to replayed responses for performance testing
- **Async logging**: High-throughput traffic capture without blocking requests

## Quick Start

```bash
# Install
pip install capturly

# Record real API responses
capturly record --backend https://api.example.com --port 9999

# Replay saved responses
capturly replay --port 9999

# Replay with 500ms delay (simulate slow API)
capturly replay --port 9999 --delay 500

# Hybrid mode: replay if cached, otherwise proxy to backend
capturly hybrid --backend https://api.example.com --port 9999

# Log mode: proxy and capture full request/response traffic
capturly log --backend https://api.openai.com/v1 --port 9999
```

Then point your application to `http://localhost:9999` and Capturly handles the rest.

## Use Cases

### API Testing & Development
- Record slow third-party APIs and replay them instantly during development
- Test error scenarios by replaying recorded failures
- Create deterministic test fixtures from real traffic

### Debugging & Troubleshooting
- Capture production traffic to debug issues offline
- Inspect complete request/response cycles including streaming
- Share reproducible bug scenarios with teammates

### AI Development
- Log complete LLM conversations including streaming tokens
- Debug tool call sequences and function responses
- Analyze token usage and response patterns
- Compare model responses across providers

### Performance Testing
- Simulate slow APIs with configurable delays
- Generate realistic load test scenarios
- Cache responses to eliminate backend dependencies

## License

MIT
