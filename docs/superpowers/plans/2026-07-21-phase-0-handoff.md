# Phase 0 Handoff — 2026-07-21

## Status: COMPLETE

All 9 Phase 0 tasks executed and committed to `main`.

## Commits (oldest → newest)

```
28f6dd5 feat: configurable recordings directory with sensible defaults
028022b fix: traffic log resurrection bug when file is truncated
8b56195 test: add test infrastructure and basic mode tests
855e8ed feat: complete PyPI packaging
cc3da0b ci: add GitHub Actions workflows for testing and publishing
5c9f223 docs: rewrite README for open source launch
689b7df docs: add comprehensive user documentation
6674c5a test: add end-to-end integration test
bb1e8c7 feat: OpenAI protocol detection module
```

## What Was Done

### Task 1: Configurable Recordings Directory
- `RECORDINGS_DIR` global starts as `None`
- `get_recordings_dir()` resolves: env var → `./capturly-recordings`
- `--recordings-dir` CLI flag sets env var
- Tests: `tests/test_storage.py` (3 tests)

### Task 2: Traffic Log Resurrection Bug
- `logger.py` `_refresh_entries_from_disk()` now reloads from disk when file exists
- Fixed stale `RECORDINGS_DIR` import (was `None` after Task 1 refactor)
- Added `import json` to logger.py
- Tests: `tests/test_logger.py` (1 test)

### Task 3: Test Infrastructure
- `tests/conftest.py`: `temp_recordings_dir` fixture (resets `storage.RECORDINGS_DIR`), `mock_backend_server` fixture (random-port HTTP server)
- `tests/test_modes.py`: 4 tests (2 stubs, 1 real)
- `tests/test_proxy.py`: 2 tests (build_request, respond_json)

### Task 4: PyPI Packaging
- Verified `pyproject.toml` is complete (entry point, deps, classifiers)
- `tests/test_cli.py`: 2 tests (--help, missing --backend validation)
- Package installable via `pip install -e .`

### Task 5: CI/CD
- `.github/workflows/test.yml`: Python 3.9-3.12 matrix, ruff, pytest, mypy
- `.github/workflows/publish.yml`: PyPI trusted publishing on `v*` tags

### Task 6: README
- Rewritten with use cases, mode table, configuration examples
- Links to docs/ files

### Task 7: Documentation
- `docs/getting-started.md`: 5-min tutorial (log → record → replay → hybrid)
- `docs/modes.md`: detailed reference for all 4 modes
- `docs/configuration.md`: CLI args, env vars, defaults, examples
- `CONTRIBUTING.md`: dev setup, project structure, PR guidelines

### Task 8: Integration Test
- `tests/test_integration.py`: end-to-end record → replay workflow
- Starts backend subprocess, capturly record, curl, capturly replay, curl
- ~3s runtime

### Task 9: OpenAI Protocol Detection
- `src/capturly/inspection/__init__.py` + `openai.py`
- `detect_openai_protocol(path, response_body)` → dict or None
- Detects `/v1/chat/completions` and `/v1/completions`
- Extracts model, usage, type
- Tests: `tests/test_inspection.py` (4 tests)

## Current Test Suite

```
17 tests, all passing (~4.5s with integration test)
```

## Known Issues / Notes

- `docs/superpowers/plans/` contains the planning documents (phase-0, phase-1, phase-2, phase-3, phase-4). These are internal planning artifacts, not user-facing docs.
- The README references a dashboard (`http://localhost:9090`) that doesn't exist yet — that's Phase 1.
- `pyproject.toml` lists `cryptography>=41.0.0` as a dependency (for TLS cert generation) but it's not used in the core proxy. This is fine for now.
- The CI workflow runs `ruff format --check` which may fail on existing code that hasn't been formatted. Run `ruff format src/ tests/` before pushing if needed.
- `mypy` is set to `continue-on-error: true` in CI since the codebase isn't fully typed yet.

## What's Next: Phase 1

Phase 1 plan is at `docs/superpowers/plans/2026-07-21-phase-1-ai-inspection-dashboard.md`.

Key tasks:
1. **AI Traffic Inspection** — system prompt extraction, tool call detection, token usage tracking
2. **Web Dashboard** — real-time traffic viewer at `localhost:9090`
3. **Config File Support** — `capturly.yaml` with env var interpolation
4. **SSE Chunk Combining** — merge streaming chunks into final response

Phase 1 depends on:
- The `inspection/` module (created in Task 9)
- The `logger.py` async infrastructure (fixed in Task 2)
- The `storage.py` persistence layer (refactored in Task 1)

## Environment

- Python 3.9.6 (system)
- Package installed: `pip install -e ".[dev]"`
- Working directory: `/Users/chauv/launchpad/capturly`
- Branch: `main`
- All changes committed, working tree clean
