"""Milestone 7: the harness-input reconstruction gate.

What is claimed: everything OUR harness supplied to each `claude` invocation can
be rebuilt from the logs alone and matches what was recorded.

What is NOT claimed, and is not testable: Claude Code's hidden system prompt, or
the provider HTTP request. Those are not under our control and not observable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from arms import multi_nl, single
from harness import claude_cli, events as ev
from tasks import registry


# --------------------------------------------------------------------------
# Rebuilding an invocation from the logs
# --------------------------------------------------------------------------


def _session_start_events(run_dir: Path) -> list[dict]:
    return [
        e
        for e in ev.iter_events(run_dir / "events.jsonl")
        if e["type"] == ev.CLAUDE_SESSION_START
    ]


def _task_statement_from_log(run_dir: Path) -> str:
    for e in ev.iter_events(run_dir / "events.jsonl"):
        if e["type"] == ev.TASK_START:
            return e["payload"]["statement"]
    raise AssertionError("no TASK_START event in the log")


def _handoffs_from_log(run_dir: Path) -> dict[str, str]:
    """label -> exact body, read from the handoff sidecar files."""
    out: dict[str, str] = {}
    for e in ev.iter_events(run_dir / "events.jsonl"):
        if e["type"] == ev.HANDOFF_SENT:
            p = e["payload"]
            out[p["label"]] = (run_dir / p["path"]).read_text(encoding="utf-8")
    return out


def rebuild_prompt(run_dir: Path, arm: str, role: str, step: str, task) -> str:
    """Reconstruct the exact stdin the harness sent, using only logged data."""
    statement = _task_statement_from_log(run_dir)
    assert statement == task.statement, "the logged statement must be verbatim"
    h = _handoffs_from_log(run_dir)

    if arm == "A":
        return single.build_solo_prompt(task)
    if step == "coordinator_kickoff":
        return multi_nl.coordinator_kickoff_prompt(task)
    if step == "investigate":
        return multi_nl.investigator_prompt(task, h["investigation_instruction"])
    if step == "coordinator_plan":
        return multi_nl.coordinator_after_investigation_prompt(h["investigation_report"])
    if step == "implement":
        return multi_nl.implementer_prompt(
            task, h["implementation_instruction"], h["forwarded_investigation_report"]
        )
    if step == "coordinator_wrapup":
        return multi_nl.coordinator_wrapup_prompt(h["implementation_report"])
    raise AssertionError(f"unknown step {step!r}")


def reconstruct_run(run_dir: Path) -> list[tuple[str, list[str]]]:
    """Rebuild every invocation of a run from the logs and diff against the record.

    Returns [(session_key, [mismatches])].
    """
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    task = registry.get_task(meta["task_id"])
    arm = meta["arm"]
    cfg = meta["config"]

    results = []
    for start in _session_start_events(run_dir):
        p = start["payload"]
        session_key = p["session_key"]
        stored = json.loads(
            (run_dir / "sessions" / session_key / "invocation.json").read_text(encoding="utf-8")
        )
        step = stored["prompt_inputs"].get("step", "solo")

        prompt = rebuild_prompt(run_dir, arm, p["role"], step, task)
        rebuilt_inv = claude_cli.build_invocation(
            cli_path=stored["cli_path"],
            session_key=session_key,
            agent_id=start["agent_id"],
            role=p["role"],
            prompt=prompt,
            cwd=p["cwd"],
            model=p["model"],
            tools=p["tools"],
            allowed_tools=p["allowed_tools"],
            disallowed_tools=p["disallowed_tools"],
            permission_mode=p["permission_mode"],
            permission_prompts=p["permission_prompts"],
            max_turns=p["max_turns"],
            max_wall_seconds=p["max_wall_seconds"],
            session_id=p["assigned_session_id"],
            resume_session_id=p["resumed_session_id"],
            append_system_prompt=stored["append_system_prompt"],
            include_hook_events=cfg["include_hook_events"],
            prompt_inputs=stored["prompt_inputs"],
            base_env={"PATH": "x"},
        )
        diffs = claude_cli.compare_reconstruction(
            claude_cli.reconstruct_from_log(stored),
            claude_cli.reconstruct_from_log(rebuilt_inv.as_dict()),
        )
        results.append((session_key, diffs))
    return results


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["A", "B"])
def test_every_harness_input_is_reconstructible_from_the_logs(mock_runs, arm):
    run_dir, _summary = mock_runs[arm]
    results = reconstruct_run(run_dir)
    assert results, "no sessions to reconstruct"
    failures = {k: d for k, d in results if d}
    assert not failures, f"reconstruction mismatches: {failures}"


@pytest.mark.parametrize("arm", ["A", "B"])
def test_reconstruction_covers_the_prompt_bytes_exactly(mock_runs, arm):
    run_dir, _ = mock_runs[arm]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    task = registry.get_task(meta["task_id"])
    for start in _session_start_events(run_dir):
        p = start["payload"]
        stored = json.loads(
            (run_dir / "sessions" / p["session_key"] / "invocation.json").read_text(
                encoding="utf-8"
            )
        )
        step = stored["prompt_inputs"].get("step", "solo")
        rebuilt = rebuild_prompt(run_dir, meta["arm"], p["role"], step, task)
        assert rebuilt == stored["stdin_text"], (
            f"{p['session_key']}: reconstructed prompt differs byte-for-byte"
        )


def test_reconstruction_fields_do_not_claim_the_hidden_prompt():
    fields = set(claude_cli.RECONSTRUCTION_FIELDS)
    for forbidden in ("system_prompt_full", "provider_request", "http_request", "hidden_prompt"):
        assert forbidden not in fields
    # what we DO claim
    for required in ("argv", "cwd", "stdin_text", "tools", "model", "session_id"):
        assert required in fields


def test_compare_reconstruction_detects_a_difference():
    stored = {"argv": ["claude", "-p"], "cwd": "/a", "stdin_text": "x"}
    rebuilt = {"argv": ["claude", "-p"], "cwd": "/b", "stdin_text": "x"}
    diffs = claude_cli.compare_reconstruction(stored, rebuilt)
    assert any("cwd" in d for d in diffs)


def test_compare_reconstruction_is_not_order_or_type_blind():
    stored = {"tools": ["Read", "Grep"]}
    rebuilt = {"tools": ["Grep", "Read"]}
    assert claude_cli.compare_reconstruction(stored, rebuilt)


def test_tuple_and_list_are_treated_as_equal():
    assert not claude_cli.compare_reconstruction({"tools": ("Read",)}, {"tools": ["Read"]})


# --------------------------------------------------------------------------
# invocation.json must be written BEFORE execution
# --------------------------------------------------------------------------


def test_invocation_is_recorded_even_when_the_process_cannot_start(tmp_path):
    """Proof of ordering: the record survives a spawn that never happened."""
    inv = claude_cli.build_invocation(
        cli_path=str(tmp_path / "definitely-not-an-executable"),
        session_key="01_solo",
        agent_id="solo",
        role="solo",
        prompt="the exact prompt bytes",
        cwd=tmp_path,
        model="sonnet",
        tools=("Read",),
        base_env={"PATH": "x"},
    )
    out = tmp_path / "session"
    result = claude_cli.run_invocation(inv, out, base_env={"PATH": "x"})

    assert result.termination_reason == claude_cli.TERM_SPAWN_FAILED
    assert (out / "invocation.json").is_file(), (
        "invocation.json must be written before the process is spawned"
    )
    stored = json.loads((out / "invocation.json").read_text(encoding="utf-8"))
    assert stored["stdin_text"] == "the exact prompt bytes"
    assert result.billing_ok is False, "a failed spawn must not be reported as verified"


def test_spawn_failure_still_writes_stdout_stderr_and_exit_records(tmp_path):
    inv = claude_cli.build_invocation(
        cli_path=str(tmp_path / "nope"),
        session_key="01_solo",
        agent_id="solo",
        role="solo",
        prompt="p",
        cwd=tmp_path,
        model="sonnet",
        tools=(),
        base_env={"PATH": "x"},
    )
    out = tmp_path / "s"
    claude_cli.run_invocation(inv, out, base_env={"PATH": "x"})
    assert (out / "claude_stdout.jsonl").is_file()
    assert (out / "claude_stderr.txt").is_file()


# --------------------------------------------------------------------------
# Recorded environment is part of the reconstruction surface
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["A", "B"])
def test_child_environment_manifest_is_recorded_per_session(mock_runs, arm):
    run_dir, _ = mock_runs[arm]
    for sd in sorted((run_dir / "sessions").iterdir()):
        stored = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        manifest = stored["env_manifest"]
        assert "inherited_keys" in manifest and "stripped_keys" in manifest
        assert manifest["billing_guard_vars_present"] == []


@pytest.mark.parametrize("arm", ["A", "B"])
def test_turn_and_time_limits_are_recorded(mock_runs, arm):
    run_dir, _ = mock_runs[arm]
    for sd in sorted((run_dir / "sessions").iterdir()):
        stored = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        assert stored["max_turns"] == config.SMOKE_LIMITS.max_turns_per_session
        assert stored["max_wall_seconds"] == config.SMOKE_LIMITS.max_wall_seconds_per_session
        assert stored["turn_limit_enforcement"] == "harness_side"


def test_permission_policy_is_reconstructible_from_the_log(mock_runs):
    """The allowlist must be recoverable from events.jsonl alone, not only from
    invocation.json, or the gate could not detect a policy change."""
    import config

    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        starts = _session_start_events(run_dir)
        assert starts
        for e in starts:
            payload = e["payload"]
            assert payload["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)
            assert payload["permission_prompts"] == "none"
            assert payload["permission_mode"] == "acceptEdits"


def test_config_hash_is_recorded_in_run_metadata(mock_runs):
    import config

    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["config_hash"]
        assert meta["config"]["config_hash"] == meta["config_hash"]
        assert meta["permission_policy"]["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)
        assert meta["permission_policy"]["allowed_tools_policy_id"] == "narrow_pytest_v1"
        assert meta["permission_policy"]["identical_across_arms"] is True
        assert meta["effective_config"]["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)


def test_both_arms_share_one_config_hash(mock_runs):
    """Requirement: the exact same permission policy in Arm A and Arm B."""
    hashes = set()
    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        hashes.add(meta["config_hash"])
    assert len(hashes) == 1, f"arms were configured differently: {hashes}"
