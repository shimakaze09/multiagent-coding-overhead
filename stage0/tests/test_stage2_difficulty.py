"""Stage 2 amendment: difficulty-calibrated quality x cost (PREREGISTRATION 19).

Local/mocked validation only (tools/mock_claude.py); no Claude session.
"""

from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

import pytest

import config
import runner
from analysis import difficulty, quality, quality_cost, report as report_mod, stage2
from arms import multi_nl, s2_routing, single
from tasks import registry, stage2_design

ROOT = Path(__file__).resolve().parent.parent
MOCK_CLI = ROOT / "tools" / "mock_claude.py"

OLD_S2_HASHES = {  # frozen by section 18 (ee0f020); unchanged by section 19
    "S2_R1": "f1a4b7f32952ee7618e3062c0f0a5ebd", "S2_R2": "073ebc97390854d8331531a681a8374d",
    "S2_R3": "4b75b94f619fe83c93aa3582a3da0aa3", "S2_S": "d9e92534b466f8793b390b222c18fe76",
    "S2_SS": "d2cd1cf2024412d57d685340261f5fe5",
}
S2_M_SMOKE_HASH = "d5459739f84ac4c5202bb144b45507da"
AMENDMENT_BASE_HASH = "38c37c95c6160f3a17fc71d910e4f6fb"
AMENDMENT_HASHES = {  # topology_config_hash(RunConfig(limits=STAGE2_LIMITS), stage2_topology(arm))
    "S2_R1": "423975fef4e6c99390df411b65b0ab73", "S2_R2": "67b4604c80555090d15190521e94c09a",
    "S2_R3": "2fa77f9c107074e39c5d389f8647d5f8", "S2_S": "cdd156bf965213748ee194a71ec2b2b2",
    "S2_SS": "7b8ebe0d1074c2ae2cc984bc63c59e30", "S2_M": "ecbff88e07b7f4255cd7ab0e22ab9503",
}


# --------------------------------------------------------------------------
# 1-3: old routing definitions, S2_M, historical outputs
# --------------------------------------------------------------------------


def test_1_old_stage2_routing_definitions_are_unchanged():
    got = {a: config.topology_config_hash(config.RunConfig(), config.stage2_topology(a)) for a in OLD_S2_HASHES}
    assert got == OLD_S2_HASHES
    assert config.STAGE2_PILOT_ARMS == ("S2_R1", "S2_R2", "S2_R3", "S2_S")
    assert config.CHEAP_MODEL == "claude-haiku-4-5-20251001" and config.STRONG_MODEL == "sonnet"


def test_2_s2_m_uses_the_strong_model_for_every_role(mock_s2_runs):
    topo = config.stage2_topology("S2_M")
    assert topo["role_models_requested"] == {r: "sonnet" for r in ("coordinator", "investigator", "implementer")}
    assert topo["base_arm_topology"] == "B" and topo["prompt_version"]["prompts"] == "arm_b_v1_verbatim"
    assert config.topology_config_hash(config.RunConfig(), topo) == S2_M_SMOKE_HASH
    assert config.STAGE2_ARCHITECTURES["M"] == "S2_M" and "S2_M" in runner.SMOKE_ARMS
    run_dir, summary = mock_s2_runs["S2_M"]
    r = report_mod.run_report(run_dir)
    assert [(x["logical_role"], x["requested_model"], x["resolved_model"]) for x in r["model_routing"]["invocations"]] == [
        (role, "sonnet", "claude-sonnet-5")
        for role in ("coordinator", "investigator", "coordinator", "implementer", "coordinator")]
    assert r["validity"]["valid"] and summary["session_count"] == 5
    assert r["topology"]["fresh_sessions"] == 3


def test_2_amendment_identities_and_limits_are_pinned():
    cfg = config.RunConfig(limits=config.STAGE2_LIMITS)
    assert cfg.config_hash() == AMENDMENT_BASE_HASH
    assert {a: config.topology_config_hash(cfg, config.stage2_topology(a)) for a in AMENDMENT_HASHES} == AMENDMENT_HASHES
    assert (config.STAGE2_LIMITS.max_turns_per_session, config.STAGE2_LIMITS.max_wall_seconds_per_session) == (60, 1800)
    assert config.stage2_limits_for("shipping_inch_dimensions") is config.SMOKE_LIMITS
    assert config.stage2_limits_for("s2t05_flowq") is config.STAGE2_LIMITS


