"""Stage 2 amendment, Phase E: quality x cost x difficulty (PREREGISTRATION 19).

Evaluation runs only (``stage2_phase == "evaluation"``), on the frozen
benchmark, plus the EASY-control stratum's exact-parity historical runs. Per
stratum and architecture: success probability with a Wilson interval, cost,
latency and tokens per run and per success, mechanical quality; then the
primary comparison (All-Strong Multi vs Single Strong), the routing
comparisons, the decision tree, the quality-cost frontier and the crossover
table. Descriptive: effect sizes and uncertainty, no significance claims.
"""

from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

import config
from analysis import difficulty

QUALITY_COST_VERSION = 1
WILSON_Z = 1.959963984540054          # two-sided 95%
E1_INITIAL_RUNS = 3
E1_MAX_RUNS = 5
E2_RUNS = 3
MEANINGFUL_GAIN = 0.15                # |delta success| threshold for "a difference"
PRESERVE_FRACTION = 0.8               # "preserves most of the gain"
CEILING_RATE = 0.9                    # Single Strong at/above this: not a hard-task comparison
FLOOR_RATE = 0.1                      # every architecture at/below this: beyond capability
ARCH_ORDER = ("A", "M", "H1", "H2", "CM", "CS")
ARCH_LABELS = {"A": "Single Strong", "M": "All-Strong Multi", "H1": "Strong-Investigator Hybrid",
               "H2": "Strong-Implementer Hybrid", "CM": "All-Cheap Multi", "CS": "Single Cheap"}
ARM_TO_ARCH = {arm: k for k, arm in config.STAGE2_ARCHITECTURES.items()}
STRATUM_ORDER = (difficulty.EASY_CONTROL_STRATUM,) + difficulty.STRATA


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def wilson(successes: int, n: int, z: float = WILSON_Z) -> tuple:
    """Wilson score interval; (None, None) for n = 0. Deterministic."""
    if n <= 0:
        return (None, None)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def _stats(values: Sequence[float]) -> dict:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "total": None}
    return {"n": len(vals), "mean": round(statistics.fmean(vals), 6),
            "median": round(statistics.median(vals), 6), "min": min(vals), "max": max(vals),
            "total": round(sum(vals), 6)}


def per_success(total: Optional[float], successes: int) -> Optional[float]:
    """Total over all attempts / successes; None (undefined, infinite) at 0 successes."""
    if total is None or successes <= 0:
        return None
    return round(total / successes, 6)


def summarize(entries: Sequence[dict]) -> dict:
    """One architecture in one stratum (or task): repeated attempts pooled."""
    n = len(entries)
    k = sum(1 for e in entries if e["solved"])
    rate = k / n if n else None
    m = [e["metrics"] for e in entries]
    cost = _stats([x["cost_usd"] for x in m])
    wall = _stats([x["wall_seconds"] for x in m])
    tokens = _stats([x["tokens"]["total_input"] + x["tokens"]["output"] for x in m])
    q = [e.get("quality") or {} for e in entries]
    return {
        "n": n,
        "successes": k,
        "success_rate": round(rate, 4) if rate is not None else None,
        "wilson_95": wilson(k, n),
        "cost_usd": cost,
        "wall_seconds": wall,
        "tokens_total": tokens,
        "total_input": _stats([x["tokens"]["total_input"] for x in m]),
        "uncached_input": _stats([x["tokens"]["input"] for x in m]),
        "cache_read": _stats([x["tokens"]["cache_read"] for x in m]),
        "cache_write": _stats([x["tokens"]["cache_write"] for x in m]),
        "output": _stats([x["tokens"]["output"] for x in m]),
        "thinking": _stats([x["tokens"]["thinking_if_exposed"] for x in m]),
        "strong_cost_usd": _stats([x["strong"]["cost_usd"] for x in m]),
        "tool_calls": _stats([e.get("tool_calls") for e in entries]),
        "model_calls": _stats([e.get("model_calls") for e in entries]),
        "strong_model_calls": _stats([e.get("strong_model_calls") for e in entries]),
        "cheap_model_calls": _stats([e.get("cheap_model_calls") for e in entries]),
        "cost_per_success": per_success(cost["total"], k),
        "wall_per_success": per_success(wall["total"], k),
        "tokens_per_success": per_success(tokens["total"], k),
        "expected_cost_to_success_estimate": (round(cost["mean"] / rate, 6)
                                              if cost["mean"] is not None and rate else None),
        "q2_heldout_fraction_mean": _stats([x.get("q2_heldout_fraction") for x in q])["mean"],
        "q5_constraint_fraction_mean": _stats([x.get("q5_constraint_fraction") for x in q])["mean"],
        "q3_regression_preserved": sum(1 for x in q if (x.get("q3_regression") or {}).get("preserved")),
        "run_ids": [e["run_id"] for e in entries],
    }


