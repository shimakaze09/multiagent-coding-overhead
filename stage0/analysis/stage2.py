"""Stage 2A reporting (PREREGISTRATION section 18): task success at API-equivalent cost.

    runner.py stage2-report --task <task>     one task, every configuration
    runner.py stage2-summary                  the 4-task pilot, comparisons A-D, cases A-E

Configurations: Single Strong (historical Arm A with exact parity, else a fresh
S2_SS run), Single Cheap (S2_S), All-Cheap Multi (S2_R3), Strong-Investigator
Hybrid (S2_R1), Strong-Implementer Hybrid (S2_R2). Arm B (all-strong multi) is
shown for reference only and enters no comparison.

Rules (all preregistered, applied mechanically):
  * SOLVED is the held-out verifier's exit code only. No LLM judge.
  * No scalar quality-cost score. Cost is compared ONLY between runs that both
    solved the task, both valid, and both with exact cost.
  * Invalid runs are listed with their reasons and never enter a comparison.
  * The first valid run of a (task, configuration) is the one used.
"""

from __future__ import annotations

import os
import re
import statistics
from pathlib import Path
from typing import Optional, Sequence

import config
from analysis import ingest, report as report_mod, routing
from arms import single
from harness import claude_cli
from tasks import registry

FAILURE_LOCATION_VERSION = 1
ESCALATION_EVIDENCE_VERSION = 1
# Preregistered descriptive cut for "substantially cheaper" in case A: pooled
# cost ratio at most 0.75 (>= 25% cheaper). The exact ratio is always shown.
SUBSTANTIALLY_CHEAPER_MAX_RATIO = 0.75

SINGLE_STRONG = "SINGLE_STRONG"
CONFIG_ORDER = (SINGLE_STRONG, "S2_S", "S2_R3", "S2_R1", "S2_R2")
LABELS = {
    SINGLE_STRONG: "Single Strong",
    "S2_S": "Single Cheap",
    "S2_R3": "All-Cheap Multi",
    "S2_R1": "Strong-Investigator Hybrid",
    "S2_R2": "Strong-Implementer Hybrid",
    "B": "All-Strong Multi (Arm B, reference only)",
}
COMPARISONS = {
    "A": (SINGLE_STRONG, "S2_R1", "Can one strong Investigator plus cheap orchestration/implementation "
                                  "preserve success at lower cost?"),
    "B": ("S2_S", "S2_R1", "Does selective strong-model use buy reliability above the cheap model alone?"),
    "C": ("S2_R1", "S2_R2", "Where is expensive capability more valuable: diagnosis or implementation?"),
    "D": ("S2_S", "S2_R3", "Does decomposition compensate for weaker model capability?"),
}
CHEAP_CANON = config.MODEL_CLASSES["CHEAP"]["canonical"]
STRONG_CANON = config.MODEL_CLASSES["STRONG"]["canonical"]
A_BASE_CONFIG_HASH = config.RunConfig().config_hash()
PINNED_CLI_VERSION = "2.1.260"


# --------------------------------------------------------------------------
# Per-run metrics: E2 cost by role and model, E3 strong usage, E4 tokens, E5 wall
# --------------------------------------------------------------------------


def _bucket(canon: Optional[str]) -> str:
    return "cheap" if canon == CHEAP_CANON else "strong" if canon == STRONG_CANON else "other"


