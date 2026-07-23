# Configuration

Capturly can be configured via command-line arguments or environment variables.

## Command-Line Arguments

```bash
capturly [OPTIONS]

Core options:
  --mode [record|replay|hybrid|log]    Operating mode (default: replay)
  --backend URL                         Backend URL for proxy modes
  --port INT                            Listen port (default: 9999)
  --host HOST                           Listen host (default: 0.0.0.0)
  --recordings-dir PATH                 Directory for recordings

Mode-specific:
  --delay INT                           Replay delay in milliseconds (replay/hybrid)
  --combine-chunks                      Combine SSE chunks — auto-detects OpenAI or AGUI protocol (log mode)
```

**Validation:**
- `--backend` required for record, hybrid, and log modes
- `--delay` must be non-negative

## Environment Variables

```bash
CAPTURLY_RECORDINGS_DIR=./recordings
```

The recordings directory can be set via environment variable. CLI `--recordings-dir` takes precedence.

## Default Values

| Setting | Default |
|---------|---------|
| mode | `replay` |
| port | `9999` |
| host | `0.0.0.0` |
| recordings_dir | `./capturly-recordings` |
| delay | `0` |
| combine_chunks | `false` |

## Dashboard

The web dashboard is available at `http://localhost:9090` (configurable via `--dashboard-port`).

**API endpoints:**
- `GET /api/traffic?limit=N&offset=N&ai=true` — paginated traffic list
- `GET /api/traffic/{index}` — full entry detail
- `GET /api/stats` — summary statistics
- `POST /api/truncate` — clear all traffic entries

## Dashboard

The web dashboard is available at `http://localhost:9090` (configurable via `--dashboard-port`).

**API endpoints:**
- `GET /api/traffic?limit=N&offset=N&ai=true` — paginated traffic list
- `GET /api/traffic/{index}` — full entry detail
- `GET /api/stats` — summary statistics
- `POST /api/truncate` — clear all traffic entries

## Recordings Directory

**Default:** `./capturly-recordings` in current working directory

**Structure:**
```
capturly-recordings/
├── <cache-key>.json       # Response cache files
├── <cache-key>.sse        # SSE stream markers
├── traffic_log.json       # Full request/response log (log mode)
└── sse-events/            # Individual SSE event streams
    └── <timestamp>-<cache-key>.jsonl
```

**Configuration:**
- CLI: `--recordings-dir /path/to/recordings`
- Env: `CAPTURLY_RECORDINGS_DIR=/path/to/recordings`

**Created automatically** if it doesn't exist.

## Configuration Examples

**Example 1: Record Stripe API**
```bash
capturly --mode record --backend https://api.stripe.com --port 8080
```

**Example 2: Replay with custom directory**
```bash
capturly --mode replay --recordings-dir ./fixtures --delay 100
```

**Example 3: Log OpenAI traffic**
```bash
capturly --mode log --backend https://api.openai.com --combine-chunks
```

**Example 4: Custom recordings location via env**
```bash
export CAPTURLY_RECORDINGS_DIR=/tmp/api-recordings
capturly --mode record --backend https://api.example.com
```