@pytest.mark.skipif(not (ROOT / "runs" / "20260911T031118Z_sla_weekend_hours_C1_r1").is_dir(),
                    reason="historical runs not present locally")
def test_3_historical_stage05_and_stage1_summaries_reproduce(capsys):
    for argv, frozen in ((["summary"], "results/stage05_pilot/cross_task_summary.txt"),
                         (["stage1-summary"], "results/stage1_c1/stage1_summary.txt")):
        assert runner.main(argv) == 0
        out = capsys.readouterr().out.replace("\r\n", "\n").strip()
        assert out == (ROOT / frozen).read_text(encoding="utf-8").replace("\r\n", "\n").strip(), frozen


# --------------------------------------------------------------------------
# Synthetic run entries
# --------------------------------------------------------------------------


def E(task, arm, rep, solved, *, phase="evaluation", valid=True, cost=1.0, wall=10.0, tin=100, tout=10,
      strong=0.0, q2=None):
    return {"run_id": f"20260101T0000{rep:02d}Z_{task}_{arm}_r{rep}", "task_id": task, "arm": arm,
            "repeat_id": rep, "solved": solved, "valid": valid, "stage2_phase": phase,
            "metrics": {"cost_usd": cost, "cost_exact": True, "wall_seconds": wall,
                        "tokens": {"input": tin, "cache_read": 0, "cache_write": 0, "total_input": tin,
                                   "output": tout, "thinking_if_exposed": 0},
                        "strong": {"cost_usd": strong, "invocations": 0}},
            "quality": {"q2_heldout_fraction": q2, "q5_constraint_fraction": None,
                        "q3_regression": {"preserved": True}},
            "tool_calls": 3, "model_calls": 4, "strong_model_calls": 0, "cheap_model_calls": 0}


def labels_with(stratum_tasks):
    bench = {s: [] for s in difficulty.STRATA}
    bench.update(stratum_tasks)
    return {"benchmark": bench, "easy_controls": []}


HARD = ("s2t05_flowq", "s2t09_docpipe")
LABELS = labels_with({"hard": list(HARD)})


# --------------------------------------------------------------------------
# 4-5: calibration and evaluation are separated
# --------------------------------------------------------------------------


def test_4_calibration_runs_never_enter_evaluation_aggregates():
    entries = [E(t, "S2_SS", i, True, phase="calibration") for t in HARD for i in (1, 2, 3)]
    entries += [E(t, "S2_SS", i, False) for t in HARD for i in (1, 2, 3)]
    entries += [E(t, "S2_M", i, i == 1, phase="unassigned") for t in HARD for i in (1, 2, 3)]
    ev = quality_cost.evaluation_entries(entries, LABELS)
    assert ev and all(e["stage2_phase"] == "evaluation" for e in ev)
    s = quality_cost.analyse(entries, LABELS)["strata"]["hard"]["architectures"]
    assert set(s) == {"A"} and (s["A"]["n"], s["A"]["successes"]) == (6, 0)


def _calibration_set():
    out = []
    pattern = {0: (True, True, True), 1: (True, True, False, True, True), 2: (False, True, False, True, False),
               3: (False,) * 5}
    for i, t in enumerate(config.STAGE2_CANDIDATES):
        for rep, ok in enumerate(pattern[i % 4], start=1):
            out.append(E(t, "S2_SS", rep, ok, phase="calibration"))
    return out


def test_5_evaluation_results_cannot_change_difficulty_labels():
    cal = _calibration_set()
    noise = [E(t, arm, rep, True) for t in config.STAGE2_CANDIDATES for arm in ("S2_SS", "S2_M", "S2_R1")
             for rep in (1, 2, 3)]
    noise += [E(t, "S2_M", 9, False, phase="calibration") for t in config.STAGE2_CANDIDATES]
    assert difficulty.calibration_summary(cal) == difficulty.calibration_summary(cal + noise)
    assert difficulty.freeze_labels(cal) == difficulty.freeze_labels(noise + cal)


# --------------------------------------------------------------------------
# 6: repeated runs group correctly
# --------------------------------------------------------------------------


