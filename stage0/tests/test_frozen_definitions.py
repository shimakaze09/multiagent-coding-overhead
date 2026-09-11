"""Freeze guard: the metric and classifier definitions fixed before Pair 2.

PREREGISTRATION.md "Frozen definitions v1" (2026-09-11) records these values.
If any assertion here fails, a definition changed: bump the relevant version,
record a dated amendment, and update this file in the same commit. Never edit
this file merely to make a changed metric pass.
"""

from __future__ import annotations

import re
import subprocess

import pytest

import config
from analysis import handoff, isolation, metrics
from harness import claude_cli, events, telemetry, tools


def _alternatives(pattern: re.Pattern) -> set[str]:
    body = re.search(r"\\b\((.*)\)\\b", pattern.pattern, re.S).group(1)
    return set(body.split("|"))


# --------------------------------------------------------------------------
# Versions and configuration identity
# --------------------------------------------------------------------------


def test_versions_are_frozen():
    assert telemetry.PARSER_VERSION == 4
    assert tools.COVERAGE_FORMULA_VERSION == 2
    assert metrics.REACQUISITION_CLASSIFIER_VERSION == 1
    assert handoff.HANDOFF_SCHEMA_VERSION == 1
    assert isolation.ISOLATION_CHECK_VERSION == 1
    assert events.SCHEMA_VERSION == 1
    # 3 since amendment 7 (2026-09-11): the harness-side turn limit counts
    # distinct API messages. Wrapper v2 counted assistant stream lines.
    assert claude_cli.WRAPPER_VERSION == 3


def test_experiment_visible_configuration_is_frozen():
    cfg = config.RunConfig()
    # Amendment 7 changed turn_limit_enforcement, so the hash changed from
    # 22ce9b7e5249dd497ee7c4c0318216b4 (Pair 1, Pair 2, Stage-0.5 task 3 and
    # the invalid task-4 Arm A) to the value below (every later run).
    assert cfg.config_hash() == "9edbfb5d0d082d49a61969068fafd4ac"
    assert cfg.turn_limit_enforcement == "harness_side_api_messages"
    assert cfg.model == "sonnet"
    assert cfg.permission_mode == "acceptEdits"
    assert cfg.permission_prompts == "none"
    assert config.ALLOWED_TOOLS_POLICY_ID == "narrow_pytest_v1"
    assert config.BASH_TEST_ALLOWLIST == (
        "Bash(python -m pytest *)", "Bash(python -m pytest)", "Bash(python -m pytest:*)",
    )
    assert config.SMOKE_LIMITS.max_sessions_per_invocation == 6
    assert config.SMOKE_LIMITS.max_turns_per_session == 25


# --------------------------------------------------------------------------
# Overlap, temporal availability, priming
# --------------------------------------------------------------------------


def test_overlap_constants_are_frozen():
    assert metrics.SHINGLE_OVERLAP_THRESHOLD == 0.60
    assert tools.SHINGLE_LINES == 3


def test_temporal_availability_rule_is_strict_end_before_start():
    def acq(uid, start, end, agent):
        return metrics.Acq(
            session_index=2, session_key=f"02_{agent}", agent_id=agent, tool_use_id=uid,
            tool_name="Read", acquisition_class=tools.STRUCTURED_READ, target_path="a.py",
            query=None, command=None, result_sha="x", result_shingles=("s",),
            result_chars=1, result_bytes=1, start_line=start, concurrency_group_line=start,
            end_line=end, start_ts=None, end_ts=None, turn_id=1, completed=True,
        )
    # producer ends exactly where consumer starts -> NOT available (strict <)
    assert metrics.analyze([acq("p", 1, 5, "i"), acq("c", 5, 9, "m")], []).findings == []
    assert len(metrics.analyze([acq("p", 1, 4, "i"), acq("c", 5, 9, "m")], []).findings) == 1


def test_subcategory_names_are_frozen():
    assert metrics.REACQ_SUBCATEGORIES == (
        "edit_precondition_associated",
        "verification_associated",
        "discretionary_information_reacquisition",
        "unknown",
    )
    assert metrics.REACQ_NOT_APPLICABLE == "not_applicable"


def test_edit_precondition_is_verified_only_for_2_1_260():
    assert set(metrics.EDIT_REQUIRES_PRIOR_READ) == {"2.1.260"}
    assert "File has not been read yet" in metrics.EDIT_REQUIRES_PRIOR_READ["2.1.260"]


def test_verification_verbs_are_frozen():
    assert _alternatives(metrics._VERIFICATION_RE) == {
        "verify", "verifies", "verified", "re-verify", "reverify", "confirm", "confirms",
        "double-check", "re-check", "recheck", "validate", "make sure", "check that",
        "check whether", "check if",
    }


def test_test_outcome_words_are_frozen():
    assert _alternatives(metrics._TEST_OUTCOME_RE) == {
        "pass", "passes", "passing", "passed", "fail", "fails", "failing", "failed",
        "pytest", "test suite",
    }


# --------------------------------------------------------------------------
# Handoff duplication and isolation
# --------------------------------------------------------------------------


def test_content_window_constants_are_frozen():
    assert tools.WINDOW_MIN_ALNUM == 16
    assert tools.WINDOW_MIN_CONTENT_LINES == 2


def test_isolation_markers_are_frozen():
    assert isolation.EXPOSURE_MARKERS == (
        "tasks/holdout", "test_holdout_", "test_fixture_", "_reference_fix", "preregistration",
    )


# --------------------------------------------------------------------------
# Documentation matches code
# --------------------------------------------------------------------------


def test_preregistration_records_the_frozen_definitions_and_pair_2(request):
    text = (request.config.rootpath / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "Frozen definitions v1" in text
    assert "Second Controlled A/B Pair — Pre-run hypotheses" in text
    for name in (
        "gross_primed_reacquisition", "edit_precondition_associated",
        "verification_associated", "discretionary_information_reacquisition",
        "unknown", "handoff_repository_content_duplication",
        "handoff_reacquisition_overlap",
    ):
        assert name in text, name
    for value in ("0.60", "22ce9b7e5249dd497ee7c4c0318216b4", "2.1.260"):
        assert value in text, value
    for h in ("H1", "H2", "H3", "H4", "H5"):
        assert h in text


# --------------------------------------------------------------------------
# Local tests cannot consume Claude quota
# --------------------------------------------------------------------------


def test_the_real_claude_cli_cannot_be_launched_from_local_tests():
    with pytest.raises(RuntimeError, match="real Claude Code CLI launch refused"):
        subprocess.Popen(["claude", "--version"])
    with pytest.raises(RuntimeError, match="real Claude Code CLI launch refused"):
        subprocess.run([r"C:\somewhere\claude.exe", "-p", "hi"])
