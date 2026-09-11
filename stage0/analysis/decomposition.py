"""Multi-agent overhead decomposition: prospective, Stage 0.5 (PREREGISTRATION section 15).

Question: when Arm B uses more resources than Arm A, how much of the extra
MAIN-MODEL INPUT is associated with

  1. session/context fanout: the context each session starts from (system
     prompt, tool schemas, role framing, the task statement, and for a resumed
     session its history), re-submitted on every API call;
  2. handoff transmission: natural-language inter-agent messages in context;
  3. discretionary information reacquisition: frozen classifier, unchanged;
  4. tool-required reacquisition: `edit_precondition_associated`, unchanged;
  5. unique downstream acquisition: content a later agent acquired that neither
     the earlier agent's tool results nor the handoffs contained?

WHAT IS EXACT AND WHAT IS NOT. Claude Code reports, for every API call, that
call's uncached input, cache read and cache write. Summed per distinct
message.id, these reproduce `result.usage` exactly (checked on every real
session so far, and reported as `reconciles_with_result_usage`). So:

* EXACT OBSERVABLE: per-call input; each session's FIRST-call input (the first
  call precedes any tool use, so it is the context that exists before any
  agent-specific repository work) with its cache read/write split; handoff
  chars and bytes.
* RECONSTRUCTED: context-weighted token figures. A text present in a session's
  context from API call k onward is re-submitted on each of the remaining calls;
  its weight is the number of calls that include it. Session base context is
  taken as first-call input minus the estimated handoff text in it.
* ESTIMATED: token sizes of texts (chars/4 with a word floor, the same local
  heuristic as `tools.handoff_size`). They are never provider token counts.
* UNAVAILABLE: the split of Claude Code's hidden system prompt vs tool schemas,
  per-content provider token counts, and the size of an assistant turn inside
  later calls.

The categories are disjoint by CONTEXT POSITION (session base vs handoff text vs
individual tool results), so they do not double count prompt positions. They are
NOT forced to sum to the observable total: whatever is not attributed
(assistant text, tool inputs, thinking carried forward, estimation error) is
reported as the unattributed remainder, which may be negative. Content-level
overlap between handoff text and later tool results (v1 and v2
acquire -> handoff -> reacquire) is reported separately and never subtracted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from analysis import metrics
from harness import tools

DECOMPOSITION_VERSION = 1

UPSTREAM_ROLE = "investigator"
DOWNSTREAM_ROLE = "implementer"
HANDOFF_PROMPT_KEYS = ("coordinator_instruction", "investigator_report", "implementer_report")
TASK_STATEMENT_KEY = "task_statement_verbatim"

EXACT = "exact_observable"
RECONSTRUCTED = "reconstructed"
ESTIMATED = "estimated"
UNAVAILABLE = "unavailable"

_FILE_READ = (tools.STRUCTURED_READ, tools.BASH_READ)
_SEARCH = (tools.STRUCTURED_SEARCH, tools.BASH_SEARCH, tools.BASH_DIRECTORY_LISTING)
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:py|json|toml|cfg|ini|md|txt|yaml|yml)\b")

REACQ_BUCKETS = {
    metrics.REACQ_DISCRETIONARY: "discretionary_reacquisition",
    metrics.REACQ_EDIT_PRECONDITION: "tool_required_reacquisition",
    metrics.REACQ_VERIFICATION: "verification_associated_reacquisition",
    metrics.REACQ_UNKNOWN: "unknown_reacquisition",
}

UNAVAILABLE_ITEMS = [
    "exact split of Claude Code's hidden system prompt vs tool schemas",
    "provider token counts for individual texts (handoffs, tool results)",
    "size of earlier assistant turns inside later calls",
]


def est_tokens(text: Optional[str]) -> int:
    return tools.handoff_size(text)["estimated_tokens"] if text else 0


def _rel(path: Optional[str]) -> Optional[str]:
    if not path:
        return path
    p = path.replace("\\", "/")
    i = p.lower().find("/workspace/")
    return p[i + len("/workspace/"):] if i >= 0 else p


# --------------------------------------------------------------------------
# Exact per-call usage
# --------------------------------------------------------------------------


def api_calls(session_dir) -> list[dict]:
    """One entry per distinct API assistant message, in stream order.

    Line numbers use the same 1-based numbering as `telemetry.parse_stream`.
    """
    path = Path(session_dir) / "claude_stdout.jsonl"
    if not path.is_file():
        return []
    calls: list[dict] = []
    last_id: object = object()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        mid = msg.get("id")
        if mid is not None and mid == last_id:
            continue
        last_id = mid
        u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        vals = (u.get("input_tokens"), u.get("cache_read_input_tokens"),
                u.get("cache_creation_input_tokens"))
        known = all(isinstance(v, (int, float)) for v in vals)
        calls.append({
            "message_id": mid,
            "first_line": line_no,
            "input_tokens": vals[0],
            "cache_read_tokens": vals[1],
            "cache_write_tokens": vals[2],
            "total_input_tokens": int(sum(vals)) if known else None,
        })
    return calls


def _result_total_input(sa) -> Optional[int]:
    u = (sa.parsed.result or {}).get("usage") if isinstance(sa.parsed.result, dict) else None
    if not isinstance(u, dict):
        return None
    vals = (u.get("input_tokens"), u.get("cache_read_input_tokens"),
            u.get("cache_creation_input_tokens"))
    return int(sum(vals)) if all(isinstance(v, (int, float)) for v in vals) else None


# --------------------------------------------------------------------------
# What each session's context contains at its first call
# --------------------------------------------------------------------------


def _handoff_bodies(raw: dict) -> list[dict]:
    run_dir = Path(raw["run_dir"])
    out = []
    for h in (raw.get("summary") or {}).get("handoffs") or []:
        p = run_dir / h.get("path", "")
        body = p.read_text(encoding="utf-8") if p.is_file() else ""
        out.append({**h, "body": body})
    return out


def session_contexts(sessions, handoff_texts: set[str]) -> dict[str, dict]:
    """Texts in each session's context from its first call.

    A fresh session holds its own prompt. A RESUMED session (same logical agent,
    `--resume`) also carries its earlier prompts and replies. A reply that was
    delivered as a handoff counts as handoff text.
    """
    prior: dict[str, list[tuple[str, str]]] = {}
    out: dict[str, dict] = {}
    for sa in sorted(sessions, key=lambda s: s.session_index):
        inv = sa.invocation or {}
        agent = inv.get("agent_id") or sa.session_key
        pin = inv.get("prompt_inputs") or {}
        own: list[tuple[str, str]] = []
        if isinstance(pin.get(TASK_STATEMENT_KEY), str) and pin[TASK_STATEMENT_KEY]:
            own.append(("task_statement", pin[TASK_STATEMENT_KEY]))
        for key in HANDOFF_PROMPT_KEYS:
            if isinstance(pin.get(key), str) and pin[key]:
                own.append(("handoff", pin[key]))
        resumed = bool(inv.get("resume_session_id"))
        carried = list(prior.get(agent, [])) if resumed else []
        out[sa.session_key] = {
            "agent": agent,
            "role": inv.get("role"),
            "resumed": resumed,
            "items": own + carried,
            "stdin_text": inv.get("stdin_text") or "",
            "append_system_prompt": inv.get("append_system_prompt") or "",
        }
        final = (sa.parsed.result or {}).get("result") if isinstance(sa.parsed.result, dict) else None
        history = carried + own
        if isinstance(final, str) and final.strip():
            kind = "handoff" if final.strip() in handoff_texts else "agent_output"
            history.append((kind, final))
        prior[agent] = history
    return out


# --------------------------------------------------------------------------
# Unique downstream acquisition
# --------------------------------------------------------------------------


def unique_downstream(raw: dict, acquisitions_by_session: dict) -> dict:
    """Content the downstream agent (Implementer) acquired with tools that was in
    neither the upstream agent's (Investigator's) tool results nor any handoff
    delivered to it. Unit: preregistered 3-line shingles. Characters are an
    estimate (result chars x unique fraction).

    Materiality is reported only through MECHANICAL indicators. Whether a finding
    changed the diagnosis or corrected an earlier one needs judgement and is left
    `undetermined`: no LLM judge is used.
    """
    sessions = raw["sessions"]
    role_of = {sa.session_key: (sa.invocation or {}).get("role") for sa in sessions}
    if not any(r == DOWNSTREAM_ROLE for r in role_of.values()) or not any(
            r == UPSTREAM_ROLE for r in role_of.values()):
        return {"applicable": False, "reason": "no investigator/implementer pair in this run"}

    def of_role(role):
        return [a for k, acqs in acquisitions_by_session.items() if role_of.get(k) == role
                for a in acqs]

    up = [a for a in of_role(UPSTREAM_ROLE)
          if a.is_acquisition and a.completed and not a.permission_denied]
    down = of_role(DOWNSTREAM_ROLE)
    handoffs = _handoff_bodies(raw)
    to_down = [h["body"] for h in handoffs if h.get("recipient") == DOWNSTREAM_ROLE]

    known: set[str] = set()
    for a in up:
        known |= set(a.result_shingles)
    for text in to_down:
        known |= set(tools.shingles(text))

    edits = [a for a in down if a.acquisition_class == tools.STRUCTURED_EDIT and a.completed
             and not a.permission_denied and not a.is_error]
    first_edit_end = min((a.end_line for a in edits if a.end_line is not None), default=None)
    edited = sorted({_rel(a.target_path) for a in edits if a.target_path})
    up_files = {_rel(a.target_path) for a in up if a.acquisition_class in _FILE_READ and a.target_path}
    up_queries = {a.query for a in up if a.query}

    rows = []
    for a in down:
        if not (a.is_acquisition and a.completed and not a.permission_denied):
            continue
        sh = set(a.result_shingles)
        new = sh - known
        if not new:
            continue
        frac = len(new) / len(sh)
        after_edit = first_edit_end is not None and a.start_line > first_edit_end
        rel = _rel(a.target_path)
        if after_edit and (a.acquisition_class == tools.BASH_GIT_INSPECTION or rel in edited):
            kind = "self_generated_after_own_edit"
        elif a.acquisition_class in _FILE_READ and rel not in up_files:
            kind = "file_upstream_never_read"
        elif a.acquisition_class in _SEARCH and a.query and a.query not in up_queries:
            kind = "search_upstream_never_ran"
        else:
            kind = "other_new_content"
        rows.append({
            "tool_use_id": a.tool_use_id,
            "tool": a.tool_name,
            "acquisition_class": a.acquisition_class,
            "target": rel or a.query or (a.command or "")[:120],
            "kind": kind,
            "phase": "after_first_own_edit" if after_edit else "before_first_own_edit",
            "unique_shingles": len(new),
            "unique_fraction": round(frac, 4),
            "unique_chars_estimate": int(round(a.result_chars * frac)),
        })

    informational = [r for r in rows if r["kind"] != "self_generated_after_own_edit"]
    new_files_before_edit = sorted({r["target"] for r in informational
                                    if r["kind"] == "file_upstream_never_read"
                                    and r["phase"] == "before_first_own_edit"})

    def named(rel: Optional[str]) -> bool:
        if not rel:
            return False
        base = rel.rsplit("/", 1)[-1]
        return any(rel in t or base in t for t in to_down)

    # Judge the implementation by what is still changed when the run ends, so a
    # scratch file the agent created and deleted cannot count as a change.
    integrity = (raw.get("summary") or {}).get("workspace_integrity") or {}
    final_changed = sorted(
        c["path"] for c in integrity.get("changed_paths") or []
        if not c.get("path", "").startswith(("tests/",)) and "__pycache__" not in c.get("path", "")
    )
    basis = final_changed if integrity.get("changed_paths") is not None else edited
    edited_not_named = [p for p in basis if not named(p)]
    transient = sorted(set(edited) - set(final_changed)) if integrity.get("changed_paths") is not None else []

    def mentioned(text: str) -> set[str]:
        return {m.replace("\\", "/").rsplit("/", 1)[-1] for m in _PATH_TOKEN.findall(text or "")}

    up_report = next((h["body"] for h in handoffs if h.get("label") == "investigation_report"), "")
    down_report = next((h["body"] for h in handoffs if h.get("label") == "implementation_report"), "")
    new_mentions = sorted(mentioned(down_report) - mentioned(up_report))
    tests_run = sum(1 for a in down if a.acquisition_class == tools.BASH_VERIFICATION
                    and not a.permission_denied)

    material = {
        "changed_diagnosis": {
            "determination": "undetermined_without_judgement",
            "mechanical_indicator": bool(new_mentions),
            "rule": "the downstream report names a file the upstream report does not",
            "evidence": {"files_named_only_in_downstream_report": new_mentions},
        },
        "corrected_earlier_finding": {
            "determination": "undetermined_without_judgement",
            "mechanical_indicator": None,
            "rule": "no mechanical rule; see changed_diagnosis / changed_implementation",
            "evidence": {},
        },
        "added_new_constraint": {
            "determination": "candidate" if new_files_before_edit else "no_mechanical_evidence",
            "mechanical_indicator": bool(new_files_before_edit),
            "rule": "before its first edit, the downstream agent read a file the upstream agent never read, and got content absent from every handoff",
            "evidence": {"files": new_files_before_edit},
        },
        "changed_implementation": {
            "determination": "yes" if edited_not_named else "no",
            "mechanical_indicator": bool(edited_not_named),
            "rule": "a production file changed at the end of the run is named in no handoff "
                    "to the downstream agent",
            "evidence": {"changed_files_not_named_in_any_handoff": edited_not_named,
                         "changed_files_at_end_of_run": basis,
                         "edited_then_removed_or_reverted": transient},
        },
        "merely_verified": {
            "determination": "yes" if not informational else "no",
            "mechanical_indicator": not informational,
            "rule": "all unique downstream content is self-generated after its own edit (diffs, re-reads of edited files) or there is none",
            "evidence": {"test_runs": tests_run,
                         "self_generated_acquisitions": len(rows) - len(informational)},
        },
    }

    return {
        "applicable": True,
        "unit": "3-line shingles (preregistered unit); chars are estimates",
        "known_basis": "upstream tool results + every handoff delivered to the downstream agent",
        "unique_shingles": sum(r["unique_shingles"] for r in rows),
        "unique_chars_estimate": sum(r["unique_chars_estimate"] for r in rows),
        "informational_unique_shingles": sum(r["unique_shingles"] for r in informational),
        "informational_unique_chars_estimate": sum(r["unique_chars_estimate"] for r in informational),
        "self_generated_unique_chars_estimate": sum(
            r["unique_chars_estimate"] for r in rows if r["kind"] == "self_generated_after_own_edit"),
        "acquisitions": rows,
        "material": material,
        "llm_judge_used": False,
    }


# --------------------------------------------------------------------------
# The decomposition
# --------------------------------------------------------------------------


def _bucket() -> dict:
    return {"count": 0, "chars": 0, "estimated_tokens_once": 0, "context_weighted_tokens": 0}


def decompose(raw: dict, findings: list, unique: dict) -> dict:
    sessions = raw["sessions"]
    handoffs = _handoff_bodies(raw)
    inter = [h for h in handoffs if h.get("recipient") not in (None, "user")]
    handoff_texts = {h["body"].strip() for h in inter if h["body"].strip()}
    contexts = session_contexts(sessions, handoff_texts)

    sub_by_consumer = {
        f.get("consumer_tool_use_id"): f.get("reacquisition_subcategory")
        for f in findings or []
        if f.get("relation") == metrics.INTER_AGENT
        and f.get("reacquisition_subcategory") in REACQ_BUCKETS
    }
    unique_frac = {r["tool_use_id"]: r["unique_fraction"]
                   for r in (unique or {}).get("acquisitions") or []}

    buckets = {name: _bucket() for name in REACQ_BUCKETS.values()}
    buckets["unique_downstream_acquisition"] = _bucket()
    buckets["other_tool_results"] = _bucket()

    per_session = []
    total = calls_total = 0
    reconciles = True
    first_total = first_cr = first_cw = first_uncached = 0
    fanout_cw = handoff_cw = statement_cw = 0
    statement_occ = 0
    hidden_init = 0
    hidden_sessions = 0
    for sa in sorted(sessions, key=lambda s: s.session_index):
        calls = api_calls(sa.dir) if getattr(sa, "dir", None) else []
        n = len(calls)
        s_total = sum(c["total_input_tokens"] or 0 for c in calls)
        res_total = _result_total_input(sa)
        if res_total is not None and res_total != s_total:
            reconciles = False
        ctx = contexts.get(sa.session_key, {"items": [], "resumed": False,
                                            "stdin_text": "", "append_system_prompt": ""})
        h_tok = sum(est_tokens(t) for k, t in ctx["items"] if k == "handoff")
        s_items = [t for k, t in ctx["items"] if k == "task_statement"]
        s_tok = sum(est_tokens(t) for t in s_items)
        first = calls[0] if calls else None
        F = first["total_input_tokens"] if first else None
        sess = {
            "session_key": sa.session_key,
            "role": ctx.get("role"),
            "resumed": ctx["resumed"],
            "api_calls": n,
            "total_input_tokens": s_total,
            "first_call_input_tokens": F,
            "first_call_cache_read_tokens": first["cache_read_tokens"] if first else None,
            "first_call_cache_write_tokens": first["cache_write_tokens"] if first else None,
            "handoff_text_estimated_tokens_in_context": h_tok,
            "task_statement_occurrences": len(s_items),
        }
        total += s_total
        calls_total += n
        if F is not None:
            first_total += F
            first_cr += first["cache_read_tokens"] or 0
            first_cw += first["cache_write_tokens"] or 0
            first_uncached += first["input_tokens"] or 0
            fanout_cw += n * max(F - h_tok, 0)
            if not ctx["resumed"]:
                visible = est_tokens(ctx["stdin_text"]) + est_tokens(ctx["append_system_prompt"])
                sess["hidden_initialization_tokens_by_subtraction"] = F - visible
                hidden_init += F - visible
                hidden_sessions += 1
        handoff_cw += n * h_tok
        statement_cw += n * s_tok
        statement_occ += len(s_items)

        for c in sa.parsed.tool_calls:
            if c.end_line is None:
                continue
            text = c.result_text or ""
            weight = sum(1 for m in calls if m["first_line"] > c.end_line)
            tok = est_tokens(text)
            sub = sub_by_consumer.get(c.tool_use_id)
            if sub:
                b = buckets[REACQ_BUCKETS[sub]]
                b["count"] += 1
                b["chars"] += len(text)
                b["estimated_tokens_once"] += tok
                b["context_weighted_tokens"] += tok * weight
                continue
            frac = unique_frac.get(c.tool_use_id)
            if frac:
                u_tok = int(round(tok * frac))
                b = buckets["unique_downstream_acquisition"]
                b["count"] += 1
                b["chars"] += int(round(len(text) * frac))
                b["estimated_tokens_once"] += u_tok
                b["context_weighted_tokens"] += u_tok * weight
                o = buckets["other_tool_results"]
                o["estimated_tokens_once"] += tok - u_tok
                o["context_weighted_tokens"] += (tok - u_tok) * weight
                o["chars"] += len(text) - int(round(len(text) * frac))
                continue
            o = buckets["other_tool_results"]
            o["count"] += 1
            o["chars"] += len(text)
            o["estimated_tokens_once"] += tok
            o["context_weighted_tokens"] += tok * weight
        per_session.append(sess)

    attributed = fanout_cw + handoff_cw + sum(b["context_weighted_tokens"] for b in buckets.values())
    return {
        "version": DECOMPOSITION_VERSION,
        "units": "main-model input tokens (uncached + cache read + cache write); "
                 "context-weighted = tokens x number of API calls whose input contains them",
        "observable": {
            "basis": EXACT,
            "total_input_tokens": total,
            "api_calls": calls_total,
            "sessions": len(sessions),
            "reconciles_with_result_usage": reconciles,
        },
        "session_fanout_context": {
            EXACT: {
                "first_call_input_tokens": first_total,
                "first_call_cache_read_tokens": first_cr,
                "first_call_cache_write_tokens": first_cw,
                "first_call_uncached_input_tokens": first_uncached,
                "sessions": len(sessions),
                "resumed_sessions": sum(1 for s in per_session if s["resumed"]),
                "note": "a session's first API call precedes any tool use",
            },
            RECONSTRUCTED: {
                "context_weighted_tokens": fanout_cw,
                "method": "per session: API calls x (first-call input - estimated handoff text "
                          "in that context); assumes the initial context is re-submitted on "
                          "every call",
            },
            ESTIMATED: {
                "task_statement_occurrences": statement_occ,
                "task_statement_context_weighted_tokens": statement_cw,
                "task_statement_note": "a subset of the fanout figure, not additional",
                "hidden_initialization_tokens_by_subtraction": hidden_init,
                "hidden_initialization_sessions": hidden_sessions,
                "hidden_initialization_note": "fresh sessions only: first-call input minus the "
                                              "estimated visible prompt and role appendix; "
                                              "system prompt + tool schemas together",
            },
            UNAVAILABLE: UNAVAILABLE_ITEMS,
        },
        "handoff_context": {
            EXACT: {
                "handoffs": len(inter),
                "chars": sum(len(h["body"]) for h in inter),
                "utf8_bytes": sum(len(h["body"].encode("utf-8")) for h in inter),
            },
            ESTIMATED: {"tokens_once": sum(est_tokens(h["body"]) for h in inter)},
            RECONSTRUCTED: {"context_weighted_tokens": handoff_cw},
            "provider_usage": "not separately attributable: handoff text is submitted inside "
                              "the prompt, and provider usage covers the whole prompt",
        },
        **{name: {"basis": f"{RECONSTRUCTED} (token sizes {ESTIMATED}; counts and chars exact)",
                  **b} for name, b in buckets.items()},
        "unique_downstream_applicable": bool((unique or {}).get("applicable")),
        "attributed_context_weighted_tokens": attributed,
        "unattributed_remainder_tokens": total - attributed,
        "unattributed_note": "assistant text, tool inputs and thinking carried into later calls, "
                             "plus estimation error; not forced to zero and may be negative",
        "overlap": "categories are disjoint by context position; content-level overlap "
                   "(acquire -> handoff -> reacquire, v1 and v2) is reported separately "
                   "and not subtracted",
        "per_session": per_session,
    }


CATEGORY_ROWS = (
    ("session/context fanout", "fanout"),
    ("handoff transmission", "handoff"),
    ("discretionary reacquisition", "discretionary_reacquisition"),
    ("tool-required reacquisition", "tool_required_reacquisition"),
    ("unique downstream acquisition", "unique_downstream_acquisition"),
    ("verification-associated reacq.", "verification_associated_reacquisition"),
    ("unknown-subcategory reacq.", "unknown_reacquisition"),
    ("other tool results (not a category)", "other_tool_results"),
)


def category_value(d: dict, key: str) -> int:
    if key == "fanout":
        return d["session_fanout_context"][RECONSTRUCTED]["context_weighted_tokens"]
    if key == "handoff":
        return d["handoff_context"][RECONSTRUCTED]["context_weighted_tokens"]
    return d[key]["context_weighted_tokens"]


def pair_decomposition(da: dict, db: dict) -> dict:
    a_total = da["observable"]["total_input_tokens"]
    b_total = db["observable"]["total_input_tokens"]
    rows = []
    for label, key in CATEGORY_ROWS:
        va, vb = category_value(da, key), category_value(db, key)
        rows.append({"category": label, "key": key, "arm_a": va, "arm_b": vb, "delta": vb - va})
    delta = b_total - a_total
    attributed_delta = sum(r["delta"] for r in rows)
    return {
        "arm_a_total_input": a_total,
        "arm_b_total_input": b_total,
        "observable_delta": delta,
        "rows": rows,
        "first_call_input_exact": {
            "arm_a": da["session_fanout_context"][EXACT]["first_call_input_tokens"],
            "arm_b": db["session_fanout_context"][EXACT]["first_call_input_tokens"],
        },
        "handoff_chars_exact": db["handoff_context"][EXACT]["chars"],
        "unattributed_remainder_delta": delta - attributed_delta,
    }