def test_6_repeated_runs_group_by_task_and_architecture():
    entries = [E(t, arm, rep, rep % 2 == 0) for t in HARD for arm in ("S2_SS", "S2_M") for rep in (3, 1, 2)]
    g = quality_cost.grouped(entries, LABELS)
    for t in HARD:
        assert [e["repeat_id"] for e in g["by_task"][t]["A"]] == [1, 2, 3]
        assert [e["repeat_id"] for e in g["by_task"][t]["M"]] == [1, 2, 3]
    assert len(g["by_stratum"]["hard"]["A"]) == 6 and len(g["by_stratum"]["hard"]["M"]) == 6


def test_6_sequential_rules_for_calibration_and_e1():
    s = difficulty.task_status("s2t01_ledgerly", [E("s2t01_ledgerly", "S2_SS", r, True, phase="calibration")
                                                  for r in (1, 2, 3)])
    assert (s["status"], s["stratum"], s["next_repeat_ids"]) == ("complete", "easy", [])
    runs = [E("s2t01_ledgerly", "S2_SS", 1, True, phase="calibration"),
            E("s2t01_ledgerly", "S2_SS", 2, False, phase="calibration", valid=False),
            E("s2t01_ledgerly", "S2_SS", 3, False, phase="calibration"),
            E("s2t01_ledgerly", "S2_SS", 4, True, phase="calibration")]
    s = difficulty.task_status("s2t01_ledgerly", runs)
    assert (s["status"], s["n"], s["next_repeat_ids"]) == ("needs_runs", 3, [5, 6])
    assert len(s["invalid_run_ids"]) == 1 and s["stratum"] is None
    both3 = {"A": [E("x", "S2_SS", r, True) for r in (1, 2, 3)], "M": [E("x", "S2_M", r, True) for r in (1, 2, 3)]}
    assert quality_cost.e1_task_status(both3) == {"complete": True, "target": 3}
    mixed = {"A": [E("x", "S2_SS", r, r != 2) for r in (1, 2, 3)], "M": both3["M"]}
    assert quality_cost.e1_task_status(mixed) == {"complete": False, "target": 5}


# --------------------------------------------------------------------------
# 7: held-out partial scores
# --------------------------------------------------------------------------


SOURCE = """
def test_c1_a():
    pass
def test_c1_b():
    pass
def test_c2_a():
    pass
def test_c3_missing():
    pass
def test_r_keep():
    pass
"""
DESIGN = {"heldout": {"constraints": {"C1": "one", "C2": "two", "C3": "three"}}}


def test_7_held_out_partial_scores_compute_correctly():
    out = ("PASSED test_holdout_x.py::test_c1_a\n"
           "FAILED test_holdout_x.py::test_c1_b - AssertionError: x\n"
           "PASSED test_holdout_x.py::test_c2_a\nPASSED test_holdout_x.py::test_r_keep\n"
           "1 failed, 3 passed in 0.10s\n")
    s = quality.held_out_scores(out, SOURCE, DESIGN)
    assert (s["tests_defined"], s["tests_passed"], s["q2_heldout_fraction"]) == (5, 3, 0.6)
    assert s["per_test"]["test_c3_missing"] == "not_reported"
    assert (s["q5_constraints_satisfied"], s["q5_constraints_total"], s["q5_constraint_fraction"]) == (1, 3, 0.3333)
    assert (s["heldout_regression_passed"], s["heldout_regression_total"]) == (1, 1)
    legacy = quality.held_out_scores("..F.\n1 failed, 3 passed in 0.2s\n", "", None)
    assert (legacy["basis"], legacy["q2_heldout_fraction"], legacy["q5_constraint_fraction"]) == (
        "summary_counts", 0.75, None)


def test_7_patch_scope_is_mechanical():
    diff = ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n@@ -1 +1,2 @@\n-x = 1\n+x = 2\n+y = 3\n"
            "diff --git a/pkg/__pycache__/a.cpython-312.pyc b/pkg/__pycache__/a.cpython-312.pyc\n"
            "diff --git a/docs/b.md b/docs/b.md\n--- a/docs/b.md\n+++ b/docs/b.md\n@@ -1 +1 @@\n-old\n+new\n")
    s = quality.patch_scope(diff, ["pkg/a.py"])
    assert (s["files_changed"], s["loc_added"], s["loc_removed"]) == (2, 3, 2)
    assert s["unexpected_files"] == ["docs/b.md"]