def run_metrics(mr: dict) -> dict:
    rows = mr["invocations"]
    by_role: dict = {}
    by_model = {b: {"role_assigned": 0.0, "auxiliary": 0.0, "total": 0.0} for b in ("cheap", "strong", "other")}
    tok = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    aux_tok = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    strong = {"invocations": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    thinking = 0
    wall = api_ms = 0.0
    for x in rows:
        a = x["accounting"]
        role = x["logical_role"]
        rc, ac = a["role_cost_usd"] or 0.0, a["auxiliary_cost_usd"] or 0.0
        slot = by_role.setdefault(role, {"invocations": 0, "model_class": x["model_class"],
                                         "role_assigned_cost": 0.0, "auxiliary_cost": 0.0, "cost": 0.0,
                                         "input": 0, "cache_read": 0, "cache_write": 0, "output": 0,
                                         "wall_seconds": 0.0})
        slot["invocations"] += 1
        slot["role_assigned_cost"] += rc
        slot["auxiliary_cost"] += ac
        slot["cost"] += rc + ac
        slot["wall_seconds"] += x["wall_seconds"]
        for k in tok:
            v = a["role_usage"].get(k, 0) or 0
            slot[k] += v
            tok[k] += v
        b = _bucket(a["role_model"])
        by_model[b]["role_assigned"] += rc
        for canon, au in a["auxiliary"].items():
            by_model[_bucket(canon)]["auxiliary"] += au["cost_usd"]
            for k in tok:
                tok[k] += au[k]
                aux_tok[k] += au[k]
            if canon == STRONG_CANON:
                for k in ("input", "cache_read", "cache_write", "output"):
                    strong[k] += au[k]
        if x["model_class"] == "STRONG":
            strong["invocations"] += 1
        if a["role_model"] == STRONG_CANON:
            for k in ("input", "cache_read", "cache_write", "output"):
                strong[k] += a["role_usage"].get(k, 0) or 0
        thinking += sum((a.get("thinking_by_model") or {}).values())
        wall += x["wall_seconds"]
        api_ms += x["duration_api_ms"] or 0
    for b in by_model.values():
        b["total"] = b["role_assigned"] + b["auxiliary"]
    total = sum(b["total"] for b in by_model.values())
    cli_totals = [x["accounting"]["cli_total_cost_usd"] for x in rows]
    strong_cost = by_model["strong"]["total"]
    return {
        "cost_usd": round(total, 6),
        "cost_exact": not routing.has_lower_bound_cost(mr),
        "cli_total_cost_usd": round(sum(cli_totals), 6) if rows and all(
            isinstance(v, (int, float)) for v in cli_totals) else None,
        "cost_by_role": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                         for k, v in by_role.items()},
        "cost_by_model": {k: {kk: round(vv, 6) for kk, vv in v.items()} for k, v in by_model.items()},
        "role_assigned_cost_usd": round(sum(b["role_assigned"] for b in by_model.values()), 6),
        "auxiliary_cost_usd": round(sum(b["auxiliary"] for b in by_model.values()), 6),
        "strong": {**strong, "cost_usd": round(strong_cost, 6),
                   "cost_share": round(strong_cost / total, 4) if total else None},
        "tokens": {**tok, "total_input": tok["input"] + tok["cache_read"] + tok["cache_write"],
                   "thinking_if_exposed": thinking},
        "auxiliary_tokens": aux_tok,
        "wall_seconds": round(wall, 2),
        "api_duration_seconds": round(api_ms / 1000.0, 2),
        "invocations": len(rows),
    }


# --------------------------------------------------------------------------
# Loading runs
# --------------------------------------------------------------------------


def _handoff_texts(raw: dict) -> dict:
    out = {}
    for h in (raw.get("summary") or {}).get("handoffs") or []:
        p = Path(raw["run_dir"]) / h.get("path", "")
        if p.is_file():
            out[h.get("label")] = p.read_text(encoding="utf-8")
    return out


_PYTEST_FAILED = re.compile(r"\b\d+ (failed|errors?)\b", re.I)
_PYTEST_PASSED = re.compile(r"\b\d+ passed\b", re.I)


def _last_agent_test_run(raw: dict) -> dict:
    """Outcome of the last `pytest` call the agents themselves made - a signal
    a runtime escalation trigger could observe (evidence only)."""
    last = None
    for sa in sorted(raw.get("sessions") or [], key=lambda s: s.session_index):
        for c in sa.parsed.tool_calls:
            cmd = (c.input or {}).get("command") or ""
            if "pytest" in cmd and c.result_text is not None and c.tool_use_id not in sa.parsed.denied_tool_use_ids:
                last = (sa.invocation.get("role"), c.result_text)
    if last is None:
        return {"ran": False, "outcome": None, "role": None}
    role, text = last
    outcome = "failed" if _PYTEST_FAILED.search(text) else "passed" if _PYTEST_PASSED.search(text) else "unknown"
    return {"ran": True, "outcome": outcome, "role": role}


