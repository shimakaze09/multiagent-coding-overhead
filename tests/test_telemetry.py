"""Milestone 4 tests: the stream-json parser, against captured fixtures.

The parser is versioned separately from Claude Code. These tests exist so a
schema change is caught as a test failure, not as silently missing data.
"""

from __future__ import annotations

import json

import pytest

from harness import telemetry


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


# --------------------------------------------------------------------------
# The real captured stream (auth failure, zero cost)
# --------------------------------------------------------------------------


def test_real_captured_stream_parses(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "real_stream_auth_failed.jsonl"))
    assert ps.init is not None
    assert ps.cli_version == "2.1.260"
    assert ps.api_key_source == "none"
    assert ps.session_id
    assert ps.is_error() is True
    assert ps.unparsable_lines == 0
    assert ps.unknown_event_type_report() == {}, "no unknown types in the real fixture"


def test_real_captured_stream_reports_auth_failure_not_a_crash(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "real_stream_auth_failed.jsonl"))
    assert ps.errors, "the authentication failure must be surfaced as an error"
    assert any("authentication" in str(e.get("error", "")).lower() for e in ps.errors)
    summary = telemetry.session_summary(ps)
    assert summary["is_error"] is True
    # cost is reported by this build, and is zero for a failed auth
    assert summary["cost"]["cli_reported_cost_usd"]["availability"] == telemetry.REPORTED
    assert summary["cost"]["cli_reported_cost_usd"]["value"] == 0
    assert summary["cost"]["subscription_execution"] is True
    assert summary["cost"]["cli_cost_interpretation"] == "api_equivalent_not_amount_paid"


# --------------------------------------------------------------------------
# Synthetic stream: tool pairing, unknown types, new optional fields
# --------------------------------------------------------------------------


def test_tool_use_is_paired_with_tool_result(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    calls = {c.tool_use_id: c for c in ps.tool_calls}
    assert set(calls) == {"toolu_A", "toolu_B"}

    a = calls["toolu_A"]
    assert a.name == "Read"
    assert a.input == {"file_path": "a.py"}
    assert a.completed
    assert a.result_text == "line1\nline2\nline3\nline4"
    assert a.start_line < a.end_line
    assert a.start_ts and a.end_ts

    # tool_result content may be a bare string rather than a block list
    assert calls["toolu_B"].result_text == "plain string result"


def test_parallel_tool_calls_share_a_start_line(fixtures_dir):
    """The concurrency case: two reads issued in one assistant message."""
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    starts = {c.tool_use_id: c.start_line for c in ps.tool_calls}
    assert starts["toolu_A"] == starts["toolu_B"]


def test_unknown_event_type_survives_and_is_counted(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    report = ps.unknown_event_type_report()
    assert report.get("totally_new_event_kind") == 1, "unknown types must be counted"
    kept = [u for u in ps.unknown_events if u.get("reason") == "unknown_type"]
    assert kept and kept[0]["raw"]["type"] == "totally_new_event_kind", "raw must be preserved"


def test_orphan_tool_result_is_reported_not_dropped(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    assert ps.unknown_type_counts.get("orphan_tool_result") == 1


def test_new_optional_fields_do_not_crash_the_parser(fixtures_dir):
    """The fixture carries brand_new_future_field and some_new_usage_field."""
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    assert ps.init is not None
    assert "brand_new_future_field" in ps.init, "unknown init fields must be retained"
    assert ps.usage_snapshots


def test_parser_never_raises_on_garbage():
    garbage = [
        "not json at all",
        "{",
        "[]",
        "null",
        '{"type": null}',
        '{"type": "assistant", "message": "not a dict"}',
        '{"type": "assistant", "message": {"content": "not a list"}}',
        '{"type": "user", "message": {"content": [null, 5, {"type": "tool_result"}]}}',
        '{"type": "result", "usage": "not a dict"}',
    ]
    ps = telemetry.parse_stream(garbage)
    assert ps.unparsable_lines >= 3
    telemetry.session_summary(ps)  # must not raise


def test_parser_version_is_independent_of_cli_version(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "real_stream_auth_failed.jsonl"))
    assert isinstance(telemetry.PARSER_VERSION, int)
    assert ps.parser_version == telemetry.PARSER_VERSION
    assert ps.cli_version == "2.1.260"
    assert ps.parser_version != ps.cli_version


# --------------------------------------------------------------------------
# Measurement triples
# --------------------------------------------------------------------------


def test_usage_fields_are_value_source_availability_triples(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    agg = telemetry.aggregate_usage(ps)
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_ephemeral_1h_tokens",
        "cache_ephemeral_5m_tokens",
        "thinking_tokens",
        "total_tokens",
    ):
        m = agg[key]
        assert set(m) == {"value", "source", "availability"}
        assert m["availability"] in (
            telemetry.REPORTED,
            telemetry.NOT_EXPOSED,
            telemetry.ESTIMATED,
            telemetry.UNRELIABLE,
        )

    assert agg["input_tokens"] == {
        "value": 10,
        "source": telemetry.SOURCE_STREAM,
        "availability": telemetry.REPORTED,
    }
    assert agg["thinking_tokens"]["value"] == 2
    assert agg["aggregation"] == "cli_result_event"


def test_absent_field_is_not_exposed_not_zero():
    snap = telemetry.parse_usage({"input_tokens": 5}, 1, None)
    assert snap.input_tokens.as_dict() == {
        "value": 5,
        "source": telemetry.SOURCE_STREAM,
        "availability": telemetry.REPORTED,
    }
    assert snap.output_tokens.as_dict() == {
        "value": None,
        "source": None,
        "availability": telemetry.NOT_EXPOSED,
    }


def test_present_and_zero_is_reported_not_missing():
    snap = telemetry.parse_usage({"cache_read_input_tokens": 0}, 1, None)
    assert snap.cache_read_tokens.value == 0
    assert snap.cache_read_tokens.availability == telemetry.REPORTED


def test_non_numeric_usage_value_is_unreliable_not_fabricated():
    snap = telemetry.parse_usage({"input_tokens": "lots"}, 1, None)
    assert snap.input_tokens.value is None
    assert snap.input_tokens.availability == telemetry.UNRELIABLE


def test_usage_with_no_data_at_all_is_not_exposed():
    ps = telemetry.parse_stream(['{"type":"system","subtype":"init","session_id":"x"}'])
    agg = telemetry.aggregate_usage(ps)
    assert agg["aggregation"] == "none_available"
    assert agg["input_tokens"]["availability"] == telemetry.NOT_EXPOSED
    assert agg["input_tokens"]["value"] is None


# --------------------------------------------------------------------------
# Usage-limit detection
# --------------------------------------------------------------------------


def test_usage_limit_condition_is_detected(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_usage_limit.jsonl"))
    summary = telemetry.session_summary(ps)
    assert summary["usage_limit_suspected"] is True
    assert summary["is_error"] is True


def test_normal_result_is_not_flagged_as_usage_limit(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    assert telemetry.session_summary(ps)["usage_limit_suspected"] is False


def test_subagent_fanout_is_recorded(fixtures_dir):
    ps = telemetry.parse_stream(_lines(fixtures_dir / "synthetic_stream_v1.jsonl"))
    assert telemetry.session_summary(ps)["subagent_spawned"] == 0
