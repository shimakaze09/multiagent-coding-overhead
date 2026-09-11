"""Stage 0 reporting: per-run metrics, arm comparison, human-readable trace.

Every number either comes from observed telemetry or is explicitly marked
unavailable. Nothing is filled in with fake precision.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Optional, Sequence

from analysis import handoff, ingest, isolation, metrics
from harness import events as ev, telemetry, tools

NA = "N/A - not exposed by subscription CLI telemetry"


def _run_cli_version(raw: dict) -> Optional[str]:
    """The Claude Code version every session reported, or None if they differ.
    Used to decide whether the Edit read-before-write rule is verified."""
    versions = {sa.summary.get("cli_version") for sa in raw["sessions"]} - {None}
    return versions.pop() if len(versions) == 1 else None


def _fmt_measure(m: Optional[dict]) -> str:
    if not isinstance(m, dict):
        return NA
    if m.get("availability") == telemetry.REPORTED and m.get("value") is not None:
        v = m["value"]
        return f"{v:g}" if isinstance(v, float) else str(v)
    if m.get("availability") == telemetry.ESTIMATED:
        return f"{m.get('value')} (estimated)"
    return NA


# --------------------------------------------------------------------------
# Per-run report
# --------------------------------------------------------------------------


def run_report(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    raw = ingest.load_run(run_dir)
    if raw is None:
        raise FileNotFoundError(f"not a run directory: {run_dir}")

    meta = raw["metadata"]
    summary = raw["summary"]
    sessions = raw["sessions"]

    acqs = ingest.run_acquisitions(run_dir)
    msgs = ingest.run_messages(run_dir)
    dup = metrics.analyze(acqs, msgs, cli_version=_run_cli_version(raw))

    raw_acq_objs = [
        a for sa in sessions for a in ingest.acquisitions_for_session(sa)
    ]

    repo_texts, repo_avail = handoff.repository_texts(run_dir, meta)
    communication = handoff.analyze_loaded(
        raw,
        {sa.session_key: ingest.acquisitions_for_session(sa) for sa in sessions},
        repo_texts,
        repo_avail,
    )

    isolation_check = isolation.check_run(raw)
    per_agent = per_agent_acquisition(raw)
    task_meta = meta.get("task") or {}
    supporting_paths = list(task_meta.get("supporting_paths") or [])
    focal_paths = list(task_meta.get("expected_modified_paths") or [])
    resolved_models = sorted({sa.summary.get("model") for sa in sessions} - {None})
    session_cli_versions = sorted({sa.summary.get("cli_version") for sa in sessions} - {None})
    api_equivalent_cost = sum(
        (((sa.summary.get("cost") or {}).get("cli_reported_cost_usd") or {}).get("value") or 0)
        for sa in sessions
    )
    cov = tools.coverage(
        raw_acq_objs, minimum=(meta.get("config") or {}).get("min_acquisition_coverage", 0.90)
    )

    tool_names = Counter(a.tool_name for a in raw_acq_objs)
    classes = Counter(a.acquisition_class for a in raw_acq_objs)

    assistant_stream_events = sum(
        int(sa.summary.get("assistant_stream_events") or 0) for sa in sessions
    )
    thinking_only = sum(
        int(sa.summary.get("thinking_only_stream_events") or 0) for sa in sessions
    )
    cli_num_turns = [
        (sa.summary.get("cli_reported_num_turns") or {}).get("value") for sa in sessions
    ]
    api_messages = sum(int(sa.summary.get("api_assistant_messages") or 0) for sa in sessions)
    tool_use_blocks = sum(int(sa.summary.get("tool_use_blocks") or 0) for sa in sessions)
    wall = sum(float(sa.exit.get("wall_seconds") or 0.0) for sa in sessions)

    # token / cache telemetry, aggregated with availability preserved
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    avail: set[str] = set()
    for sa in sessions:
        u = sa.summary.get("usage", {})
        for key, field in (
            ("input", "input_tokens"),
            ("output", "output_tokens"),
            ("cache_read", "cache_read_tokens"),
            ("cache_write", "cache_write_tokens"),
        ):
            m = u.get(field) or {}
            if m.get("availability"):
                avail.add(m["availability"])
            if isinstance(m.get("value"), (int, float)):
                tokens[key] += m["value"]
    token_availability = ingest._fold_availability(avail)

    files_read = sorted({a.target_path for a in acqs if a.is_read and a.target_path})
    files_edited = sorted(
        {
            a.target_path
            for a in raw_acq_objs
            if a.acquisition_class == tools.STRUCTURED_EDIT and a.target_path
        }
    )

    report = {
        "run_id": meta["run_id"],
        "task_id": meta["task_id"],
        "task_category": meta.get("task_category"),
        "arm": meta["arm"],
        "repeat_id": meta.get("repeat_id"),
        "model": (meta.get("config") or {}).get("model"),
        "cli_version": (meta.get("cli") or {}).get("version"),
        # parser_version is what produced the RAW run (from metadata.json).
        # reanalysis_* is what produced the numbers in THIS report. They differ
        # after a parser/classifier fix is applied to an existing run.
        "parser_version": (meta.get("versions") or {}).get("parser_version"),
        "reanalysis_parser_version": telemetry.PARSER_VERSION,
        "reanalysis_coverage_formula_version": tools.COVERAGE_FORMULA_VERSION,
        # --- outcome
        "solved": summary.get("solved"),
        "verifier_exit_code": (raw["verification"] or {}).get("exit_code"),
        "judge": "deterministic_held_out_tests",
        "llm_judge_used": False,
        # --- resources
        "wall_seconds": round(wall, 2),
        "claude_sessions": len(sessions),
        "logical_agents": len({sa.invocation.get("agent_id") for sa in sessions}),
        # Three distinct quantities, named so they cannot be confused. See
        # telemetry.session_summary()["turn_accounting_note"].
        "assistant_stream_events": assistant_stream_events,
        "thinking_only_stream_events": thinking_only,
        "api_assistant_messages": api_messages,
        "tool_use_blocks": tool_use_blocks,
        "assistant_events_excluding_thinking_only": assistant_stream_events - thinking_only,
        "cli_reported_num_turns": cli_num_turns,
        "cli_reported_num_turns_total": sum(v for v in cli_num_turns if isinstance(v, int)),
        "turn_accounting_note": (
            "Four different quantities. assistant_stream_events counts Claude "
            "Code stream events (one per content block, so it over-counts model "
            "responses). api_assistant_messages counts distinct message.id "
            "values, i.e. actual model responses. tool_use_blocks counts "
            "individual tool calls. cli_reported_num_turns is Claude Code's own "
            "counter; on real runs observed so far it equals tool_use_blocks + 1, "
            "which is an empirical fit (n=3), not a specification."
        ),
        # --- tool activity
        "tool_calls_total": len(raw_acq_objs),
        "read_calls": tool_names.get("Read", 0),
        "grep_calls": tool_names.get("Grep", 0),
        "glob_calls": tool_names.get("Glob", 0),
        "bash_calls": sum(v for k, v in tool_names.items() if k.lower() in tools.BASH_TOOLS),
        "edit_calls": sum(
            v for k, v in tool_names.items() if k.lower() in tools.EDIT_TOOLS
        ),
        # Denied calls performed nothing, so they are never counted as work done.
        "test_runs": sum(
            1
            for a in raw_acq_objs
            if a.acquisition_class == tools.BASH_VERIFICATION and not a.permission_denied
        ),
        "tool_name_counts": dict(tool_names),
        "acquisition_class_counts": dict(classes),
        "bash_category_counts": {
            k: v
            for k, v in classes.items()
            if k.startswith("bash_") or k == tools.TOOL_DENIED
        },
        "permission_denied_calls": sum(1 for a in raw_acq_objs if a.permission_denied),
        "permission_denied_detail": [
            {
                "tool_name": a.tool_name,
                "tool_use_id": a.tool_use_id,
                "would_have_been": a.attempted_class,
                "command": a.command,
            }
            for a in raw_acq_objs
            if a.permission_denied
        ],
        # --- acquisition volume
        "files_read_unique": len(files_read),
        "files_read": files_read,
        "files_edited": files_edited,
        "unique_repository_regions_read": dup.unique_repository_regions,
        "unique_result_identities": dup.unique_result_shas,
        "total_acquired_chars": dup.total_acquired_chars,
        "total_acquired_bytes": dup.total_acquired_bytes,
        # --- duplication
        "inter_agent_overlapping_acquisitions": dup.inter_agent_duplicate_acquisitions,
        "intra_agent_repeat_acquisitions": dup.intra_agent_repeat_acquisitions,
        "globally_previously_available": dup.globally_previously_available,
        "potentially_avoidable_acquisitions": dup.potentially_avoidable_acquisitions,
        "primed_reacquisitions": dup.primed_reacquisitions,
        "unprimed_overlapping_discoveries": dup.unprimed_overlapping_discoveries,
        "priming_undetermined": dup.priming_undetermined,
        "repeated_searches_same_query_inter_agent": dup.repeated_searches_same_query_inter_agent,
        "repeated_search_results_inter_agent": dup.repeated_search_results_inter_agent,
        "duplicate_chars_inter_agent": dup.duplicate_chars_inter_agent,
        "duplicate_bytes_inter_agent": dup.duplicate_bytes_inter_agent,
        "duplicate_shingles_inter_agent": dup.duplicate_shingles_inter_agent,
        "concurrency_excluded_pairs": dup.concurrency_excluded_pairs,
        "oracle_upper_bound": dup.oracle_upper_bound,
        # --- reacquisition classification (amendment 5). The gross count above
        # is unchanged; these partition it.
        "reacquisition_classification": {
            "gross_primed_reacquisitions": dup.primed_reacquisitions,
            "edit_precondition_associated": dup.edit_precondition_associated,
            "verification_associated": dup.verification_associated,
            "discretionary_information_reacquisition": dup.discretionary_information_reacquisition,
            "unknown": dup.reacquisition_unknown,
            "subcategory_physical": dup.subcategory_physical,
            "discretionary_upper_bound": dup.discretionary_upper_bound,
            "edit_precondition_cli_version": dup.edit_precondition_cli_version,
            "edit_precondition_verified": dup.edit_precondition_verified,
        },
        # --- communication duplication (amendment 5)
        "communication": communication,
        # --- reported separately from SOLVED
        "visible_tests": summary.get("visible_tests"),
        "workspace_integrity": summary.get("workspace_integrity"),
        "solved_basis": summary.get("solved_basis", "held-out verifier exit code"),
        # --- identity and parity inputs (frozen before Pair 2)
        "config_hash": meta.get("config_hash"),
        "resolved_models": resolved_models,
        "session_cli_versions": session_cli_versions,
        "api_equivalent_cost_usd": round(api_equivalent_cost, 6),
        "analysis_versions": {
            "parser": telemetry.PARSER_VERSION,
            "coverage_formula": tools.COVERAGE_FORMULA_VERSION,
            "reacquisition_classifier": metrics.REACQUISITION_CLASSIFIER_VERSION,
            "handoff_metrics": handoff.HANDOFF_SCHEMA_VERSION,
            "isolation_check": isolation.ISOLATION_CHECK_VERSION,
        },
        "per_agent_acquisition": per_agent,
        # Pair-2 hypotheses H1 (supporting files) and H2 (the file being fixed)
        "supporting_file_reacquisition": file_breakdown(dup.findings, supporting_paths),
        "focal_file_reacquisition": file_breakdown(dup.findings, focal_paths),
        "isolation_check": isolation_check,
        "findings": dup.findings,
        # --- handoffs
        "handoff_count": len(summary.get("handoffs") or []),
        "handoff_total_chars": summary.get("handoff_total_chars", 0),
        "handoff_total_bytes": sum(
            (h.get("sizes") or {}).get("utf8_bytes", 0) for h in summary.get("handoffs") or []
        ),
        "handoff_total_estimated_tokens": summary.get("handoff_total_estimated_tokens", 0),
        "handoff_estimated_tokens_availability": telemetry.ESTIMATED,
        "handoffs": [
            {
                "index": h.get("index"),
                "sender": h.get("sender"),
                "recipient": h.get("recipient"),
                "label": h.get("label"),
                "sizes": h.get("sizes"),
            }
            for h in summary.get("handoffs") or []
        ],
        # --- observability
        "acquisition_coverage": cov.as_dict(),
        # kept under its historical name so before/after reports line up
        "unclassified_bash_acquisitions": cov.bash_unknown,
        "acquisition_classified": cov.acquisition_classified,
        "acquisition_unknown": cov.acquisition_unknown,
        "acquisition_candidates": cov.acquisition_candidates,
        "non_acquisition_calls": cov.non_acquisition_calls,
        "denied_calls": cov.denied_calls,
        "unknown_tool_calls": cov.unknown_tool,
        "unknown_cli_event_types": _merge_counters(
            [sa.summary.get("unknown_event_types") or {} for sa in sessions]
        ),
        "unparsable_stream_lines": sum(
            int(sa.summary.get("unparsable_lines") or 0) for sa in sessions
        ),
        "low_observability": (cov.meets_minimum is False),
        "unrecognized_system_subtypes": [
            x for sa in sessions for x in (sa.summary.get("unrecognized_system_subtypes") or [])
        ],
        "system_subtype_counts": _merge_counters(
            [sa.summary.get("system_subtype_counts") or {} for sa in sessions]
        ),
        "rate_limit_telemetry": [sa.summary.get("rate_limit") for sa in sessions],
        "context_residency_level": meta.get("context_residency_level"),
        "context_residency_note": meta.get("context_residency_note"),
        # --- token / cache / cost telemetry
        "token_telemetry": {
            "availability": token_availability,
            "reported_input_tokens": tokens["input"] if token_availability != telemetry.NOT_EXPOSED else None,
            "reported_output_tokens": tokens["output"] if token_availability != telemetry.NOT_EXPOSED else None,
            "reported_cache_read_tokens": tokens["cache_read"] if token_availability != telemetry.NOT_EXPOSED else None,
            "reported_cache_write_tokens": tokens["cache_write"] if token_availability != telemetry.NOT_EXPOSED else None,
            "source": telemetry.SOURCE_STREAM if token_availability != telemetry.NOT_EXPOSED else None,
            "reported_total_input_tokens": (
                tokens["input"] + tokens["cache_read"] + tokens["cache_write"]
            )
            if token_availability != telemetry.NOT_EXPOSED
            else None,
            "semantics": telemetry.TOKEN_SEMANTICS,
        },
        "model_usage_totals": [sa.summary.get("model_usage_totals") for sa in sessions],
        "cost_telemetry": {
            "subscription_execution": True,
            "api_charge": (summary.get("all_sessions_subscription_ok") and "false_expected") or "unknown",
            "cli_reported_cost_usd": [
                (sa.summary.get("cost") or {}).get("cli_reported_cost_usd") for sa in sessions
            ],
            "interpretation": "api_equivalent_not_amount_paid",
        },
        # --- integrity
        "all_sessions_subscription_ok": summary.get("all_sessions_subscription_ok"),
        "arm_label_valid": summary.get("arm_label_valid"),
        "arm_label_note": summary.get("arm_label_note"),
        "base_commit": (meta.get("workspace") or {}).get("base_commit"),
        "final_tree_hash": summary.get("final_tree_hash"),
        "diff_bytes": summary.get("diff_bytes"),
    }
    return report


def _merge_counters(dicts: Sequence[dict]) -> dict:
    c: Counter = Counter()
    for d in dicts:
        c.update(d or {})
    return dict(c)


# --------------------------------------------------------------------------
# Text rendering
# --------------------------------------------------------------------------


def render_run_report(r: dict) -> str:
    L: list[str] = []
    A = L.append
    A(f"=== Stage 0 run report: {r['run_id']} ===")
    A(f"task            : {r['task_id']}  ({r.get('task_category')})")
    A(f"arm             : {r['arm']}   repeat_id={r.get('repeat_id')}")
    A(f"model           : {r.get('model')}   claude code {r.get('cli_version')}")
    A(f"parser version  : raw run produced by v{r.get('parser_version')}; "
      f"re-analysed by v{r.get('reanalysis_parser_version')} "
      f"(coverage formula v{r.get('reanalysis_coverage_formula_version')})")
    A("")
    A(f"SOLVED          : {'YES' if r.get('solved') else 'NO'}"
      f"   (held-out deterministic tests, exit={r.get('verifier_exit_code')}, LLM judge: no)")
    A("")
    A("-- resources ------------------------------------------------------")
    A(f"wall time            : {r['wall_seconds']} s")
    A(f"claude sessions      : {r['claude_sessions']}   logical agents: {r['logical_agents']}")
    A("turn accounting (four different quantities - do not conflate):")
    A(f"  assistant stream events (1 per block)  : {r['assistant_stream_events']}")
    A(f"    of which thinking-only stream events : {r['thinking_only_stream_events']}")
    A(f"  API assistant messages (model replies) : {r['api_assistant_messages']}")
    A(f"  tool_use blocks                        : {r['tool_use_blocks']}")
    A(f"  Claude CLI num_turns (per session)     : {r['cli_reported_num_turns']}")
    A("")
    A("-- tool activity --------------------------------------------------")
    A(f"tool calls total : {r['tool_calls_total']}")
    A(f"  Read           : {r['read_calls']}")
    A(f"  Grep           : {r['grep_calls']}")
    A(f"  Glob           : {r['glob_calls']}")
    A(f"  Bash           : {r['bash_calls']}")
    A(f"  Edit/Write     : {r['edit_calls']}")
    A(f"  test runs      : {r['test_runs']}   (denied calls excluded)")
    if r.get("bash_category_counts"):
        A("bash categories (derived from the raw command; raw text preserved):")
        for k, v in sorted(r["bash_category_counts"].items()):
            A(f"    {k:<28} {v}")
    if r.get("permission_denied_calls"):
        A(f"PERMISSION-DENIED tool calls : {r['permission_denied_calls']}"
          "   (the CLI refused these; nothing ran, nothing was acquired)")
        for d in r["permission_denied_detail"]:
            A(f"    {d['tool_name']}  would_have_been={d['would_have_been']}")
            A(f"      {_clip(str(d.get('command') or ''), 150)}")
    A("")
    A("-- acquisition volume ---------------------------------------------")
    A(f"unique files read          : {r['files_read_unique']}")
    A(f"unique repository regions  : {r['unique_repository_regions_read']} (3-line shingles)")
    A(f"unique result identities   : {r['unique_result_identities']}")
    A(f"total acquired chars/bytes : {r['total_acquired_chars']} / {r['total_acquired_bytes']}")
    A(f"files edited               : {', '.join(r['files_edited']) or '(none)'}")
    A("")
    A("-- duplication (temporal availability: producer.end < consumer.start) --")
    A(f"globally previously available   : {r['globally_previously_available']}")
    A(f"  inter-agent duplicates        : {r['inter_agent_overlapping_acquisitions']}")
    A(f"  intra-agent repeats           : {r['intra_agent_repeat_acquisitions']}")
    A(f"potentially avoidable (inter)   : {r['potentially_avoidable_acquisitions']}")
    A(f"  primed reacquisitions         : {r['primed_reacquisitions']}")
    A(f"  unprimed overlapping discovery: {r['unprimed_overlapping_discoveries']}")
    A(f"  priming undetermined          : {r['priming_undetermined']}")
    A(f"repeated searches (same query)  : {r['repeated_searches_same_query_inter_agent']}")
    A(f"repeated search results         : {r['repeated_search_results_inter_agent']}")
    A(f"duplicate chars / bytes (inter) : {r['duplicate_chars_inter_agent']} / {r['duplicate_bytes_inter_agent']}")
    A(f"duplicate content chunks        : {r['duplicate_shingles_inter_agent']}")
    A(f"pairs excluded by concurrency   : {r['concurrency_excluded_pairs']}  (K(t): not yet available)")
    A("")
    ob = r["oracle_upper_bound"]
    A("-- oracle upper bound (mechanically removable, primed inter-agent only) --")
    A(f"unit                        : {ob.get('unit')}")
    A(f"primed repeated file reads  : {ob.get('primed_repeated_file_acquisitions')}")
    A(f"primed repeated searches    : {ob.get('primed_repeated_search_acquisitions')}")
    A(f"duplicate tool calls        : {ob.get('duplicate_tool_calls')}")
    A(f"duplicate content chunks    : {ob.get('duplicate_content_chunks_shingles')}")
    A(f"duplicate chars / bytes     : {ob.get('duplicate_chars')} / {ob.get('duplicate_bytes')}")
    A("")
    A("-- natural-language handoffs --------------------------------------")
    A(f"handoffs               : {r['handoff_count']}")
    A(f"total chars / bytes    : {r['handoff_total_chars']} / {r['handoff_total_bytes']}")
    A(f"total estimated_tokens : {r['handoff_total_estimated_tokens']}  "
      f"({r['handoff_estimated_tokens_availability']}; NOT provider-billed tokens)")
    for h in r["handoffs"]:
        s = h.get("sizes") or {}
        A(f"  [{h['index']}] {h['sender']} -> {h['recipient']:<13} {h['label']:<32} "
          f"{s.get('chars', 0):>6} chars  ~{s.get('estimated_tokens', 0):>5} est. tokens")
    A("")
    rc = r.get("reacquisition_classification") or {}
    comm = r.get("communication") or {}
    cs = comm.get("communication_summary") or {}
    hro = comm.get("handoff_reacquisition_overlap") or {}
    A("-- communication duplication --------------------------------------")
    if not comm.get("per_handoff"):
        A("no natural-language handoffs in this run")
    else:
        A(f"investigator acquired content      : {cs.get('investigator_acquired_chars')} chars, "
          f"{cs.get('investigator_acquired_chunks')} content chunks")
        A(f"investigator report size           : {cs.get('investigator_report_chars')} chars "
          f"(~{cs.get('investigator_report_estimated_tokens')} estimated tokens)")
        A(f"repository/tool chunks copied into report: "
          f"{cs.get('report_repository_chunks')} repository chunks; "
          f"{cs.get('report_chunks_copied_from_investigator_acquisitions')} chunks of the "
          f"investigator's own tool output "
          f"(~{cs.get('report_chars_copied_from_investigator_acquisitions_estimate')} chars)")
        A(f"handoff repository quote fraction  : report "
          f"{cs.get('report_repository_quote_fraction')}; all handoffs "
          f"{cs.get('all_handoffs_repository_quote_fraction')} "
          f"(quoted-chars estimate / handoff chars)")
        A("per handoff - chunks from repository / sender's own tool output / relayed:")
        for h in comm["per_handoff"]:
            A(f"  [{h['index']}] {h['sender']:>12} -> {h['recipient']:<12} {h['label']:<31}"
              f"{h['handoff_chars']:>6} ch  repo {h['repository_content_chunks_in_handoff']:>3}"
              f"  tool {h['tool_output_chunks_in_handoff']:>3}"
              f"  relay {h['relayed_chunks_in_handoff']:>3}"
              f"  quote {h['handoff_repository_quote_fraction']}")
        A(f"  (window {comm.get('window')}; repository source "
          f"{comm.get('repository_content_source')})")
    A("")
    A("-- reacquisition classification -----------------------------------")
    A(f"gross primed reacquisitions        : {rc.get('gross_primed_reacquisitions')}")
    phys = rc.get("subcategory_physical") or {}
    for key, label in (
        ("edit_precondition_associated", "edit-precondition-associated"),
        ("verification_associated", "verification-associated"),
        ("discretionary_information_reacquisition", "discretionary"),
        ("unknown", "unknown"),
    ):
        p = phys.get(key) or {}
        A(f"  {label:<33}: {rc.get(key)}   "
          f"({p.get('chars', 0)} chars, {p.get('content_chunks', 0)} chunks)")
    if rc.get("edit_precondition_verified"):
        A(f"  edit precondition rule           : verified for Claude Code "
          f"{rc.get('edit_precondition_cli_version')}")
    else:
        A(f"  edit precondition rule           : NOT verified for CLI "
          f"{rc.get('edit_precondition_cli_version')} - such reads are reported as unknown")
    du = rc.get("discretionary_upper_bound") or {}
    A(f"discretionary upper bound          : {du.get('tool_calls', 0)} tool calls, "
      f"{du.get('chars', 0)} chars")
    if hro.get("applicable"):
        A("handoff -> repository reacquisition overlap:")
        A(f"  chunks in investigator acquisition      : {hro['chunks_in_investigator_acquisition']}")
        A(f"  ... also copied into handoffs to impl.  : {hro['chunks_copied_into_handoffs_to_implementer']}")
        A(f"  ... also later reacquired by implementer: {hro['chunks_later_reacquired_by_implementer']}")
        A(f"  in all three                            : {hro['chunks_in_all_three']} "
          f"(~{hro['implementer_reacquired_chars_in_all_three_estimate']} chars of implementer tool output)")
        for t, v in (hro.get("by_target") or {}).items():
            A(f"    {t:<40} {v['chunks']} chunks, ~{v['chars_estimate']} chars")
    else:
        A("handoff -> repository reacquisition overlap: not applicable")
    A("")
    vt = r.get("visible_tests")
    wi = r.get("workspace_integrity") or {}
    A("-- visible tests and workspace integrity (NOT part of SOLVED) ------")
    A(f"SOLVED basis                       : {r.get('solved_basis')}")
    if vt is None:
        A("visible tests                      : not run by the harness (run predates this check)")
    else:
        A(f"visible tests                      : {'PASS' if vt.get('passed') else 'FAIL'} "
          f"(exit {vt.get('exit_code')})")
    if wi:
        A(f"changed paths                      : {[c['path'] for c in wi.get('changed_paths', [])]}")
        A(f"read-only (protected) files        : {len(wi.get('read_only_files') or [])}"
          f"   changed: {wi.get('protected_paths_changed') or []}")
        if wi.get("expected_modified_paths"):
            A(f"changed outside expected scope     : {wi.get('changed_outside_expected_scope') or []}")
    iso = r.get("isolation_check") or {}
    if iso.get("available"):
        A(f"held-out isolation breach suspected: {iso.get('breach_suspected')}   "
          f"(outside-workspace accesses {len(iso.get('outside_workspace_accesses') or [])}, "
          f"held-out/reference exposures {len(iso.get('held_out_or_reference_exposure') or [])}; "
          f"detection only)")
    sf = r.get("supporting_file_reacquisition") or {}
    if sf.get("applicable"):
        A(f"supporting-file discretionary rereads: {sf.get('discretionary_rereads')} "
          f"(gross primed {sf.get('gross_primed')}, unprimed overlaps {sf.get('unprimed_overlaps')})")
    A("")
    A("-- observability --------------------------------------------------")
    cov = r["acquisition_coverage"]
    covv = cov.get("acquisition_coverage")
    A(f"structured Read operations      : {cov.get('structured_read')}")
    A(f"structured Grep/Glob operations : {cov.get('structured_search')}")
    A(f"bash reads                      : {cov.get('bash_read')}")
    A(f"bash searches                   : {cov.get('bash_search')}")
    A(f"bash directory listings         : {cov.get('bash_directory_listing')}")
    A(f"bash git inspections            : {cov.get('bash_git_inspection')}")
    A(f"bash unknown (opaque)           : {cov.get('bash_unknown')}")
    A(f"unknown tool events             : {cov.get('unknown_tool')}")
    A("")
    A(f"acquisition_classified          : {cov.get('acquisition_classified')}")
    A(f"acquisition_unknown             : {cov.get('acquisition_unknown')}")
    A(f"acquisition_candidates (denom)  : {cov.get('acquisition_candidates')}")
    A(f"non_acquisition (excluded)      : {cov.get('non_acquisition_calls')}"
      "   [edits, verification, build, workspace mgmt, metadata]")
    A(f"permission-denied (excluded)    : {cov.get('denied_calls')}"
      "   [nothing ran, so nothing could be acquired]")
    A(f"unknown CLI event types         : {r['unknown_cli_event_types'] or dict()}")
    A(f"unrecognized system subtypes    : {r.get('unrecognized_system_subtypes') or list()}")
    A(f"CLI system subtypes seen        : {r.get('system_subtype_counts') or dict()}")
    A(f"unparsable stream lines         : {r['unparsable_stream_lines']}")
    A(f"acquisition_coverage            : "
      f"{'n/a (no acquisition candidates)' if covv is None else f'{covv:.3f}'}"
      f"   minimum required {cov.get('minimum_required')}"
      f"   [formula v{cov.get('formula_version')}]")
    A(f"  formula: {cov.get('formula')}")
    for rl in r.get("rate_limit_telemetry") or []:
        if not rl or rl.get("availability") != telemetry.REPORTED:
            continue
        A(f"subscription quota telemetry    : status={rl.get('latest_status')} "
          f"type={rl.get('latest_rate_limit_type')} "
          f"utilization={rl.get('latest_utilization')} "
          f"overage={rl.get('is_using_overage')} "
          f"execution_failure={rl.get('indicates_execution_failure')}")
    if r.get("low_observability"):
        A("  !! LOW OBSERVABILITY: duplication conclusions from this run are not "
          "eligible for headline comparison.")
    A(f"context residency level         : {r.get('context_residency_level')} "
      f"({'reconstructed from session event history' if r.get('context_residency_level') == 2 else 'see note'})")
    A("")
    A("-- token / cache / cost telemetry ---------------------------------")
    t = r["token_telemetry"]
    if t["availability"] == telemetry.NOT_EXPOSED:
        A(f"reported input tokens      : {NA}")
        A(f"reported output tokens     : {NA}")
        A(f"reported cache tokens      : {NA}")
    else:
        A("reported input tokens      : UNCACHED INPUT ONLY - not context length")
        A(f"                             {t['reported_input_tokens']}  ({t['availability']})")
        A(f"reported cache read tokens : {t['reported_cache_read_tokens']}")
        A(f"reported cache write tokens: {t['reported_cache_write_tokens']}")
        A(f"total INPUT tokens         : {t['reported_total_input_tokens']}"
          "   <- submitted context volume (input + cache_read + cache_write)")
        A(f"reported output tokens     : {t['reported_output_tokens']}  ({t['availability']})")
        for mu in r.get("model_usage_totals") or []:
            if not mu or mu.get("availability") != telemetry.REPORTED:
                continue
            if (mu.get("model_count") or 0) > 1:
                A(f"NOTE: {mu['model_count']} models were used. result.usage covers the "
                  "main model only; per-model from modelUsage:")
                for name, u in (mu.get("models") or {}).items():
                    A(f"    {name:<32} in={u.get('input_tokens')} "
                      f"out={u.get('output_tokens')} cost_usd={u.get('cost_usd')}")
    c = r["cost_telemetry"]
    A(f"subscription_execution     : {c['subscription_execution']}")
    A(f"API_charge                 : {c['api_charge']}")
    A(f"cli reported cost (per session, api-equivalent, NOT amount paid):")
    for m in c["cli_reported_cost_usd"]:
        A(f"    {_fmt_measure(m)}")
    A("")
    A("-- integrity ------------------------------------------------------")
    A(f"all sessions on subscription : {r['all_sessions_subscription_ok']}")
    A(f"arm label valid              : {r['arm_label_valid']}  - {r.get('arm_label_note')}")
    A(f"base commit                  : {r.get('base_commit')}")
    A(f"final tree hash              : {r.get('final_tree_hash')}")
    A(f"diff bytes                   : {r.get('diff_bytes')}")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Arm comparison
# --------------------------------------------------------------------------


def comparison_report(a: dict, b: dict) -> dict:
    """Structured Arm B / Arm A comparison, for aggregating across the pilot."""
    structured = metrics.compare_arms(_as_metrics_shape(a), _as_metrics_shape(b))
    return {
        "task_id": a.get("task_id"),
        "arm_a_run_id": a.get("run_id"),
        "arm_b_run_id": b.get("run_id"),
        "solved": {"arm_a": a.get("solved"), "arm_b": b.get("solved")},
        "acquisition_overhead": structured,
        "eligible_for_headline": {
            "arm_a": not a.get("low_observability"),
            "arm_b": not b.get("low_observability"),
        },
        "arm_labels_valid": {
            "arm_a": a.get("arm_label_valid"),
            "arm_b": b.get("arm_label_valid"),
        },
        "units": "physical counts and bytes; not dollars",
    }


def render_comparison(a: dict, b: dict) -> str:
    structured = comparison_report(a, b)["acquisition_overhead"]
    L: list[str] = []
    A = L.append
    A("=== Stage 0 primary comparison: Arm B overhead over Arm A ===")
    A(f"task: {a.get('task_id')}")
    A("")
    A(f"{'metric':<34}{'Arm A':>12}{'Arm B':>12}{'delta':>12}{'B/A':>10}")
    rows = (
        ("solved", a.get("solved"), b.get("solved")),
        ("claude sessions", a.get("claude_sessions"), b.get("claude_sessions")),
        # (was "agent_turns_observed", a key renamed earlier; the row printed None)
        ("Claude CLI num_turns (sum)", a.get("cli_reported_num_turns_total"), b.get("cli_reported_num_turns_total")),
        ("model calls (API messages)", a.get("api_assistant_messages"), b.get("api_assistant_messages")),
        ("wall seconds", a.get("wall_seconds"), b.get("wall_seconds")),
        ("tool calls", a.get("tool_calls_total"), b.get("tool_calls_total")),
        ("Read calls", a.get("read_calls"), b.get("read_calls")),
        ("Grep calls", a.get("grep_calls"), b.get("grep_calls")),
        ("Glob calls", a.get("glob_calls"), b.get("glob_calls")),
        ("Bash calls", a.get("bash_calls"), b.get("bash_calls")),
        ("unique files read", a.get("files_read_unique"), b.get("files_read_unique")),
        ("unique repo regions", a.get("unique_repository_regions_read"), b.get("unique_repository_regions_read")),
        ("acquired chars", a.get("total_acquired_chars"), b.get("total_acquired_chars")),
        ("acquired bytes", a.get("total_acquired_bytes"), b.get("total_acquired_bytes")),
        ("handoff chars", a.get("handoff_total_chars"), b.get("handoff_total_chars")),
    )
    # Rows the structured comparison already computes are read from it, so the
    # printed table and the machine-readable output cannot disagree.
    structured_by_name = {
        "tool calls": "total_tool_calls",
        "Read calls": "reads",
        "acquired chars": "total_acquired_chars",
        "acquired bytes": "total_acquired_bytes",
        "unique repo regions": "unique_repository_regions",
    }
    for name, va, vb in rows:
        key = structured_by_name.get(name)
        if key and key in structured:
            cell = structured[key]
            va, vb, d, rt = cell["arm_a"], cell["arm_b"], cell["delta"], cell["ratio_b_over_a"]
        else:
            d = (vb - va) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
            rt = (
                round(vb / va, 3)
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va
                else None
            )
        A(f"{name:<34}{str(va):>12}{str(vb):>12}{str(d):>12}{str(rt):>10}")
    A("")
    A("-- coordination redundancy (Arm B only concept) --")
    A(f"inter-agent duplicate acquisitions : {b.get('inter_agent_overlapping_acquisitions')}")
    A(f"  of which primed reacquisition    : {b.get('primed_reacquisitions')}")
    A(f"  of which unprimed discovery      : {b.get('unprimed_overlapping_discoveries')}")
    A(f"Arm A intra-agent repeats (control): {a.get('intra_agent_repeat_acquisitions')}")
    A("")
    A("-- observability gate --")
    for label, r in (("Arm A", a), ("Arm B", b)):
        cov = (r.get("acquisition_coverage") or {}).get("acquisition_coverage")
        A(f"{label} acquisition_coverage: "
          f"{'n/a' if cov is None else f'{cov:.3f}'}"
          f"   eligible for headline comparison: {not r.get('low_observability')}")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Per-agent and per-file breakdowns (frozen before Pair 2)
# --------------------------------------------------------------------------

_FILE_READ_CLASSES = (tools.STRUCTURED_READ, tools.BASH_READ)


def per_agent_acquisition(raw: dict) -> dict:
    """Tool activity and repository acquisition per logical agent."""
    out: dict = {}
    for sa in raw["sessions"]:
        agent = sa.invocation.get("agent_id") or sa.session_key
        slot = out.setdefault(agent, {
            "role": sa.invocation.get("role"), "sessions": 0, "tool_calls": 0,
            "reads": 0, "searches_and_listings": 0, "bash_acquisitions": 0,
            "edits_executed": 0, "tests_executed": 0, "acquisitions_completed": 0,
            "acquired_chars": 0, "acquired_bytes": 0, "_regions": set(),
        })
        slot["sessions"] += 1
        for a in ingest.acquisitions_for_session(sa):
            slot["tool_calls"] += 1
            k = a.acquisition_class
            if k in _FILE_READ_CLASSES:
                slot["reads"] += 1
            if k in (tools.STRUCTURED_SEARCH, tools.BASH_SEARCH,
                     tools.BASH_DIRECTORY_LISTING, tools.BASH_GIT_INSPECTION):
                slot["searches_and_listings"] += 1
            if (a.tool_name or "").lower() in tools.BASH_TOOLS and k in tools.ACQUISITION_CLASSES:
                slot["bash_acquisitions"] += 1
            if k == tools.STRUCTURED_EDIT and not a.permission_denied and not a.is_error:
                slot["edits_executed"] += 1
            if k == tools.BASH_VERIFICATION and not a.permission_denied:
                slot["tests_executed"] += 1
            if a.is_acquisition and a.completed and not a.permission_denied:
                slot["acquisitions_completed"] += 1
                slot["acquired_chars"] += a.result_chars
                slot["acquired_bytes"] += a.result_bytes
                slot["_regions"].update(a.result_shingles)
    for slot in out.values():
        slot["unique_regions"] = len(slot.pop("_regions"))
    return out


def file_breakdown(findings: Sequence[dict], paths: Sequence[str]) -> dict:
    """Inter-agent reacquisition findings whose consumer is a FILE READ of one of
    `paths` (matched by path suffix), counted by priming and subcategory.
    Searches are not attributed to a file."""
    per = {
        p: {"gross_primed": 0, **{s: 0 for s in metrics.REACQ_SUBCATEGORIES},
            "unprimed_overlaps": 0, "priming_undetermined": 0,
            "discretionary_chars": 0, "discretionary_chunks": 0}
        for p in paths
    }
    for f in findings:
        if f.get("relation") != metrics.INTER_AGENT or f.get("consumer_class") not in _FILE_READ_CLASSES:
            continue
        p = next((q for q in paths if metrics._same_file(f.get("consumer_target"), q)), None)
        if p is None:
            continue
        slot = per[p]
        if f.get("priming") == metrics.PRIMED:
            slot["gross_primed"] += 1
            sub = f.get("reacquisition_subcategory")
            if sub in slot:
                slot[sub] += 1
            if sub == metrics.REACQ_DISCRETIONARY:
                slot["discretionary_chars"] += int(f.get("duplicate_chars") or 0)
                slot["discretionary_chunks"] += int(f.get("overlap_shingles") or 0)
        elif f.get("priming") == metrics.UNPRIMED:
            slot["unprimed_overlaps"] += 1
        else:
            slot["priming_undetermined"] += 1

    def total(key):
        return sum(v[key] for v in per.values())

    return {
        "applicable": bool(paths),
        "paths": list(paths),
        "per_file": per,
        "gross_primed": total("gross_primed"),
        "edit_precondition_associated": total(metrics.REACQ_EDIT_PRECONDITION),
        "verification_associated": total(metrics.REACQ_VERIFICATION),
        "discretionary_rereads": total(metrics.REACQ_DISCRETIONARY),
        "unknown": total(metrics.REACQ_UNKNOWN),
        "unprimed_overlaps": total("unprimed_overlaps"),
        "priming_undetermined": total("priming_undetermined"),
        "discretionary_chars": total("discretionary_chars"),
        "discretionary_chunks": total("discretionary_chunks"),
        "note": "inter-agent file reads only (Read tool or bash read)",
    }


# --------------------------------------------------------------------------
# The pre-registered primary output for a controlled A/B pair
# --------------------------------------------------------------------------

PAIR_SUMMARY_SECTIONS = (
    "-- parity",
    "-- Arm A vs Arm B",
    "-- Arm B coordination",
    "-- gross primed reacquisition",
    "-- supporting-file discretionary rereads (H1)",
    "-- file being fixed (H2)",
    "-- handoff repository-content duplication (H3)",
    "-- handoff_reacquisition_overlap (H4)",
    "-- observability and integrity",
)


def _all_model_input(r: dict) -> Optional[int]:
    vals = [
        ((mu or {}).get("all_models_input_tokens") or {}).get("value")
        for mu in r.get("model_usage_totals") or []
    ]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return int(sum(vals)) if vals else None


def render_pair_summary(a: dict, b: dict) -> str:
    """Frozen 2026-09-11, before Pair 2 (PREREGISTRATION "Pair-2 primary
    outputs"). Anything not shown here is exploratory and must be labelled so."""
    L: list[str] = []
    A = L.append

    def tok(r, k):
        return (r.get("token_telemetry") or {}).get(k)

    def integ(r, k):
        return (r.get("workspace_integrity") or {}).get(k)

    A(f"=== Stage 0 controlled pair: {a.get('task_id')} ===")
    A(f"Arm A run: {a.get('run_id')}")
    A(f"Arm B run: {b.get('run_id')}")
    A("")
    A("-- parity (all must PASS for the pair to be comparable) -----------")
    checks = (
        ("same task", a.get("task_id") == b.get("task_id")),
        ("same config_hash", bool(a.get("config_hash")) and a.get("config_hash") == b.get("config_hash")),
        ("same base commit", bool(a.get("base_commit")) and a.get("base_commit") == b.get("base_commit")),
        ("same Claude Code version",
         len(a.get("session_cli_versions") or []) == 1
         and a.get("session_cli_versions") == b.get("session_cli_versions")),
        ("same resolved main model",
         bool(a.get("resolved_models")) and a.get("resolved_models") == b.get("resolved_models")),
        ("same protected files", integ(a, "read_only_files") == integ(b, "read_only_files")),
    )
    for name, ok in checks:
        A(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    A(f"  config_hash {a.get('config_hash')} / {b.get('config_hash')}; "
      f"CLI {a.get('session_cli_versions')} / {b.get('session_cli_versions')}; "
      f"model {a.get('resolved_models')} / {b.get('resolved_models')}")
    A("")
    A("-- Arm A vs Arm B (descriptive; n=1 per arm) -----------------------")
    rows = (
        ("solved (held-out verifier)", a.get("solved"), b.get("solved")),
        ("Claude sessions", a.get("claude_sessions"), b.get("claude_sessions")),
        ("CLI turns (sum of num_turns)", a.get("cli_reported_num_turns_total"), b.get("cli_reported_num_turns_total")),
        ("model calls (API assistant messages)", a.get("api_assistant_messages"), b.get("api_assistant_messages")),
        ("tool calls", a.get("tool_calls_total"), b.get("tool_calls_total")),
        ("total input tokens (main model)", tok(a, "reported_total_input_tokens"), tok(b, "reported_total_input_tokens")),
        ("  uncached input", tok(a, "reported_input_tokens"), tok(b, "reported_input_tokens")),
        ("  cache read", tok(a, "reported_cache_read_tokens"), tok(b, "reported_cache_read_tokens")),
        ("  cache write", tok(a, "reported_cache_write_tokens"), tok(b, "reported_cache_write_tokens")),
        ("output tokens (main model)", tok(a, "reported_output_tokens"), tok(b, "reported_output_tokens")),
        # modelUsage inputTokens excludes cache, so this is uncached input only
        ("uncached input, all models (modelUsage)", _all_model_input(a), _all_model_input(b)),
        ("API-equivalent cost USD (not paid)", a.get("api_equivalent_cost_usd"), b.get("api_equivalent_cost_usd")),
        ("wall seconds (sum of sessions)", a.get("wall_seconds"), b.get("wall_seconds")),
        ("repository acquisition chars", a.get("total_acquired_chars"), b.get("total_acquired_chars")),
        ("repository acquisition bytes", a.get("total_acquired_bytes"), b.get("total_acquired_bytes")),
        ("unique repository regions", a.get("unique_repository_regions_read"), b.get("unique_repository_regions_read")),
        ("unique files read", a.get("files_read_unique"), b.get("files_read_unique")),
    )
    A(f"  {'':<40}{'Arm A':>14}{'Arm B':>14}")
    for name, va, vb in rows:
        A(f"  {name:<40}{str(va):>14}{str(vb):>14}")
    A("")
    A("-- Arm B coordination ----------------------------------------------")
    per = b.get("per_agent_acquisition") or {}
    for agent in ("investigator", "implementer"):
        s = per.get(agent)
        if not s:
            A(f"  {agent}: (no session)")
            continue
        A(f"  {agent:<13} tool calls {s['tool_calls']}, reads {s['reads']}, "
          f"searches/listings {s['searches_and_listings']}, bash acquisitions "
          f"{s['bash_acquisitions']}, edits {s['edits_executed']}, tests {s['tests_executed']}; "
          f"acquired {s['acquired_chars']} chars, {s['unique_regions']} regions")
    comm = b.get("communication") or {}
    cs = comm.get("communication_summary") or {}
    A(f"  handoffs: {len(comm.get('per_handoff') or [])}, total {cs.get('all_handoffs_chars')} chars; "
      f"investigator report {cs.get('investigator_report_chars')} chars")
    A("")
    rc = b.get("reacquisition_classification") or {}
    A("-- gross primed reacquisition (Arm B) ------------------------------")
    A(f"  gross primed reacquisitions      : {rc.get('gross_primed_reacquisitions')}")
    phys = rc.get("subcategory_physical") or {}
    for key, label in (
        ("edit_precondition_associated", "edit-precondition-associated"),
        ("verification_associated", "verification-associated"),
        ("discretionary_information_reacquisition", "discretionary"),
        ("unknown", "unknown"),
    ):
        p = phys.get(key) or {}
        A(f"    {label:<31}: {rc.get(key)}  ({p.get('chars', 0)} chars, {p.get('content_chunks', 0)} chunks)")
    A(f"  unprimed overlapping discoveries : {b.get('unprimed_overlapping_discoveries')}")
    A(f"  concurrency-excluded pairs       : {b.get('concurrency_excluded_pairs')}")
    A(f"  edit precondition verified for   : {rc.get('edit_precondition_cli_version')} "
      f"({'yes' if rc.get('edit_precondition_verified') else 'NO'})")
    A("")
    for title, key in (("-- supporting-file discretionary rereads (H1)", "supporting_file_reacquisition"),
                       ("-- file being fixed (H2)", "focal_file_reacquisition")):
        fb = b.get(key) or {}
        A(title + " " + "-" * max(0, 68 - len(title) - 1))
        if not fb.get("applicable"):
            A("  not applicable (task declares no such paths)")
        else:
            A(f"  total: gross primed {fb['gross_primed']}, edit-precondition "
              f"{fb['edit_precondition_associated']}, verification {fb['verification_associated']}, "
              f"discretionary {fb['discretionary_rereads']} ({fb['discretionary_chars']} chars), "
              f"unknown {fb['unknown']}, unprimed overlaps {fb['unprimed_overlaps']}")
            for path, s in fb["per_file"].items():
                A(f"    {path:<26} gross {s['gross_primed']}  edit {s['edit_precondition_associated']}  "
                  f"verif {s['verification_associated']}  disc {s['discretionary_information_reacquisition']}  "
                  f"unknown {s['unknown']}  unprimed {s['unprimed_overlaps']}")
        A("")
    A("-- handoff repository-content duplication (H3) ---------------------")
    A(f"  investigator acquired            : {cs.get('investigator_acquired_chars')} chars, "
      f"{cs.get('investigator_acquired_chunks')} chunks")
    A(f"  report repository chunks         : {cs.get('report_repository_chunks')} "
      f"(~{cs.get('report_chars_copied_from_investigator_acquisitions_estimate')} chars from its own tool output)")
    A(f"  quote fraction                   : report {cs.get('report_repository_quote_fraction')}, "
      f"all handoffs {cs.get('all_handoffs_repository_quote_fraction')}")
    A("")
    hro = comm.get("handoff_reacquisition_overlap") or {}
    A("-- handoff_reacquisition_overlap (H4) ------------------------------")
    if hro.get("applicable"):
        A(f"  investigator {hro['chunks_in_investigator_acquisition']} -> copied into handoffs "
          f"{hro['chunks_copied_into_handoffs_to_implementer']} -> re-acquired "
          f"{hro['chunks_later_reacquired_by_implementer']}; in all three {hro['chunks_in_all_three']} "
          f"(~{hro['implementer_reacquired_chars_in_all_three_estimate']} chars)")
        for t, v in (hro.get("by_target") or {}).items():
            A(f"    {t:<32} {v['chunks']} chunks, ~{v['chars_estimate']} chars")
    else:
        A("  not applicable")
    A("")
    A("-- observability and integrity -------------------------------------")
    for label, r in (("Arm A", a), ("Arm B", b)):
        cov = (r.get("acquisition_coverage") or {}).get("acquisition_coverage")
        iso = r.get("isolation_check") or {}
        vt = r.get("visible_tests")
        A(f"  {label}: coverage {('n/a' if cov is None else f'{cov:.3f}')}, "
          f"low_observability {r.get('low_observability')}, unknown CLI types "
          f"{r.get('unknown_cli_event_types') or {}}, unknown tools {r.get('unknown_tool_calls')}, "
          f"unclassified bash {r.get('unclassified_bash_acquisitions')}, unparsable lines "
          f"{r.get('unparsable_stream_lines')}")
        A(f"         isolation breach suspected {iso.get('breach_suspected')}, visible tests "
          f"{'n/a' if vt is None else ('PASS' if vt.get('passed') else 'FAIL')}, protected changed "
          f"{len(integ(r, 'protected_paths_changed') or [])}, outside expected scope "
          f"{integ(r, 'changed_outside_expected_scope') or []}, permission denials "
          f"{r.get('permission_denied_calls')}, subscription {r.get('all_sessions_subscription_ok')}, "
          f"arm label valid {r.get('arm_label_valid')}")
    A("")
    A("Anything not shown above is exploratory and must be labelled as such "
      "(PREREGISTRATION.md, Pair-2 primary outputs).")
    return "\n".join(L)


def _as_metrics_shape(r: dict) -> dict:
    """Map report keys onto the names metrics.compare_arms expects."""
    return {
        "total_tool_calls": r.get("tool_calls_total"),
        "total_acquisitions": (r.get("read_calls", 0) or 0)
        + (r.get("grep_calls", 0) or 0)
        + (r.get("glob_calls", 0) or 0),
        "reads": r.get("read_calls"),
        "searches": (r.get("grep_calls", 0) or 0) + (r.get("glob_calls", 0) or 0),
        "total_acquired_chars": r.get("total_acquired_chars"),
        "total_acquired_bytes": r.get("total_acquired_bytes"),
        "unique_repository_regions": r.get("unique_repository_regions_read"),
        "intra_agent_repeat_acquisitions": r.get("intra_agent_repeat_acquisitions"),
        "inter_agent_duplicate_acquisitions": r.get("inter_agent_overlapping_acquisitions"),
        "primed_reacquisitions": r.get("primed_reacquisitions"),
        "unprimed_overlapping_discoveries": r.get("unprimed_overlapping_discoveries"),
    }


# --------------------------------------------------------------------------
# Human-readable trace
# --------------------------------------------------------------------------

_ROLE_LABEL = {
    "solo": "Solo agent",
    "coordinator": "Coordinator",
    "investigator": "Investigator",
    "implementer": "Implementer",
}


def human_trace(run_dir: str | Path, *, max_message_chars: int = 700) -> str:
    """A readable narrative of one run, annotated with potential reacquisitions."""
    run_dir = Path(run_dir)
    raw = ingest.load_run(run_dir)
    if raw is None:
        raise FileNotFoundError(f"not a run directory: {run_dir}")

    events = sorted(raw["events"], key=lambda e: e.get("seq") or 0)
    acqs = ingest.run_acquisitions(run_dir)
    msgs = ingest.run_messages(run_dir)
    dup = metrics.analyze(acqs, msgs, cli_version=_run_cli_version(raw))
    findings_by_consumer = {f["consumer_tool_use_id"]: f for f in dup.findings}

    # events.jsonl is a RAW artifact: it was written during the run and embeds the
    # classifier version of that moment. It must never be rewritten. So the
    # narrative order comes from it, but every derived label is recomputed here
    # from the raw CLI stream with the CURRENT classifier - the same rule the
    # report and the SQLite index follow.
    current = {
        a.tool_use_id: a
        for sa in raw["sessions"]
        for a in ingest.acquisitions_for_session(sa)
    }
    denials = {
        d.tool_use_id: d
        for sa in raw["sessions"]
        for d in sa.parsed.permission_denials
    }
    rate_limits = [
        (sa.session_key, rl)
        for sa in raw["sessions"]
        for rl in sa.parsed.rate_limit_events
    ]

    L: list[str] = []
    A = L.append
    meta = raw["metadata"]
    A(f"################ TRACE {meta['run_id']} ################")
    A(f"task {meta['task_id']} | arm {meta['arm']} | model "
      f"{(meta.get('config') or {}).get('model')} | claude code {(meta.get('cli') or {}).get('version')}")
    A(f"base commit {(meta.get('workspace') or {}).get('base_commit')}")
    A(f"raw run written by parser v{(meta.get('versions') or {}).get('parser_version')}; "
      f"labels below recomputed by parser v{telemetry.PARSER_VERSION} / "
      f"coverage formula v{tools.COVERAGE_FORMULA_VERSION}")
    for session_key, rl in rate_limits:
        A(f"quota telemetry [{session_key}]: status={rl.status} "
          f"type={rl.rate_limit_type} utilization={rl.utilization} "
          f"overage={rl.is_using_overage} "
          f"execution_failure={rl.indicates_execution_failure}")
    A("")

    current_agent: Optional[str] = None

    for e in events:
        t = e.get("type")
        p = e.get("payload") or {}
        agent_id = e.get("agent_id")

        if t == ev.TASK_START:
            A("[USER]")
            A(_indent(_clip(p.get("statement", ""), max_message_chars)))
            A("")
            continue

        if t == ev.CLAUDE_SESSION_START:
            role = p.get("role") or agent_id or "?"
            label = _ROLE_LABEL.get(role, role)
            resumed = " (resumed session)" if p.get("is_resume") else ""
            A(f"[{label}]  session {p.get('session_key')}{resumed}")
            A(f"    tools: {', '.join(p.get('tools') or []) or '(none)'}"
              f"   prompt: {(p.get('prompt_sizes') or {}).get('chars')} chars")
            current_agent = agent_id
            continue

        if t == ev.AGENT_MESSAGE and p.get("direction") == "received_handoff":
            A(f"    received {p.get('label')} from {p.get('from')} "
              f"({(p.get('sizes') or {}).get('chars')} chars)")
            continue

        if t in (ev.FILE_READ_END, ev.SEARCH_END, ev.EDIT, ev.TEST_RUN) or (
            t == ev.TOOL_CALL_END and p.get("specialized_as") is None
        ):
            tid = p.get("tool_use_id")
            acq = current.get(tid)
            if acq is None:
                continue
            _emit_tool_line(A, acq, denials.get(tid), findings_by_consumer)
            continue

        if t == ev.HANDOFF_SENT:
            A(f"    handoff -> {p.get('recipient')}  [{p.get('label')}] "
              f"({(p.get('sizes') or {}).get('chars')} chars, "
              f"~{(p.get('sizes') or {}).get('estimated_tokens')} est. tokens)")
            A(_indent(_clip(p.get("text", ""), max_message_chars), "        "))
            A("")
            continue

        if t == ev.ERROR:
            A(f"    ERROR  {p.get('error')}  {_clip(str(p.get('text') or ''), 200)}")
            continue

        if t == ev.TURN_LIMIT_EXCEEDED:
            A(f"    !! turn limit exceeded ({p.get('max_turns')}); "
              f"terminated {p.get('enforcement')}")
            continue

        if t == ev.CLAUDE_SESSION_END:
            s = p.get("summary") or {}
            key = p.get("session_key")
            sa = next((x for x in raw["sessions"] if x.session_key == key), None)
            live = sa.summary if sa is not None else s
            A(f"    session end: assistant_stream_events="
              f"{live.get('assistant_stream_events')} "
              f"(thinking-only={live.get('thinking_only_stream_events')}) "
              f"cli_num_turns={(live.get('cli_reported_num_turns') or {}).get('value')} "
              f"tool_calls={live.get('tool_calls')} "
              f"exit={(p.get('exit') or {}).get('exit_code')} "
              f"({(p.get('exit') or {}).get('termination_reason')})")
            if live.get("permission_denied_count"):
                A(f"    !! {live['permission_denied_count']} tool call(s) were "
                  "DENIED by the CLI and never ran")
            A("")
            continue

        if t == ev.VERIFICATION:
            A("[VERIFIER]  held-out deterministic tests")
            A(f"    solved={p.get('solved')}  exit={p.get('exit_code')}  "
              f"argv={' '.join(p.get('argv') or [])}")
            A(_indent(_clip(p.get("stdout_tail") or "", 600), "        "))
            A("")
            continue

    A("################ SUMMARY ################")
    A(f"inter-agent potentially avoidable acquisitions : {dup.potentially_avoidable_acquisitions}")
    A(f"  primed reacquisitions                        : {dup.primed_reacquisitions}")
    A(f"  unprimed overlapping discoveries             : {dup.unprimed_overlapping_discoveries}")
    A(f"intra-agent repeats                            : {dup.intra_agent_repeat_acquisitions}")
    A(f"pairs excluded because not yet available (K(t)): {dup.concurrency_excluded_pairs}")
    return "\n".join(L)


_TRACE_LABEL = {
    tools.STRUCTURED_READ: "Read",
    tools.STRUCTURED_SEARCH: "Search",
    tools.STRUCTURED_EDIT: "Edit",
    tools.BASH_READ: "Bash/read",
    tools.BASH_SEARCH: "Bash/search",
    tools.BASH_DIRECTORY_LISTING: "Bash/list",
    tools.BASH_GIT_INSPECTION: "Bash/git",
    tools.BASH_VERIFICATION: "Bash/test",
    tools.BASH_BUILD: "Bash/build",
    tools.BASH_WORKSPACE_MANAGEMENT: "Bash/workspace",
    tools.BASH_NON_ACQUISITION: "Bash/meta",
    tools.BASH_UNKNOWN: "Bash/UNKNOWN",
    tools.UNKNOWN_TOOL: "UNKNOWN TOOL",
    tools.TOOL_DENIED: "DENIED",
}


def _emit_tool_line(A, acq, denial, findings: dict) -> None:
    """One line per tool call, labelled by the CURRENT classifier."""
    label = _TRACE_LABEL.get(acq.acquisition_class, acq.acquisition_class)
    if acq.acquisition_class in (tools.STRUCTURED_SEARCH, tools.BASH_SEARCH):
        target = acq.query or acq.target_path or ""
    else:
        target = acq.target_path or acq.command or acq.query or ""

    if acq.permission_denied:
        A(f"    {label:<14} {_clip(str(target), 100)}")
        A(f"        !! the CLI REFUSED this call - the action was NOT performed")
        A(f"        would have been: {acq.attempted_class}")
        if denial is not None:
            A(f"        reason: {denial.decision_reason_type} - {denial.decision_reason}")
        return

    A(f"    {label:<14} {_clip(str(target), 100)}   [{acq.result_chars} chars]")
    if acq.acquisition_class in tools.OPAQUE_ACQUISITION_CLASSES:
        A("        !! opaque acquisition: not mechanically attributable")
    f = findings.get(acq.tool_use_id)
    if not f:
        return
    if f["relation"] == metrics.INTER_AGENT:
        sub = f.get("reacquisition_subcategory")
        tag = f["priming"].upper() + (
            f"; {sub}" if sub and sub != metrics.REACQ_NOT_APPLICABLE else ""
        )
        A(f"        POTENTIAL REACQUISITION ({tag})")
        A(f"          overlaps {f['overlap_kind']} ratio={f['overlap_ratio']} "
          f"({f['overlap_shingles']} shared 3-line chunks)")
        A(f"          information previously available from {f['producer_agent']} "
          f"(session {f['producer_session_key']}, target {f['producer_target']})")
        if f["priming"] == metrics.PRIMED:
            e = f.get("priming_evidence") or {}
            A(f"          primed by {e.get('message_label')} from {e.get('message_sender')}: "
              f"matched {e.get('matched_token')!r}")
    else:
        A(f"        intra-agent repeat: overlaps own earlier "
          f"{f['overlap_kind']} ratio={f['overlap_ratio']}")


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + f" ... [+{len(s) - n} chars]"


def _indent(s: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in (s or "").splitlines())