def dominates(x: dict, y: dict, cost_key: str = "cost") -> bool:
    """x dominates y: at least as successful, no more expensive, one strictly."""
    if None in (x["rate"], y["rate"], x[cost_key], y[cost_key]):
        return False
    return (x["rate"] >= y["rate"] and x[cost_key] <= y[cost_key]
            and (x["rate"] > y["rate"] or x[cost_key] < y[cost_key]))


def frontier(points: Sequence[dict], cost_key: str = "cost") -> list[str]:
    """Names of the nondominated points."""
    return [p["name"] for p in points
            if not any(dominates(q, p, cost_key) for q in points if q is not p)]


def _ratio(a, b):
    return round(a / b, 4) if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b else None


# --------------------------------------------------------------------------
# Which runs count
# --------------------------------------------------------------------------


def evaluation_entries(entries: Sequence[dict], labels: Optional[dict]) -> list[dict]:
    """Valid evaluation-phase runs of Stage-2 architectures on the frozen
    benchmark or the easy controls. Calibration runs never appear here."""
    out = []
    for e in entries:
        if e.get("stage2_phase") != "evaluation" or not e["valid"] or e["arm"] not in ARM_TO_ARCH:
            continue
        if difficulty.task_stratum(labels, e["task_id"]) is None:
            continue
        out.append(e)
    return out


def control_entries(entries: Sequence[dict]) -> list[dict]:
    """EASY controls: the frozen exact-parity historical runs (19.11), as
    Single Strong (historical Arm A) and All-Strong Multi (historical Arm B)."""
    from analysis import stage2
    out = []
    for task in config.STAGE2_EASY_CONTROLS:
        for key, pick in (("A", stage2.select_single_strong(entries, task)),
                          ("M", stage2.select_all_strong_multi(entries, task))):
            e = pick.get("entry")
            if e is not None and e["arm"] in ("A", "B"):
                out.append({**e, "arch": key, "historical_control": True})
    return out


def grouped(entries: Sequence[dict], labels: Optional[dict]) -> dict:
    """{stratum: {arch: [entries]}}, plus {task: {arch: [entries]}}."""
    by_stratum: dict = {}
    by_task: dict = {}
    rows = [{**e, "arch": ARM_TO_ARCH[e["arm"]]} for e in evaluation_entries(entries, labels)]
    have_control = {(e["task_id"], e["arch"]) for e in rows}
    rows += [e for e in control_entries(entries) if (e["task_id"], e["arch"]) not in have_control]
    for e in sorted(rows, key=lambda e: (e["task_id"], e["arch"], e.get("repeat_id") or 0, e["run_id"])):
        stratum = difficulty.task_stratum(labels, e["task_id"])
        by_stratum.setdefault(stratum, {}).setdefault(e["arch"], []).append(e)
        by_task.setdefault(e["task_id"], {}).setdefault(e["arch"], []).append(e)
    return {"by_stratum": by_stratum, "by_task": by_task}


# --------------------------------------------------------------------------
# Adaptive allocation (fixed in advance)
# --------------------------------------------------------------------------


