"""Stage 2A: which model actually served an invocation, and whose usage is whose.

Pure functions over one invocation's own telemetry. The orchestrator uses them to
stop a run the moment a resolved model differs from its assignment; the analysis
recomputes the same checks from the raw logs.

Evidence used (Claude Code 2.1.260, grounded in the stored runs):
  * `system/init.model`: the model the session runs on (`sonnet` -> `claude-sonnet-5`).
  * every assistant `message.model`: the model that produced each API response.
  * `result.usage`: the MAIN model only. On all 57 stored sessions it equals
    `result.modelUsage[<main model>]` exactly, so it is the role-assigned usage.
  * `result.modelUsage`: every model, including Claude Code's own auxiliary calls
    (`claude-haiku-4-5-20251001`). When the role model is itself Haiku, those
    auxiliary tokens land in the SAME modelUsage entry, so auxiliary usage is
    `modelUsage[m] - result.usage` for the main model and `modelUsage[m]` for
    any other model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import config

MODEL_ROUTING_CHECK_VERSION = 1
COST_ACCOUNTING_VERSION = 1

TOKEN_CLASSES = ("input", "output", "cache_read", "cache_write")
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def canonical_model(model: Optional[str]) -> Optional[str]:
    """`claude-haiku-4-5-20251001` -> `claude-haiku-4-5`; `claude-sonnet-5[1m]` ->
    `claude-sonnet-5`. Aliases such as `sonnet` are returned unchanged."""
    if not isinstance(model, str) or not model.strip():
        return None
    return _DATE_SUFFIX.sub("", model.split("[", 1)[0].strip())


def result_usage_classes(usage: Optional[dict]) -> dict:
    u = usage if isinstance(usage, dict) else {}
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    return {
        "input": int(u.get("input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "cache_write": int(u.get("cache_creation_input_tokens") or 0),
        "cache_write_5m": cc.get("ephemeral_5m_input_tokens"),
        "cache_write_1h": cc.get("ephemeral_1h_input_tokens"),
    }


def model_usage_classes(entry: Optional[dict]) -> dict:
    e = entry if isinstance(entry, dict) else {}
    cost = e.get("costUSD")
    return {
        "input": int(e.get("inputTokens") or 0),
        "output": int(e.get("outputTokens") or 0),
        "cache_read": int(e.get("cacheReadInputTokens") or 0),
        "cache_write": int(e.get("cacheCreationInputTokens") or 0),
        "thinking": int(e.get("thinkingTokens") or 0),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
    }


def api_equivalent_cost(canonical: Optional[str], usage: dict) -> Optional[float]:
    """Price tokens with the CLI's own registry table. Cache writes use the
    5m/1h split when known; otherwise all at the 5m rate (the only rate any
    auxiliary call has used)."""
    p = config.MODEL_PRICING_USD_PER_MTOK.get(canonical or "")
    if p is None:
        return None
    w5, w1 = usage.get("cache_write_5m"), usage.get("cache_write_1h")
    if w5 is None and w1 is None:
        w5, w1 = usage.get("cache_write", 0), 0
    total = (usage.get("input", 0) * p["input"] + usage.get("output", 0) * p["output"]
             + usage.get("cache_read", 0) * p["cache_read"]
             + (w5 or 0) * p["cache_write_5m"] + (w1 or 0) * p["cache_write_1h"])
    return total / 1e6


def stream_observation(stdout_path: str | Path) -> dict:
    """Assistant message models, and per-message usage summed over distinct
    message ids (used only when a session has no result event)."""
    path = Path(stdout_path)
    models: set[str] = set()
    per_msg: dict = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if isinstance(msg.get("model"), str):
                models.add(msg["model"])
            if isinstance(msg.get("usage"), dict):
                per_msg[msg.get("id") or f"line{len(per_msg)}"] = msg["usage"]
    summed = {k: 0 for k in TOKEN_CLASSES}
    for u in per_msg.values():
        c = result_usage_classes(u)
        for k in TOKEN_CLASSES:
            summed[k] += c[k]
    return {"assistant_message_models": sorted(models), "stream_usage": summed,
            "api_messages": len(per_msg)}


def _main_key(model_usage: dict, init_model: Optional[str]) -> Optional[str]:
    if init_model in model_usage:
        return init_model
    want = canonical_model(init_model)
    return next((k for k in model_usage if want and canonical_model(k) == want), None)


def check_invocation(*, requested: Optional[str], expected_requested: str,
                     expected_resolved: str, init: Optional[dict],
                     assistant_models: list, result: Optional[dict]) -> dict:
    """Did the preregistered model serve this invocation? Checks that need the
    result event are None (not available) when the process was terminated
    before it; the init and message checks are always required."""
    want = canonical_model(expected_resolved)
    init_model = (init or {}).get("model") if isinstance(init, dict) else None
    res = result if isinstance(result, dict) else None
    mu = (res or {}).get("modelUsage") if isinstance((res or {}).get("modelUsage"), dict) else {}
    key = _main_key(mu, init_model) if res else None
    others = [k for k in mu if k != key]
    unexpected = sorted(k for k in others if canonical_model(k) not in config.AUXILIARY_MODELS_CANONICAL)
    role = result_usage_classes((res or {}).get("usage"))
    main = model_usage_classes(mu.get(key)) if key else None
    checks = {
        "requested_matches_assignment": requested == expected_requested,
        "init_model_matches_assignment": canonical_model(init_model) == want,
        "assistant_messages_match_assignment": bool(assistant_models)
        and all(canonical_model(m) == want for m in assistant_models),
        "assigned_model_in_model_usage": (key is not None and canonical_model(key) == want) if res else None,
        "role_usage_within_model_usage": (main is not None and all(
            role[k] <= main[k] for k in TOKEN_CLASSES)) if res else None,
        "no_unexpected_models": (not unexpected) if res else None,
    }
    return {
        "version": MODEL_ROUTING_CHECK_VERSION,
        "verified": all(v is not False for v in checks.values()),
        "checks": checks,
        "requested_model": requested,
        "expected_resolved_model": expected_resolved,
        "resolved_model": init_model,
        "resolved_canonical": canonical_model(init_model),
        "assistant_message_models": list(assistant_models),
        "model_usage_models": sorted(mu),
        "main_model_usage_key": key,
        "unexpected_models": unexpected,
        "result_event_present": res is not None,
    }


def split_usage(*, result: Optional[dict], init_model: Optional[str],
                stream_usage: Optional[dict] = None) -> dict:
    """Role-assigned (main-loop) vs Claude Code auxiliary usage and cost."""
    res = result if isinstance(result, dict) else None
    role_canon = canonical_model(init_model)
    if res is None:
        # Terminated before the result event: per-message input/cache usage is
        # exact, per-message output is a streamed snapshot, i.e. a lower bound.
        role = dict(stream_usage or {k: 0 for k in TOKEN_CLASSES})
        role.update({"cache_write_5m": None, "cache_write_1h": None})
        cost = api_equivalent_cost(role_canon, role)
        return {"basis": "stream_messages_lower_bound", "exact": False, "consistent": True,
                "role_model": role_canon, "role_usage": role, "role_cost_usd": cost,
                "auxiliary": {}, "auxiliary_cost_usd": 0.0, "total_cost_usd": cost,
                "cli_total_cost_usd": None, "thinking_by_model": {}}

    mu = res.get("modelUsage") if isinstance(res.get("modelUsage"), dict) else {}
    key = _main_key(mu, init_model)
    role = result_usage_classes(res.get("usage"))
    role_cost = api_equivalent_cost(canonical_model(key) or role_canon, role)
    aux: dict = {}
    consistent = True
    total = 0.0
    thinking: dict = {}
    for name, entry in mu.items():
        m = model_usage_classes(entry)
        canon = canonical_model(name)
        thinking[canon] = thinking.get(canon, 0) + m["thinking"]
        model_cost = m["cost_usd"] if m["cost_usd"] is not None else api_equivalent_cost(canon, m)
        total += model_cost or 0.0
        if name == key:
            left = {k: m[k] - role[k] for k in TOKEN_CLASSES}
            left_cost = (model_cost - role_cost) if (model_cost is not None and role_cost is not None) else None
        else:
            left = {k: m[k] for k in TOKEN_CLASSES}
            left_cost = model_cost
        if any(v < 0 for v in left.values()) or (left_cost is not None and left_cost < -1e-9):
            consistent = False
        if any(left.values()) or (left_cost or 0) > 1e-12:
            slot = aux.setdefault(canon, {**{k: 0 for k in TOKEN_CLASSES}, "cost_usd": 0.0})
            for k in TOKEN_CLASSES:
                slot[k] += left[k]
            slot["cost_usd"] = round(slot["cost_usd"] + max(left_cost or 0.0, 0.0), 10)
    if key is None:
        consistent = False
    cli_total = res.get("total_cost_usd")
    return {
        "basis": "result_event",
        "exact": True,
        "consistent": consistent,
        "role_model": canonical_model(key) or role_canon,
        "role_usage": role,
        "role_cost_usd": round(role_cost, 10) if role_cost is not None else None,
        "auxiliary": aux,
        "auxiliary_cost_usd": round(sum(v["cost_usd"] for v in aux.values()), 10),
        "total_cost_usd": round(total, 10),
        "cli_total_cost_usd": cli_total if isinstance(cli_total, (int, float)) else None,
        "thinking_by_model": thinking,
    }


def reasoning_effort_record() -> dict:
    return {
        "requested": "not_passed",
        "policy": config.STAGE2_EFFORT_POLICY["policy"],
        "resolved": "not_reported_by_cli_telemetry",
    }
