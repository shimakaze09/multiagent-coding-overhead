"""Append-only normalized event log with monotonic, concurrency-safe sequencing.

Two independent raw sources exist per run and neither may overwrite the other:
  1. raw Claude Code CLI output  (sessions/<key>/claude_stdout.jsonl)
  2. this normalized event log   (events.jsonl)

`seq` is authoritative for ordering only where the harness knows the order. It
does not establish causal order for events already concurrent inside Claude Code;
such events carry order_confidence="cli_reported".
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# Event type vocabulary
# --------------------------------------------------------------------------

TASK_START = "TASK_START"
TASK_END = "TASK_END"
CLAUDE_SESSION_START = "CLAUDE_SESSION_START"
CLAUDE_SESSION_END = "CLAUDE_SESSION_END"
AGENT_TURN_START = "AGENT_TURN_START"
AGENT_TURN_END = "AGENT_TURN_END"
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_END = "TOOL_CALL_END"
FILE_READ_START = "FILE_READ_START"
FILE_READ_END = "FILE_READ_END"
SEARCH_START = "SEARCH_START"
SEARCH_END = "SEARCH_END"
EDIT = "EDIT"
TEST_RUN = "TEST_RUN"
AGENT_MESSAGE = "AGENT_MESSAGE"
HANDOFF_SENT = "HANDOFF_SENT"
ERROR = "ERROR"
USAGE_REPORT = "USAGE_REPORT"
RAW_CLAUDE_EVENT = "RAW_CLAUDE_EVENT"
UNKNOWN_CLAUDE_EVENT = "UNKNOWN_CLAUDE_EVENT"
TURN_LIMIT_EXCEEDED = "TURN_LIMIT_EXCEEDED"
WORKSPACE_PREPARED = "WORKSPACE_PREPARED"
VERIFICATION = "VERIFICATION"
USAGE_LIMIT_REPORTED = "USAGE_LIMIT_REPORTED"
# Observed for real on Claude Code 2.1.260:
PERMISSION_DENIED = "PERMISSION_DENIED"      # the CLI refused a tool call
RATE_LIMIT_REPORTED = "RATE_LIMIT_REPORTED"  # subscription-quota telemetry
VISIBLE_TESTS = "VISIBLE_TESTS"  # harness-run visible tests; never decides SOLVED
# Stage 1 (C1): one physical Worker session changing logical role on resume.
# Additive type: every existing event keeps its shape, so SCHEMA_VERSION stays 1.
WORKER_ROLE_TRANSITION = "WORKER_ROLE_TRANSITION"

ORDER_HARNESS_OBSERVED = "harness_observed"
ORDER_CLI_REPORTED = "cli_reported"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class Event:
    event_id: str
    seq: int
    ts: str
    run_id: str
    task_id: Optional[str]
    arm: Optional[str]
    agent_id: Optional[str]
    turn_id: Optional[int]
    type: str
    parent_event_id: Optional[str]
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    raw_ref: Optional[dict[str, Any]] = None
    order_confidence: str = ORDER_HARNESS_OBSERVED
    schema_version: int = SCHEMA_VERSION

    def to_json_line(self) -> str:
        # sort_keys keeps the file diffable and byte-stable across rebuilds.
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


class EventLog:
    """Append-only JSONL writer.

    Crash tolerance: each event is one line, written and flushed (and fsynced by
    default) as a unit. A process killed mid-write leaves at most one trailing
    partial line, which `read_events` skips while reporting it.
    """

    def __init__(
        self,
        path: str | os.PathLike,
        run_id: str,
        *,
        default_task_id: Optional[str] = None,
        default_arm: Optional[str] = None,
        fsync: bool = True,
        start_seq: Optional[int] = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.default_task_id = default_task_id
        self.default_arm = default_arm
        self._fsync = fsync
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if start_seq is not None:
            self._seq = int(start_seq)
        else:
            # Resume-safe: continue after the highest seq already on disk.
            self._seq = _max_seq_on_disk(self.path)

        self._fh = open(self.path, "a", encoding="utf-8", newline="\n")

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._fh and not self._fh.closed:
                self._fh.flush()
                self._fh.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def append(
        self,
        type: str,
        *,
        payload: Optional[dict] = None,
        agent_id: Optional[str] = None,
        turn_id: Optional[int] = None,
        task_id: Optional[str] = None,
        arm: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        source: str = "harness",
        raw_ref: Optional[dict] = None,
        order_confidence: str = ORDER_HARNESS_OBSERVED,
        ts: Optional[str] = None,
    ) -> Event:
        """Allocate the next seq and append one event. Thread-safe."""
        with self._lock:
            self._seq += 1
            ev = Event(
                event_id=str(uuid.uuid4()),
                seq=self._seq,
                ts=ts or utc_now_iso(),
                run_id=self.run_id,
                task_id=task_id if task_id is not None else self.default_task_id,
                arm=arm if arm is not None else self.default_arm,
                agent_id=agent_id,
                turn_id=turn_id,
                type=type,
                parent_event_id=parent_event_id,
                source=source,
                payload=payload or {},
                raw_ref=raw_ref,
                order_confidence=order_confidence,
            )
            self._fh.write(ev.to_json_line() + "\n")
            self._fh.flush()
            if self._fsync:
                try:
                    os.fsync(self._fh.fileno())
                except OSError:
                    pass
            return ev

    @property
    def current_seq(self) -> int:
        with self._lock:
            return self._seq


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@dataclass
class ReadReport:
    events: list[dict]
    partial_lines: int
    malformed_lines: int


def _max_seq_on_disk(path: Path) -> int:
    if not path.is_file():
        return 0
    best = 0
    for ev in iter_events(path):
        s = ev.get("seq")
        if isinstance(s, int) and s > best:
            best = s
    return best


def iter_events(path: str | os.PathLike) -> Iterator[dict]:
    """Yield well-formed events, skipping a trailing partial/corrupt line."""
    p = Path(path)
    if not p.is_file():
        return
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_events(path: str | os.PathLike) -> ReadReport:
    """Read the log and report how many lines were unusable."""
    p = Path(path)
    events: list[dict] = []
    partial = 0
    malformed = 0
    if not p.is_file():
        return ReadReport(events, partial, malformed)
    raw = p.read_text(encoding="utf-8")
    lines = raw.split("\n")
    trailing_incomplete = bool(raw) and not raw.endswith("\n")
    for idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            is_last_content_line = trailing_incomplete and idx == len(lines) - 1
            if is_last_content_line:
                partial += 1
            else:
                malformed += 1
    return ReadReport(events, partial, malformed)


def assert_monotonic(events: list[dict]) -> None:
    """Raise if seq is not strictly increasing by one from 1."""
    expected = 1
    for ev in events:
        if ev.get("seq") != expected:
            raise AssertionError(
                f"seq not strictly monotonic: expected {expected}, got {ev.get('seq')}"
            )
        expected += 1
