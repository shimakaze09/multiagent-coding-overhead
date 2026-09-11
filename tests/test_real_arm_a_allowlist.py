"""Regression tests for the narrow-permission validation run.

Fixture: tests/fixtures/real_stream_arm_a_allowlist_2.1.260.jsonl, captured
verbatim from run 20260910T125941Z_palindrome_punctuation_A_r1 - the first real
Arm A run executed with the narrow Bash allowlist
(config.BASH_TEST_ALLOWLIST = the `python -m pytest` family only).

What this run demonstrates, and what these tests lock in:

  * `python -m pytest -q` WAS executed - not denied - and Claude received the
    real output ("6 passed in 0.02s").
  * The compound form `cd "<ws>" && python -m pytest -q` was permitted by the
    single pytest rule, so `cd` needs no rule of its own.
  * `python -c "..."` was STILL denied, which is the intended narrow behaviour,
    and the CLI names the offending sub-command in its denial message. That is
    direct evidence that compound commands are decomposed and each part checked.
  * The denial instrumentation still works with an allowlist in place.

This run is NOT a research result. No conclusion about coordination redundancy
follows from it; Arm B has not run.
"""

from __future__ import annotations

import json

import pytest

import config
from harness import telemetry, tools

FIXTURE = "real_stream_arm_a_allowlist_2.1.260.jsonl"
RUN_ID = "20260910T125941Z_palindrome_punctuation_A_r1"


def _fixture_events(rootpath):
    text = (rootpath / "tests" / "fixtures" / FIXTURE).read_text(encoding="utf-8")
    return [json.loads(l) for l in text.splitlines()]


@pytest.fixture(scope="module")
def parsed(request):
    text = (request.config.rootpath / "tests" / "fixtures" / FIXTURE).read_text(
        encoding="utf-8"
    )
    return telemetry.parse_stream(text.splitlines())


@pytest.fixture(scope="module")
def summary(parsed):
    return telemetry.session_summary(parsed)


@pytest.fixture(scope="module")
def acquisitions(parsed):
    denied = parsed.denied_tool_use_ids
    return [
        tools.classify_tool_call(
            tool_use_id=c.tool_use_id,
            tool_name=c.name,
            tool_input=c.input,
            result_text=c.result_text,
            agent_id="solo",
            session_key="01_solo",
            start_ts=c.start_ts,
            end_ts=c.end_ts,
            start_line=c.start_line,
            end_line=c.end_line,
            is_error=c.result_is_error,
            turn_id=c.agent_turn,
            permission_denied=c.tool_use_id in denied,
        )
        for c in parsed.tool_calls
    ]


@pytest.fixture(scope="module")
def bash_calls(acquisitions):
    return [a for a in acquisitions if a.tool_name == "Bash"]


def _pytest_command(a) -> bool:
    return "python -m pytest" in (a.command or "")


# --------------------------------------------------------------------------
# The point of the whole exercise: pytest actually ran
# --------------------------------------------------------------------------


def test_claude_invoked_python_dash_m_pytest(bash_calls):
    assert [a for a in bash_calls if _pytest_command(a)], "pytest was never invoked"


def test_the_pytest_command_was_actually_executed_not_denied(bash_calls):
    executed = [a for a in bash_calls if _pytest_command(a) and not a.permission_denied]
    assert len(executed) == 1
    call = executed[0]
    assert call.is_error is False
    assert call.permission_denied is False
    assert call.acquisition_class == tools.BASH_VERIFICATION
    assert call.completed is True


def test_the_compound_cd_and_pytest_form_was_permitted(bash_calls):
    """One pytest rule covered `cd "<ws>" && python -m pytest -q`, so `cd`
    needed no rule of its own."""
    call = next(a for a in bash_calls if _pytest_command(a) and not a.permission_denied)
    assert (call.command or "").startswith("cd ")
    assert "&&" in (call.command or "")
    assert call.bash_segment_classes == [
        tools.BASH_NON_ACQUISITION,
        tools.BASH_VERIFICATION,
    ]