def load_entry(run_dir: Path) -> Optional[dict]:
    raw = ingest.load_run(Path(run_dir))
    if raw is None:
        return None
    meta = raw["metadata"]
    arm = meta.get("arm")
    if arm not in ("A", "B") and not routing.is_stage2_arm(arm):
        return None
    r = report_mod.run_report(run_dir)
    mr = r.get("model_routing") or routing.check_run(raw)
    reasons = list((r.get("validity") or {}).get("reasons") or [])
    if not routing.is_stage2_arm(arm) and not mr["verified"]:
        reasons.append(f"model routing not verified: {mr['failed_checks']}")
    stored_prompts = {sa.session_key: sa.invocation.get("stdin_text") for sa in raw["sessions"]}
    return {
        "run_id": r["run_id"],
        "run_dir": str(run_dir),
        "task_id": r["task_id"],
        "arm": arm,
        "repeat_id": r.get("repeat_id"),
        "solved": r.get("solved"),
        "valid": not reasons,
        "validity_reasons": reasons,
        "config_hash": r.get("config_hash"),
        "base_config_hash": (meta.get("stage2_identity") or {}).get("base_config_hash", r.get("config_hash")),
        "stage2_identity": meta.get("stage2_identity"),
        "base_commit": r.get("base_commit"),
        "cli_versions": r.get("session_cli_versions"),
        "task_meta": meta.get("task") or {},
        "visible_tests": r.get("visible_tests"),
        "workspace_integrity": r.get("workspace_integrity"),
        "model_routing": mr,
        "metrics": run_metrics(mr),
        "handoff_texts": _handoff_texts(raw),
        "last_agent_test_run": _last_agent_test_run(raw),
        "stored_prompts": stored_prompts,
    }


def load_entries(run_dirs: Sequence[Path], tasks: Sequence[str] = config.STAGE2_TASKS) -> list[dict]:
    out = []
    for rd in run_dirs:
        meta = ingest._load_json(Path(rd) / "metadata.json") or {}
        if meta.get("task_id") not in tasks:
            continue
        e = load_entry(rd)
        if e is not None:
            out.append(e)
    return out


# --------------------------------------------------------------------------
# Baseline reuse (section 18.6): exact parity or a fresh S2_SS run
# --------------------------------------------------------------------------


_TASK_FIELDS = ("statement", "verifier_source", "verifier_dest_name", "verifier_command",
                "visible_test_command", "read_only_paths")


def baseline_parity(e: dict, reference_base_commit: Optional[str] = None) -> dict:
    """Exact-parity checks for a Single-Strong candidate (Arm A or S2_SS)."""
    task = registry.get_task(e["task_id"])
    cur = task.as_dict()
    mr = e["model_routing"]
    solo = [x for x in mr["invocations"] if x["logical_role"] == "solo"]
    prompts = list(e.get("stored_prompts", {}).values())
    checks = {
        "single agent, one session": e["arm"] in ("A", "S2_SS") and len(mr["invocations"]) == 1 and len(solo) == 1,
        "base config identical (model request, tools, permission policy, limits, turn counting)":
            e["base_config_hash"] == A_BASE_CONFIG_HASH
            and (e["arm"] != "A" or e["config_hash"] == A_BASE_CONFIG_HASH),
        "task commit": bool(e["base_commit"]) and (reference_base_commit is None
                                                   or e["base_commit"] == reference_base_commit),
        "Claude Code version": e["cli_versions"] == [PINNED_CLI_VERSION],
        "requested model 'sonnet', resolved claude-sonnet-5": bool(solo) and all(
            x["requested_model"] == config.STRONG_MODEL
            and x["verification"].get("resolved_canonical") == STRONG_CANON for x in solo)
            and mr["verified"],
        "prompt byte-identical to Arm A": len(prompts) == 1 and prompts[0] == single.build_solo_prompt(task),
        "task statement, held-out verifier, protected paths": all(
            e["task_meta"].get(k) == cur.get(k) for k in _TASK_FIELDS),
        "valid run": e["valid"],
    }
    return {"exact": all(checks.values()), "checks": checks}


def _reference_commit(entries: Sequence[dict], task_id: str) -> Optional[str]:
    commits = {e["base_commit"] for e in entries if e["task_id"] == task_id
               and routing.is_stage2_arm(e["arm"]) and e["base_commit"]}
    return commits.pop() if len(commits) == 1 else None


