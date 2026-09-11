"""Rebuild the derived SQLite index from raw run artifacts.

The database is disposable. Everything here is recomputed from:
    metadata.json, run_summary.json,
    sessions/*/invocation.json, sessions/*/claude_stdout.jsonl, sessions/*/exit.json,
    handoffs/*.txt, verify/verification.json, events.jsonl

Acquisitions (including content shingles) are recomputed from the RAW CLI stream,
not read back from events.jsonl, so the raw stream stays the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import config
from analysis import handoff, isolation, metrics
from harness import events as ev, telemetry, tools

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def create_db(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _load_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _mv(measure: Optional[dict]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Unpack a value/source/availability triple."""
    if not isinstance(measure, dict):
        return None, None, None
    return measure.get("value"), measure.get("source"), measure.get("availability")


@dataclass
class SessionArtifacts:
    session_key: str
    session_index: int
    dir: Path
    invocation: dict
    exit: dict
    parsed: telemetry.ParsedStream
    summary: dict


def load_session(session_dir: Path) -> Optional[SessionArtifacts]:
    inv = _load_json(session_dir / "invocation.json")
    if inv is None:
        return None
    stdout_path = session_dir / "claude_stdout.jsonl"
    lines = (
        stdout_path.read_text(encoding="utf-8").splitlines()
        if stdout_path.is_file()
        else []
    )
    parsed = telemetry.parse_stream(lines)
    return SessionArtifacts(
        session_key=inv.get("session_key") or session_dir.name,
        session_index=metrics.session_index_of(inv.get("session_key") or session_dir.name),
        dir=session_dir,
        invocation=inv,
        exit=_load_json(session_dir / "exit.json") or {},
        parsed=parsed,
        summary=telemetry.session_summary(parsed),
    )


def acquisitions_for_session(sa: SessionArtifacts) -> list[tools.Acquisition]:
    """Recompute acquisitions from the raw stream."""
    agent_id = sa.invocation.get("agent_id") or ""
    denied_ids = sa.parsed.denied_tool_use_ids
    out = []
    for call in sa.parsed.tool_calls:
        out.append(
            tools.classify_tool_call(
                tool_use_id=call.tool_use_id,
                tool_name=call.name,
                tool_input=call.input,
                result_text=call.result_text,
                agent_id=agent_id,
                session_key=sa.session_key,
                start_ts=call.start_ts,
                end_ts=call.end_ts,
                start_line=call.start_line,
                concurrency_group_line=call.concurrency_group_line,
                end_line=call.end_line,
                is_error=call.result_is_error,
                turn_id=call.agent_turn,
                permission_denied=call.tool_use_id in denied_ids,
            )
        )
    return out


