"""Harness-side turn limit (amendment 7, 2026-09-11).

A turn is one API assistant message (one distinct message.id). Wrapper v2 counted
raw assistant stream lines, one per content block, and killed Task 4 Arm A of the
Stage-0.5 pilot (20260911T014415Z_settings_list_fields_A_r1) after 11 model turns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from harness import claude_cli

ROOT = Path(__file__).resolve().parent.parent
T4A = ROOT / "runs" / "20260911T014415Z_settings_list_fields_A_r1" / "sessions" / "01_solo" / "claude_stdout.jsonl"


def _assistant(mid, block_type="text"):
    block = {"type": block_type, "text": "x"} if block_type == "text" else {
        "type": "tool_use", "id": f"t{mid}", "name": "Read", "input": {}}
    return json.dumps({"type": "assistant", "message": {"id": mid, "content": [block]}})


def test_several_content_blocks_of_one_message_are_one_turn():
    c = claude_cli.TurnCounter(limit=25)
    exceeded = False
    for i in range(10):                       # 10 messages x 4 blocks = 40 stream lines
        for kind in ("text", "tool_use", "tool_use", "tool_use"):
            exceeded |= c.observe(_assistant(f"m{i}", kind))
    assert (c.turns, c.assistant_events, exceeded) == (10, 40, False)


def test_the_limit_fires_after_limit_plus_one_distinct_messages():
    c = claude_cli.TurnCounter(limit=25)
    results = [c.observe(_assistant(f"m{i}")) for i in range(26)]
    assert results[:25] == [False] * 25 and results[25] is True


def test_assistant_text_inside_other_events_is_not_a_turn():
    c = claude_cli.TurnCounter(limit=1)
    tool_result = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": '{"type":"assistant"} {"type": "assistant"}'}]}})
    for _ in range(5):
        assert c.observe(tool_result) is False
    assert c.observe("not json but \"type\":\"assistant\"") is False
    assert c.turns == 0


def test_messages_without_an_id_count_one_line_each():
    c = claude_cli.TurnCounter(limit=2)
    line = json.dumps({"type": "assistant", "message": {"content": []}})
    assert [c.observe(line) for _ in range(3)] == [False, False, True]


def test_invocations_record_the_amended_enforcement():
    assert claude_cli.WRAPPER_VERSION == 3
    assert claude_cli.TURN_LIMIT_ENFORCEMENT == "harness_side_api_messages"
    assert config.RunConfig().turn_limit_enforcement == claude_cli.TURN_LIMIT_ENFORCEMENT
    assert config.SMOKE_LIMITS.max_turns_per_session == 25


@pytest.mark.skipif(not T4A.is_file(), reason="Stage-0.5 Task 4 Arm A run not present")
def test_the_invalidated_task4_arm_a_session_was_far_below_25_turns():
    """The defect, reproduced on the preserved raw stream: 26 stream lines, 11 turns."""
    c = claude_cli.TurnCounter(limit=config.SMOKE_LIMITS.max_turns_per_session)
    exceeded = any(c.observe(l) for l in T4A.read_text(encoding="utf-8").splitlines())
    assert (c.assistant_events, c.turns, exceeded) == (26, 11, False)


def test_no_valid_historical_session_is_affected_by_the_amendment():
    """Every session that completed under wrapper v2 also completes under v3,
    so the amendment cannot change any valid run already collected."""
    for f in sorted((ROOT / "runs").glob("2026*_r1/sessions/*/claude_stdout.jsonl")):
        exit_ = json.loads((f.parent / "exit.json").read_text(encoding="utf-8"))
        if exit_.get("termination_reason") != "completed":
            continue
        c = claude_cli.TurnCounter(limit=config.SMOKE_LIMITS.max_turns_per_session)
        assert not any(c.observe(l) for l in f.read_text(encoding="utf-8").splitlines()), f
        assert c.assistant_events <= 25, f