# --------------------------------------------------------------------------
# 8-11: aggregation, Wilson, cost per success, dominance
# --------------------------------------------------------------------------


def test_8_success_rate_aggregation_is_deterministic():
    runs = [E("t", "S2_M", r, ok, cost=c) for r, ok, c in ((1, True, 1.0), (2, False, 2.0), (3, True, 3.0))]
    a = quality_cost.summarize(runs)
    shuffled = runs[:]
    random.Random(7).shuffle(shuffled)
    b = quality_cost.summarize(shuffled)
    for k in a:
        if k != "run_ids":
            assert a[k] == b[k], k
    assert (a["n"], a["successes"], a["success_rate"]) == (3, 2, 0.6667)
    assert a["cost_usd"]["mean"] == 2.0 and a["cost_per_success"] == 3.0
    assert a["expected_cost_to_success_estimate"] == 3.0


def test_9_wilson_interval_is_deterministic():
    assert quality_cost.wilson(0, 3) == (0.0, 0.5615)
    assert quality_cost.wilson(3, 3) == (0.4385, 1.0)
    assert quality_cost.wilson(1, 5) == (0.0362, 0.6245)
    assert quality_cost.wilson(5, 10) == (0.2366, 0.7634)
    assert quality_cost.wilson(0, 0) == (None, None)
    assert quality_cost.wilson(2, 5) == quality_cost.wilson(2, 5)


def test_10_cost_per_success_with_zero_successes_is_undefined():
    s = quality_cost.summarize([E("t", "S2_SS", r, False, cost=2.0) for r in (1, 2, 3)])
    assert s["successes"] == 0 and s["cost_per_success"] is None
    assert s["wall_per_success"] is None and s["tokens_per_success"] is None
    assert s["expected_cost_to_success_estimate"] is None
    assert quality_cost.per_success(6.0, 0) is None and quality_cost.per_success(6.0, 2) == 3.0
    labels = labels_with({"hard": ["s2t05_flowq"]})
    entries = [E("s2t05_flowq", arm, r, False) for arm in ("S2_SS", "S2_M") for r in (1, 2, 3)]
    assert "undefined" in quality_cost.render(quality_cost.analyse(entries, labels), labels)


def test_11_quality_cost_dominance():
    pts = [{"name": "A", "rate": 0.5, "cost": 1.0}, {"name": "M", "rate": 0.8, "cost": 3.0},
           {"name": "H1", "rate": 0.8, "cost": 2.0}, {"name": "CS", "rate": 0.5, "cost": 0.5},
           {"name": "CM", "rate": 0.3, "cost": 1.0}]
    assert quality_cost.frontier(pts) == ["H1", "CS"]
    assert quality_cost.dominates(pts[2], pts[1]) and not quality_cost.dominates(pts[1], pts[2])
    same = {"name": "X", "rate": 0.5, "cost": 1.0}
    assert not quality_cost.dominates(same, dict(same)) and not quality_cost.dominates(pts[0], pts[1])
    assert not quality_cost.dominates({"name": "N", "rate": None, "cost": 0}, pts[0])


# --------------------------------------------------------------------------
# 12-13: design metadata and difficulty labels never reach prompts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", config.STAGE2_CANDIDATES)
def test_12_task_design_metadata_never_enters_prompts(task_id):
    task = registry.get_task(task_id)
    design = stage2_design.load_design(task_id)
    blob = json.dumps(design)
    prompts = [single.build_solo_prompt(task), multi_nl.coordinator_kickoff_prompt(task),
               multi_nl.investigator_prompt(task, "i"), multi_nl.implementer_prompt(task, "i", "r")]
    for p in prompts:
        assert "design.json" not in p and "task_family" not in p
        for value in (*design["design_features"]["plausible_root_causes"],
                      *design["heldout"]["constraints"].values(), design["task_family"]):
            assert value not in p
    assert task.notes in json.dumps(task.as_dict()) and "ANALYSIS ONLY" in task.notes
    assert "statement" not in blob


