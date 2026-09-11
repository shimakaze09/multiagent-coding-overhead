"""Milestone 2 tests: the append-only event log."""

from __future__ import annotations

import json
import threading

from harness import events as ev


def test_1000_append_and_reload(tmp_path):
    path = tmp_path / "events.jsonl"
    with ev.EventLog(path, "run-1", default_task_id="t", default_arm="A") as log:
        for i in range(1000):
            log.append("TOOL_CALL_START", payload={"i": i}, agent_id="solo")

    rep = ev.read_events(path)
    assert len(rep.events) == 1000
    assert rep.partial_lines == 0
    assert rep.malformed_lines == 0
    ev.assert_monotonic(rep.events)
    assert rep.events[0]["seq"] == 1
    assert rep.events[-1]["seq"] == 1000
    assert rep.events[-1]["payload"]["i"] == 999
    assert {e["run_id"] for e in rep.events} == {"run-1"}
    assert {e["task_id"] for e in rep.events} == {"t"}


def test_every_line_is_valid_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    with ev.EventLog(path, "r") as log:
        log.append("TASK_START", payload={"unicode": "café → ok", "nested": {"a": [1, 2]}})
        log.append("TASK_END")

    for line in path.read_text(encoding="utf-8").splitlines():
        assert line
        obj = json.loads(line)
        assert isinstance(obj, dict)
        assert "seq" in obj and "event_id" in obj and "ts" in obj


def test_concurrent_allocation_is_safe_and_unique(tmp_path):
    path = tmp_path / "events.jsonl"
    threads = 8
    per_thread = 125
    log = ev.EventLog(path, "r")

    def worker(n: int) -> None:
        for i in range(per_thread):
            log.append("TOOL_CALL_END", payload={"t": n, "i": i}, agent_id=f"a{n}")

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    log.close()

    rep = ev.read_events(path)
    total = threads * per_thread
    assert len(rep.events) == total
    seqs = [e["seq"] for e in rep.events]
    assert sorted(seqs) == list(range(1, total + 1)), "seq must be unique and gapless"
    assert seqs == sorted(seqs), "appended lines must be in seq order"
    assert len({e["event_id"] for e in rep.events}) == total


def test_append_only_never_rewrites_history(tmp_path):
    path = tmp_path / "events.jsonl"
    with ev.EventLog(path, "r") as log:
        log.append("TASK_START")
    first = path.read_text(encoding="utf-8")

    with ev.EventLog(path, "r") as log:  # reopened: must resume, not truncate
        log.append("TASK_END")
    after = path.read_text(encoding="utf-8")

    assert after.startswith(first), "existing lines must be preserved verbatim"
    rep = ev.read_events(path)
    assert [e["seq"] for e in rep.events] == [1, 2]


def test_crash_tolerant_partial_history(tmp_path):
    """A process killed mid-write leaves one truncated line; the rest must load."""
    path = tmp_path / "events.jsonl"
    with ev.EventLog(path, "r") as log:
        for i in range(5):
            log.append("TOOL_CALL_START", payload={"i": i})

    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"event_id": "half", "seq": 6, "ts": "2026-')  # torn write

    rep = ev.read_events(path)
    assert len(rep.events) == 5
    assert rep.partial_lines == 1
    assert rep.malformed_lines == 0
    ev.assert_monotonic(rep.events)

    # And a new log must continue after the last GOOD seq rather than colliding.
    with ev.EventLog(path, "r") as log:
        e = log.append("TASK_END")
    assert e.seq == 6


def test_order_confidence_is_recorded(tmp_path):
    path = tmp_path / "events.jsonl"
    with ev.EventLog(path, "r") as log:
        a = log.append("TASK_START")
        b = log.append(
            "RAW_CLAUDE_EVENT", source="claude_code", order_confidence=ev.ORDER_CLI_REPORTED
        )
    assert a.order_confidence == ev.ORDER_HARNESS_OBSERVED
    assert b.order_confidence == ev.ORDER_CLI_REPORTED


def test_raw_ref_links_back_to_raw_stream(tmp_path):
    path = tmp_path / "events.jsonl"
    with ev.EventLog(path, "r") as log:
        e = log.append(
            "FILE_READ_END",
            source="claude_code",
            raw_ref={"session_key": "02_investigator", "line_no": 17},
        )
    assert e.raw_ref == {"session_key": "02_investigator", "line_no": 17}
