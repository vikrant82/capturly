# Dashboard: Per-Call Duration & Multi-Pipe Source Attribution

**Date**: 2026-07-31
**Status**: Approved (design)
**Scope**: Capturly traffic dashboard + traffic-log entry schema

## Problem

Two gaps in the traffic dashboard:

1. **No per-call duration.** The table shows a time-of-day column but not how long each
   request took. Duration is valuable for debugging and performance work and is currently
   captured only for combined-SSE streams (`sse_duration_ms`), not regular requests.

2. **No source attribution across pipes.** A common deployment runs multiple capturly
   "pipes" (instances), each proxying a different backend, all recording into one shared
   recordings directory. One pipe runs with `--dashboard` and reads the shared
   `traffic_log.jsonl`. Because entries carry no source identifier, it is impossible to
   tell which backend/pipe a given call came from.

## Goals

- Show the duration of each call in the dashboard.
- Let the dashboard attribute each row to the pipe/backend that produced it, and filter by it.
- Keep the existing single-file, single-dashboard architecture (no standalone launcher, no
  multi-file merging, no network fan-out).
- Remain backward compatible with existing traffic logs.

## Non-Goals

- A standalone dashboard process or subcommand.
- Auto-discovery / scanning of recordings directories.
- Cross-host aggregation.
- Per-source statistics breakdown (possible future add).
- Duration for non-combined SSE streams (see Limitations).

## Architecture

Unchanged in structure. All pipes share one recordings directory
(`CAPTURLY_RECORDINGS_DIR`) and append to the same `traffic_log.jsonl`. Concurrent appends
are already safe: `storage.append_traffic_log_entry` opens the file in append mode and
writes one JSON line per call, and each process serializes its own writes through the async
logger thread.

One pipe runs with `--dashboard`. `server._start_dashboard` passes `entries=None` and the
shared `traffic_log_path`, so the dashboard re-reads the file on every request and naturally
shows all pipes' traffic. Aggregation happens for free via the shared file; this change only
adds attribution + duration fields and surfaces them in the UI.

## Data Model

Every traffic-log entry gains three fields:

| Field | Type | Source | Meaning |
|---|---|---|---|
| `source_name` | `str \| null` | `handler.pipe_name` (from `--pipe`) | Friendly pipe name; null if unnamed |
| `backend_url` | `str \| null` | `handler.backend_url` (from `--backend`) | Backend this pipe proxies |
| `duration_ms` | `int \| null` | computed | Wall-clock time the call took |

Display label resolution in the dashboard: `source_name || backend_url || "default"`.

`timestamp_ms` keeps its current meaning (completion time). `duration_ms` is additive.

## Changes

### CLI & config

- New optional `--pipe NAME` flag in `cli.py` (default `None`).
- `config.py`: add `pipe` to the config→args mapping so it can be set in `capturly.yaml`
  and merged like `backend`. CLI args take priority over config.

### Handler

- Add class attribute `MockServiceHandler.pipe_name = None`.
- `server.run_server`: set `MockServiceHandler.pipe_name = getattr(args, "pipe", None)`
  alongside the existing `backend_url` assignment.

### Entry builders (`modes/log.py`)

All three builders already receive `handler`, so each stamps `source_name` and
`backend_url` from it. A small shared helper (e.g. `_source_meta(handler)` returning
`{"source_name": ..., "backend_url": ...}`) keeps the stamping consistent across builders.

- **`build_log_entry` (regular, non-SSE requests)**: accept a `started_timestamp_ms`
  parameter; set `timestamp_ms` to completion time and add
  `duration_ms = max(0, completed - started)`. In `log_and_proxy`, capture
  `started_timestamp_ms = int(time.time() * 1000)` at the top (before
  `proxy.forward_request`) and pass it to both `build_log_entry` call sites — the success
  path and the `urllib.error.HTTPError` path — so errored calls also record a duration.
- **`build_combined_sse_log_entry`**: already computes `sse_duration_ms`; additionally set
  top-level `duration_ms` to the same value for a uniform column.
- **`build_sse_log_entry` (non-combined SSE)**: stamp `source_name`/`backend_url` but **no
  `duration_ms`** — this entry is written when the stream starts, before completion is known.

### Dashboard (`dashboard.py`)

**Summary API** — `_summary_entry` adds `source_name`, `backend_url`, and `duration_ms` so
the table has them without a detail fetch.

**Table** — two new columns:

- `Source`: the resolved label, rendered as a colored badge.
- `Duration`: formatted via a new `fmtDuration(ms)` helper (`<1ms`, `240ms`, `1.2s`); `-`
  when absent.

The header grows from 8 to 10 columns; the empty-state row `colspan` is updated to match.

**Source filter** — a `<select>` beside the existing method/status filters, populated from
the distinct sources present in the loaded entries. Selecting a source narrows the table; it
integrates with `matchesFilters` and `clearFilters`.

**Detail panel** — the Overview section gains Source and Duration lines.

**Ordering** — sort entries by `timestamp_ms` descending (newest first) before rendering.
With multiple processes appending concurrently, raw file order is not a reliable timeline;
sorting yields a correct live view. This changes today's oldest-first order.

**Stats** — unchanged.

## Backward Compatibility & Edge Cases

- Existing entries lacking the new fields render as source `default` and duration `-`. No
  migration required.
- Concurrent appends to the shared log are already safe; no change.
- "Clear Log" truncates the shared file for all pipes — expected, unchanged.
- Non-combined SSE rows show `-` for duration (limitation below).

## Limitations

- **Non-combined SSE duration unavailable.** `build_sse_log_entry` is written at stream
  start; completion time lives in the separate SSE event log. Adding a duration would
  require a follow-up write or a schema change to that path. Out of scope; the column shows
  `-`.

## Testing

- **Unit (`modes/log.py`)**: `build_log_entry` includes `source_name`, `backend_url`, and a
  correctly computed `duration_ms`; the HTTPError path also records a duration; combined-SSE
  sets `duration_ms == sse_duration_ms`.
- **CLI/config**: `--pipe` parses; `pipe` merges from `capturly.yaml` with CLI priority.
- **Dashboard**: `_summary_entry` carries the new fields; the source filter narrows
  correctly; `fmtDuration` formats sub-ms / ms / seconds; missing fields resolve to
  `default` / `-`; entries are sorted newest-first.
- **Integration**: two handlers with distinct `pipe_name`/`backend_url` write to one shared
  recordings dir; the dashboard lists both and labels each row correctly.

## Decisions Log

- Architecture kept: shared recordings dir + one `--dashboard` pipe (no standalone
  launcher). — user direction
- Source label: friendly name with backend-URL fallback. — user choice
- Flag named `--pipe`. — approved
- Non-combined SSE shows no duration. — approved
- Table sorted newest-first. — approved