def select_single_strong(entries: Sequence[dict], task_id: str) -> dict:
    ref = _reference_commit(entries, task_id)
    considered = []
    for arm in ("A", "S2_SS"):  # historical reuse first, then a fresh baseline
        for e in sorted((x for x in entries if x["task_id"] == task_id and x["arm"] == arm),
                        key=lambda x: x["run_id"]):
            p = baseline_parity(e, ref)
            considered.append({"run_id": e["run_id"], "arm": arm, "parity": p})
            if p["exact"]:
                return {"entry": e, "source": "historical Arm A" if arm == "A" else "fresh S2_SS",
                        "considered": considered}
    return {"entry": None, "source": "pending: a fresh Single-Strong (S2_SS) run is required",
            "considered": considered}


def select_config(entries: Sequence[dict], task_id: str, key: str) -> dict:
    if key == SINGLE_STRONG:
        return select_single_strong(entries, task_id)
    runs = sorted((x for x in entries if x["task_id"] == task_id and x["arm"] == key),
                  key=lambda x: x["run_id"])
    valid = [x for x in runs if x["valid"]]
    chosen = valid[0] if valid else (runs[0] if runs else None)
    return {"entry": chosen, "source": "first valid run" if valid else ("invalid only" if runs else "pending"),
            "others": [x["run_id"] for x in runs if x is not chosen]}


# --------------------------------------------------------------------------
# E6 failure location (mechanical, no LLM judge) and E7 escalation evidence
# --------------------------------------------------------------------------


_ROLE_LOCATION = {"coordinator": "coordinator_routing", "investigator": "investigator_diagnosis",
                  "implementer": "implementer_coding", "solo": "implementer_coding"}
_LOCATION_ROLE = {"coordinator_routing": "coordinator", "investigator_diagnosis": "investigator",
                  "implementer_coding": "implementer", "verification": "implementer"}


def _mentions(text: str, paths: Sequence[str]) -> bool:
    return any(p in text or os.path.basename(p) in text for p in paths)


def failure_location(e: dict) -> Optional[dict]:
    """Rules R1-R7, first match wins; None when the run solved the task."""
    if e["solved"] is not False:
        return None
    task = registry.get_task(e["task_id"])
    expected = list(task.expected_edit_paths)
    changed = sorted(c.get("path") for c in (e["workspace_integrity"] or {}).get("changed_paths") or [])
    touched = bool(set(changed) & set(expected))
    visible = (e["visible_tests"] or {}).get("passed")
    h = e["handoff_texts"]
    rows = e["model_routing"]["invocations"]
    evidence = {"expected_edit_paths": expected, "changed_paths": changed,
                "expected_file_changed": touched, "visible_tests_passed": visible,
                "report_names_expected_file": _mentions(h.get("investigation_report", ""), expected)
                if "investigation_report" in h else None,
                "instruction_names_expected_file": _mentions(h.get("implementation_instruction", ""), expected)
                if "implementation_instruction" in h else None,
                "last_agent_test_run": e["last_agent_test_run"]}

    single_agent = e["arm"] in ("A", "S2_S", "S2_SS")

    def out(location, rule, role=None):
        return {"version": FAILURE_LOCATION_VERSION, "location": location, "rule": rule,
                "role": role or ("solo" if single_agent else _LOCATION_ROLE.get(location)),
                "evidence": evidence}

    for x in rows:
        if x["termination_reason"] != claude_cli.TERM_COMPLETED or x["is_error"]:
            return out(_ROLE_LOCATION.get(x["logical_role"], "unknown"),
                       f"R1 {x['session_key']} did not complete ({x['termination_reason']})", x["logical_role"])
    multi = any(x["logical_role"] == "investigator" for x in rows)
    if multi and not evidence["report_names_expected_file"]:
        return out("investigator_diagnosis", "R2 the investigation report names no file the fix must change")
    if multi and not evidence["instruction_names_expected_file"] and not touched:
        return out("coordinator_routing", "R3 the implementation instruction names no expected file, and none was changed")
    if not touched:
        return out("implementer_coding", "R4 no file the fix must change was changed")
    if visible is False:
        return out("implementer_coding", "R5 the visible tests fail on the final workspace")
    if visible is True:
        return out("verification", "R6 the visible tests pass but the held-out verifier fails")
    return out("unknown", "R7 no rule matched")


