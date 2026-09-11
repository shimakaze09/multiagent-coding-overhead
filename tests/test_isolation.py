"""Held-out isolation check (detection only; PREREGISTRATION amendment 6)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis import ingest, isolation
from harness import telemetry, tools

WS = r"D:\dev\subagent test\stage0\runs\X_r1\workspace"


def _raw(calls: list[tuple[str, dict, str]]) -> dict:
    """Build a load_run-shaped dict from (tool_name, input, result) triples."""
    lines = [json.dumps({"type": "system", "subtype": "init", "session_id": "s",
                         "apiKeySource": "none"})]
    for i, (name, inp, result) in enumerate(calls):
        tid = f"t{i}"
        lines.append(json.dumps({"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result}]}}))
    parsed = telemetry.parse_stream(lines)
    return {"metadata": {"workspace": {"path": WS}},
            "sessions": [SimpleNamespace(session_key="01_solo", parsed=parsed)]}


def test_paths_inside_the_workspace_are_fine():
    raw = _raw([
        ("Read", {"file_path": WS + r"\shopcart\cart.py"}, "x"),
        ("Read", {"file_path": "/d/dev/subagent test/stage0/runs/X_r1/workspace/shopcart/money.py"}, "x"),
        ("Glob", {"pattern": "**/*.py"}, "x"),
        ("Grep", {"pattern": "def", "path": "."}, "x"),
        ("Bash", {"command": f'cd "{WS}" && python -m pytest -q'}, "7 passed"),
        ("Bash", {"command": 'find . -type f -not -path "*/.git/*" | sort'}, "x"),
    ])
    rep = isolation.check_run(raw)
    assert rep["breach_suspected"] is False
    assert rep["outside_workspace_accesses"] == []


@pytest.mark.parametrize("call", [
    ("Read", {"file_path": r"D:\dev\subagent test\stage0\tasks\holdout\x\test_holdout_cart.py"}, "x"),
    ("Read", {"file_path": "/d/dev/subagent test/stage0/tests/test_fixture_cart.py"}, "x"),
    ("Glob", {"pattern": "../../../**/*.py"}, "x"),
    ("Grep", {"pattern": "def", "path": "../.."}, "x"),
    ("Bash", {"command": "cat ../../../tasks/defs/cart_invoice_rounding.json"}, "x"),
    ("Bash", {"command": "cd .. && ls"}, "x"),
    ("Bash", {"command": "ls ~/Documents"}, "x"),
])
def test_any_access_outside_the_workspace_is_flagged(call):
    rep = isolation.check_run(_raw([call]))
    assert rep["breach_suspected"] is True
    assert rep["outside_workspace_accesses"]


@pytest.mark.parametrize("result", [
    "D:/dev/subagent test/stage0/tasks/holdout/cart_invoice_rounding",
    "def _reference_fix(ws):",
    "see PREREGISTRATION.md",
    "test_holdout_palindrome.py",
])
def test_held_out_or_reference_material_in_a_tool_result_is_flagged(result):
    rep = isolation.check_run(_raw([("Bash", {"command": "find . -name '*.py'"}, result)]))
    assert rep["breach_suspected"] is True
    assert rep["held_out_or_reference_exposure"]


def test_posix_absolute_paths_without_a_drive_are_reported_not_guessed():
    rep = isolation.check_run(_raw([("Bash", {"command": "grep -rn /api/ ."}, "x")]))
    assert rep["breach_suspected"] is False
    assert rep["unresolved_posix_absolute_paths"]


def test_missing_workspace_path_is_unavailable_not_clean():
    rep = isolation.check_run({"metadata": {}, "sessions": []})
    assert rep["available"] is False
    assert rep["breach_suspected"] is None


@pytest.mark.parametrize("run", [
    "20260910T122445Z_palindrome_punctuation_A_r1",
    "20260910T125941Z_palindrome_punctuation_A_r1",
    "20260910T230448Z_palindrome_punctuation_B_r1",
])
def test_the_pre_freeze_real_runs_show_no_isolation_breach(request, run):
    d = request.config.rootpath / "runs" / run
    if not (d / "metadata.json").is_file():
        pytest.skip(f"{run} not present")
    rep = isolation.check_run(ingest.load_run(d))
    assert rep["available"] is True
    assert rep["outside_workspace_accesses"] == []
    assert rep["held_out_or_reference_exposure"] == []
    assert rep["breach_suspected"] is False


def test_residual_risk_is_real_the_holdout_is_reachable_from_a_workspace(request):
    """Documents WHY detection is needed: a run workspace sits three levels below
    stage0/, so the held-out tests are one relative path away. Filesystem
    isolation is not enforced by the harness."""
    root = request.config.rootpath
    ws = root / "runs" / "SOME_RUN_r1" / "workspace"
    assert (ws / ".." / ".." / ".." / "tasks" / "holdout" / "cart_invoice_rounding"
            / "test_holdout_cart.py").resolve().is_file()
