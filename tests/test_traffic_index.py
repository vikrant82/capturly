"""Tests for the JSONL traffic log offset index."""

import json
import os

from capturly.traffic_index import TrafficIndex


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _entry(**overrides):
    base = {
        "timestamp_ms": 1000,
        "method": "POST",
        "path": "/v1/chat/completions",
        "status_code": 200,
        "request_body_size": 10,
        "response_body_size": 20,
        "source_name": "pipe-a",
        "backend_url": "http://localhost:8000",
        "duration_ms": 5,
    }
    base.update(overrides)
    return base


def test_offset_round_trip(tmp_path):
    """Every _index loads back the exact original entry dict."""
    log = str(tmp_path / "traffic_log.jsonl")
    entries = [_entry(path=f"/{i}") for i in range(5)]
    _write_jsonl(log, entries)
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 5
    for i, original in enumerate(entries):
        assert idx.load_entry(i) == original


def test_summaries_are_slim(tmp_path):
    """Summaries carry small ai fields and never large content."""
    log = str(tmp_path / "traffic_log.jsonl")
    entry = _entry(
        ai_insights={
            "request": {
                "model": "gpt-4o",
                "message_count": 42,
                "system_prompts": ["S" * 100000],
                "tool_names": ["search", "edit"],
                "tool_count": 2,
            },
            "response": {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "tool_call_names": ["search"],
            },
        },
        sse=True,
    )
    _write_jsonl(log, [entry])
    idx = TrafficIndex(log)
    idx.sync()
    summary = idx.summaries()[0]
    assert summary["ai"] == {
        "model": "gpt-4o",
        "message_count": 42,
        "tool_names": ["search", "edit"],
        "tool_count": 2,
        "total_tokens": 15,
    }
    assert summary["sse"] is True
    assert summary["tools"] is True
    assert summary["_index"] == 0
    assert summary["method"] == "POST"
    assert summary["source_name"] == "pipe-a"
    # No large fields leak into summaries
    text = json.dumps(idx.summaries())
    assert "S" * 100 not in text
    assert "system_prompts" not in text


def test_agui_flag_in_summary(tmp_path):
    """Entries whose response_body is an AGUI completion get the agui flag."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(response_body={"object": "agui.completion", "run": {}})])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.summaries()[0]["agui"] is True


def test_incremental_append(tmp_path):
    """Appending new lines indexes only the new entries."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a")])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 1
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(_entry(path="/b")) + "\n")
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(0)["path"] == "/a"
    assert idx.load_entry(1)["path"] == "/b"


def test_partial_line_deferred(tmp_path):
    """A line without a trailing newline is not indexed until complete."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a")])
    with open(log, "a", encoding="utf-8") as f:
        f.write('{"timestamp_ms": 2000, "path": "/par')
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 1
    with open(log, "a", encoding="utf-8") as f:
        f.write('tial"}\n')
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(1) == {"timestamp_ms": 2000, "path": "/partial"}


def test_malformed_line_skipped(tmp_path):
    """Malformed lines are skipped without disturbing entry numbering."""
    log = str(tmp_path / "traffic_log.jsonl")
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(path="/a")) + "\n")
        f.write("not json at all\n")
        f.write(json.dumps(_entry(path="/b")) + "\n")
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(0)["path"] == "/a"
    assert idx.load_entry(1)["path"] == "/b"


def test_truncate_resets(tmp_path):
    """Shrinking the file resets the index; new lines are re-indexed."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a"), _entry(path="/b")])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 2
    with open(log, "w", encoding="utf-8") as f:
        f.truncate(0)
    idx.sync()
    assert idx.count() == 0
    assert idx.stats()["total_requests"] == 0
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(_entry(path="/c")) + "\n")
    idx.sync()
    assert idx.count() == 1
    assert idx.load_entry(0)["path"] == "/c"


def test_replaced_file_resets(tmp_path):
    """Replacing the file (new inode) resets the index."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry(path="/a")])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.count() == 1
    os.remove(log)
    _write_jsonl(log, [_entry(path="/x"), _entry(path="/y")])
    idx.sync()
    assert idx.count() == 2
    assert idx.load_entry(0)["path"] == "/x"


def test_missing_file(tmp_path):
    """A missing log file yields an empty index, not an error."""
    idx = TrafficIndex(str(tmp_path / "nope.jsonl"))
    idx.sync()
    assert idx.count() == 0
    assert idx.summaries() == []
    assert idx.stats() == {
        "total_requests": 0,
        "ai_requests": 0,
        "total_tokens": 0,
        "models": [],
    }


def test_load_entry_out_of_range(tmp_path):
    """Out-of-range indices return None."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(log, [_entry()])
    idx = TrafficIndex(log)
    idx.sync()
    assert idx.load_entry(1) is None
    assert idx.load_entry(-1) is None


def test_stats(tmp_path):
    """Stats counters aggregate model, AI request, and token totals."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(
        log,
        [
            _entry(
                ai_insights={
                    "request": {"model": "gpt-4o", "message_count": 2},
                    "response": {"usage": {"total_tokens": 15}},
                }
            ),
            _entry(),
            _entry(
                ai_insights={
                    "request": {"model": "claude", "message_count": 3},
                    "response": {"usage": {"total_tokens": 30}},
                }
            ),
        ],
    )
    idx = TrafficIndex(log)
    idx.sync()
    stats = idx.stats()
    assert stats["total_requests"] == 3
    assert stats["ai_requests"] == 2
    assert stats["total_tokens"] == 45
    assert stats["models"] == ["claude", "gpt-4o"]


def test_stats_incremental_across_syncs(tmp_path):
    """Stats stay correct when entries arrive in later syncs."""
    log = str(tmp_path / "traffic_log.jsonl")
    _write_jsonl(
        log,
        [
            _entry(
                ai_insights={
                    "request": {"model": "gpt-4o", "message_count": 2},
                    "response": {"usage": {"total_tokens": 15}},
                }
            )
        ],
    )
    idx = TrafficIndex(log)
    idx.sync()
    with open(log, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                _entry(
                    ai_insights={
                        "request": {"model": "claude", "message_count": 3},
                        "response": {"usage": {"total_tokens": 30}},
                    }
                )
            )
            + "\n"
        )
    idx.sync()
    stats = idx.stats()
    assert stats["total_requests"] == 2
    assert stats["ai_requests"] == 2
    assert stats["total_tokens"] == 45
    assert stats["models"] == ["claude", "gpt-4o"]
