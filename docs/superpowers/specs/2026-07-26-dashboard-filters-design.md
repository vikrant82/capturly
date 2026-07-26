# Dashboard Traffic Filters — Design Spec

**Date:** 2026-07-26
**Status:** Approved
**Scope:** Client-side filtering for the Capturly web dashboard (`src/capturly/dashboard.py`)

## Problem

The dashboard displays up to 200 traffic entries with no way to narrow them down. When debugging specific requests (e.g. all POST failures to `/api/chat`), users must visually scan the entire table. The only existing filter is an "AI Only" toggle, which is too coarse for most debugging workflows.

## Requirements

Four filter dimensions, all applied client-side to the already-fetched entries:

| Dimension | Control | Behavior |
|-----------|---------|----------|
| **Method** | `<select>` dropdown | Exact match on HTTP method (GET, POST, PUT, DELETE, PATCH). Default: all. |
| **Path** | `<input type="text">` | Case-insensitive substring match against the request path. Filters on every keystroke. Default: empty (no filter). |
| **Status** | `<select>` dropdown | Bucket match: `2xx` (200–299), `4xx` (400–499), `5xx` (500+). Default: all. |
| **Tags** | Toggle chips (AI, AGUI, SSE, Tools) | Multi-select, OR semantics — an entry matches if it has any active tag. No active tags = show all. |

- **Client-side only.** No backend API changes. Filters apply to the in-memory entries (up to 200).
- **Dedicated filter row** below the existing controls bar, always visible.
- **Replace** the existing "All Traffic / AI Only" toggle buttons with the Tags chips.
- **Clear button** (`✕ Clear`) appears when any filter is non-default; resets all filters in one click.

## Architecture & Data Flow

### Filter state

A single global object is the source of truth:

```js
var filters = { method: null, path: '', status: null, tags: [] };
```

### Data flow

1. `refresh()` fetches `/api/traffic?limit=200` (the `?ai=true` param and `aiOnly` variable are removed). The full unfiltered response is stored in a new global `allEntries`.
2. `refresh()` calls `applyFilters()`.
3. `applyFilters()` runs `allEntries` through each active filter predicate, assigns the surviving subset to `entries`, and calls `renderTable()`.
4. Every filter control's event handler updates `filters` and calls `applyFilters()` — instant, no network round-trip.
5. The 3s `setInterval` calls `refresh()`, which re-fetches and re-applies filters automatically.

`entries` (the filtered view) is what `renderTable` and `showDetail` operate on, so the existing detail-panel code works unchanged. The `_index` field on each entry still points to the server-side index for the detail fetch.

### Removed code

- `aiOnly` global variable
- `setFilter()` function
- "All Traffic" / "AI Only" buttons from the controls bar
- `?ai=true` query param construction in `refresh()`

## UI Layout

A new `<div class="filter-row">` sits between the controls bar and the table:

```
[ Method ▾ ]  [ Path contains...        ]  [ Status ▾ ]  [ AI ] [ AGUI ] [ SSE ] [ Tools ]  [ ✕ Clear ]
```

### Method dropdown

- Options: `All Methods` (value `""`), `GET`, `POST`, `PUT`, `DELETE`, `PATCH`
- Styled to match the dark theme (`#21262d` background, `#30363d` border, `#c9d1d9` text)

### Path input

- `<input type="text">` with placeholder `"Filter by path..."`
- ~220px wide, same dark styling
- Filters on every `input` event, case-insensitive substring match

### Status dropdown

- Options: `All Status` (value `""`), `2xx`, `4xx`, `5xx`
- Same styling as Method dropdown

### Tag chips

- Four clickable pills: `AI`, `AGUI`, `SSE`, `Tools`
- Active chips: filled background matching their existing badge color (e.g. AI = `#1f6feb33` bg + `#58a6ff` text + border)
- Inactive chips: outlined only (`transparent` bg, dimmed border/text)
- Multi-select toggle; OR semantics for matching

### Clear button

- Small text button `✕ Clear`, hidden by default (`display: none`)
- Shown when any filter deviates from defaults
- Resets `filters` to `{ method: null, path: '', status: null, tags: [] }`, resets all controls to their default visual state, and calls `applyFilters()`

### Existing controls bar

- Remove: `All Traffic` button, `AI Only` button
- Keep: `Refresh` button, `Clear Log` button (unchanged)

## Interaction Details

### Empty state

When filters produce zero matches, the table shows: `"No traffic matches the current filters"` — distinct from the default `"No traffic recorded yet"` shown when `allEntries` is empty.

### Selected row + filters

The `selectedIdx` highlight persists across filter changes. If the selected row is filtered out, the highlight is simply not visible until filters include it again. No special handling needed — `renderTable` already checks `e._index === selectedIdx` per row.

### Overlay open + filter change

If the detail panel is open and filters change, the overlay stays open. The table behind it re-renders with the new filter, but the detail panel content is unaffected (fetched independently). No extra code required.

### Auto-refresh + filters

The 3s refresh re-fetches all entries and re-applies filters. New matching traffic appears automatically. `selectedIdx` is keyed on the stable server-side `_index`, so the highlight persists across refreshes.

### Filter reset on truncate

"Clear Log" truncates server data. After `closeDetail(); refresh();`, `allEntries` is empty and the table shows the default empty state. Filters remain set (harmless on empty data); the Clear button stays visible for manual reset.

### No debounce

With <=200 entries and simple substring matching, filtering is sub-millisecond. Every keystroke re-renders instantly.

## Testing

- Existing dashboard tests (`test_dashboard.py`, `test_dashboard_integration.py`) must continue to pass — they test the API and HTML serving, which are unaffected.
- The HTML-serving test should be updated to assert the new filter-row markup is present (method select, path input, status select, tag chips).
- Manual verification: apply each filter individually and in combination; verify clear button resets all; verify filters survive auto-refresh; verify selected-row highlight persists.

## Out of Scope

- Server-side filtering / new API query params
- URL-synced filter state (shareable links)
- Free-text search across request/response bodies
- Regex path matching
- Filter persistence across page reloads (localStorage)
