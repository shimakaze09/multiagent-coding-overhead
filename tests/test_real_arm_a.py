"""Regression tests against the FIRST REAL authenticated Arm A run.

Fixture: tests/fixtures/real_stream_arm_a_2.1.260.jsonl, captured verbatim from
run 20260910T122445Z_palindrome_punctuation_A_r1 (Claude Code 2.1.260, Claude
subscription, sonnet). Every assertion here is grounded in that captured output,
not in an assumption about the schema.

The run itself is NOT a research result. Its value here is that it exposed four
real defects, each pinned by a test below:

  1. `rate_limit_event` was an unknown CLI event type.
  2. Two Bash calls were PERMISSION-DENIED - the actions never ran - yet one was
     counted as a test run and one as an opaque acquisition.
  3. A trailing `| sort` made a `find` unclassifiable, depressing coverage.
  4. `input_tokens` (14) was reported next to cache figures (126874 / 10369) in a
     way that invited reading it as context length.
"""

from __future__ import annotations

import json

import pytest

from harness import telemetry, tools

FIXTURE = "real_stream_arm_a_2.1.260.jsonl"


@pytest.fixture(scope="module")
def real_lines(request):
    root = request.config.rootpath
    return (root / "tests" / "fixtures" / FIXTURE).read_text(encoding="utf-8").splitlines()


@pytest.fixture(scope="module")
def parsed(real_lines):
    return telemetry.parse_stream(real_lines)


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


# --------------------------------------------------------------------------
# The stream parses cleanly and completely
# --------------------------------------------------------------------------


def test_the_real_run_succeeded(parsed, summary):
    assert parsed.result is not None
    assert summary["is_error"] is False
    assert summary["stop_reason"] == "end_turn"
    assert summary["terminal_reason"] == "completed"
    assert summary["api_key_source"] == "none", "subscription OAuth, not an API key"
    assert summary["cli_version"] == "2.1.260"


def test_no_unknown_cli_event_types_remain(summary):
    """Defect 1: rate_limit_event used to appear here."""
    assert summary["unknown_event_types"] == {}
    assert summary["unrecognized_system_subtypes"] == []
    assert summary["unparsable_lines"] == 0


def test_every_system_subtype_in_the_real_run_is_recognized(summary):
    assert summary["system_subtype_counts"] == {
        "init": 1,
        "thinking_tokens": 2,
        "permission_denied": 2,
    }
    for subtype in summary["system_subtype_counts"]:
        assert subtype in telemetry.KNOWN_SYSTEM_SUBTYPES


def test_all_tool_calls_completed(summary):
    assert summary["tool_calls"] == 10
    assert summary["tool_calls_incomplete"] == 0


def test_no_internal_subagent_fanout(summary):
    assert summary["subagent_spawned"] == 0


# --------------------------------------------------------------------------
# rate_limit_event
# --------------------------------------------------------------------------


def test_rate_limit_event_is_parsed_with_its_real_fields(parsed):
    assert len(parsed.rate_limit_events) == 1
    e = parsed.rate_limit_events[0]
    assert e.status == "allowed_warning"
    assert e.rate_limit_type == "seven_day"
    assert e.utilization == 0.85
    assert e.is_using_overage is False
    assert e.surpassed_threshold == 0.75
    assert e.resets_at_epoch == 1789128000
    assert set(e.windows) == {"five_hour", "seven_day"}
    assert e.windows["seven_day"]["utilization"] == 0.85


def test_rate_limit_event_is_not_treated_as_an_error(parsed, summary):
    """The run succeeded, so the event's mere presence cannot mean failure."""
    e = parsed.rate_limit_events[0]
    assert e.indicates_execution_failure is False
    assert e.status_recognized is True
    assert summary["is_error"] is False
    assert summary["rate_limit"]["indicates_execution_failure"] is False
    assert parsed.errors == []


def test_a_blocking_rate_limit_status_would_be_flagged():
    """The permissive reading must not extend to a genuinely blocked request."""
    ps = telemetry.parse_stream(
        [
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"},
                }
            )
        ]
    )
    assert ps.rate_limit_events[0].indicates_execution_failure is True