def e1_task_status(task_runs: dict) -> dict:
    """E1 per task: 3 runs each of A and M; if both are 3/3 or both 0/3, stop;
    otherwise both go to 5. The rule looks at both arms symmetrically and never
    at which one is ahead."""
    a, m = task_runs.get("A", []), task_runs.get("M", [])
    na, nm = min(len(a), E1_MAX_RUNS), min(len(m), E1_MAX_RUNS)
    if na < E1_INITIAL_RUNS or nm < E1_INITIAL_RUNS:
        return {"complete": False, "target": E1_INITIAL_RUNS}
    ka = sum(1 for e in a[:E1_INITIAL_RUNS] if e["solved"])
    km = sum(1 for e in m[:E1_INITIAL_RUNS] if e["solved"])
    concordant = (ka, km) in ((E1_INITIAL_RUNS, E1_INITIAL_RUNS), (0, 0))
    target = E1_INITIAL_RUNS if concordant else E1_MAX_RUNS
    return {"complete": na >= target and nm >= target, "target": target}


def stratum_e1(strat_runs: dict, tasks: Sequence[str], by_task: dict) -> dict:
    complete = all(e1_task_status(by_task.get(t, {}))["complete"] for t in tasks) if tasks else False
    a = [e for t in tasks for e in by_task.get(t, {}).get("A", [])[:E1_MAX_RUNS]]
    m = [e for t in tasks for e in by_task.get(t, {}).get("M", [])[:E1_MAX_RUNS]]
    ra = sum(1 for e in a if e["solved"]) / len(a) if a else None
    rm = sum(1 for e in m if e["solved"]) / len(m) if m else None
    delta = round(rm - ra, 4) if ra is not None and rm is not None else None
    return {"complete": complete, "delta": delta,
            "e2_gate_open": bool(complete and delta is not None and delta >= MEANINGFUL_GAIN)}


def evaluation_plan(entries: Sequence[dict], labels: Optional[dict]) -> list[dict]:
    """The next preregistered evaluation runs: (task, arm, repeat ids)."""
    if not labels:
        return []
    g = grouped(entries, labels)
    by_task = g["by_task"]
    plan = []
    used = {}
    for e in entries:
        if e.get("stage2_phase") == "evaluation":
            used.setdefault((e["task_id"], e["arm"]), []).append(e.get("repeat_id") or 0)

    def need(task, arm, have, target):
        if have >= target:
            return
        start = max(used.get((task, arm), [0])) + 1
        plan.append({"task": task, "arm": arm, "repeat_ids": list(range(start, start + target - have))})

    for stratum, tasks in labels["benchmark"].items():
        for t in tasks:
            st = e1_task_status(by_task.get(t, {}))
            for key in ("A", "M"):
                need(t, config.STAGE2_ARCHITECTURES[key], len(by_task.get(t, {}).get(key, [])), st["target"])
        s = stratum_e1(None, tasks, by_task)
        if s["e2_gate_open"]:
            for t in tasks:
                for key in ("H1", "H2", "CM", "CS"):
                    need(t, config.STAGE2_ARCHITECTURES[key], len(by_task.get(t, {}).get(key, [])), E2_RUNS)
    for t in labels.get("easy_controls", []):
        for key in ("A", "M"):
            need(t, config.STAGE2_ARCHITECTURES[key], len(by_task.get(t, {}).get(key, [])), 1)
    return plan


# --------------------------------------------------------------------------
# Stratum analysis: comparisons, decision tree, frontier, crossover
# --------------------------------------------------------------------------


def _compare(x: dict, a: dict) -> dict:
    """Architecture x against Single Strong a, in one stratum."""
    return {
        "delta_success": (round(x["success_rate"] - a["success_rate"], 4)
                          if None not in (x["success_rate"], a["success_rate"]) else None),
        "relative_success": _ratio(x["success_rate"], a["success_rate"]),
        "cost_ratio": _ratio(x["cost_usd"]["mean"], a["cost_usd"]["mean"]),
        "token_ratio": _ratio(x["tokens_total"]["mean"], a["tokens_total"]["mean"]),
        "total_input_ratio": _ratio(x["total_input"]["mean"], a["total_input"]["mean"]),
        "output_ratio": _ratio(x["output"]["mean"], a["output"]["mean"]),
        "cache_write_ratio": _ratio(x["cache_write"]["mean"], a["cache_write"]["mean"]),
        "wall_ratio": _ratio(x["wall_seconds"]["mean"], a["wall_seconds"]["mean"]),
        "cost_per_success_ratio": _ratio(x["cost_per_success"], a["cost_per_success"]),
        "tokens_per_success_ratio": _ratio(x["tokens_per_success"], a["tokens_per_success"]),
    }