@pytest.fixture(scope="module")
def evaluation_mock_run(tmp_path_factory):
    import os
    from harness import claude_cli
    os.environ["STAGE0_CLAUDE_CLI"] = str(MOCK_CLI)
    try:
        cli = config.find_claude_cli()
        caps = claude_cli.detect_capabilities(cli)
        runs_dir = tmp_path_factory.mktemp("runs_s2_eval")
        out = {}
        for arm, phase, diff in (("S2_M", "evaluation", {"stratum": "very_hard", "labels_sha256": "f" * 64}),
                                 ("S2_SS", "calibration", None)):
            s = s2_routing.run(arm=arm, task=registry.get_task("palindrome_punctuation"), repeat_id=1,
                               cfg=config.RunConfig(), cli=cli, capability_report=caps.as_dict(),
                               runs_dir=runs_dir, phase=phase, difficulty=diff)
            out[phase] = runs_dir / s["run_id"]
        return out
    finally:
        os.environ.pop("STAGE0_CLAUDE_CLI", None)


def test_13_difficulty_labels_never_enter_prompts(evaluation_mock_run):
    run_dir = evaluation_mock_run["evaluation"]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["stage2_phase"] == "evaluation"
    assert meta["stage2_difficulty"] == {"stratum": "very_hard", "labels_sha256": "f" * 64}
    for sd in sorted((run_dir / "sessions").iterdir()):
        inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
        for text in ("very_hard", "stratum", "f" * 64, "evaluation", "calibration"):
            assert text not in inv["stdin_text"]
            assert text not in " ".join(inv["argv"])
            assert text not in json.dumps(inv["prompt_inputs"])
            assert text not in json.dumps(inv["env_manifest"].get("set_by_harness", {}))
    e = stage2.load_entry(run_dir)
    assert e["stage2_phase"] == "evaluation" and e["stage2_difficulty"]["stratum"] == "very_hard"
    assert e["quality"]["q1_solved"] is True and e["strong_model_calls"] > 0 and e["cheap_model_calls"] == 0
    cal = stage2.load_entry(evaluation_mock_run["calibration"])
    assert cal["stage2_phase"] == "calibration" and "stage2_difficulty" not in json.loads(
        (evaluation_mock_run["calibration"] / "metadata.json").read_text(encoding="utf-8"))


def test_13_unknown_phases_are_refused(tmp_path):
    cli = config.ClaudeCli(path=str(MOCK_CLI), version="", discovered_via="test")
    with pytest.raises(ValueError):
        s2_routing.run(arm="S2_SS", task=registry.get_task("palindrome_punctuation"), repeat_id=1,
                       cfg=config.RunConfig(), cli=cli, capability_report={}, runs_dir=tmp_path, phase="tuning")
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# 14: no test launches Claude
# --------------------------------------------------------------------------


def test_14_no_real_claude_is_launched(evaluation_mock_run):
    with pytest.raises(RuntimeError, match="refused"):
        subprocess.Popen(["claude", "--version"])
    for run_dir in evaluation_mock_run.values():
        for sd in (run_dir / "sessions").iterdir():
            inv = json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
            assert inv["cli_path"].replace("\\", "/").endswith("tools/mock_claude.py")


# --------------------------------------------------------------------------
# Strata, selection, freeze
# --------------------------------------------------------------------------


def test_strata_follow_the_preregistered_rule():
    cases = {(3, 3): "easy", (5, 5): "easy", (4, 5): "medium", (3, 5): "hard", (2, 5): "hard",
             (1, 5): "very_hard", (0, 5): "beyond"}
    assert {k: difficulty.stratum_for(*k) for k in cases} == cases
    with pytest.raises(ValueError):
        difficulty.stratum_for(0, 0)


def test_benchmark_selection_is_capped_deterministic_and_family_balanced():
    statuses = {t: {"stratum": "hard"} for t in config.STAGE2_CANDIDATES}
    a, b = difficulty.select_benchmark(statuses), difficulty.select_benchmark(dict(reversed(list(statuses.items()))))
    assert a == b
    chosen = a["selected"]["hard"]
    assert len(chosen) == difficulty.STRATUM_CAPS["hard"] == 3
    assert len({stage2_design.load_design(t)["task_family"] for t in chosen}) == 3
    assert sorted(chosen + a["excluded_over_cap"]["hard"]) == sorted(config.STAGE2_CANDIDATES)


