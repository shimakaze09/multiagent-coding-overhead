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
}


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
    if len(invalid) > MAX_INVALID_REPLACEMENTS:
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
        "counted_run_ids": [e["run_id"] for e in counted],
        "invalid_run_ids": [e["run_id"] for e in invalid],
        "ignored_extra_run_ids": [e["run_id"] for e in valid[MAX_RUNS:]],
        "next_repeat_ids": list(range(start, start + needed)),
    }


def calibration_summary(entries: Sequence[dict]) -> dict:
    return {t: task_status(t, entries) for t in config.STAGE2_CANDIDATES}


def _family(task_id: str) -> str:
    design = stage2_design.load_design(task_id) or {}
    return design.get("task_family", "unknown")


def select_benchmark(statuses: dict) -> dict:
    """Deterministic selection per stratum (cap, family round-robin)."""
    selected, excluded = {}, {}
    for stratum in STRATA:
        members = [t for t, s in statuses.items() if s.get("stratum") == stratum]
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
    """The frozen difficulty record. Refuses unless every candidate is complete."""
    statuses = calibration_summary(entries)
    incomplete = [t for t, s in statuses.items() if s["status"] != "complete"]
    if incomplete:
        raise ValueError(f"calibration incomplete for: {incomplete}")
    bench = select_benchmark(statuses)
    run_ids = sorted(r for s in statuses.values() for r in s["counted_run_ids"])
    return {
        "protocol": PROTOCOL,
        "tasks": {t: {k: s[k] for k in ("n", "successes", "rate", "stratum", "counted_run_ids")}
                  for t, s in statuses.items()},
        "benchmark": bench["selected"],
        "excluded_over_cap": bench["excluded_over_cap"],
        "easy_controls": list(config.STAGE2_EASY_CONTROLS),
        "calibration_runs_sha256": hashlib.sha256("\n".join(run_ids).encode()).hexdigest(),
    }


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
    done = sum(1 for s in statuses.values() if s["status"] == "complete")
    L.append("")
    L.append(f"complete: {done}/{len(statuses)} candidates")
    if labels:
        L.append(f"difficulty labels FROZEN: benchmark {labels['benchmark']}")
    elif done == len(statuses):
        L.append("all candidates complete: freeze with `python runner.py difficulty-freeze`")
    L.append("Strata use Single-Strong calibration runs only; evaluation runs never change them.")
    return "\n".join(L)