def test_an_unrecognized_rate_limit_status_is_reported_not_assumed():
    ps = telemetry.parse_stream(
        [json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "brand_new"}})]
    )
    e = ps.rate_limit_events[0]
    assert e.status_recognized is False
    assert e.indicates_execution_failure is False
    assert telemetry.rate_limit_report(ps)["any_status_unrecognized"] is True


def test_rate_limit_raw_event_is_preserved_whole(parsed):
    raw = parsed.rate_limit_events[0].raw
    assert raw["type"] == "rate_limit_event"
    assert "rate_limit_info" in raw and "uuid" in raw and "session_id" in raw


# --------------------------------------------------------------------------
# Permission denials
# --------------------------------------------------------------------------


def test_both_permission_denials_are_captured(parsed, summary):
    assert len(parsed.permission_denials) == 2
    assert summary["permission_denied_count"] == 2
    for d in parsed.permission_denials:
        assert d.tool_name == "Bash"
        assert d.decision_reason_type == "asyncAgent"
        assert "no approval surface" in (d.decision_reason or "")
        assert d.tool_use_id


def test_denials_are_corroborated_by_the_result_event(parsed):
    from_events = {d.tool_use_id for d in parsed.permission_denials}
    from_result = {
        e["tool_use_id"] for e in (parsed.result or {}).get("permission_denials") or []
    }
    assert from_events == from_result
    assert parsed.denied_tool_use_ids == from_events


def test_denied_calls_are_not_counted_as_work_performed(acquisitions):
    """Defect 2. Claude Code says "The action was NOT performed"."""
    denied = [a for a in acquisitions if a.permission_denied]
    assert len(denied) == 2
    for a in denied:
        assert a.acquisition_class == tools.TOOL_DENIED
        assert a.is_acquisition is False
        assert a.is_acquisition_candidate is False

    real_test_runs = [
        a
        for a in acquisitions
        if a.acquisition_class == tools.BASH_VERIFICATION and not a.permission_denied
    ]
    assert real_test_runs == [], (
        "the agent was blocked from running the tests, so no test run happened"
    )


def test_denied_calls_record_what_they_would_have_been(acquisitions):
    attempted = sorted(
        a.attempted_class for a in acquisitions if a.permission_denied
    )
    assert attempted == [tools.BASH_VERIFICATION, tools.BASH_VERIFICATION]


# --------------------------------------------------------------------------
# The four real Bash commands
# --------------------------------------------------------------------------


def test_the_four_real_bash_commands_are_all_classified(acquisitions):
    bash = [a for a in acquisitions if a.tool_name == "Bash"]
    assert len(bash) == 4

    by_line = {a.start_line: a for a in bash}
    assert sorted(by_line) == [4, 6, 23, 27]

    # #1  find ... -iname "*tinylib*" ... | head -50      -> structure discovery
    assert "find" in by_line[4].command
    assert by_line[4].acquisition_class == tools.BASH_DIRECTORY_LISTING
    assert by_line[4].is_acquisition_candidate

    # #2  cd "<ws>" && find . -type f ... | sort           -> structure discovery
    #     Defect 3: the trailing `sort` used to make this unclassifiable.
    assert by_line[6].command.endswith("| sort")
    assert by_line[6].acquisition_class == tools.BASH_DIRECTORY_LISTING
    assert by_line[6].bash_segment_classes == [
        tools.BASH_NON_ACQUISITION,
        tools.BASH_DIRECTORY_LISTING,
        tools.NEUTRAL,
    ]

    # #3 and #4  pytest invocations, both DENIED
    assert by_line[23].acquisition_class == tools.TOOL_DENIED
    assert by_line[27].acquisition_class == tools.TOOL_DENIED

    assert not any(a.acquisition_class == tools.BASH_UNKNOWN for a in bash), (
        "no Bash command in this run should be left unclassified"
    )


def test_the_two_find_commands_really_did_acquire_repository_structure(acquisitions):
    listings = [
        a for a in acquisitions if a.acquisition_class == tools.BASH_DIRECTORY_LISTING
    ]
    assert len(listings) == 2
    for a in listings:
        assert a.result_chars > 0
        assert a.result_sha, "returned content must have an identity"

    by_line = {a.start_line: a for a in listings}
    # The file listing yields extractable paths.
    assert any("tinylib/text.py" in p for p in by_line[6].result_paths)
    # The first `find` returned a single extension-less directory path
    # ("<ws>/tinylib"), which the path-extraction heuristic does not match. The
    # acquisition is still fully recorded by content hash; only the convenience
    # path list is empty. Documented limitation, not a silent gap.
    assert by_line[4].result_paths == []
    assert by_line[4].result_sha