# For each role, the configurations that assign STRONG to it (the counterfactual).
_STRONG_FOR_ROLE = {"investigator": ("S2_R1",), "implementer": ("S2_R2",), "solo": (SINGLE_STRONG,),
                    "coordinator": ("B",)}


def _class_of(key: str, role: Optional[str]) -> Optional[str]:
    if key in config.STAGE2_CONFIGS:
        return config.STAGE2_CONFIGS[key]["role_classes"].get(role)
    return "STRONG" if key in (SINGLE_STRONG, "B") else None


def escalation_evidence(key: str, e: dict, failure: Optional[dict], task_picks: dict) -> Optional[dict]:
    """E7, evidence only (no escalation is implemented): would a strong model in
    the failing role plausibly have recovered this failure?"""
    if failure is None:
        return None
    role = failure["role"]
    cls = _class_of(key, role)
    base = {"version": ESCALATION_EVIDENCE_VERSION, "failure_role": role, "failure_role_model_class": cls,
            "involves_cheap_role": cls == "CHEAP",
            "runtime_observable_signals": {
                "invocation_not_completed": failure["rule"].startswith("R1"),
                "last_agent_test_run": e["last_agent_test_run"]}}
    if cls != "CHEAP":
        return {**base, "verdict": "not_applicable (failing role already strong)"}
    counter = []
    for k in _STRONG_FOR_ROLE.get(role, ()):
        pick = task_picks.get(k) or {}
        c = pick.get("entry")
        if c is not None and c["valid"]:
            counter.append({"config": k, "run_id": c["run_id"], "solved": c["solved"]})
    if any(x["solved"] for x in counter):
        verdict = "potentially_recoverable (a configuration with a strong model in this role solved the task)"
    elif counter:
        verdict = "not_indicated (the strong-in-this-role configuration also failed)"
    else:
        verdict = "undetermined (no valid strong-in-this-role counterpart)"
    return {**base, "verdict": verdict, "counterfactual_runs": counter}


# --------------------------------------------------------------------------
# Per-task picks, comparisons A-D, interpretation cases A-E
# --------------------------------------------------------------------------


def task_picks(entries: Sequence[dict], task_id: str) -> dict:
    picks = {k: select_config(entries, task_id, k) for k in CONFIG_ORDER}
    bs = sorted((x for x in entries if x["task_id"] == task_id and x["arm"] == "B"), key=lambda x: x["run_id"])
    b_valid = [x for x in bs if x["valid"] and x["config_hash"] == A_BASE_CONFIG_HASH]
    picks["B"] = {"entry": b_valid[0] if b_valid else None,
                  "source": "historical Arm B (exact base config)" if b_valid else
                  ("no Arm B run with exact parity" if bs else "none")}
    for k, p in picks.items():
        e = p.get("entry")
        p["failure"] = failure_location(e) if e is not None and e["valid"] else None
    for k, p in picks.items():
        e = p.get("entry")
        p["escalation"] = (escalation_evidence(k, e, p["failure"], picks)
                           if e is not None and k != SINGLE_STRONG and k != "B" else None)
    return picks


def _usable(p: dict) -> Optional[dict]:
    e = (p or {}).get("entry")
    return e if e is not None and e["valid"] else None