def decide(stratum: str, s: dict, e1: dict) -> dict:
    """The preregistered decision tree (19.9) for one stratum."""
    a, m = s.get("A"), s.get("M")
    if not a or not m or not e1.get("complete", stratum == difficulty.EASY_CONTROL_STRATUM):
        return {"outcome": "pending", "detail": "E1 incomplete"}
    rates = [v["success_rate"] for v in s.values() if v["success_rate"] is not None]
    if rates and max(rates) <= FLOOR_RATE:
        return {"outcome": "beyond_current_capability",
                "detail": f"every architecture <= {FLOOR_RATE}: no cost conclusions"}
    if stratum not in ("easy", difficulty.EASY_CONTROL_STRATUM) and a["success_rate"] >= CEILING_RATE:
        return {"outcome": "ceiling_not_interpretable",
                "detail": f"Single Strong {a['success_rate']} >= {CEILING_RATE} in evaluation: "
                          "reported as a calibration ceiling, not as 'multi-agent ineffective'"}
    gain = m["success_rate"] - a["success_rate"]
    if abs(gain) < MEANINGFUL_GAIN:
        return {"outcome": "multi_agent_unnecessary", "gain_m": round(gain, 4),
                "detail": f"|M - A| = {abs(gain):.3f} < {MEANINGFUL_GAIN}"}
    if gain <= -MEANINGFUL_GAIN:
        return {"outcome": "multi_agent_worse", "gain_m": round(gain, 4)}
    out = {"gain_m": round(gain, 4)}

    def preserves(key):
        h = s.get(key)
        if not h:
            return None
        retained = (h["success_rate"] - a["success_rate"]) / gain
        cheaper = (h["cost_usd"]["mean"] is not None and m["cost_usd"]["mean"] is not None
                   and h["cost_usd"]["mean"] < m["cost_usd"]["mean"])
        return {"retained_fraction": round(retained, 4), "cheaper_than_m": cheaper,
                "preserves": retained >= PRESERVE_FRACTION and cheaper}

    h1, h2 = preserves("H1"), preserves("H2")
    out.update({"h1": h1, "h2": h2})
    if h1 is None:
        return {**out, "outcome": "multi_agent_gain_e2_pending"}
    if h1["preserves"]:
        return {**out, "outcome": "heterogeneous_routing_useful_h1"}
    if h2 is None:
        return {**out, "outcome": "multi_agent_gain_e2_pending"}
    if h2["preserves"]:
        return {**out, "outcome": "implementation_capability_more_important_h2"}
    return {**out, "outcome": "all_strong_capability_may_be_necessary"}