def test_the_five_reads_are_the_five_distinct_project_files(acquisitions):
    reads = [a for a in acquisitions if a.acquisition_class == tools.STRUCTURED_READ]
    assert len(reads) == 5
    names = sorted((a.target_path or "").rsplit("/", 1)[-1] for a in reads)
    assert names == [
        "__init__.py",
        "palindrome.py",
        "report.py",
        "test_basic.py",
        "text.py",
    ]
    assert len({a.result_sha for a in reads}) == 5, "five distinct file contents"


def test_the_single_edit_is_recorded_as_non_acquisition(acquisitions):
    edits = [a for a in acquisitions if a.acquisition_class == tools.STRUCTURED_EDIT]
    assert len(edits) == 1
    assert edits[0].is_acquisition_candidate is False


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


def test_acquisition_coverage_passes_the_pregistered_gate(acquisitions):
    cov = tools.coverage(acquisitions, minimum=0.90)
    assert cov.acquisition_classified == 7   # 5 Read + 2 directory listings
    assert cov.acquisition_unknown == 0
    assert cov.acquisition_candidates == 7
    assert cov.non_acquisition_calls == 1    # the Edit
    assert cov.denied_calls == 2
    assert cov.acquisition_coverage == 1.0
    assert cov.meets_minimum is True
    assert cov.unknown_tool == 0


def test_the_coverage_change_is_not_merely_a_denial_loophole(acquisitions):
    """Even counting the denied calls as if they had run, coverage still passes:
    both were pytest invocations, i.e. verification, not acquisition. The fix to
    the metric therefore does not depend on the denial exclusion."""
    as_if_executed = [
        tools.classify_tool_call(
            tool_use_id=a.tool_use_id,
            tool_name=a.tool_name,
            tool_input={"command": a.command} if a.command else {"file_path": a.target_path},
            result_text="x",
            agent_id=a.agent_id,
            session_key=a.session_key,
            start_ts=a.start_ts,
            end_ts=a.end_ts,
            start_line=a.start_line,
            end_line=a.end_line,
            is_error=False,
            permission_denied=False,
        )
        for a in acquisitions
    ]
    cov = tools.coverage(as_if_executed, minimum=0.90)
    assert cov.acquisition_candidates == 7
    assert cov.acquisition_coverage == 1.0
    assert cov.bash_verification == 2


# --------------------------------------------------------------------------
# Turn accounting
# --------------------------------------------------------------------------


def test_turn_quantities_for_this_run(parsed, summary):
    """This run's numbers. NOTE: `stream_events - thinking_only == num_turns`
    holds here (14-3=11) but is a COINCIDENCE - the later allowlist run has
    11-2=9 against num_turns=8. See test_real_arm_a_allowlist.py."""
    assert summary["assistant_stream_events"] == 14
    assert summary["thinking_only_stream_events"] == 3
    assert summary["api_assistant_messages"] == 7
    assert summary["tool_use_blocks"] == 10
    assert summary["cli_reported_num_turns"]["value"] == 11
    # the relationship that holds on every real run observed so far
    assert summary["cli_reported_num_turns"]["value"] == summary["tool_use_blocks"] + 1


def test_five_parallel_reads_shared_one_api_message(parsed):
    """The five file Reads were issued together, not sequentially. Before the
    concurrency-group fix they were ordered by raw stream line and would have
    been treated as sequential."""
    reads = [c for c in parsed.tool_calls if c.name == "Read"]
    assert len(reads) == 5
    assert len({c.api_message_id for c in reads}) == 1
    assert len({c.start_line for c in reads}) == 5
    assert len({c.concurrency_group_line for c in reads}) == 1


def test_turn_fields_are_named_so_they_cannot_be_confused(summary):
    assert "assistant_stream_events" in summary
    assert "cli_reported_num_turns" in summary
    assert "api_assistant_messages" in summary
    assert "tool_use_blocks" in summary
    assert "turn_accounting_note" in summary
    note = summary["turn_accounting_note"].lower()
    assert "none interchangeable" in note
    assert "not a specification" in note