def compare(picks_by_task: dict, x_key: str, y_key: str) -> dict:
    """y relative to x. Cost ratios only where both are valid, both solved, and
    both have exact cost. Invalid runs never enter."""
    rows, xs, ys = [], [], []
    for task, picks in picks_by_task.items():
        ex, ey = _usable(picks.get(x_key)), _usable(picks.get(y_key))
        if ex is None or ey is None:
            rows.append({"task": task, "complete": False})
            continue
        row = {"task": task, "complete": True, "x_solved": ex["solved"], "y_solved": ey["solved"]}
        if ex["solved"] and ey["solved"] and ex["metrics"]["cost_exact"] and ey["metrics"]["cost_exact"]:
            cx, cy = ex["metrics"]["cost_usd"], ey["metrics"]["cost_usd"]
            row.update({"x_cost": cx, "y_cost": cy, "cost_ratio": round(cy / cx, 3) if cx else None,
                        "wall_ratio": round(ey["metrics"]["wall_seconds"] / ex["metrics"]["wall_seconds"], 3)
                        if ex["metrics"]["wall_seconds"] else None})
            xs.append(cx)
            ys.append(cy)
        rows.append(row)
    done = [r for r in rows if r["complete"]]
    ratios = [r["cost_ratio"] for r in done if r.get("cost_ratio") is not None]
    return {
        "x": x_key, "y": y_key, "rows": rows,
        "tasks_compared": len(done),
        "x_solved": sum(1 for r in done if r["x_solved"]),
        "y_solved": sum(1 for r in done if r["y_solved"]),
        "jointly_solved_with_exact_cost": len(xs),
        "pooled_cost_ratio": round(sum(ys) / sum(xs), 3) if xs and sum(xs) else None,
        "median_cost_ratio": round(statistics.median(ratios), 3) if ratios else None,
    }


def _solved_set(picks_by_task: dict, key: str) -> Optional[set]:
    out = set()
    for task, picks in picks_by_task.items():
        e = _usable(picks.get(key))
        if e is None:
            return None
        if e["solved"]:
            out.add(task)
    return out


def interpretation_cases(picks_by_task: dict) -> dict:
    """Section 18.8. Not mutually exclusive; evaluated only when every
    configuration has a valid run on every task."""
    s = {k: _solved_set(picks_by_task, k) for k in CONFIG_ORDER}
    if any(v is None for v in s.values()):
        missing = [k for k, v in s.items() if v is None]
        return {"complete": False, "pending_configurations": missing, "cases": {}}
    ss, cheap, r3, r1, r2 = (s[SINGLE_STRONG], s["S2_S"], s["S2_R3"], s["S2_R1"], s["S2_R2"])
    a = compare(picks_by_task, SINGLE_STRONG, "S2_R1")
    b = compare(picks_by_task, "S2_R1", "S2_S")
    c = compare(picks_by_task, "S2_R1", "S2_R2")
    cases = {
        "A": {"holds": r1 == ss and a["pooled_cost_ratio"] is not None
              and a["pooled_cost_ratio"] <= SUBSTANTIALLY_CHEAPER_MAX_RATIO,
              "detail": f"Strong-Investigator solves {sorted(r1)} vs Single Strong {sorted(ss)}; "
                        f"pooled cost ratio R1/SS {a['pooled_cost_ratio']} (cut {SUBSTANTIALLY_CHEAPER_MAX_RATIO})"},
        "B": {"holds": cheap >= r1 and b["pooled_cost_ratio"] is not None and b["pooled_cost_ratio"] < 1,
              "detail": f"Single Cheap solves {sorted(cheap)} vs hybrid {sorted(r1)}; pooled cost "
                        f"S/R1 {b['pooled_cost_ratio']}; Single Cheap matches Single Strong: {cheap >= ss}"},
        "C": {"holds": bool(ss - r1),
              "detail": f"tasks Single Strong solves and Strong-Investigator fails: {sorted(ss - r1)}; "
                        f"(Strong-Implementer fails: {sorted(ss - r2)})"},
        "D": {"holds": len(r2) > len(r1),
              "cost_only_variant": len(r2) == len(r1) and c["pooled_cost_ratio"] is not None
              and c["pooled_cost_ratio"] < 1,
              "detail": f"Strong-Implementer solves {len(r2)}, Strong-Investigator {len(r1)}; "
                        f"pooled cost R2/R1 {c['pooled_cost_ratio']}"},
        "E": {"holds": len(r3) > len(cheap),
              "detail": f"All-Cheap Multi solves {len(r3)}, Single Cheap {len(cheap)}"},
    }
    return {"complete": True, "cases": cases, "solved_sets": {k: sorted(v) for k, v in s.items()}}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _f(v, nd=4):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _table_row(label: str, p: dict) -> str:
    e = p.get("entry")
    if e is None:
        return f"  {label:<42}{p.get('source', 'pending')}"
    m = e["metrics"]
    cost = _f(m["cost_usd"]) + ("" if m["cost_exact"] else " (lower bound)")
    return (f"  {label:<42}{_f(e['solved']):<8}{('yes' if e['valid'] else 'NO'):<7}{cost:<22}"
            f"{_f(m['wall_seconds'], 1):<9}{m['strong']['invocations']:<8}{_f(m['strong']['cost_usd']):<12}"
            f"{_f(m['tokens']['total_input']):<13}{_f(m['tokens']['output']):<10}{e['run_id']}")