def base_commit_blobs(run_dir: Path, meta: dict) -> dict[str, str]:
    """path -> git blob SHA at the run's base commit.

    This is a secondary version hint only. Primary information identity is the
    hash of the content actually returned to the agent, because a file can change
    mid-run and Claude Code exposes no read ranges. Returns {} when the workspace
    has been cleaned up.
    """
    ws = (meta.get("workspace") or {}).get("path")
    base = (meta.get("workspace") or {}).get("base_commit")
    candidates = [Path(ws)] if ws else []
    candidates.append(run_dir / "workspace")
    for path in candidates:
        if not base or not (path / ".git").is_dir():
            continue
        try:
            proc = subprocess.run(
                ["git", "ls-tree", "-r", base],
                cwd=str(path),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        out: dict[str, str] = {}
        for line in (proc.stdout or "").splitlines():
            # "<mode> blob <sha>\t<path>"
            meta_part, _, rel = line.partition("\t")
            parts = meta_part.split()
            if len(parts) >= 3 and rel:
                out[rel.strip().replace("\\", "/")] = parts[2]
        return out
    return {}


def _lookup_blob(blobs: dict[str, str], path: Optional[str]) -> Optional[str]:
    if not path or not blobs:
        return None
    p = path.replace("\\", "/")
    if p in blobs:
        return blobs[p]
    # tool inputs are often absolute; match on the longest repo-relative suffix
    for rel, sha in blobs.items():
        if p.endswith("/" + rel):
            return sha
    return None


def load_run(run_dir: Path) -> Optional[dict]:
    """Load one run's raw artifacts into plain dicts/lists."""
    meta = _load_json(run_dir / "metadata.json")
    if meta is None:
        return None
    summary = _load_json(run_dir / "run_summary.json") or {}

    sessions_root = run_dir / "sessions"
    sessions: list[SessionArtifacts] = []
    if sessions_root.is_dir():
        for sd in sorted(sessions_root.iterdir()):
            if sd.is_dir():
                sa = load_session(sd)
                if sa is not None:
                    sessions.append(sa)

    events = list(ev.iter_events(run_dir / "events.jsonl"))
    verification = _load_json(run_dir / "verify" / "verification.json") or {}
    return {
        "run_dir": run_dir,
        "metadata": meta,
        "summary": summary,
        "sessions": sessions,
        "events": events,
        "verification": verification,
    }


# --------------------------------------------------------------------------
# Insertion
# --------------------------------------------------------------------------


def ingest_run(conn: sqlite3.Connection, run_dir: Path) -> Optional[str]:
    run = load_run(run_dir)
    if run is None:
        return None

    meta = run["metadata"]
    summary = run["summary"]
    run_id = meta["run_id"]
    sessions: list[SessionArtifacts] = run["sessions"]
    events: list[dict] = run["events"]
    blobs = base_commit_blobs(run_dir, meta)

    # Coverage is RECOMPUTED here from the raw stream with the current classifier,
    # never copied from run_summary.json (which was written by whatever classifier
    # version produced the run). This is what makes a re-ingest pick up a fix.
    all_acqs = [a for sa in sessions for a in acquisitions_for_session(sa)]
    run_cov = tools.coverage(
        all_acqs,
        minimum=(meta.get("config") or {}).get("min_acquisition_coverage", 0.90),
    )

    # -- run-level token / cache / cost roll-up across sessions -----------
    tok = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    tok_avail: set[str] = set()
    cost_total = 0.0
    cost_avail: set[str] = set()
    any_cost = False
    for sa in sessions:
        usage = sa.summary.get("usage", {})
        for k in tok:
            v, _s, a = _mv(usage.get(k))
            if a:
                tok_avail.add(a)
            if isinstance(v, (int, float)):
                tok[k] += v
        c = sa.summary.get("cost", {}).get("cli_reported_cost_usd")
        v, _s, a = _mv(c)
        if a:
            cost_avail.add(a)
        if isinstance(v, (int, float)):
            cost_total += v
            any_cost = True

    token_availability = _fold_availability(tok_avail)
    wall = sum(float(sa.exit.get("wall_seconds") or 0.0) for sa in sessions)

    conn.execute(
        """INSERT OR REPLACE INTO runs (
            run_id, task_id, task_category, arm, repeat_id, created_at, ended_at,
            model, cli_version, parser_version, event_schema_version,
            base_commit, final_tree_hash, solved, session_count, wall_seconds,
            diff_bytes, handoff_total_chars, handoff_total_estimated_tokens,
            acquisition_coverage, coverage_meets_minimum, coverage_formula_version,
            acquisition_classified, acquisition_unknown, acquisition_candidates,
            non_acquisition_calls, denied_calls, reanalysis_parser_version,
            subscription_execution,
            api_charge, all_sessions_subscription_ok, arm_label_valid,
            context_residency_level, reported_input_tokens, reported_output_tokens,
            reported_cache_read_tokens, reported_cache_write_tokens,
            reported_total_input_tokens,
            token_availability, cache_availability, cli_reported_cost_usd,
            cost_availability, metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            meta.get("task_id"),
            meta.get("task_category"),
            meta.get("arm"),
            meta.get("repeat_id"),
            meta.get("created_at"),
            summary.get("ended_at"),
            (meta.get("config") or {}).get("model"),
            (meta.get("cli") or {}).get("version"),
            (meta.get("versions") or {}).get("parser_version"),
            (meta.get("versions") or {}).get("event_schema_version"),
            (meta.get("workspace") or {}).get("base_commit"),
            summary.get("final_tree_hash"),
            _b(summary.get("solved")),
            summary.get("session_count") or len(sessions),
            wall,
            summary.get("diff_bytes"),
            summary.get("handoff_total_chars"),
            summary.get("handoff_total_estimated_tokens"),
            run_cov.acquisition_coverage,
            _b(run_cov.meets_minimum),
            run_cov.formula_version,
            run_cov.acquisition_classified,
            run_cov.acquisition_unknown,
            run_cov.acquisition_candidates,
            run_cov.non_acquisition_calls,
            run_cov.denied_calls,
            telemetry.PARSER_VERSION,
            _b((meta.get("billing_guard") or {}).get("subscription_execution")),
            _api_charge_verdict(summary, sessions),
            _b(summary.get("all_sessions_subscription_ok")),
            _b(summary.get("arm_label_valid")),
            meta.get("context_residency_level"),
            tok["input_tokens"] if token_availability == telemetry.REPORTED else None,
            tok["output_tokens"] if token_availability == telemetry.REPORTED else None,
            tok["cache_read_tokens"] if token_availability == telemetry.REPORTED else None,
            tok["cache_write_tokens"] if token_availability == telemetry.REPORTED else None,
            (
                tok["input_tokens"] + tok["cache_read_tokens"] + tok["cache_write_tokens"]
            )
            if token_availability == telemetry.REPORTED
            else None,
            token_availability,
            token_availability,
            cost_total if any_cost else None,
            _fold_availability(cost_avail),
            json.dumps(meta, sort_keys=True),
        ),
    )

    # -- sessions --------------------------------------------------------
    for sa in sessions:
        inv = sa.invocation
        s = sa.summary
        prompt = inv.get("stdin_text") or ""
        sizes = tools.handoff_size(prompt)
        num_turns, _src, _av = _mv(s.get("cli_reported_num_turns"))
        conn.execute(
            """INSERT OR REPLACE INTO sessions (
                run_id, session_key, session_index, agent_id, role, cli_session_id,
                resumed_session_id, model, api_key_source, exit_code,
                termination_reason, wall_seconds,
                assistant_stream_events, thinking_only_stream_events,
                assistant_events_excluding_thinking_only, cli_reported_num_turns,
                turns_observed, num_turns_reported,
                permission_denied_count, rate_limit_status, rate_limit_type,
                rate_limit_utilization, rate_limit_is_overage, model_count,
                tool_call_count, subagent_spawned, billing_ok, billing_note,
                prompt_chars, prompt_estimated_tokens, unknown_event_count,
                unparsable_lines, raw_stdout_lines, summary_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                sa.session_key,
                sa.session_index,
                inv.get("agent_id"),
                inv.get("role"),
                s.get("session_id"),
                inv.get("resume_session_id"),
                inv.get("model"),
                s.get("api_key_source"),
                sa.exit.get("exit_code"),
                sa.exit.get("termination_reason"),
                sa.exit.get("wall_seconds"),
                s.get("assistant_stream_events"),
                s.get("thinking_only_stream_events"),
                s.get("assistant_events_excluding_thinking_only"),
                num_turns,
                s.get("assistant_stream_events"),
                num_turns,
                s.get("permission_denied_count"),
                (s.get("rate_limit") or {}).get("latest_status"),
                (s.get("rate_limit") or {}).get("latest_rate_limit_type"),
                (s.get("rate_limit") or {}).get("latest_utilization"),
                _b((s.get("rate_limit") or {}).get("is_using_overage")),
                (s.get("model_usage_totals") or {}).get("model_count"),
                s.get("tool_calls"),
                s.get("subagent_spawned"),
                _b(sa.exit.get("billing_ok")),
                sa.exit.get("billing_note"),
                sizes["chars"],
                sizes["estimated_tokens"],
                sum((s.get("unknown_event_types") or {}).values()),
                s.get("unparsable_lines"),
                sa.exit.get("raw_stdout_lines"),
                json.dumps(s, sort_keys=True),
            ),
        )

        # usage per session (result scope)
        usage = s.get("usage", {})
        conn.execute(
            """INSERT INTO usage_reports (
                run_id, session_key, scope, line_no, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, thinking_tokens,
                total_tokens, service_tier, availability, source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                sa.session_key,
                "session_result",
                None,
                _mv(usage.get("input_tokens"))[0],
                _mv(usage.get("output_tokens"))[0],
                _mv(usage.get("cache_read_tokens"))[0],
                _mv(usage.get("cache_write_tokens"))[0],
                _mv(usage.get("thinking_tokens"))[0],
                _mv(usage.get("total_tokens"))[0],
                usage.get("service_tier"),
                _mv(usage.get("input_tokens"))[2],
                _mv(usage.get("input_tokens"))[1],
            ),
        )

        # rate-limit telemetry (subscription quota; never an error on its own)
        for rl in sa.parsed.rate_limit_events:
            conn.execute(
                """INSERT INTO rate_limit_events (
                    run_id, session_key, line_no, status, rate_limit_type,
                    utilization, resets_at_epoch, is_using_overage,
                    surpassed_threshold, indicates_execution_failure,
                    windows_json, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    sa.session_key,
                    rl.line_no,
                    rl.status,
                    rl.rate_limit_type,
                    rl.utilization,
                    rl.resets_at_epoch,
                    _b(rl.is_using_overage),
                    rl.surpassed_threshold,
                    _b(rl.indicates_execution_failure),
                    json.dumps(rl.windows, sort_keys=True),
                    json.dumps(rl.raw, sort_keys=True),
                ),
            )

        # permission denials (the action was NOT performed)
        denial_inputs = {
            e.get("tool_use_id"): e.get("tool_input")
            for e in ((sa.parsed.result or {}).get("permission_denials") or [])
            if isinstance(e, dict)
        }
        for d in sa.parsed.permission_denials:
            conn.execute(
                """INSERT INTO permission_denials (
                    run_id, session_key, line_no, tool_name, tool_use_id,
                    decision_reason_type, decision_reason, tool_input_json
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    sa.session_key,
                    d.line_no,
                    d.tool_name,
                    d.tool_use_id,
                    d.decision_reason_type,
                    d.decision_reason,
                    json.dumps(denial_inputs.get(d.tool_use_id), sort_keys=True),
                ),
            )

        # per-model usage from result.modelUsage (covers auxiliary models that
        # result.usage omits)
        for model, u in ((s.get("model_usage_totals") or {}).get("models") or {}).items():
            conn.execute(
                """INSERT INTO model_usage (
                    run_id, session_key, model, canonical_model, provider,
                    input_tokens, output_tokens, cache_read_input_tokens,
                    cache_creation_input_tokens, thinking_tokens, cost_usd
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    sa.session_key,
                    model,
                    u.get("canonical_model"),
                    u.get("provider"),
                    u.get("input_tokens"),
                    u.get("output_tokens"),
                    u.get("cache_read_input_tokens"),
                    u.get("cache_creation_input_tokens"),
                    u.get("thinking_tokens"),
                    u.get("cost_usd"),
                ),
            )

        # unknown events
        for uev in sa.parsed.unknown_events:
            conn.execute(
                """INSERT INTO unknown_events (
                    run_id, session_key, line_no, cli_type, reason, raw_keys_json
                ) VALUES (?,?,?,?,?,?)""",
                (
                    run_id,
                    sa.session_key,
                    uev.get("line_no"),
                    (uev.get("raw") or {}).get("type") if isinstance(uev.get("raw"), dict) else None,
                    uev.get("reason"),
                    json.dumps(
                        sorted((uev.get("raw") or {}).keys())
                        if isinstance(uev.get("raw"), dict)
                        else [],
                        sort_keys=True,
                    ),
                ),
            )

        # tool calls / reads / searches / bash / shingles
        for acq in acquisitions_for_session(sa):
            conn.execute(
                """INSERT INTO tool_calls (
                    run_id, session_key, session_index, agent_id, tool_use_id,
                    tool_name, acquisition_class, confidence, target_path, query,
                    command, start_line, end_line, start_ts, end_ts, turn_id,
                    completed, is_error, permission_denied, attempted_class,
                    result_chars, result_bytes, result_sha,
                    shingle_count
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    sa.session_key,
                    sa.session_index,
                    acq.agent_id,
                    acq.tool_use_id,
                    acq.tool_name,
                    acq.acquisition_class,
                    acq.confidence,
                    acq.target_path,
                    acq.query,
                    acq.command,
                    acq.start_line,
                    acq.end_line,
                    acq.start_ts,
                    acq.end_ts,
                    acq.turn_id,
                    _b(acq.completed),
                    _b(acq.is_error),
                    _b(acq.permission_denied),
                    acq.attempted_class,
                    acq.result_chars,
                    acq.result_bytes,
                    acq.result_sha,
                    len(acq.result_shingles),
                ),
            )

            if acq.acquisition_class in (tools.STRUCTURED_READ, tools.CLASSIFIED_BASH_READ):
                conn.execute(
                    """INSERT INTO file_reads (
                        run_id, session_key, session_index, agent_id, tool_use_id,
                        path, path_version_hint, result_sha, result_chars,
                        start_line, end_line, start_ts, end_ts, acquisition_class,
                        range_available, range_note
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        sa.session_key,
                        sa.session_index,
                        acq.agent_id,
                        acq.tool_use_id,
                        acq.target_path,
                        _lookup_blob(blobs, acq.target_path),
                        acq.result_sha,
                        acq.result_chars,
                        acq.start_line,
                        acq.end_line,
                        acq.start_ts,
                        acq.end_ts,
                        acq.acquisition_class,
                        0,
                        "Claude Code does not expose byte/line ranges; identity is "
                        "the BLAKE2b hash of the returned content.",
                    ),
                )

            if acq.acquisition_class in (tools.STRUCTURED_SEARCH, tools.CLASSIFIED_BASH_SEARCH):
                conn.execute(
                    """INSERT INTO searches (
                        run_id, session_key, session_index, agent_id, tool_use_id,
                        tool_name, pattern, scope, result_sha, result_chars,
                        result_path_count, result_paths_json, start_line, end_line,
                        acquisition_class
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        sa.session_key,
                        sa.session_index,
                        acq.agent_id,
                        acq.tool_use_id,
                        acq.tool_name,
                        acq.query,
                        acq.target_path,
                        acq.result_sha,
                        acq.result_chars,
                        len(acq.result_paths),
                        json.dumps(acq.result_paths[:200]),
                        acq.start_line,
                        acq.end_line,
                        acq.acquisition_class,
                    ),
                )

            if acq.command is not None:
                conn.execute(
                    """INSERT INTO bash_classifications (
                        run_id, session_key, agent_id, tool_use_id, command,
                        overall_class, segment_classes_json, permission_denied,
                        attempted_class, is_error, result_chars, result_text
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        sa.session_key,
                        acq.agent_id,
                        acq.tool_use_id,
                        acq.command,
                        acq.acquisition_class,
                        json.dumps(acq.bash_segment_classes),
                        _b(acq.permission_denied),
                        acq.attempted_class,
                        _b(acq.is_error),
                        acq.result_chars,
                        _raw_tool_result(sa, acq.tool_use_id),
                    ),
                )

            for ordinal, sh in enumerate(acq.result_shingles):
                conn.execute(
                    """INSERT INTO content_shingles (
                        run_id, session_key, agent_id, tool_use_id, shingle, ordinal
                    ) VALUES (?,?,?,?,?,?)""",
                    (run_id, sa.session_key, acq.agent_id, acq.tool_use_id, sh, ordinal),
                )

    # -- reacquisition findings and communication duplication (derived) --
    versions = {sa.summary.get("cli_version") for sa in sessions} - {None}
    dup_rep = metrics.analyze(
        run_acquisitions(run_dir),
        run_messages(run_dir),
        cli_version=versions.pop() if len(versions) == 1 else None,
    )
    for f in dup_rep.findings:
        ev_ = f.get("subcategory_evidence") or {}
        conn.execute(
            """INSERT INTO reacquisition_findings (
                run_id, consumer_tool_use_id, consumer_agent, consumer_session_key,
                consumer_class, consumer_target, producer_tool_use_id, producer_agent,
                producer_target, relation, overlap_kind, overlap_ratio, overlap_chunks,
                priming, reacquisition_subcategory, subcategory_rule,
                subcategory_evidence_json, duplicate_chars, duplicate_bytes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, f["consumer_tool_use_id"], f["consumer_agent"],
                f["consumer_session_key"], f["consumer_class"], f["consumer_target"],
                f["producer_tool_use_id"], f["producer_agent"], f["producer_target"],
                f["relation"], f["overlap_kind"], f["overlap_ratio"], f["overlap_shingles"],
                f["priming"], f["reacquisition_subcategory"], ev_.get("rule"),
                json.dumps(ev_, sort_keys=True), f["duplicate_chars"], f["duplicate_bytes"],
            ),
        )

    repo, repo_avail = handoff.repository_texts(run_dir, meta)
    comm = handoff.analyze_loaded(
        run, {sa.session_key: acquisitions_for_session(sa) for sa in sessions}, repo, repo_avail
    )
    for h in comm["per_handoff"]:
        conn.execute(
            """INSERT INTO handoff_duplication (
                run_id, idx, sender, recipient, label, handoff_chars, handoff_utf8_bytes,
                handoff_estimated_tokens, informative_chunks, repository_chunks,
                repository_quote_chars_estimate, repository_quote_fraction,
                tool_output_chunks, tool_output_quote_chars_estimate, relayed_chunks,
                relayed_chars_estimate, repository_files_quoted_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, h["index"], h["sender"], h["recipient"], h["label"],
                h["handoff_chars"], h["handoff_utf8_bytes"], h["handoff_estimated_tokens"],
                h["informative_chunks"], h["repository_content_chunks_in_handoff"],
                h["repository_quote_chars_estimate"], h["handoff_repository_quote_fraction"],
                h["tool_output_chunks_in_handoff"], h["tool_output_quote_chars_estimate"],
                h["relayed_chunks_in_handoff"], h["relayed_chars_estimate"],
                json.dumps(h["repository_files_quoted"], sort_keys=True),
            ),
        )

    integ = summary.get("workspace_integrity") or {}
    vis = summary.get("visible_tests") or {}
    conn.execute(
        """INSERT OR REPLACE INTO run_integrity (
            run_id, visible_tests_ran, visible_tests_passed, visible_tests_exit_code,
            read_only_files_json, protected_paths_changed_json, changed_paths_json,
            changed_outside_expected_scope_json, handoff_reacquisition_overlap_json,
            isolation_check_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            _b(vis.get("ran")),
            _b(vis.get("passed")),
            vis.get("exit_code"),
            json.dumps(integ.get("read_only_files") or [], sort_keys=True),
            json.dumps(integ.get("protected_paths_changed") or [], sort_keys=True),
            json.dumps(integ.get("changed_paths") or [], sort_keys=True),
            json.dumps(integ.get("changed_outside_expected_scope") or [], sort_keys=True),
            json.dumps(comm["handoff_reacquisition_overlap"], sort_keys=True),
            json.dumps(isolation.check_run(run), sort_keys=True),
        ),
    )

    # -- events ----------------------------------------------------------
    for e in events:
        raw_ref = e.get("raw_ref") or {}
        conn.execute(
            """INSERT INTO events (
                run_id, event_id, seq, ts, task_id, arm, agent_id, turn_id, type,
                parent_event_id, source, order_confidence, raw_session_key,
                raw_line_no, payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                e.get("event_id"),
                e.get("seq"),
                e.get("ts"),
                e.get("task_id"),
                e.get("arm"),
                e.get("agent_id"),
                e.get("turn_id"),
                e.get("type"),
                e.get("parent_event_id"),
                e.get("source"),
                e.get("order_confidence"),
                raw_ref.get("session_key") if isinstance(raw_ref, dict) else None,
                raw_ref.get("line_no") if isinstance(raw_ref, dict) else None,
                json.dumps(e.get("payload") or {}, sort_keys=True),
            ),
        )

    # -- handoffs / messages --------------------------------------------
    for h in summary.get("handoffs") or []:
        body_path = run["run_dir"] / h.get("path", "")
        body = body_path.read_text(encoding="utf-8") if body_path.is_file() else ""
        sizes = h.get("sizes") or tools.handoff_size(body)
        recipient = h.get("recipient")
        conn.execute(
            """INSERT INTO agent_messages (
                run_id, kind, idx, sender, recipient, label,
                delivered_before_session_index, chars, utf8_bytes, words, lines,
                estimated_tokens, estimated_tokens_availability, sha, body
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                "handoff",
                h.get("index"),
                h.get("sender"),
                recipient,
                h.get("label"),
                _delivery_session_index(events, recipient, h.get("index")),
                sizes.get("chars"),
                sizes.get("utf8_bytes"),
                sizes.get("words"),
                sizes.get("lines"),
                sizes.get("estimated_tokens"),
                sizes.get("estimated_tokens_availability", telemetry.ESTIMATED),
                h.get("sha"),
                body,
            ),
        )

    # -- verification ----------------------------------------------------
    v = run["verification"]
    if v:
        conn.execute(
            """INSERT INTO verifications (
                run_id, solved, exit_code, argv_json, wall_seconds, judge,
                llm_judge_used, stdout_tail
            ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                run_id,
                _b(v.get("solved")),
                v.get("exit_code"),
                json.dumps(v.get("argv") or []),
                v.get("wall_seconds"),
                "deterministic_held_out_tests",
                0,
                (v.get("stdout") or "")[-4000:],
            ),
        )

    conn.commit()
    return run_id


def _delivery_session_index(
    events: Sequence[dict], recipient: Optional[str], handoff_index: Optional[int]
) -> Optional[int]:
    """Which session of the recipient this handoff was delivered before.

    Derived from the event log: the first CLAUDE_SESSION_START for `recipient`
    that appears after the HANDOFF_SENT event.
    """
    if not recipient:
        return None
    handoff_seq = None
    for e in events:
        if e.get("type") == ev.HANDOFF_SENT and (e.get("payload") or {}).get("index") == handoff_index:
            handoff_seq = e.get("seq")
            break
    if handoff_seq is None:
        return None
    for e in events:
        if (
            e.get("type") == ev.CLAUDE_SESSION_START
            and e.get("agent_id") == recipient
            and (e.get("seq") or 0) > handoff_seq
        ):
            key = (e.get("payload") or {}).get("session_key") or ""
            return metrics.session_index_of(key)
    return None


def _raw_tool_result(sa: SessionArtifacts, tool_use_id: str) -> Optional[str]:
    """The verbatim tool_result body, so the DB keeps the raw output alongside
    the derived category. The authoritative copy is still the raw stream."""
    for call in sa.parsed.tool_calls:
        if call.tool_use_id == tool_use_id:
            return call.result_text
    return None


def _b(v) -> Optional[int]:
    if v is None:
        return None
    return 1 if v else 0


def _fold_availability(avail: set[str]) -> str:
    if not avail:
        return telemetry.NOT_EXPOSED
    if avail == {telemetry.REPORTED}:
        return telemetry.REPORTED
    if telemetry.REPORTED in avail:
        return "partial"
    if telemetry.UNRELIABLE in avail:
        return telemetry.UNRELIABLE
    return telemetry.NOT_EXPOSED


def _api_charge_verdict(summary: dict, sessions: Sequence[SessionArtifacts]) -> str:
    for sa in sessions:
        src = sa.summary.get("api_key_source")
        if src in config.API_KEY_SOURCES_MEANING_API_BILLING:
            return "api_suspected"
    if not sessions:
        return "unknown"
    return "false_expected" if summary.get("all_sessions_subscription_ok") else "unknown"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build(db_path: str | Path, run_dirs: Iterable[Path]) -> list[str]:
    """Create a fresh DB and ingest every run directory. Idempotent by construction."""
    conn = create_db(db_path)
    try:
        ingested = []
        for rd in run_dirs:
            rid = ingest_run(conn, Path(rd))
            if rid:
                ingested.append(rid)
        return ingested
    finally:
        conn.close()


def discover_runs(runs_dir: str | Path = None) -> list[Path]:
    base = Path(runs_dir or config.RUNS_DIR)
    if not base.is_dir():
        return []
    return sorted(d for d in base.iterdir() if d.is_dir() and (d / "metadata.json").is_file())


# --------------------------------------------------------------------------
# Loading back out for analysis
# --------------------------------------------------------------------------


def run_acquisitions(run_dir: Path) -> list[metrics.Acq]:
    """Acquisitions for one run, ordered, ready for duplication analysis."""
    run = load_run(run_dir)
    if run is None:
        return []
    out: list[metrics.Acq] = []
    for sa in run["sessions"]:
        for acq in acquisitions_for_session(sa):
            out.append(metrics.acq_from_dict(acq.as_dict(), sa.session_index))
    return out


def run_messages(run_dir: Path) -> list[metrics.Message]:
    """Messages delivered to each agent, with the session they preceded."""
    run = load_run(run_dir)
    if run is None:
        return []
    events = run["events"]
    summary = run["summary"]
    out: list[metrics.Message] = []
    for h in summary.get("handoffs") or []:
        recipient = h.get("recipient")
        if not recipient or recipient == "user":
            continue
        body_path = run_dir / h.get("path", "")
        body = body_path.read_text(encoding="utf-8") if body_path.is_file() else ""
        idx = _delivery_session_index(events, recipient, h.get("index"))
        if idx is None:
            continue
        out.append(
            metrics.Message(
                recipient=recipient,
                sender=h.get("sender") or "",
                label=h.get("label") or "",
                text=body,
                delivered_before_session_index=idx,
            )
        )
    return out