def test_a_standalone_thinking_event_shares_usage_with_the_next_event(real_lines):
    """Evidence for the reconciliation: consecutive thinking/tool_use assistant
    events carry the SAME message usage, i.e. one API message, two stream events.
    This is also why per-message usage must not be summed."""
    evs = [json.loads(l) for l in real_lines]
    thinking = evs[2]
    tool_use = evs[3]
    assert [b["type"] for b in thinking["message"]["content"]] == ["thinking"]
    assert [b["type"] for b in tool_use["message"]["content"]] == ["tool_use"]
    assert thinking["message"]["usage"] == tool_use["message"]["usage"]


def test_usage_is_taken_from_the_result_event_not_summed(parsed):
    agg = telemetry.aggregate_usage(parsed)
    assert agg["aggregation"] == "cli_result_event"


# --------------------------------------------------------------------------
# Token semantics
# --------------------------------------------------------------------------


def test_real_token_values_are_preserved_exactly(summary):
    u = summary["usage"]
    assert u["input_tokens"]["value"] == 14
    assert u["cache_read_tokens"]["value"] == 126874
    assert u["cache_write_tokens"]["value"] == 10369
    assert u["output_tokens"]["value"] == 2223
    assert u["thinking_tokens"]["value"] == 123
    for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"):
        assert u[key]["availability"] == telemetry.REPORTED
        assert u[key]["source"] == telemetry.SOURCE_STREAM


def test_input_tokens_is_not_context_length(summary):
    """Defect 4. 14 uncached input tokens alongside ~137k of submitted context."""
    u = summary["usage"]
    assert u["total_input_tokens"]["value"] == 14 + 126874 + 10369 == 137257
    assert u["input_tokens"]["value"] < u["total_input_tokens"]["value"] / 1000
    sem = u["semantics"]
    assert "NOT the prompt or context length" in sem["input_tokens"]
    assert "submitted context volume" in sem["total_input_tokens"]


def test_cache_telemetry_is_available_and_split_by_ttl(summary):
    u = summary["usage"]
    assert u["cache_ephemeral_1h_tokens"]["value"] == 10369
    assert u["cache_ephemeral_5m_tokens"]["value"] == 0
    assert u["cache_ephemeral_5m_tokens"]["availability"] == telemetry.REPORTED


def test_result_usage_covers_only_the_main_model(summary):
    """A second model was used. result.usage omits it; modelUsage does not."""
    mu = summary["model_usage_totals"]
    assert mu["availability"] == telemetry.REPORTED
    assert mu["model_count"] == 2
    assert "claude-haiku-4-5-20251001" in mu["models"]
    assert "claude-sonnet-5" in mu["models"]

    main = mu["models"]["claude-sonnet-5"]
    aux = mu["models"]["claude-haiku-4-5-20251001"]
    assert main["input_tokens"] == summary["usage"]["input_tokens"]["value"] == 14
    assert aux["input_tokens"] == 1104, "invisible in result.usage"
    assert mu["all_models_input_tokens"]["value"] == 14 + 1104
    assert "main model only" in telemetry.TOKEN_SEMANTICS["scope"].lower()


def test_reported_cost_covers_both_models_and_is_labelled_api_equivalent(summary):
    cost = summary["cost"]
    assert cost["cli_reported_cost_usd"]["value"] == pytest.approx(0.0903028)
    per_model = sum(
        m["cost_usd"] for m in summary["model_usage_totals"]["models"].values()
    )
    assert per_model == pytest.approx(0.0903028)
    assert cost["cli_cost_interpretation"] == "api_equivalent_not_amount_paid"
    assert cost["subscription_execution"] is True


# --------------------------------------------------------------------------
# Re-analysis of the stored run (no Claude call)
# --------------------------------------------------------------------------

REAL_RUN_ID = "20260910T122445Z_palindrome_punctuation_A_r1"


@pytest.fixture(scope="module")
def real_run_dir(request):
    d = request.config.rootpath / "runs" / REAL_RUN_ID
    if not (d / "metadata.json").is_file():
        pytest.skip(f"real run {REAL_RUN_ID} not present in runs/")
    return d


