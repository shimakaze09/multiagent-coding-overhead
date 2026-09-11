"""Communication-duplication measurement for natural-language handoffs.

Two relationships, both measured with `tools.content_windows` (3-line windows,
indentation-insensitive, uninformative windows dropped):

1. HANDOFF DUPLICATION - how much of a handoff body is repository content, tool
   output the sender had observed, or content the sender had itself received and
   is relaying. I.e. source material copied into prose.

2. HANDOFF_REACQUISITION_OVERLAP - content that
      (a) the Investigator acquired with tools,
      (b) was copied into a handoff delivered to the Implementer, and
      (c) the Implementer then acquired again with tools.
   That content was carried downstream twice: once as prose, once as a tool
   result. Kept separate from ordinary inter-agent reacquisition.

Window (chunk) overlap is the primary metric. Character figures are estimates:
the stripped length of the lines covered by at least one matched window.

Pure over already-loaded run data, so `ingest` can call it without an import
cycle; `analyze_run` is the convenience entry point.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional

from harness import tools

HANDOFF_SCHEMA_VERSION = 1

# Fixed-topology role names (Arm B).
UPSTREAM_ROLE = "investigator"
DOWNSTREAM_ROLE = "implementer"


# --------------------------------------------------------------------------
# Repository content at the base commit
# --------------------------------------------------------------------------


def repository_texts(run_dir: Path, metadata: dict) -> tuple[dict[str, str], str]:
    """path -> text for every file at the run's base commit, read from the run's
    own workspace repository. Returns ({}, "not_available") if it is gone."""
    ws = (metadata.get("workspace") or {}).get("path")
    base = (metadata.get("workspace") or {}).get("base_commit")
    candidates = ([Path(ws)] if ws else []) + [Path(run_dir) / "workspace"]
    for path in candidates:
        if not base or not (path / ".git").is_dir():
            continue
        try:
            listing = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", base],
                cwd=str(path), capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if listing.returncode != 0:
            continue
        out: dict[str, str] = {}
        for rel in (listing.stdout or "").splitlines():
            rel = rel.strip()
            if not rel:
                continue
            show = subprocess.run(
                ["git", "show", f"{base}:{rel}"],
                cwd=str(path), capture_output=True, timeout=120,
            )
            if show.returncode != 0:
                continue
            try:
                out[rel] = show.stdout.decode("utf-8")
            except UnicodeDecodeError:
                continue  # binary: not quotable prose
        return out, "base_commit"
    return {}, "not_available"


# --------------------------------------------------------------------------
# Per-handoff duplication
# --------------------------------------------------------------------------


def _event_seq(events: list[dict], type_: str, pred) -> Optional[int]:
    for e in events:
        if e.get("type") == type_ and pred(e):
            return e.get("seq")
    return None


def analyze_loaded(raw: dict, acquisitions_by_session: dict, repo: dict[str, str],
                   repo_availability: str) -> dict:
    """raw: ingest.load_run output. acquisitions_by_session: session_key ->
    list[tools.Acquisition]."""
    run_dir = Path(raw["run_dir"])
    sessions = raw["sessions"]
    events = raw["events"]
    handoffs = (raw["summary"] or {}).get("handoffs") or []

    # digest -> repository files containing it
    repo_owner: dict[str, set[str]] = defaultdict(set)
    for rel, text in repo.items():
        for d in tools.content_chunk_set(text):
            repo_owner[d].add(rel)
    repo_set = set(repo_owner)

    results_by_tool_use = {
        c.tool_use_id: c.result_text for sa in sessions for c in sa.parsed.tool_calls
    }
    session_end_seq = {
        sa.session_key: _event_seq(
            events, "CLAUDE_SESSION_END",
            lambda e, k=sa.session_key: (e.get("payload") or {}).get("session_key") == k,
        )
        for sa in sessions
    }
    agent_of = {sa.session_key: sa.invocation.get("agent_id") for sa in sessions}
    handoff_seq = {
        h["index"]: _event_seq(
            events, "HANDOFF_SENT",
            lambda e, i=h["index"]: (e.get("payload") or {}).get("index") == i,
        )
        for h in handoffs
    }
    bodies = {
        h["index"]: (run_dir / h["path"]).read_text(encoding="utf-8")
        if (run_dir / h["path"]).is_file() else ""
        for h in handoffs
    }

    per_handoff = []
    for h in handoffs:
        idx, sender = h["index"], h["sender"]
        body = bodies[idx]
        hseq = handoff_seq.get(idx) or 0

        # tool output the sender had observed before sending
        tool_set: set[str] = set()
        for key, acqs in acquisitions_by_session.items():
            if agent_of.get(key) != sender:
                continue
            end = session_end_seq.get(key)
            if end is None or end > hseq:
                continue
            for a in acqs:
                if a.permission_denied or not a.completed:
                    continue
                tool_set |= tools.content_chunk_set(results_by_tool_use.get(a.tool_use_id))

        # content the sender had itself received and may be relaying
        relay_set: set[str] = set()
        for g in handoffs:
            if g["recipient"] == sender and (handoff_seq.get(g["index"]) or 0) < hseq:
                relay_set |= tools.content_chunk_set(bodies[g["index"]])

        informative = len(tools.content_windows(body))
        repo_n, repo_chars, repo_matched = tools.covered_by(body, repo_set)
        tool_n, tool_chars, _ = tools.covered_by(body, tool_set)
        relay_n, relay_chars, _ = tools.covered_by(body, relay_set)
        chars = len(body)
        files_quoted: dict[str, int] = defaultdict(int)
        for d in repo_matched:
            for rel in repo_owner[d]:
                files_quoted[rel] += 1

        per_handoff.append({
            "index": idx,
            "sender": sender,
            "recipient": h["recipient"],
            "label": h["label"],
            "handoff_chars": chars,
            "handoff_utf8_bytes": len(body.encode("utf-8")),
            "handoff_estimated_tokens": (h.get("sizes") or {}).get(
                "estimated_tokens", tools.handoff_size(body)["estimated_tokens"]),
            "informative_chunks": informative,
            "repository_content_chunks_in_handoff": repo_n,
            "repository_quote_chars_estimate": repo_chars,
            "handoff_repository_quote_fraction": round(repo_chars / chars, 4) if chars else None,
            "repository_chunk_fraction": round(repo_n / informative, 4) if informative else None,
            "tool_output_chunks_in_handoff": tool_n,
            "tool_output_quote_chars_estimate": tool_chars,
            "relayed_chunks_in_handoff": relay_n,
            "relayed_chars_estimate": relay_chars,
            "repository_files_quoted": dict(sorted(files_quoted.items())),
        })

    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "repository_content_source": repo_availability,
        "window": {
            "lines": tools.SHINGLE_LINES,
            "min_alnum": tools.WINDOW_MIN_ALNUM,
            "min_content_lines": tools.WINDOW_MIN_CONTENT_LINES,
            "indentation": "stripped",
        },
        "per_handoff": per_handoff,
        "communication_summary": _communication_summary(
            sessions, acquisitions_by_session, results_by_tool_use, per_handoff, bodies),
        "handoff_reacquisition_overlap": _handoff_reacquisition_overlap(
            sessions, acquisitions_by_session, results_by_tool_use, handoffs, bodies),
    }


def _role_acqs(sessions, acquisitions_by_session, role):
    keys = [sa.session_key for sa in sessions if sa.invocation.get("role") == role]
    return [a for k in keys for a in acquisitions_by_session.get(k, [])]


def _acquired(acqs, results_by_tool_use):
    """Completed, executed repository acquisitions and their returned text."""
    return [
        (a, results_by_tool_use.get(a.tool_use_id) or "")
        for a in acqs
        if a.is_acquisition and a.completed and not a.permission_denied
    ]


def _communication_summary(sessions, acquisitions_by_session, results_by_tool_use,
                           per_handoff, bodies) -> dict:
    inv = _acquired(_role_acqs(sessions, acquisitions_by_session, UPSTREAM_ROLE),
                    results_by_tool_use)
    inv_set: set[str] = set()
    for _, text in inv:
        inv_set |= tools.content_chunk_set(text)
    report = next((h for h in per_handoff if h["label"] == "investigation_report"), None)
    report_chunks_from_acq = None
    if report is not None:
        report_chunks_from_acq, report_chars_from_acq, _ = tools.covered_by(
            bodies[report["index"]], inv_set)
    total_chars = sum(h["handoff_chars"] for h in per_handoff)
    total_repo_chars = sum(h["repository_quote_chars_estimate"] for h in per_handoff)
    return {
        "applicable": bool(inv) or bool(per_handoff),
        "investigator_acquired_chars": sum(a.result_chars for a, _ in inv),
        "investigator_acquired_chunks": len(inv_set),
        "investigator_report_chars": report["handoff_chars"] if report else None,
        "investigator_report_estimated_tokens": report["handoff_estimated_tokens"] if report else None,
        "report_chunks_copied_from_investigator_acquisitions": report_chunks_from_acq,
        "report_chars_copied_from_investigator_acquisitions_estimate":
            report_chars_from_acq if report else None,
        "report_repository_chunks": report["repository_content_chunks_in_handoff"] if report else None,
        "report_repository_quote_fraction": report["handoff_repository_quote_fraction"] if report else None,
        "all_handoffs_chars": total_chars,
        "all_handoffs_repository_quote_chars_estimate": total_repo_chars,
        "all_handoffs_repository_quote_fraction":
            round(total_repo_chars / total_chars, 4) if total_chars else None,
    }


def _handoff_reacquisition_overlap(sessions, acquisitions_by_session, results_by_tool_use,
                                   handoffs, bodies) -> dict:
    up = _acquired(_role_acqs(sessions, acquisitions_by_session, UPSTREAM_ROLE),
                   results_by_tool_use)
    down = _acquired(_role_acqs(sessions, acquisitions_by_session, DOWNSTREAM_ROLE),
                     results_by_tool_use)
    if not up or not any(sa.invocation.get("role") == DOWNSTREAM_ROLE for sa in sessions):
        return {"applicable": False, "reason": "no investigator/implementer pair in this run"}

    I: set[str] = set()
    for _, text in up:
        I |= tools.content_chunk_set(text)
    H: set[str] = set()
    for h in handoffs:
        if h["recipient"] == DOWNSTREAM_ROLE:
            H |= tools.content_chunk_set(bodies[h["index"]])
    R: set[str] = set()
    for _, text in down:
        R |= tools.content_chunk_set(text)
    IHR = I & H & R

    by_target: dict[str, dict] = {}
    chars_total = 0
    for a, text in down:
        n, chars, _ = tools.covered_by(text, IHR)
        if n:
            key = (a.target_path or a.query or a.tool_use_id).replace("\\", "/")
            key = key.split("/workspace/", 1)[-1]
            slot = by_target.setdefault(key, {"chunks": 0, "chars_estimate": 0, "tool_calls": 0})
            slot["chunks"] += n
            slot["chars_estimate"] += chars
            slot["tool_calls"] += 1
            chars_total += chars

    return {
        "applicable": True,
        "temporal_basis": "implementer session starts after every handoff it receives",
        "chunks_in_investigator_acquisition": len(I),
        "chunks_copied_into_handoffs_to_implementer": len(I & H),
        "chunks_in_handoffs_to_implementer": len(H),
        "chunks_later_reacquired_by_implementer": len(R & I),
        "chunks_in_implementer_acquisition": len(R),
        "chunks_in_all_three": len(IHR),
        "implementer_reacquired_chars_in_all_three_estimate": chars_total,
        "by_target": dict(sorted(by_target.items())),
    }


def analyze_run(run_dir: str | Path) -> dict:
    from analysis import ingest  # local: ingest imports this module

    run_dir = Path(run_dir)
    raw = ingest.load_run(run_dir)
    if raw is None:
        raise FileNotFoundError(f"not a run directory: {run_dir}")
    acqs = {sa.session_key: ingest.acquisitions_for_session(sa) for sa in raw["sessions"]}
    repo, avail = repository_texts(run_dir, raw["metadata"])
    return analyze_loaded(raw, acqs, repo, avail)
