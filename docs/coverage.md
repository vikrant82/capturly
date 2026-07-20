# Test Coverage Report

**Date:** 2026-07-21 (v0.1.0)
**Overall:** 59% (62 tests, 1170 statements, 483 missed)

## Module Coverage

| Module | Stmts | Miss | Cover | Notes |
|--------|-------|------|-------|-------|
| `inspection/openai.py` | 109 | 8 | 93% | Phase 1 core — well covered |
| `dashboard.py` | 113 | 10 | 91% | API + frontend serving |
| `config.py` | 91 | 14 | 85% | Missing: yaml import fallback, edge paths |
| `logger.py` | 78 | 23 | 71% | Missing: SSE event drain, error paths |
| `proxy.py` | 35 | 11 | 69% | Missing: actual HTTP forwarding |
| `utils.py` | 61 | 20 | 67% | Missing: gzip, binary fallback |
| `handler.py` | 106 | 50 | 53% | Mostly delegation methods (thin wrappers) |
| `storage.py` | 77 | 36 | 53% | Missing: actual file I/O paths |
| `sse.py` | 248 | 124 | 50% | Chunk combining tested; live streaming untested |
| `modes/log.py` | 78 | 51 | 35% | Entry builders tested; `log_and_proxy()` untested |
| `modes/hybrid.py` | 15 | 11 | 27% | Needs integration test with real server |
| `modes/replay.py` | 32 | 25 | 22% | Needs integration test with real server |
| `modes/record.py` | 30 | 25 | 17% | Needs integration test with real server |
| `server.py` | 64 | 49 | 23% | Needs integration test |
| `cli.py` | 28 | 24 | 14% | Only `--help` tested via subprocess |

## Coverage Gaps (Priority Order)

### High Priority — Core proxy paths
- `modes/record.py`, `modes/replay.py`, `modes/hybrid.py` — need real HTTP integration tests
- `server.py` — needs startup/shutdown integration test
- `handler.py` — delegation methods need end-to-end coverage

### Medium Priority — Error handling
- `logger.py` — SSE event drain, write failure paths
- `storage.py` — file I/O error handling, atomic write verification
- `proxy.py` — actual HTTP forwarding, timeout handling

### Low Priority — Edge cases
- `sse.py` — live streaming (respond_sse_stream), client disconnect
- `utils.py` — gzip decompression, binary base64 fallback
- `config.py` — PyYAML missing fallback, home directory config

## Running Coverage

```bash
pytest tests/ --cov=src/capturly --cov-report=term-missing
```

## Targets

- Phase 2 goal: 75% overall (focus on modes/ and server.py integration tests)
- Phase 3 goal: 85% overall (error paths, edge cases)