def test_real_run_report_passes_the_observability_gate(real_run_dir):
    from analysis import report as report_mod

    r = report_mod.run_report(real_run_dir)
    assert r["solved"] is True
    assert r["acquisition_coverage"]["acquisition_coverage"] == 1.0
    assert r["low_observability"] is False
    assert r["unknown_cli_event_types"] == {}
    assert r["unclassified_bash_acquisitions"] == 0
    assert r["permission_denied_calls"] == 2
    assert r["test_runs"] == 0, "both pytest calls were denied"
    assert r["token_telemetry"]["reported_total_input_tokens"] == 137257
    assert r["cli_reported_num_turns"] == [11]
    assert r["assistant_stream_events"] == 14


def test_real_run_arm_a_has_no_inter_agent_duplication_by_construction(real_run_dir):
    """Arm A has one logical agent, so this is a definition, not a finding.
    No conclusion about coordination redundancy exists until Arm B runs."""
    from analysis import report as report_mod

    r = report_mod.run_report(real_run_dir)
    assert r["claude_sessions"] == 1
    assert r["logical_agents"] == 1
    assert r["inter_agent_overlapping_acquisitions"] == 0
    assert r["primed_reacquisitions"] == 0
    assert r["oracle_upper_bound"]["duplicate_tool_calls"] == 0


def test_real_run_reanalysis_records_which_versions_produced_it(real_run_dir):
    from analysis import report as report_mod

    r = report_mod.run_report(real_run_dir)
    assert r["parser_version"] == 3, "the raw run was written by parser v3"
    assert r["reanalysis_parser_version"] == telemetry.PARSER_VERSION
    assert r["reanalysis_coverage_formula_version"] == tools.COVERAGE_FORMULA_VERSION
    assert r["reanalysis_parser_version"] != r["parser_version"]


def test_real_run_db_is_rebuildable_and_deterministic(real_run_dir, tmp_path):
    from analysis import ingest

    a = tmp_path / "a.sqlite3"
    b = tmp_path / "b.sqlite3"
    assert ingest.build(a, [real_run_dir]) == [REAL_RUN_ID]
    a.unlink()
    assert ingest.build(a, [real_run_dir]) == [REAL_RUN_ID]
    ingest.build(b, [real_run_dir])

    import sqlite3

    def snap(p):
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        try:
            return {
                t: sorted(
                    json.dumps({k: r[k] for k in r.keys() if not k.endswith("_pk")},
                               sort_keys=True, default=str)
                    for r in conn.execute(f"SELECT * FROM {t}")
                )
                for t in ("runs", "sessions", "tool_calls", "bash_classifications",
                          "rate_limit_events", "permission_denials", "model_usage")
            }
        finally:
            conn.close()

    assert snap(a) == snap(b)


def test_real_run_trace_uses_recomputed_labels_not_stale_ones(real_run_dir):
    """events.jsonl is raw and was written by the old classifier. The trace must
    relabel from the raw CLI stream, or it would still call a denied call a test
    run."""
    from analysis import report as report_mod

    trace = report_mod.human_trace(real_run_dir)
    assert "DENIED" in trace
    assert "the action was NOT performed" in trace
    assert "would have been: bash_verification" in trace
    assert "Bash/list" in trace, "the two find commands are directory listings"
    assert "recomputed by parser v" in trace
    assert "quota telemetry" in trace
    assert "utilization=0.85" in trace
    assert "assistant_stream_events=14" in trace and "cli_num_turns=11" in trace
    assert "POTENTIAL REACQUISITION" not in trace, "single agent"


def test_raw_artifacts_are_not_modified_by_re_analysis(real_run_dir):
    """Re-analysis must be read-only over the raw run."""
    import hashlib

    raw_files = [
        real_run_dir / "sessions" / "01_solo" / "claude_stdout.jsonl",
        real_run_dir / "sessions" / "01_solo" / "invocation.json",
        real_run_dir / "sessions" / "01_solo" / "result.json",
        real_run_dir / "events.jsonl",
        real_run_dir / "metadata.json",
    ]
    before = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in raw_files}

    from analysis import ingest, report as report_mod

    report_mod.run_report(real_run_dir)
    report_mod.human_trace(real_run_dir)
    ingest.run_acquisitions(real_run_dir)

    after = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in raw_files}
    assert before == after