def test_claude_received_the_test_result(bash_calls, request):
    call = next(a for a in bash_calls if _pytest_command(a) and not a.permission_denied)
    assert call.result_chars > 0
    assert call.result_sha, "the returned output has a content identity"

    evs = _fixture_events(request.config.rootpath)
    body = None
    for ev in evs:
        if ev.get("type") != "user":
            continue
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("tool_use_id") == call.tool_use_id:
                c = b.get("content")
                body = (
                    c
                    if isinstance(c, str)
                    else "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                )
    assert body is not None
    assert "passed" in body
    assert "failed" not in body
    assert "Permission" not in body


def test_the_edit_happened_before_the_test_run(request):
    """So the passing suite was observed on the FIXED code, not the original."""
    evs = _fixture_events(request.config.rootpath)
    edit_line = pytest_line = None
    for i, ev in enumerate(evs, 1):
        if ev.get("type") != "assistant":
            continue
        for b in (ev.get("message") or {}).get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            if b.get("name") == "Edit" and edit_line is None:
                edit_line = i
            cmd = (b.get("input") or {}).get("command", "")
            if b.get("name") == "Bash" and "python -m pytest" in cmd and pytest_line is None:
                pytest_line = i
    assert edit_line is not None and pytest_line is not None
    assert edit_line < pytest_line


# --------------------------------------------------------------------------
# The allowlist stayed narrow
# --------------------------------------------------------------------------


def test_the_remaining_denial_is_the_deliberately_excluded_inline_script(bash_calls):
    denied = [a for a in bash_calls if a.permission_denied]
    assert len(denied) == 1
    assert "python -c" in (denied[0].command or "")
    assert denied[0].acquisition_class == tools.TOOL_DENIED
    assert denied[0].is_acquisition_candidate is False


def test_the_cli_names_the_offending_subcommand(parsed):
    """Direct evidence that compound commands are decomposed per sub-command."""
    denied_result = next(
        c.result_text
        for c in parsed.tool_calls
        if c.tool_use_id in parsed.denied_tool_use_ids
    )
    assert "contains multiple operations" in denied_result
    assert "The following part requires approval" in denied_result
    assert "python -c" in denied_result


def test_python_dash_c_is_not_in_the_allowlist():
    assert not any("-c" in r for r in config.BASH_TEST_ALLOWLIST)


def test_denial_instrumentation_still_works_with_an_allowlist(summary):
    assert summary["permission_denied_count"] == 1
    assert len(summary["permission_denied_events"]) == 1
    d = summary["permission_denied_events"][0]
    assert d["tool_name"] == "Bash"
    assert d["decision_reason_type"] == "asyncAgent"


def test_claude_reported_the_test_run_it_actually_performed(parsed):
    final = (parsed.result or {}).get("result") or ""
    assert "6 passed" in final


# --------------------------------------------------------------------------
# Nothing else regressed
# --------------------------------------------------------------------------


def test_run_succeeded_on_subscription(summary):
    assert summary["is_error"] is False
    assert summary["terminal_reason"] == "completed"
    assert summary["api_key_source"] == "none"
    assert summary["cli_version"] == "2.1.260"
    assert summary["subagent_spawned"] == 0


def test_telemetry_parsing_remains_clean(summary):
    assert summary["unknown_event_types"] == {}
    assert summary["unrecognized_system_subtypes"] == []
    assert summary["unparsable_lines"] == 0
    assert summary["tool_calls_incomplete"] == 0
    assert summary["system_subtype_counts"] == {
        "init": 1,
        "thinking_tokens": 2,
        "permission_denied": 1,
    }


def test_acquisition_coverage_still_passes_the_gate(acquisitions):
    cov = tools.coverage(acquisitions, minimum=0.90)
    assert cov.acquisition_coverage == 1.0
    assert cov.meets_minimum is True
    assert cov.acquisition_unknown == 0
    assert cov.unknown_tool == 0
    assert cov.denied_calls == 1
    assert cov.bash_verification == 1


def test_allowing_pytest_did_not_turn_test_output_into_acquisition(acquisitions):
    executed = next(
        a
        for a in acquisitions
        if a.acquisition_class == tools.BASH_VERIFICATION and not a.permission_denied
    )
    assert executed.is_acquisition is False
    assert executed.is_acquisition_candidate is False


def test_turn_accounting_is_reported_as_four_distinct_quantities(summary):
    """This run DISPROVED the earlier reconciliation: 11 stream events minus 2
    thinking-only is 9, but num_turns is 8. The quantities are simply different."""
    assert summary["assistant_stream_events"] == 11
    assert summary["thinking_only_stream_events"] == 2
    assert summary["api_assistant_messages"] == 6
    assert summary["tool_use_blocks"] == 7
    assert summary["cli_reported_num_turns"]["value"] == 8
    assert (
        summary["assistant_stream_events"] - summary["thinking_only_stream_events"]
        != summary["cli_reported_num_turns"]["value"]
    ), "the earlier reconciliation does not hold in general"
    # the relationship that does hold on every real run observed so far
    assert summary["cli_reported_num_turns"]["value"] == summary["tool_use_blocks"] + 1


def test_parallel_reads_share_one_api_message(parsed):
    """Three Reads were issued together in one assistant message, so they are
    concurrent and none may be attributed to another under K(t)."""
    reads = [c for c in parsed.tool_calls if c.name == "Read"]
    assert len(reads) == 3
    assert len({c.api_message_id for c in reads}) == 1
    assert len({c.start_line for c in reads}) == 3, "distinct stream lines"
    assert len({c.concurrency_group_line for c in reads}) == 1, "one concurrency group"


def test_token_semantics_unchanged(summary):
    u = summary["usage"]
    assert u["input_tokens"]["value"] == 12
    assert u["cache_read_tokens"]["value"] == 105033
    assert u["cache_write_tokens"]["value"] == 8991
    assert u["total_input_tokens"]["value"] == 114036
    assert u["output_tokens"]["value"] == 1504


def test_rate_limit_telemetry_still_parsed_and_not_an_error(summary):
    rl = summary["rate_limit"]
    assert rl["availability"] == telemetry.REPORTED
    assert rl["indicates_execution_failure"] is False


# --------------------------------------------------------------------------
# Stored-run level checks
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_dir(request):
    d = request.config.rootpath / "runs" / RUN_ID
    if not (d / "metadata.json").is_file():
        pytest.skip(f"validation run {RUN_ID} not present")
    return d


def test_stored_run_records_the_policy_and_config_hash(run_dir):
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    pol = meta["permission_policy"]
    assert pol["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)
    assert pol["allowed_tools_policy_id"] == "narrow_pytest_v1"
    assert pol["permission_mode"] == "acceptEdits"
    assert pol["permission_prompts"] == "none"
    assert pol["identical_across_arms"] is True
    # recorded under wrapper v2; amendment 7 changed only turn_limit_enforcement
    import dataclasses

    v2 = dataclasses.replace(config.RunConfig(), turn_limit_enforcement="harness_side")
    assert meta["config_hash"] == v2.config_hash() == "22ce9b7e5249dd497ee7c4c0318216b4"

    inv = json.loads(
        (run_dir / "sessions" / "01_solo" / "invocation.json").read_text(encoding="utf-8")
    )
    assert inv["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)
    i = inv["argv"].index("--allowedTools")
    assert inv["argv"][i + 1 : i + 4] == list(config.BASH_TEST_ALLOWLIST)
    assert "bypassPermissions" not in " ".join(inv["argv"])


def test_held_out_verifier_still_passes(run_dir):
    v = json.loads((run_dir / "verify" / "verification.json").read_text(encoding="utf-8"))
    assert v["solved"] is True
    assert v["exit_code"] == 0
    assert "12 passed" in v["stdout"]


def test_visible_suite_alone_does_not_prove_the_fix(run_dir):
    """Honest caveat: the 6 visible tests pass before AND after the fix - they do
    not cover punctuation. Claude observed a passing suite on its edited code,
    but only the 12-test held-out verifier establishes correctness."""
    v = json.loads((run_dir / "verify" / "verification.json").read_text(encoding="utf-8"))
    assert "12 passed" in v["stdout"]
    visible = run_dir / "workspace" / "tests" / "test_basic.py"
    assert visible.is_file()
    assert "Panama" not in visible.read_text(encoding="utf-8")
