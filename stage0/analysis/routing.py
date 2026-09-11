"""Stage 2A: run-level model-routing verification and cost accounting.

Recomputed from the raw logs (metadata, invocation.json, the raw stream), never
taken from the harness's own routing.json. Works on historical A/B runs too: an
invocation without a Stage-2 assignment is classified by the model it requested
(`sonnet` = STRONG), which is how the Single-Strong baseline gets the same
cost-by-model accounting.
"""

from __future__ import annotations

from typing import Optional

import config
from harness import claude_cli, model_routing


def is_stage2_arm(arm: Optional[str]) -> bool:
    return arm in config.STAGE2_CONFIGS


def _class_for(role: Optional[str], requested: Optional[str], topo: dict) -> Optional[str]:
    assigned = (topo.get("role_model_classes") or {}).get(role)
    if assigned:
        return assigned
    if topo.get("role_model_classes"):
        return None  # a Stage-2 run with an unassigned role
    return next((k for k, v in config.MODEL_CLASSES.items() if v["requested"] == requested), None)


def check_run(raw: dict) -> dict:
    meta = raw.get("metadata") or {}
    topo = meta.get("topology") or {}
    classes = topo.get("model_classes") or config.MODEL_CLASSES
    rows = []
    for sa in sorted(raw.get("sessions") or [], key=lambda s: s.session_index):
        inv = sa.invocation or {}
        role = inv.get("role")
        cls = _class_for(role, inv.get("model"), topo)
        init = sa.parsed.init if isinstance(sa.parsed.init, dict) else None
        res = sa.parsed.result if isinstance(sa.parsed.result, dict) else None
        obs = model_routing.stream_observation(sa.dir / "claude_stdout.jsonl")
        if cls is None:
            check = {"verified": False, "checks": {"role_has_model_assignment": False},
                     "resolved_model": (init or {}).get("model")}
        else:
            mc = classes[cls]
            check = model_routing.check_invocation(
                requested=inv.get("model"), expected_requested=mc["requested"],
                expected_resolved=mc["expected_resolved"], init=init,
                assistant_models=obs["assistant_message_models"], result=res)
        rows.append({
            "session_key": sa.session_key,
            "logical_role": role,
            "model_class": cls,
            "physical_session_id": sa.parsed.session_id or inv.get("resume_session_id") or inv.get("session_id"),
            "is_resume": bool(inv.get("resume_session_id")),
            "requested_model": inv.get("model"),
            "resolved_model": (init or {}).get("model"),
            "reasoning_effort": model_routing.reasoning_effort_record(),
            "verification": check,
            "accounting": model_routing.split_usage(
                result=res, init_model=(init or {}).get("model"), stream_usage=obs["stream_usage"]),
            "termination_reason": (sa.exit or {}).get("termination_reason"),
            "is_error": bool((res or {}).get("is_error")),
            "api_error_status": (res or {}).get("api_error_status"),
            "wall_seconds": float((sa.exit or {}).get("wall_seconds") or 0.0),
            "duration_api_ms": (res or {}).get("duration_api_ms"),
        })
    expected = len(topo.get("invocations") or []) or len(rows)
    failed = [f"{r['session_key']}: {k}" for r in rows
              for k, v in r["verification"]["checks"].items() if v is False]
    inconsistent = [r["session_key"] for r in rows if not r["accounting"]["consistent"]]
    return {
        "version": model_routing.MODEL_ROUTING_CHECK_VERSION,
        "cost_accounting_version": model_routing.COST_ACCOUNTING_VERSION,
        "verified": bool(rows) and not failed and not inconsistent,
        "failed_checks": failed,
        "accounting_inconsistent": inconsistent,
        "expected_invocations": expected,
        "observed_invocations": len(rows),
        "complete": len(rows) == expected,
        "invocations": rows,
    }


_MODEL_OUTCOME_TERMINATIONS = (claude_cli.TERM_TURN_LIMIT, claude_cli.TERM_WALL_LIMIT)


def validity_reasons(r: dict) -> list[str]:
    """Stage-2 exclusions (section 18.9), in addition to the shared ones.

    A limit termination or a model-caused error of an assigned model is an
    OUTCOME of the configuration (the held-out verifier decides SOLVED), not an
    exclusion. Invalid are: routing mismatch, accounting inconsistency, and
    infrastructure stops (spawn failure, provider API error status, a chain
    stopped by quota protection or by a routing mismatch)."""
    mr = r.get("model_routing")
    if not mr:
        return ["Stage-2 model routing record missing"]
    reasons = []
    if mr["failed_checks"]:
        reasons.append(f"model routing not verified: {mr['failed_checks']}")
    if mr["accounting_inconsistent"]:
        reasons.append(f"usage accounting inconsistent: {mr['accounting_inconsistent']}")
    rows = mr["invocations"]
    for x in rows:
        if x["termination_reason"] == claude_cli.TERM_SPAWN_FAILED:
            reasons.append(f"{x['session_key']}: spawn failed (infrastructure)")
        if x["is_error"] and x["api_error_status"] is not None:
            reasons.append(f"{x['session_key']}: provider API error status "
                           f"{x['api_error_status']} (infrastructure)")
    if not mr["complete"]:
        last = rows[-1] if rows else None
        model_outcome = last is not None and (
            last["termination_reason"] in _MODEL_OUTCOME_TERMINATIONS
            or (last["is_error"] and last["api_error_status"] is None))
        if not model_outcome:
            reasons.append(f"chain incomplete ({mr['observed_invocations']}/"
                           f"{mr['expected_invocations']} invocations) without a model outcome "
                           "(quota protection or routing stop)")
    return reasons


def has_lower_bound_cost(mr: Optional[dict]) -> bool:
    return bool(mr) and any(not x["accounting"]["exact"] for x in mr["invocations"])