def test_freeze_refuses_incomplete_calibration_and_overwrites(tmp_path):
    with pytest.raises(ValueError, match="incomplete"):
        difficulty.freeze_labels([])
    labels = difficulty.freeze_labels(_calibration_set())
    assert {s["stratum"] for s in labels["tasks"].values()} == {"easy", "medium", "hard", "beyond"}
    path = tmp_path / "labels.json"
    sha = difficulty.write_labels(labels, path)
    assert sha == difficulty.labels_sha256(path) and difficulty.load_labels(path) == labels
    with pytest.raises(FileExistsError):
        difficulty.write_labels(labels, path)
    assert difficulty.task_stratum(labels, "shipping_inch_dimensions") == "easy_control"


# --------------------------------------------------------------------------
# Decision tree, E2 gate, evaluation plan, crossover
# --------------------------------------------------------------------------


def _summ(rate, cost):
    return {"success_rate": rate, "cost_usd": {"mean": cost}}


def test_decision_tree_outcomes():
    e1 = {"complete": True}
    d = quality_cost.decide
    assert d("hard", {"A": _summ(0.5, 1)}, e1)["outcome"] == "pending"
    assert d("hard", {"A": _summ(0.0, 1), "M": _summ(0.1, 3)}, e1)["outcome"] == "beyond_current_capability"
    assert d("hard", {"A": _summ(0.95, 1), "M": _summ(1.0, 3)}, e1)["outcome"] == "ceiling_not_interpretable"
    assert d("hard", {"A": _summ(0.5, 1), "M": _summ(0.6, 3)}, e1)["outcome"] == "multi_agent_unnecessary"
    assert d("hard", {"A": _summ(0.6, 1), "M": _summ(0.4, 3)}, e1)["outcome"] == "multi_agent_worse"
    gain = {"A": _summ(0.4, 1), "M": _summ(0.8, 3)}
    assert d("hard", gain, e1)["outcome"] == "multi_agent_gain_e2_pending"
    assert d("hard", {**gain, "H1": _summ(0.74, 2), "H2": _summ(0.5, 2)}, e1)["outcome"] == "heterogeneous_routing_useful_h1"
    assert d("hard", {**gain, "H1": _summ(0.6, 2), "H2": _summ(0.8, 2.5)}, e1)["outcome"] == \
        "implementation_capability_more_important_h2"
    assert d("hard", {**gain, "H1": _summ(0.8, 3.5), "H2": _summ(0.5, 2)}, e1)["outcome"] == \
        "all_strong_capability_may_be_necessary"
    r = d("hard", {**gain, "H1": _summ(0.74, 2), "H2": _summ(0.5, 2)}, e1)
    assert r["h1"] == {"retained_fraction": 0.85, "cheaper_than_m": True, "preserves": True}


def _e1(task, a_ok, m_ok):
    return ([E(task, "S2_SS", r, ok) for r, ok in enumerate(a_ok, 1)]
            + [E(task, "S2_M", r, ok) for r, ok in enumerate(m_ok, 1)])


def test_evaluation_plan_follows_the_e1_rule_and_the_e2_gate():
    plan = quality_cost.evaluation_plan([], LABELS)
    assert {(p["task"], p["arm"]): p["repeat_ids"] for p in plan} == {
        (t, a): [1, 2, 3] for t in HARD for a in ("S2_SS", "S2_M")}
    concordant = _e1(HARD[0], (True,) * 3, (True,) * 3) + _e1(HARD[1], (False,) * 3, (False,) * 3)
    assert quality_cost.evaluation_plan(concordant, LABELS) == []
    gain = _e1(HARD[0], (False, True, False, False, True), (True, True, True, False, True)) + \
        _e1(HARD[1], (False, False, True, False, False), (True, False, True, True, False))
    plan = quality_cost.evaluation_plan(gain, LABELS)
    assert {(p["task"], p["arm"]) for p in plan} == {(t, a) for t in HARD for a in config.STAGE2_E2_ARMS}
    assert all(p["repeat_ids"] == [1, 2, 3] for p in plan)
    no_gain = _e1(HARD[0], (True, False, True, True, False), (True, True, False, False, True)) + \
        _e1(HARD[1], (False,) * 3, (False,) * 3)
    assert quality_cost.evaluation_plan(no_gain, LABELS) == []


