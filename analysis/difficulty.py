"""Stage 2 amendment, Phase C: empirical difficulty from Single Strong only
(PREREGISTRATION section 19.5).

Only runs tagged ``stage2_phase == "calibration"``, of configuration S2_SS, on
a candidate task, ever enter here. Evaluation runs, other architectures and
untagged runs are filtered out before anything is counted, so evaluation
results cannot change a difficulty label.

Sequential rule (fixed in advance, outcome-blind w.r.t. architectures):
  * every candidate gets 3 valid Single-Strong runs;
  * 3/3 successes -> complete (all-success early stop);
  * otherwise it gets 2 more (5 in total) -> complete.
  Invalid runs are not counted and are replaced by the next repeat id.

Stratum from the pooled success rate p = k/n of the counted runs:
  easy p >= 0.9 | medium 0.7 <= p < 0.9 | hard 0.4 <= p < 0.7
  very_hard 0 < p < 0.4 | beyond p = 0
With n = 5: 5 easy, 4 medium, 3 or 2 hard, 1 very_hard, 0 beyond; n = 3: 3/3 easy.

Benchmark selection when a stratum has more tasks than its cap: round-robin
over task families (fixed family order), within a family by sha256(task_id).
Nothing but the frozen stratum and the design family is used.

Amendment 11 adds one exclusion, INFRASTRUCTURE only: a candidate listed in
`config.STAGE2_INFRASTRUCTURE_UNRESOLVED` cannot obtain its valid observations
because eligibility invalidation recurs, so it gets status
`infrastructure_unresolved`, no stratum, and is kept out of the difficulty
pool, the distribution, the frozen labels and the evaluation benchmark. Its
valid observations stay in the record as descriptive counts. This is not
performance-based selection: no measured success rate takes a task out.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional, Sequence

import config
from tasks import stage2_design

DIFFICULTY_PROTOCOL_VERSION = 1
CALIBRATION_ARM = config.STAGE2_CALIBRATION_ARM
INITIAL_RUNS = 3
MAX_RUNS = 5
MAX_INVALID_REPLACEMENTS = 2
STRATA = ("easy", "medium", "hard", "very_hard", "beyond")
INFRASTRUCTURE_UNRESOLVED_STATUS = "infrastructure_unresolved"
STRATUM_LOWER_BOUNDS = (("easy", 0.9), ("medium", 0.7), ("hard", 0.4))
STRATUM_CAPS = {"easy": 2, "medium": 3, "hard": 3, "very_hard": 3, "beyond": 2}
EASY_CONTROL_STRATUM = "easy_control"

PROTOCOL = {
    "version": DIFFICULTY_PROTOCOL_VERSION,
    "calibration_arm": CALIBRATION_ARM,
    "initial_runs": INITIAL_RUNS,
    "max_runs": MAX_RUNS,
    "early_stop": "all successes after the initial runs",
    "max_invalid_replacements": MAX_INVALID_REPLACEMENTS,
    "strata": {"easy": ">= 0.9", "medium": "[0.7, 0.9)", "hard": "[0.4, 0.7)",
               "very_hard": "(0, 0.4)", "beyond": "0"},
    "caps": STRATUM_CAPS,
    "selection": "round-robin over families in design order, then sha256(task_id)",
    "infrastructure_exclusion": {
        "amendment": 11,
        "status": INFRASTRUCTURE_UNRESOLVED_STATUS,
        "tasks": dict(config.STAGE2_INFRASTRUCTURE_UNRESOLVED),
        "basis": "infrastructure only; never a measured success rate",
    },
}


def difficulty_pool() -> tuple[str, ...]:
    """Candidates whose calibration can still produce a difficulty label."""
    return tuple(t for t in config.STAGE2_CANDIDATES
                 if t not in config.STAGE2_INFRASTRUCTURE_UNRESOLVED)


def stratum_for(successes: int, n: int) -> str:
    if n <= 0:
        raise ValueError("no counted runs")
    p = successes / n
    for name, lower in STRATUM_LOWER_BOUNDS:
        if p >= lower:
            return name
    return "very_hard" if successes > 0 else "beyond"


def calibration_entries(entries: Iterable[dict]) -> list[dict]:
    """The only runs difficulty is ever computed from."""
    return [e for e in entries
            if e.get("stage2_phase") == "calibration" and e.get("arm") == CALIBRATION_ARM
            and e.get("task_id") in config.STAGE2_CANDIDATES]


def task_status(task_id: str, entries: Sequence[dict]) -> dict:
    runs = sorted((e for e in calibration_entries(entries) if e["task_id"] == task_id),
                  key=lambda e: (e.get("repeat_id") or 0, e["run_id"]))
    valid = [e for e in runs if e["valid"]]
    invalid = [e for e in runs if not e["valid"]]
    counted = valid[:MAX_RUNS]
    n, k = len(counted), sum(1 for e in counted if e["solved"])
    excluded_reason = config.STAGE2_INFRASTRUCTURE_UNRESOLVED.get(task_id)
    if excluded_reason:
        status, needed = INFRASTRUCTURE_UNRESOLVED_STATUS, 0
    elif len(invalid) > MAX_INVALID_REPLACEMENTS:
        status, needed = "unresolved_too_many_invalid_runs", 0
    elif n < INITIAL_RUNS:
        status, needed = "needs_runs", INITIAL_RUNS - n
    elif n == INITIAL_RUNS and k == INITIAL_RUNS:
        status, needed = "complete", 0
    elif n < MAX_RUNS:
        status, needed = "needs_runs", MAX_RUNS - n
    else:
        status, needed = "complete", 0
    used = [e.get("repeat_id") or 0 for e in runs]
    start = max(used, default=0) + 1
    return {
        "task_id": task_id,
        "status": status,
        "n": n,
        "successes": k,
        "rate": round(k / n, 4) if n else None,
        "stratum": stratum_for(k, n) if status == "complete" else None,
        "included_in_difficulty_pool": excluded_reason is None,
        "included_in_evaluation_benchmark": excluded_reason is None,
        "exclusion_reason": excluded_reason,
        "valid_observations": n,
        "valid_successes": k,
        "invalid_attempts": len(invalid),
        "counted_run_ids": [e["run_id"] for e in counted],
        "invalid_run_ids": [e["run_id"] for e in invalid],
        "ignored_extra_run_ids": [e["run_id"] for e in valid[MAX_RUNS:]],
        "next_repeat_ids": list(range(start, start + needed)),
    }


def calibration_summary(entries: Sequence[dict]) -> dict:
    return {t: task_status(t, entries) for t in config.STAGE2_CANDIDATES}


def distribution(statuses: dict) -> dict:
    """Strata counts over the difficulty pool only, plus the infrastructure
    count reported beside them (amendment 11, item 9)."""
    out = {s: 0 for s in STRATA}
    for t, s in statuses.items():
        if not s.get("included_in_difficulty_pool", True):
            continue
        if s.get("stratum") in out:
            out[s["stratum"]] += 1
    out["infrastructure_unresolved"] = sum(
        1 for s in statuses.values() if s.get("status") == INFRASTRUCTURE_UNRESOLVED_STATUS)
    return out


def _codebase(task_id: str) -> str:
    design = stage2_design.load_design(task_id) or {}
    return design.get("codebase", "unknown")


def adequacy(statuses: dict) -> dict:
    """Amendment 11 items 11 and 12: does this pool support the intended
    difficulty frontier? Mechanical, from the frozen strata only."""
    d = distribution(statuses)
    middle = [t for t, s in statuses.items()
              if s.get("included_in_difficulty_pool", True)
              and s.get("stratum") in ("medium", "hard", "very_hard")]
    codebases = sorted({_codebase(t) for t in middle})
    intermediate = d["medium"] + d["hard"]
    return {
        "distribution": d,
        "at_least_one_medium": d["medium"] >= 1,
        "at_least_one_hard": d["hard"] >= 1,
        "non_easy_non_beyond_codebases": codebases,
        "non_easy_non_beyond_in_more_than_one_codebase": len(codebases) > 1,
        "intermediate_region_exists": intermediate >= 1,
        "bimodal_easy_beyond_split": (intermediate == 0 and d["easy"] >= 1
                                      and (d["beyond"] + d["very_hard"]) >= 1),
        "usable_for_quality_cost_frontier": intermediate >= 1,
    }


POOL_FAILED = "CANDIDATE POOL FAILED TO RESOLVE THE INTERMEDIATE DIFFICULTY FRONTIER"


def _family(task_id: str) -> str:
    design = stage2_design.load_design(task_id) or {}
    return design.get("task_family", "unknown")


def select_benchmark(statuses: dict) -> dict:
    """Deterministic selection per stratum (cap, family round-robin)."""
    selected, excluded = {}, {}
    for stratum in STRATA:
        members = [t for t, s in statuses.items() if s.get("stratum") == stratum
                   and s.get("included_in_difficulty_pool", True)]
        by_family: dict = {}
        for t in sorted(members, key=lambda t: hashlib.sha256(t.encode()).hexdigest()):
            by_family.setdefault(_family(t), []).append(t)
        order = []
        queues = [by_family[f] for f in stage2_design.FAMILIES + ("unknown",) if f in by_family]
        while any(queues):
            for q in queues:
                if q:
                    order.append(q.pop(0))
        cap = STRATUM_CAPS[stratum]
        selected[stratum], excluded[stratum] = order[:cap], order[cap:]
    return {"selected": selected, "excluded_over_cap": excluded}


def freeze_labels(entries: Sequence[dict]) -> dict:
    """The frozen difficulty record. Refuses unless every task in the
    difficulty pool is complete. Infrastructure-unresolved candidates
    (amendment 11) are recorded separately with descriptive counts only: they
    have no stratum, are not in `tasks`, and cannot reach the benchmark."""
    statuses = calibration_summary(entries)
    pool = difficulty_pool()
    incomplete = [t for t in pool if statuses[t]["status"] != "complete"]
    if incomplete:
        raise ValueError(f"calibration incomplete for: {incomplete}")
    adq = adequacy(statuses)
    if not adq["usable_for_quality_cost_frontier"]:
        # Amendment 11 item 12: a distribution with no Medium and no Hard task
        # is a valid calibration result and a stopping point, not a benchmark.
        raise ValueError(
            f"{POOL_FAILED}: distribution {adq['distribution']}; "
            "difficulty labels must not be frozen and no evaluation run may be generated")
    bench = select_benchmark(statuses)
    run_ids = sorted(r for t in pool for r in statuses[t]["counted_run_ids"])
    unresolved = {
        t: {"calibration_status": INFRASTRUCTURE_UNRESOLVED_STATUS.upper(),
            "valid_observations": statuses[t]["valid_observations"],
            "valid_successes": statuses[t]["valid_successes"],
            "invalid_attempts": statuses[t]["invalid_attempts"],
            "included_in_difficulty_pool": False,
            "included_in_evaluation_benchmark": False,
            "exclusion_basis": "infrastructure",
            "reason": statuses[t]["exclusion_reason"],
            "descriptive_records_only": True}
        for t in config.STAGE2_CANDIDATES if t not in pool}
    labels = {
        "protocol": PROTOCOL,
        "tasks": {t: {k: statuses[t][k]
                      for k in ("n", "successes", "rate", "stratum", "counted_run_ids")}
                  for t in pool},
        "difficulty_pool": list(pool),
        "infrastructure_unresolved": unresolved,
        "distribution": distribution(statuses),
        "adequacy": adq,
        "benchmark": bench["selected"],
        "excluded_over_cap": bench["excluded_over_cap"],
        "easy_controls": list(config.STAGE2_EASY_CONTROLS),
        "calibration_runs_sha256": hashlib.sha256("\n".join(run_ids).encode()).hexdigest(),
    }
    assert_no_excluded_task(labels)
    return labels


def assert_no_excluded_task(labels: dict) -> None:
    """An infrastructure-unresolved task must not appear anywhere a difficulty
    label, a benchmark slot or an evaluation run could be derived from."""
    excluded = set(config.STAGE2_INFRASTRUCTURE_UNRESOLVED)
    reachable = set(labels.get("tasks") or {}) | set(labels.get("easy_controls") or [])
    for tasks in (labels.get("benchmark") or {}).values():
        reachable |= set(tasks)
    for tasks in (labels.get("excluded_over_cap") or {}).values():
        reachable |= set(tasks)
    bad = sorted(excluded & reachable)
    if bad:
        raise ValueError(
            f"infrastructure-unresolved task(s) reached difficulty aggregation: {bad}")


def labels_sha256(path: Path = config.STAGE2_DIFFICULTY_LABELS) -> Optional[str]:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def load_labels(path: Path = config.STAGE2_DIFFICULTY_LABELS) -> Optional[dict]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def write_labels(labels: dict, path: Path = config.STAGE2_DIFFICULTY_LABELS) -> str:
    p = Path(path)
    if p.exists():
        raise FileExistsError(f"difficulty labels are already frozen: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return labels_sha256(p)


def task_stratum(labels: dict, task_id: str) -> Optional[str]:
    if task_id in (labels or {}).get("easy_controls", []):
        return EASY_CONTROL_STRATUM
    for stratum, tasks in (labels or {}).get("benchmark", {}).items():
        if task_id in tasks:
            return stratum
    return None


def render_summary(entries: Sequence[dict], labels: Optional[dict] = None) -> str:
    statuses = calibration_summary(entries)
    L = ["=== Stage 2 Phase C: difficulty calibration (Single Strong only; PREREGISTRATION 19.5) ==="]
    L.append(f"{'task':<18}{'status':<34}{'k/n':<7}{'rate':<7}{'stratum':<11}next repeat ids")
    for t, s in statuses.items():
        L.append(f"{t:<18}{s['status']:<34}{s['successes']}/{s['n']:<5}{str(s['rate'] or '-'):<7}"
                 f"{str(s['stratum'] or '-'):<11}{s['next_repeat_ids'] or '-'}")
        if s["invalid_run_ids"]:
            L.append(f"    invalid (not counted): {', '.join(s['invalid_run_ids'])}")
        if s["exclusion_reason"]:
            L.append(f"    EXCLUDED (infrastructure): {s['exclusion_reason']}; descriptive only: "
                     f"{s['valid_successes']}/{s['valid_observations']} valid, "
                     f"{s['invalid_attempts']} invalid")
    pool = difficulty_pool()
    done = sum(1 for t in pool if statuses[t]["status"] == "complete")
    dist = distribution(statuses)
    L.append("")
    L.append(f"complete: {done}/{len(pool)} pool candidates "
             f"({len(statuses) - len(pool)} infrastructure-unresolved, excluded)")
    L.append("distribution (pool only): " + "  ".join(f"{k}={v}" for k, v in dist.items()))
    adq = adequacy(statuses)
    L.append(f"intermediate region (Medium or Hard): "
             f"{'YES' if adq['intermediate_region_exists'] else 'NO'}"
             f"   medium={dist['medium']} hard={dist['hard']}"
             f"   non-Easy/non-Beyond codebases: {adq['non_easy_non_beyond_codebases'] or '-'}")
    if labels:
        L.append(f"difficulty labels FROZEN: benchmark {labels['benchmark']}")
    elif done == len(pool) and adq["usable_for_quality_cost_frontier"]:
        L.append("all pool candidates complete: freeze with `python runner.py difficulty-freeze`")
    elif done == len(pool):
        L.append(f"{POOL_FAILED} (amendment 11 item 12): calibration is complete, "
                 "difficulty must NOT be frozen and no evaluation run may be generated")
    L.append("Strata use Single-Strong calibration runs only; evaluation runs never change them.")
    return "\n".join(L)