def render_task_report(entries: Sequence[dict], task_id: str) -> str:
    picks = task_picks(entries, task_id)
    L: list[str] = []
    P = L.append
    P(f"=== Stage 2A report: {task_id}  (success at API-equivalent cost; PREREGISTRATION 18) ===")
    P(f"CHEAP  = {config.CHEAP_MODEL} (resolved {config.CHEAP_MODEL_RESOLVED})   "
      f"STRONG = {config.STRONG_MODEL} (resolved {config.STRONG_MODEL_RESOLVED})   "
      f"effort: {config.STAGE2_EFFORT_POLICY['policy']}")
    P("")
    P(f"  {'Config':<42}{'Solved':<8}{'Valid':<7}{'Cost USD (API-eq.)':<22}{'Wall s':<9}{'Strong':<8}"
      f"{'Strong $':<12}{'Input':<13}{'Output':<10}Run")
    P("  " + "-" * 150)
    for k in CONFIG_ORDER + ("B",):
        P(_table_row(LABELS[k], picks[k]))
    P("")
    P("Input = all-model uncached + cache read + cache write; Output = all models. Cost includes Claude")
    P("Code's auxiliary model usage. Cost is compared only between valid runs that both solved.")
    P("")
    P("-- Single-Strong baseline (section 18.6: exact parity, or a fresh S2_SS run) --")
    ss = picks[SINGLE_STRONG]
    P(f"  source: {ss['source']}")
    for c in ss.get("considered", []):
        failed = [n for n, ok in c["parity"]["checks"].items() if not ok]
        P(f"  {c['run_id']}: {'EXACT PARITY' if c['parity']['exact'] else 'not reusable: ' + '; '.join(failed)}")
    P(f"  Arm B reference: {picks['B']['source']}")
    for k in CONFIG_ORDER + ("B",):
        e = picks[k].get("entry")
        if e is None:
            continue
        m = e["metrics"]
        P("")
        P(f"-- {LABELS[k]}: {e['run_id']} --")
        if not e["valid"]:
            P(f"  INVALID: {'; '.join(e['validity_reasons'])}")
        for x in e["model_routing"]["invocations"]:
            a = x["accounting"]
            P(f"  {x['session_key']:<16}{x['logical_role'] or '-':<13}{x['model_class'] or '-':<7}"
              f"requested {x['requested_model']} -> resolved {x['resolved_model']}  "
              f"verified {x['verification']['verified']}  role ${_f(a['role_cost_usd'])}  "
              f"aux ${_f(a['auxiliary_cost_usd'])} {sorted(a['auxiliary']) or ''}  effort {x['reasoning_effort']['requested']}")
        P("  E2 cost by role (role-assigned + auxiliary = all-model):")
        for role, v in m["cost_by_role"].items():
            P(f"    {role:<13}{v['model_class'] or '-':<7}{_f(v['role_assigned_cost'])} + {_f(v['auxiliary_cost'])}"
              f" = {_f(v['cost'])}   invocations {v['invocations']}   wall {_f(v['wall_seconds'], 1)} s")
        P(f"    total        {_f(m['role_assigned_cost_usd'])} + {_f(m['auxiliary_cost_usd'])} = {_f(m['cost_usd'])}"
          f"   (CLI total_cost_usd {_f(m['cli_total_cost_usd'])})")
        P("  E2 cost by model (role-assigned / auxiliary / total):")
        for b in ("cheap", "strong", "other"):
            v = m["cost_by_model"][b]
            P(f"    {b:<13}{_f(v['role_assigned'])} / {_f(v['auxiliary'])} / {_f(v['total'])}")
        s = m["strong"]
        P(f"  E3 strong model: invocations {s['invocations']}, input {_f(s['input'])}, output {_f(s['output'])}, "
          f"cache read {_f(s['cache_read'])}, cache write {_f(s['cache_write'])}, "
          f"cost {_f(s['cost_usd'])} ({_f(s['cost_share'])} of total)")
        t = m["tokens"]
        P(f"  E4 tokens (all models): uncached input {_f(t['input'])}, cache read {_f(t['cache_read'])}, "
          f"cache write {_f(t['cache_write'])}, output {_f(t['output'])}, thinking {_f(t['thinking_if_exposed'])}"
          f"; auxiliary share: input {_f(m['auxiliary_tokens']['input'])}, output {_f(m['auxiliary_tokens']['output'])}")
        P(f"  E5 wall {_f(m['wall_seconds'], 1)} s (API time {_f(m['api_duration_seconds'], 1)} s)")
        f = picks[k].get("failure")
        if f:
            P(f"  E6 failure location: {f['location']}  [{f['rule']}]")
            P(f"     evidence: {f['evidence']}")
        esc = picks[k].get("escalation")
        if esc:
            P(f"  E7 escalation: {esc['verdict']}  (failing role {esc['failure_role']}, "
              f"class {esc['failure_role_model_class']})")
    return "\n".join(L)