def analyse(entries: Sequence[dict], labels: Optional[dict]) -> dict:
    g = grouped(entries, labels)
    strata = {}
    for stratum in STRATUM_ORDER:
        archs = g["by_stratum"].get(stratum)
        if not archs:
            continue
        tasks = (labels or {}).get("benchmark", {}).get(stratum, []) if stratum != difficulty.EASY_CONTROL_STRATUM \
            else (labels or {}).get("easy_controls", list(config.STAGE2_EASY_CONTROLS))
        runs = {k: v if stratum == difficulty.EASY_CONTROL_STRATUM else
                [e for t in tasks for e in g["by_task"].get(t, {}).get(k, [])[:E1_MAX_RUNS if k in ("A", "M") else E2_RUNS]]
                for k, v in archs.items()}
        summ = {k: summarize(v) for k, v in runs.items() if v}
        e1 = stratum_e1(None, tasks, g["by_task"]) if stratum != difficulty.EASY_CONTROL_STRATUM else {"complete": True}
        points = [{"name": k, "rate": v["success_rate"], "cost": v["cost_usd"]["mean"],
                   "strong_cost": v["strong_cost_usd"]["mean"]} for k, v in summ.items()]
        strata[stratum] = {
            "tasks": tasks,
            "architectures": summ,
            "vs_single_strong": {k: _compare(v, summ["A"]) for k, v in summ.items() if k != "A" and "A" in summ},
            "e1": e1,
            "decision": decide(stratum, summ, e1),
            "frontier_cost": frontier(points, "cost"),
            "frontier_strong_cost": frontier(points, "strong_cost"),
            "plot_points": points,
        }
    crossover = next((s for s in difficulty.STRATA if s in strata
                      and (strata[s]["vs_single_strong"].get("M") or {}).get("delta_success") is not None
                      and strata[s]["vs_single_strong"]["M"]["delta_success"] >= MEANINGFUL_GAIN), None)
    return {"version": QUALITY_COST_VERSION, "strata": strata,
            "crossover_stratum": crossover or "no crossover observed",
            "per_task": {t: {k: summarize(v) for k, v in archs.items()} for t, archs in g["by_task"].items()}}


def _f(v, nd=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render(result: dict, labels: Optional[dict]) -> str:
    L = ["=== Stage 2: quality x cost x difficulty (PREREGISTRATION 19; descriptive) ==="]
    if not labels:
        L.append("difficulty labels not frozen yet: run Phase C calibration first "
                 "(python runner.py difficulty-summary)")
    L.append(f"crossover (first stratum where M - A >= {MEANINGFUL_GAIN}): {result['crossover_stratum']}")
    for stratum, s in result["strata"].items():
        L.append("")
        L.append(f"-- stratum {stratum}: tasks {s['tasks']}")
        L.append(f"   {'arch':<30}{'n':>4}{'succ':>6}{'rate':>7}{'wilson95':>16}{'cost/run':>10}"
                 f"{'cost/succ':>11}{'wall/run':>10}{'tokens/run':>12}{'strong$':>9}{'Q2':>7}{'Q5':>7}")
        for k in ARCH_ORDER:
            v = s["architectures"].get(k)
            if not v:
                continue
            lo, hi = v["wilson_95"]
            L.append(f"   {ARCH_LABELS[k]:<30}{v['n']:>4}{v['successes']:>6}{_f(v['success_rate']):>7}"
                     f"{('[' + _f(lo) + ', ' + _f(hi) + ']'):>16}{_f(v['cost_usd']['mean'], 4):>10}"
                     f"{_f(v['cost_per_success'], 4) if v['cost_per_success'] is not None else 'undefined':>11}"
                     f"{_f(v['wall_seconds']['mean'], 1):>10}{_f(v['tokens_total']['mean'], 0):>12}"
                     f"{_f(v['strong_cost_usd']['mean'], 4):>9}{_f(v['q2_heldout_fraction_mean']):>7}"
                     f"{_f(v['q5_constraint_fraction_mean']):>7}")
        for k, c in s["vs_single_strong"].items():
            L.append(f"   {k} vs A: dSuccess {_f(c['delta_success'])}  cost x{_f(c['cost_ratio'])}  "
                     f"tokens x{_f(c['token_ratio'])}  input x{_f(c['total_input_ratio'])}  "
                     f"output x{_f(c['output_ratio'])}  cache-write x{_f(c['cache_write_ratio'])}  "
                     f"wall x{_f(c['wall_ratio'])}  cost/success x{_f(c['cost_per_success_ratio'])}")
        L.append(f"   E2 gate open: {s['e1'].get('e2_gate_open')}   decision: {s['decision']['outcome']}"
                 f"  {s['decision'].get('detail', '')}")
        L.append(f"   frontier (rate vs cost): {s['frontier_cost']}   (rate vs strong cost): {s['frontier_strong_cost']}")
    L.append("")
    L.append("cost/succ = total cost over all attempts / successes ('undefined' at 0 successes).")
    L.append("n is small: Wilson intervals are wide; no significance testing is claimed.")
    return "\n".join(L)