def test_analysis_reports_ratios_frontier_and_crossover():
    entries = (_e1(HARD[0], (False, True, False, False, True), (True, True, True, False, True))
               + _e1(HARD[1], (False, False, True, False, False), (True, False, True, True, False)))
    for e in entries:
        if e["arm"] == "S2_M":
            e["metrics"]["cost_usd"], e["metrics"]["tokens"]["total_input"] = 3.0, 250
    res = quality_cost.analyse(entries, LABELS)
    hard = res["strata"]["hard"]
    c = hard["vs_single_strong"]["M"]
    assert c["delta_success"] == 0.4 and c["cost_ratio"] == 3.0 and c["total_input_ratio"] == 2.5
    assert hard["e1"]["e2_gate_open"] is True and res["crossover_stratum"] == "hard"
    assert hard["decision"]["outcome"] == "multi_agent_gain_e2_pending"
    assert set(hard["frontier_cost"]) == {"A", "M"}
    json.dumps(res, default=str)
    assert quality_cost.analyse([], LABELS)["crossover_stratum"] == "no crossover observed"


# --------------------------------------------------------------------------
# Runner guards (all refuse before probing the CLI)
# --------------------------------------------------------------------------


def test_calibrate_and_evaluate_refuse_before_any_probe(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="not a Stage-2 candidate"):
        runner.main(["calibrate", "--task", "sla_weekend_hours", "--repeat-id", "1"])
    monkeypatch.setattr(runner.difficulty_mod, "load_labels", lambda *a, **k: LABELS)
    with pytest.raises(SystemExit, match="calibration is closed"):
        runner.main(["calibrate", "--task", "s2t01_ledgerly", "--repeat-id", "1"])
    with pytest.raises(SystemExit, match="not in the frozen benchmark"):
        runner.main(["evaluate", "--task", "s2t01_ledgerly", "--arm", "S2_M", "--repeat-id", "1"])
    with pytest.raises(SystemExit, match="not a Stage-2 evaluation architecture"):
        runner.main(["evaluate", "--task", HARD[0], "--arm", "C1", "--repeat-id", "1"])
    with pytest.raises(SystemExit, match="E2 gate closed"):
        runner.main(["evaluate", "--task", HARD[0], "--arm", "S2_R1", "--repeat-id", "1",
                     "--runs-dir", str(tmp_path)])
    monkeypatch.setattr(runner.difficulty_mod, "load_labels", lambda *a, **k: None)
    with pytest.raises(SystemExit, match="not frozen"):
        runner.main(["evaluate", "--task", HARD[0], "--arm", "S2_M", "--repeat-id", "1"])


def test_no_claude_commands_render(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(runner.difficulty_mod, "load_labels", lambda *a, **k: None)
    assert runner.main(["difficulty-summary", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert all(t in out for t in config.STAGE2_CANDIDATES) and "[1, 2, 3]" in out
    with pytest.raises(SystemExit, match="incomplete"):
        runner.main(["difficulty-freeze", "--runs-dir", str(tmp_path)])
    assert runner.main(["stage2-frontier", "--runs-dir", str(tmp_path)]) == 0
    assert "not frozen" in capsys.readouterr().out
    monkeypatch.setattr(runner.difficulty_mod, "load_labels", lambda *a, **k: LABELS)
    assert runner.main(["evaluation-plan", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"python runner.py evaluate --task {HARD[0]} --arm S2_M --repeat-id 3" in out
    assert not (ROOT / "results" / "stage2" / "difficulty_labels.json").exists()


def test_preregistration_records_the_amendment():
    text = (ROOT / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "## 19. Stage 2 amendment: difficulty-calibrated quality" in text
    for s in ("ee0f020", "SUPERSEDED", "no Stage-2 model call", "S2_M", "Wilson", "0.15", "0.8",
              *config.STAGE2_CANDIDATES, AMENDMENT_BASE_HASH, S2_M_SMOKE_HASH, *AMENDMENT_HASHES.values()):
        assert s in text, s
