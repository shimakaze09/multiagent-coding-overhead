"""End-to-end pipeline validation using the mock CLI (tools/mock_claude.py).

These runs are instrumentation validation ONLY. The mock is not a model, so
nothing here is an Arm A or Arm B result. What is being asserted is that the
harness observes, records, normalizes, indexes and reports correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from analysis import ingest, metrics, report as report_mod
from arms import multi_nl, single
from harness import claude_cli, events as ev, telemetry, tools
from tasks import registry


# --------------------------------------------------------------------------
# Raw artifact layout - both raw sources must exist and be independent
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["A", "B"])
def test_required_raw_artifacts_exist(mock_runs, arm):
    run_dir, _summary = mock_runs[arm]
    for rel in (
        "metadata.json",
        "events.jsonl",
        "run_summary.json",
        "workspace_diff.patch",
        "verify/verification.json",
        "verify/verifier_stdout.txt",
    ):
        assert (run_dir / rel).is_file(), rel

    sessions = sorted((run_dir / "sessions").iterdir())
    assert sessions
    for sd in sessions:
        for rel in ("invocation.json", "claude_stdout.jsonl", "claude_stderr.txt", "exit.json"):
            assert (sd / rel).is_file(), f"{sd.name}/{rel}"


def test_raw_cli_output_and_normalized_log_are_separate_sources(mock_runs):
    run_dir, _ = mock_runs["B"]
    raw_files = list((run_dir / "sessions").glob("*/claude_stdout.jsonl"))
    assert raw_files
    normalized = run_dir / "events.jsonl"
    assert normalized.is_file()
    assert normalized not in raw_files
    # every raw line is genuine CLI json, not our normalized schema
    for rf in raw_files:
        for line in rf.read_text(encoding="utf-8").splitlines():
            obj = json.loads(line)
            assert "seq" not in obj, "harness fields must not be written into the raw stream"
            assert "run_id" not in obj


def test_raw_stream_is_retained_unchanged_and_reparses_identically(mock_runs):
    run_dir, _ = mock_runs["B"]
    for sd in sorted((run_dir / "sessions").iterdir()):
        lines = (sd / "claude_stdout.jsonl").read_text(encoding="utf-8").splitlines()
        ps = telemetry.parse_stream(lines)
        assert ps.unparsable_lines == 0
        assert ps.init is not None
        # the recorded line count matches what the wrapper reported at run time
        exit_rec = json.loads((sd / "exit.json").read_text(encoding="utf-8"))
        assert exit_rec["raw_stdout_lines"] == len(lines)


def test_normalized_events_point_back_into_the_raw_stream(mock_runs):
    run_dir, _ = mock_runs["B"]
    events = list(ev.iter_events(run_dir / "events.jsonl"))
    linked = [e for e in events if e.get("raw_ref")]
    assert linked, "CLI-derived events must carry a raw_ref"
    for e in linked[:50]:
        ref = e["raw_ref"]
        raw = run_dir / "sessions" / ref["session_key"] / "claude_stdout.jsonl"
        assert raw.is_file()
        lines = raw.read_text(encoding="utf-8").splitlines()
        assert 1 <= ref["line_no"] <= len(lines)
        json.loads(lines[ref["line_no"] - 1])  # the pointer resolves to real json


def test_event_log_is_monotonic_and_well_formed(mock_runs):
    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        rep = ev.read_events(run_dir / "events.jsonl")
        assert rep.partial_lines == 0 and rep.malformed_lines == 0
        ev.assert_monotonic(rep.events)


def test_cli_derived_events_are_marked_cli_reported(mock_runs):
    run_dir, _ = mock_runs["B"]
    events = list(ev.iter_events(run_dir / "events.jsonl"))
    cli_events = [e for e in events if e["source"] == "claude_code"]
    assert cli_events
    assert all(e["order_confidence"] == ev.ORDER_CLI_REPORTED for e in cli_events)
    harness_events = [e for e in events if e["source"] == "harness"]
    assert any(e["order_confidence"] == ev.ORDER_HARNESS_OBSERVED for e in harness_events)


def test_required_event_types_are_all_emitted(mock_runs):
    run_dir, _ = mock_runs["B"]
    seen = {e["type"] for e in ev.iter_events(run_dir / "events.jsonl")}
    for required in (
        ev.TASK_START,
        ev.TASK_END,
        ev.CLAUDE_SESSION_START,
        ev.CLAUDE_SESSION_END,
        ev.AGENT_TURN_START,
        ev.AGENT_TURN_END,
        ev.TOOL_CALL_START,
        ev.TOOL_CALL_END,
        ev.FILE_READ_START,
        ev.FILE_READ_END,
        ev.SEARCH_START,
        ev.SEARCH_END,
        ev.EDIT,
        ev.TEST_RUN,
        ev.AGENT_MESSAGE,
        ev.RAW_CLAUDE_EVENT,
        ev.HANDOFF_SENT,
        ev.WORKSPACE_PREPARED,
        ev.VERIFICATION,
        ev.USAGE_REPORT,
    ):
        assert required in seen, f"missing normalized event type: {required}"


def test_every_raw_line_has_a_raw_claude_event(mock_runs):
    run_dir, _ = mock_runs["A"]
    events = list(ev.iter_events(run_dir / "events.jsonl"))
    raw_events = [e for e in events if e["type"] == ev.RAW_CLAUDE_EVENT]
    total_raw_lines = sum(
        len((sd / "claude_stdout.jsonl").read_text(encoding="utf-8").splitlines())
        for sd in (run_dir / "sessions").iterdir()
    )
    assert len(raw_events) == total_raw_lines, "no CLI event may be silently dropped"


# --------------------------------------------------------------------------
# Arm shape
# --------------------------------------------------------------------------


def test_arm_a_is_one_session_one_agent(mock_runs):
    _run_dir, summary = mock_runs["A"]
    assert summary["session_count"] == 1
    assert summary["handoffs"] == []
    assert summary["arm_label_valid"] is True


def test_arm_b_is_three_logical_agents_in_the_fixed_topology(mock_runs):
    run_dir, summary = mock_runs["B"]
    invs = [
        json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        for sd in sorted((run_dir / "sessions").iterdir())
    ]
    assert [i["role"] for i in invs] == [
        "coordinator",
        "investigator",
        "coordinator",
        "implementer",
        "coordinator",
    ]
    assert len({i["agent_id"] for i in invs}) == 3

    # the Coordinator is ONE logical agent: its later turns resume its session
    coord = [i for i in invs if i["role"] == "coordinator"]
    assert coord[0]["resume_session_id"] is None
    assert all(c["resume_session_id"] for c in coord[1:])

    labels = [h["label"] for h in summary["handoffs"]]
    assert labels == [
        "investigation_instruction",
        "investigation_report",
        "implementation_instruction",
        "forwarded_investigation_report",
        "implementation_report",
        "final_result",
    ]


def test_coordinator_has_no_repository_tools(mock_runs):
    run_dir, _ = mock_runs["B"]
    for sd in sorted((run_dir / "sessions").iterdir()):
        inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        if inv["role"] == "coordinator":
            assert inv["tools"] == []


def test_investigator_is_denied_write_tools(mock_runs):
    run_dir, _ = mock_runs["B"]
    for sd in sorted((run_dir / "sessions").iterdir()):
        inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        if inv["role"] == "investigator":
            assert "Edit" not in inv["tools"] and "Write" not in inv["tools"]
            assert "Edit" in inv["disallowed_tools"]


def test_both_arms_get_the_task_statement_verbatim(mock_runs):
    task = registry.get_task("palindrome_punctuation")
    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        found = False
        for sd in sorted((run_dir / "sessions").iterdir()):
            inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
            if task.statement in inv["stdin_text"]:
                found = True
                assert inv["prompt_inputs"].get("task_statement_verbatim") == task.statement
                break
        assert found, f"arm {arm} never received the verbatim task statement"


def test_handoff_bodies_are_stored_exactly(mock_runs):
    run_dir, summary = mock_runs["B"]
    for h in summary["handoffs"]:
        body = (run_dir / h["path"]).read_text(encoding="utf-8")
        assert tools.content_sha(body) == h["sha"]
        assert h["sizes"]["chars"] == len(body)
        assert h["sizes"]["utf8_bytes"] == len(body.encode("utf-8"))


def test_implementer_receives_the_investigation_report(mock_runs):
    run_dir, _ = mock_runs["B"]
    for sd in sorted((run_dir / "sessions").iterdir()):
        inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        if inv["role"] == "implementer":
            assert inv["prompt_inputs"]["investigator_report"]
            assert inv["prompt_inputs"]["investigator_report"] in inv["stdin_text"]
            return
    pytest.fail("no implementer session found")


# --------------------------------------------------------------------------
# Verification and billing integrity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["A", "B"])
def test_verifier_ran_identically_and_solved_the_task(mock_runs, arm):
    run_dir, summary = mock_runs[arm]
    v = json.loads((run_dir / "verify" / "verification.json").read_text(encoding="utf-8"))
    task = registry.get_task("palindrome_punctuation")
    assert v["argv"] == list(task.verifier_command)
    assert summary["solved"] is True, v["stdout"][-2000:]


@pytest.mark.parametrize("arm", ["A", "B"])
def test_run_records_subscription_execution_and_no_api_charge(mock_runs, arm):
    run_dir, summary = mock_runs[arm]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["billing_guard"]["ok"] is True
    assert meta["billing_guard"]["subscription_execution"] is True
    assert meta["billing_guard"]["api_charge"] == "false_expected"
    assert meta["billing_guard"]["vars_present"] == []
    assert summary["all_sessions_subscription_ok"] is True


def test_host_session_variables_were_stripped_from_children(mock_runs):
    run_dir, _ = mock_runs["A"]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    manifest = meta["env_manifest"]
    assert not any(k.startswith("CLAUDE_CODE_") for k in manifest["inherited_keys"])
    assert "values of inherited variables are intentionally not recorded" in manifest["note"]


def test_context_residency_is_reported_as_level_2_not_level_1(mock_runs):
    run_dir, _ = mock_runs["A"]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["context_residency_level"] == 2
    assert "Level 1 is not achievable" in meta["context_residency_note"]


def test_arm_c_is_recorded_as_not_implemented(mock_runs):
    run_dir, _ = mock_runs["A"]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["arm_c_implemented"] is False


# --------------------------------------------------------------------------
# Reports and duplication
# --------------------------------------------------------------------------


def test_arm_b_duplication_is_detected_and_classified(mock_runs):
    run_dir, _ = mock_runs["B"]
    r = report_mod.run_report(run_dir)
    assert r["inter_agent_overlapping_acquisitions"] > 0
    assert r["primed_reacquisitions"] > 0
    assert r["potentially_avoidable_acquisitions"] == r["inter_agent_overlapping_acquisitions"]
    assert r["oracle_upper_bound"]["duplicate_tool_calls"] > 0
    assert r["oracle_upper_bound"]["duplicate_chars"] > 0
    # each finding must carry auditable evidence
    for f in r["findings"]:
        assert f["producer_agent"] and f["consumer_agent"]
        assert f["timing_basis"].startswith("order_key")
        assert f["priming"] in (metrics.PRIMED, metrics.UNPRIMED, metrics.PRIMING_UNKNOWN)


def test_arm_a_has_no_inter_agent_duplication_by_construction(mock_runs):
    run_dir, _ = mock_runs["A"]
    r = report_mod.run_report(run_dir)
    assert r["inter_agent_overlapping_acquisitions"] == 0
    assert r["primed_reacquisitions"] == 0
    assert r["claude_sessions"] == 1


def test_concurrent_reads_are_excluded_by_temporal_availability(mock_runs):
    """The mock issues three parallel reads per message, so K(t) must bite."""
    run_dir, _ = mock_runs["B"]
    r = report_mod.run_report(run_dir)
    assert r["concurrency_excluded_pairs"] > 0


def test_acquisition_coverage_is_full_for_structured_tool_use(mock_runs):
    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        r = report_mod.run_report(run_dir)
        assert r["acquisition_coverage"]["acquisition_coverage"] == 1.0
        assert r["unknown_tool_calls"] == 0
        assert r["unclassified_bash_acquisitions"] == 0
        assert r["low_observability"] is False
        assert r["unknown_cli_event_types"] == {}


def test_token_and_cost_telemetry_are_captured_with_availability(mock_runs):
    run_dir, _ = mock_runs["A"]
    r = report_mod.run_report(run_dir)
    assert r["token_telemetry"]["availability"] == telemetry.REPORTED
    assert r["token_telemetry"]["reported_input_tokens"] > 0
    assert r["cost_telemetry"]["subscription_execution"] is True
    assert r["cost_telemetry"]["interpretation"] == "api_equivalent_not_amount_paid"


def test_handoff_sizes_are_labelled_estimated_not_billed(mock_runs):
    run_dir, _ = mock_runs["B"]
    r = report_mod.run_report(run_dir)
    assert r["handoff_total_chars"] > 0
    assert r["handoff_estimated_tokens_availability"] == telemetry.ESTIMATED


def test_reports_render_without_error(mock_runs):
    a = report_mod.run_report(mock_runs["A"][0])
    b = report_mod.run_report(mock_runs["B"][0])
    text_a = report_mod.render_run_report(a)
    text_b = report_mod.render_run_report(b)
    assert "SOLVED" in text_a and "acquisition_coverage" in text_b
    comparison = report_mod.render_comparison(a, b)
    assert "Arm B overhead over Arm A" in comparison


def test_human_trace_is_readable_and_annotates_reacquisition(mock_runs):
    trace = report_mod.human_trace(mock_runs["B"][0])
    assert "[Coordinator]" in trace
    assert "[Investigator]" in trace
    assert "[Implementer]" in trace
    assert "handoff -> investigator" in trace
    assert "received investigation_report" in trace
    assert "POTENTIAL REACQUISITION" in trace
    assert "information previously available from investigator" in trace
    assert "[VERIFIER]" in trace


def test_trace_for_arm_a_has_no_reacquisition_annotation(mock_runs):
    trace = report_mod.human_trace(mock_runs["A"][0])
    assert "[Solo agent]" in trace
    assert "POTENTIAL REACQUISITION" not in trace


def test_structured_comparison_matches_the_printed_table(mock_runs):
    """The machine-readable comparison and the rendered table share one source."""
    a = report_mod.run_report(mock_runs["A"][0])
    b = report_mod.run_report(mock_runs["B"][0])
    structured = report_mod.comparison_report(a, b)

    assert structured["task_id"] == "palindrome_punctuation"
    assert structured["solved"] == {"arm_a": True, "arm_b": True}
    assert structured["units"].endswith("not dollars")
    assert structured["eligible_for_headline"] == {"arm_a": True, "arm_b": True}

    overhead = structured["acquisition_overhead"]
    cell = overhead["total_tool_calls"]
    assert cell["arm_a"] == a["tool_calls_total"]
    assert cell["arm_b"] == b["tool_calls_total"]
    assert cell["delta"] == b["tool_calls_total"] - a["tool_calls_total"]
    assert cell["ratio_b_over_a"] > 1, "Arm B is expected to make more tool calls"

    rendered = report_mod.render_comparison(a, b)
    assert f"{cell['arm_b']:>12}" in rendered
    assert str(cell["ratio_b_over_a"]) in rendered


def test_comparison_handles_a_zero_denominator_without_dividing(mock_runs):
    a = report_mod.run_report(mock_runs["A"][0])
    b = report_mod.run_report(mock_runs["B"][0])
    a = dict(a, tool_calls_total=0)
    overhead = report_mod.comparison_report(a, b)["acquisition_overhead"]
    assert overhead["total_tool_calls"]["ratio_b_over_a"] is None