def render_summary(entries: Sequence[dict], tasks: Sequence[str] = config.STAGE2_TASKS) -> str:
    picks_by_task = {t: task_picks(entries, t) for t in tasks}
    L: list[str] = []
    P = L.append
    P("=== Stage 2A summary: heterogeneous model routing (4 tasks; PREREGISTRATION 18) ===")
    P("per task and configuration: SOLVED / API-equivalent cost USD / wall s / strong calls / strong $ / input / output")
    for t in tasks:
        P("")
        P(f"{t}")
        for k in CONFIG_ORDER:
            P(_table_row(LABELS[k], picks_by_task[t][k]))
            f = picks_by_task[t][k].get("failure")
            if f:
                esc = picks_by_task[t][k].get("escalation") or {}
                P(f"      E6 {f['location']} [{f['rule'].split(' ')[0]}]; E7 {esc.get('verdict', '-')}")
    P("")
    P("-- success (valid runs only) --")
    for k in CONFIG_ORDER:
        done = [_usable(picks_by_task[t][k]) for t in tasks]
        n = sum(1 for e in done if e is not None)
        P(f"  {LABELS[k]:<30} solved {sum(1 for e in done if e and e['solved'])}/{n} valid"
          f"{'' if n == len(tasks) else f'  ({len(tasks) - n} pending/invalid)'}")
    P("")
    P("-- comparisons (cost only where both solved; ratio = second / first) --")
    for name, (x, y, q) in COMPARISONS.items():
        c = compare(picks_by_task, x, y)
        P(f"  {name}: {LABELS[x]} vs {LABELS[y]}  - {q}")
        P(f"     tasks compared {c['tasks_compared']}; solved {c['x_solved']} vs {c['y_solved']}; "
          f"jointly solved {c['jointly_solved_with_exact_cost']}; pooled cost ratio {_f(c['pooled_cost_ratio'], 3)}; "
          f"median {_f(c['median_cost_ratio'], 3)}")
        for r in c["rows"]:
            if r["complete"]:
                P(f"       {r['task']:<26} solved {_f(r['x_solved'])}/{_f(r['y_solved'])}"
                  + (f"  cost {_f(r['x_cost'])} -> {_f(r['y_cost'])} (x{_f(r['cost_ratio'], 3)})"
                     if r.get("cost_ratio") is not None else "  (cost not compared)"))
    P("")
    ic = interpretation_cases(picks_by_task)
    P("-- interpretation cases (section 18.8; not mutually exclusive) --")
    if not ic["complete"]:
        P(f"  pending: no valid run yet for {', '.join(LABELS[k] for k in ic['pending_configurations'])} on every task")
    else:
        for name, v in ic["cases"].items():
            extra = " (cost-only variant)" if v.get("cost_only_variant") else ""
            P(f"  Case {name}: {'HOLDS' if v['holds'] else 'does not hold'}{extra} - {v['detail']}")
    P("")
    P("Descriptive only (n = 1 per task and configuration). No scalar quality-cost score.")
    P("Invalid runs are listed but never enter success counts or cost comparisons.")
    return "\n".join(L)
