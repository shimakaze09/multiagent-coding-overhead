"""Amendment 9 regression: `runner.py calibrate` and `runner.py evaluate` run end
to end through the mock CLI (tools/mock_claude.py). No Claude session.

Only the CLI's auth-status check is stubbed (the mock has no `auth status`);
capability detection, billing guard, workspace, orchestration, routing
verification and finish all run for real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
import runner
from analysis import exposure_provenance

ROOT = Path(__file__).resolve().parent.parent
MOCK_CLI = ROOT / "tools" / "mock_claude.py"
TASK = "palindrome_punctuation"


@pytest.fixture
def mock_runner(tmp_path, monkeypatch, clean_env):
    monkeypatch.setenv("STAGE0_CLAUDE_CLI", str(MOCK_CLI))
    monkeypatch.setattr(runner, "_check_cli_auth", lambda cli: {"ok": True})
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "STAGE2_CANDIDATES", config.STAGE2_CANDIDATES + (TASK,))
    return tmp_path / "runs"


def _only_run(runs_dir, pattern):
    (run_dir,) = sorted(p for p in runs_dir.glob(pattern) if p.is_dir())
    return run_dir, json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))


def test_calibrate_runs_end_to_end(mock_runner, capsys):
    monkeypatch_labels = runner.difficulty_mod.load_labels()
    assert monkeypatch_labels is None  # calibration is open
    assert runner.main(["calibrate", "--task", TASK, "--repeat-id", "1"]) == 0
    run_dir, meta = _only_run(mock_runner, f"*_{TASK}_S2_SS_r1")
    assert meta["arm"] == "S2_SS" and meta["stage2_phase"] == "calibration"
    # amendment 11: a new run records the provenance rule, so it is in force
    assert exposure_provenance.applies_to(meta) is True
    assert meta["exposure_provenance_rule"] == exposure_provenance.rule_record()
    assert "stage2_difficulty" not in meta
    assert isinstance(meta["capability_report"], dict) and meta["capability_report"]["ok"] is True
    assert meta["config"]["limits"] == config.STAGE2_LIMITS.as_dict()
    routing = json.loads((run_dir / "routing.json").read_text(encoding="utf-8"))
    assert [x["resolved_model"] for x in routing["invocations"]] == ["claude-sonnet-5"]
    assert "phase=calibration" in capsys.readouterr().out


def test_evaluate_runs_end_to_end(mock_runner, monkeypatch):
    labels = {"benchmark": {"easy": [], "medium": [], "hard": [TASK], "very_hard": [], "beyond": []},
              "easy_controls": []}
    monkeypatch.setattr(runner.difficulty_mod, "load_labels", lambda *a, **k: labels)
    monkeypatch.setattr(runner.difficulty_mod, "labels_sha256", lambda *a, **k: "a" * 64)
    assert runner.main(["evaluate", "--task", TASK, "--arm", "S2_M", "--repeat-id", "1",
                        "--runs-dir", str(mock_runner)]) == 0
    run_dir, meta = _only_run(mock_runner, f"*_{TASK}_S2_M_r1")
    assert meta["stage2_phase"] == "evaluation"
    assert meta["stage2_difficulty"] == {"stratum": "hard", "labels_sha256": "a" * 64}
    assert isinstance(meta["capability_report"], dict)
    for sd in (run_dir / "sessions").iterdir():
        inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        assert "hard" not in inv["stdin_text"].split() and "a" * 64 not in inv["stdin_text"]


def test_amendment_9_is_recorded():
    text = (ROOT / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "### Amendment 9" in text and "BEFORE any Stage-2 inference" in text
