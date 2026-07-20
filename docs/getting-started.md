# Getting Started

This guide takes you from zero to debugging your first API in 5 minutes.

## Installation

```bash
pip install capturly
```

Verify installation:

```bash
capturly --help
```

## Your First Session: Debugging OpenAI

Let's capture and inspect traffic to OpenAI's API.

### Step 1: Start Capturly in Log Mode

```bash
capturly --mode log --backend https://api.openai.com --combine-chunks
```

You'll see:
```
🚀 Mock server running in LOG mode on http://0.0.0.0:9999
📡 Proxying to: https://api.openai.com
📝 Full request/response logs will be saved to: ./capturly-recordings
🧩 SSE chunk combining: enabled

🔍 Watching requests...
```

### Step 2: Configure Your Application

Point your application at Capturly instead of OpenAI directly.

**Before:**
```python
import openai
client = openai.OpenAI(api_key="sk-...")
# Uses https://api.openai.com by default
```

**After:**
```python
import openai
client = openai.OpenAI(
    api_key="sk-...",
    base_url="http://localhost:9999/v1"
)
```

### Step 3: Make a Request

Run your application and make a normal OpenAI call:

```python
response = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is 2+2?"}
    ]
)
```

### Step 4: Inspect the Traffic Log

Open `./capturly-recordings/traffic_log.json` to see the complete request and response, including headers, body, and timing.

## Your Second Session: Record and Replay

Now let's create a test fixture from that traffic.

### Step 1: Switch to Record Mode

```bash
capturly --mode record --backend https://api.openai.com
```

### Step 2: Make the Same Request

Run your application again. Capturly will:
- Proxy the request to OpenAI
- Save the response to disk

You'll see:
```
✓ Proxied: 200 (res: 1234 bytes)
💾 Saved recording: a1b2c3d4e5f6...
```

### Step 3: Switch to Replay Mode

```bash
capturly --mode replay
```

### Step 4: Run Your Tests

```bash
pytest tests/
```

Your tests now run against the recorded response:
- No network calls (fast!)
- Deterministic (same response every time)
- No API costs

## Your Third Session: Hybrid Mode

For day-to-day development, use hybrid mode:

```bash
capturly --mode hybrid --backend https://api.openai.com
```

Behavior:
- First request to an endpoint: proxies to backend, caches response
- Subsequent requests: returns cached response instantly
- New endpoints: automatically recorded

This gives you the speed of replay with the flexibility of recording.

## Next Steps

- [Mode Reference](modes.md) — detailed behavior of each mode
- [Configuration](configuration.md) — all options and configuration sources
