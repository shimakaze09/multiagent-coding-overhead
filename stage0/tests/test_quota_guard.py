"""Subscription usage protection for multi-session runs.

Arm B spawns five Claude Code sessions in sequence. If a session's own telemetry
says the account has moved into paid overage (or was blocked), the chain must
stop before spawning the next session. The guard reads only CLI telemetry and
never alters anything Claude is given, so it is not part of config_hash.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import config
from harness import agent, telemetry


def _cli(lines: list[dict]):
    parsed = telemetry.parse_stream([json.dumps(l) for l in lines])
    return SimpleNamespace(parsed=parsed, summary=telemetry.session_summary(parsed))


def _rate(status="allowed_warning", overage=False, util=0.85):
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "rateLimitType": "seven_day",
            "utilization": util,
            "isUsingOverage": overage,
        },
    }


INIT = {"type": "system", "subtype": "init", "session_id": "s", "apiKeySource": "none"}
OK_RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "done"}


def test_the_telemetry_seen_so_far_does_not_stop_the_run():
    """Both validated Arm A runs reported exactly this: allowed_warning, 0.85,
    no overage. That must not be mistaken for a stop condition."""
    assert agent.quota_stop_reason(_cli([INIT, _rate(), OK_RESULT])) is None


def test_no_rate_limit_event_at_all_does_not_stop_the_run():
    assert agent.quota_stop_reason(_cli([INIT, OK_RESULT])) is None


def test_overage_stops_the_run():
    reason = agent.quota_stop_reason(_cli([INIT, _rate(overage=True), OK_RESULT]))
    assert reason and "isUsingOverage=true" in reason and "overage" in reason


def test_a_blocked_status_stops_the_run():
    reason = agent.quota_stop_reason(_cli([INIT, _rate(status="rejected"), OK_RESULT]))
    assert reason and "rejected" in reason


def test_a_usage_limit_result_stops_the_run():
    limited = {
        "type": "result", "subtype": "error", "is_error": True,
        "result": "Claude usage limit reached. Your limit resets at 3pm.",
    }
    reason = agent.quota_stop_reason(_cli([INIT, limited]))
    assert reason and "usage-limit" in reason


def test_the_guard_does_not_change_config_hash():
    """It is a stop condition, not an input to Claude: Arm A/Arm B parity by
    config_hash must be unaffected by its existence."""
    keys = set(config.RunConfig().effective_config())
    # token match, not substring: "min_acquisition_coverage" contains "overage"
    tokens = {t for k in keys for t in k.split("_")}
    assert not ({"quota", "overage", "guard"} & tokens)
    import dataclasses

    assert config.RunConfig().config_hash() == "9edbfb5d0d082d49a61969068fafd4ac"
    # Amendment 7 (turn-limit counting) is the ONLY configuration change since the
    # validated runs: reverting it reproduces their hash exactly.
    v2 = dataclasses.replace(config.RunConfig(), turn_limit_enforcement="harness_side")
    assert v2.config_hash() == "22ce9b7e5249dd497ee7c4c0318216b4", (
        "must equal the validated Arm A run's config_hash"
    )


def test_arm_b_checks_the_guard_before_every_subsequent_session():
    import inspect

    from arms import multi_nl

    src = inspect.getsource(multi_nl.run)
    assert src.count("stop = must_stop(r") == 4, "after sessions 1-4, before spawning 2-5"
    assert "agent.quota_stop_reason" in src
