"""Regression tests pinning the first real Arm B run.

Run 20260910T230448Z_palindrome_punctuation_B_r1 - Claude Code 2.1.260, Claude
subscription, sonnet, config_hash identical to the validated Arm A run
(20260910T125941Z_palindrome_punctuation_A_r1).

n=1. These tests exist so that a later parser or classifier change cannot
silently alter what this run was observed to contain. They are NOT evidence of
any general claim about multi-agent coding.

Tests are skipped if the run directory is not present.
"""

from __future__ import annotations

import json

import pytest

from analysis import ingest, metrics, report as report_mod
from harness import tools

RUN_ID = "20260910T230448Z_palindrome_punctuation_B_r1"
ARM_A_REF = "20260910T125941Z_palindrome_punctuation_A_r1"


@pytest.fixture(scope="module")
def run_dir(request):
    d = request.config.rootpath / "runs" / RUN_ID
    if not (d / "metadata.json").is_file():
        pytest.skip(f"Arm B run {RUN_ID} not present")
    return d


@pytest.fixture(scope="module")
def raw(run_dir):
    return ingest.load_run(run_dir)


@pytest.fixture(scope="module")
def rep(run_dir):
    return report_mod.run_report(run_dir)


@pytest.fixture(scope="module")
def dup(run_dir):
    return metrics.analyze(ingest.run_acquisitions(run_dir), ingest.run_messages(run_dir))


def _acqs(raw, role):
    return [
        a
        for sa in raw["sessions"]
        if sa.invocation["role"] == role
        for a in ingest.acquisitions_for_session(sa)
    ]


# --------------------------------------------------------------------------
# Topology and parity
# --------------------------------------------------------------------------


def test_fixed_topology_was_preserved(raw):
    assert [sa.invocation["role"] for sa in raw["sessions"]] == [
        "coordinator", "investigator", "coordinator", "implementer", "coordinator",
    ]
    for sa in raw["sessions"]:
        assert sa.exit["exit_code"] == 0
        assert sa.exit["termination_reason"] == "completed"


def test_coordinator_is_one_resumed_logical_agent(raw):
    coord = [sa for sa in raw["sessions"] if sa.invocation["role"] == "coordinator"]
    assert len({sa.summary["session_id"] for sa in coord}) == 1
    assert coord[0].invocation["resume_session_id"] is None
    assert all(sa.invocation["resume_session_id"] for sa in coord[1:])


def test_resumed_coordinator_figures_are_per_invocation_not_cumulative(raw):
    """So summing usage/cost across the five sessions does not double count."""
    coord = [sa for sa in raw["sessions"] if sa.invocation["role"] == "coordinator"]
    assert [(sa.parsed.result or {}).get("num_turns") for sa in coord] == [1, 1, 1]


def test_configuration_is_identical_to_the_arm_a_reference(run_dir, request):
    meta_b = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    a = request.config.rootpath / "runs" / ARM_A_REF / "metadata.json"
    if not a.is_file():
        pytest.skip("Arm A reference not present")
    meta_a = json.loads(a.read_text(encoding="utf-8"))
    assert meta_b["config_hash"] == meta_a["config_hash"]
    assert meta_b["workspace"]["base_commit"] == meta_a["workspace"]["base_commit"]


def test_workers_never_communicated_directly(raw):
    """Every message to a worker came from the Coordinator."""
    for h in raw["summary"]["handoffs"]:
        if h["recipient"] in ("investigator", "implementer"):
            assert h["sender"] == "coordinator"


# --------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------


def test_telemetry_is_clean(rep):
    assert rep["unknown_cli_event_types"] == {}
    assert rep["unrecognized_system_subtypes"] == []
    assert rep["unknown_tool_calls"] == 0
    assert rep["unclassified_bash_acquisitions"] == 0
    assert rep["unparsable_stream_lines"] == 0
    assert rep["acquisition_coverage"]["acquisition_coverage"] == 1.0
    assert rep["low_observability"] is False
    assert rep["all_sessions_subscription_ok"] is True
    assert rep["arm_label_valid"] is True


# --------------------------------------------------------------------------
# The observation itself
# --------------------------------------------------------------------------


def test_investigator_read_all_five_files_and_modified_nothing(raw):
    inv = _acqs(raw, "investigator")
    reads = sorted(a.target_path.split("/workspace/")[-1]
                   for a in inv if a.acquisition_class == tools.STRUCTURED_READ)
    assert reads == [
        "tests/test_basic.py", "tinylib/__init__.py", "tinylib/palindrome.py",
        "tinylib/report.py", "tinylib/text.py",
    ]
    executed_mutations = [
        a for a in inv
        if not a.permission_denied and a.acquisition_class in (
            tools.STRUCTURED_EDIT, tools.BASH_WORKSPACE_MANAGEMENT, tools.BASH_BUILD)
    ]
    assert executed_mutations == []


def test_exactly_two_primed_inter_agent_reacquisitions(dup):
    assert dup.inter_agent_duplicate_acquisitions == 2
    assert dup.primed_reacquisitions == 2
    assert dup.unprimed_overlapping_discoveries == 0
    assert dup.priming_undetermined == 0
    assert dup.intra_agent_repeat_acquisitions == 0
    assert dup.concurrency_excluded_pairs == 0
    targets = sorted(f["consumer_target"].split("/workspace/")[-1] for f in dup.findings)
    assert targets == ["tests/test_basic.py", "tinylib/palindrome.py"]
    for f in dup.findings:
        assert f["overlap_kind"] == metrics.IDENTICAL
        assert f["producer_agent"] == "investigator"
        assert f["consumer_agent"] == "implementer"


def test_primed_reacquisition_physical_amount(dup):
    ob = dup.oracle_upper_bound
    assert ob["duplicate_tool_calls"] == 2
    assert ob["duplicate_chars"] == 1694
    assert ob["duplicate_content_chunks_shingles"] == 36


def test_every_reacquired_file_was_then_edited(raw):
    """Context for interpreting the reacquisitions: Claude Code's Edit tool
    refuses a file that has not been Read in the current session ("File has not
    been read yet. Read it first before writing to it."). Both re-read files were
    edited next, so these reads were edit preconditions, not only exploration."""
    impl = _acqs(raw, "implementer")
    reads = [a for a in impl if a.acquisition_class == tools.STRUCTURED_READ]
    edits = [a for a in impl if a.acquisition_class == tools.STRUCTURED_EDIT]
    read_paths = {a.target_path for a in reads}
    edit_paths = {a.target_path for a in edits}
    assert read_paths == edit_paths
    for e in edits:
        r = next(a for a in reads if a.target_path == e.target_path)
        assert r.end_line < e.start_line


def test_implementer_ran_the_suite_and_saw_it_pass(raw):
    impl = next(sa for sa in raw["sessions"] if sa.invocation["role"] == "implementer")
    pytest_calls = [
        c for c in impl.parsed.tool_calls
        if c.name == "Bash" and "python -m pytest" in (c.input.get("command") or "")
    ]
    assert len(pytest_calls) == 1
    assert pytest_calls[0].tool_use_id not in impl.parsed.denied_tool_use_ids
    assert "7 passed" in (pytest_calls[0].result_text or "")


def test_no_permission_denials_and_verifier_passed(raw, rep):
    assert rep["permission_denied_calls"] == 0
    assert raw["verification"]["solved"] is True
    assert "13 passed" in raw["verification"]["stdout"]
