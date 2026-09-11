"""Stage 1 - C1_shared_worker_context (PREREGISTRATION section 16).

Local/mocked validation only: tools/mock_claude.py stands in for Claude Code, so
no real Claude session is opened and no quota is used.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import config
import runner
from analysis import decomposition, ingest
from analysis import report as report_mod
from arms import c1_shared_worker, multi_nl, single
from harness import events as ev
from tasks import registry

ROOT = Path(__file__).resolve().parent.parent
STAGE1_TASKS = ("shipping_inch_dimensions", "settings_list_fields",
                "rename_max_connections", "sla_weekend_hours")


def _sessions(run_dir):
    return {sd.name: json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
            for sd in sorted((run_dir / "sessions").iterdir())}


def _init(run_dir, key):
    for line in (run_dir / "sessions" / key / "claude_stdout.jsonl").read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        if o.get("type") == "system" and o.get("subtype") == "init":
            return o
    raise AssertionError(f"no init event in {key}")


@pytest.fixture(scope="module")
def c1(mock_c1_run):
    run_dir, summary = mock_c1_run
    return run_dir, summary, report_mod.run_report(run_dir)


# --------------------------------------------------------------------------
# 1-5: physical sessions and resume
# --------------------------------------------------------------------------


def test_one_coordinator_physical_session(c1):
    run_dir, _, r = c1
    coord = [x for x in r["topology"]["invocations"] if x["logical_role"] == "coordinator"]
    assert len(coord) == 3
    assert len({x["physical_session_id"] for x in coord}) == 1
    assert [x["is_resume"] for x in coord] == [False, True, True]


def test_one_worker_physical_session_and_the_invocation_counts(c1):
    _, summary, r = c1
    t = r["topology"]
    assert (t["cli_invocations"], t["fresh_sessions"], t["resumed_invocations"], t["physical_sessions"]) == (5, 2, 3, 2)
    assert t["logical_roles"] == ["coordinator", "implementer", "investigator"]
    assert summary["session_count"] == 5


def test_worker_phases_share_the_session_id(c1):
    run_dir, _, _ = c1
    inv_id = _init(run_dir, "02_investigator")["session_id"]
    assert _init(run_dir, "04_implementer")["session_id"] == inv_id


def test_implementer_phase_resumes_the_investigator_session(c1):
    run_dir, _, _ = c1
    s = _sessions(run_dir)
    inv_id = _init(run_dir, "02_investigator")["session_id"]
    imp = s["04_implementer"]
    assert imp["resume_session_id"] == inv_id
    assert imp["argv"][imp["argv"].index("--resume") + 1] == inv_id
    assert "--session-id" not in imp["argv"]
    assert s["02_investigator"]["resume_session_id"] is None


def test_coordinator_session_is_separate_from_the_worker(c1):
    run_dir, _, r = c1
    wt = r["topology"]["worker_transition"]
    assert wt["verified"] is True and all(wt["checks"].values())
    assert _init(run_dir, "01_coordinator")["session_id"] != _init(run_dir, "02_investigator")["session_id"]


# --------------------------------------------------------------------------
# 6-8: what flows where
# --------------------------------------------------------------------------


def test_worker_report_still_goes_to_the_coordinator(c1):
    run_dir, summary, _ = c1
    h = {x["label"]: x for x in summary["handoffs"]}
    assert (h["investigation_report"]["sender"], h["investigation_report"]["recipient"]) == ("investigator", "coordinator")
    report = (run_dir / h["investigation_report"]["path"]).read_text(encoding="utf-8")
    assert _sessions(run_dir)["03_coordinator"]["prompt_inputs"]["investigator_report"] == report


def test_coordinator_instruction_returns_to_the_worker(c1):
    run_dir, summary, _ = c1
    h = {x["label"]: x for x in summary["handoffs"]}
    instr = (run_dir / h["implementation_instruction"]["path"]).read_text(encoding="utf-8")
    assert h["implementation_instruction"]["recipient"] == "implementer"
    imp = _sessions(run_dir)["04_implementer"]
    assert imp["stdin_text"] == c1_shared_worker.implementer_resume_prompt(instr)


def test_investigator_report_is_not_forwarded_back_and_task_is_not_resent(c1):
    run_dir, summary, _ = c1
    labels = [x["label"] for x in summary["handoffs"]]
    assert "forwarded_investigation_report" not in labels
    assert labels == ["investigation_instruction", "investigation_report", "implementation_instruction",
                      "implementation_report", "final_result"]
    h = {x["label"]: x for x in summary["handoffs"]}
    report = (run_dir / h["investigation_report"]["path"]).read_text(encoding="utf-8")
    imp = _sessions(run_dir)["04_implementer"]
    assert report.strip()[:60] not in imp["stdin_text"]
    assert registry.get_task("palindrome_punctuation").statement not in imp["stdin_text"]
    assert set(imp["prompt_inputs"]) == {"coordinator_instruction", "step", "logical_role", "physical_session"}


# --------------------------------------------------------------------------
# 9-10: per-phase tool policy (requested AND as reported by each init event)
# --------------------------------------------------------------------------


def test_investigator_phase_is_read_only(c1):
    run_dir, _, _ = c1
    inv = _sessions(run_dir)["02_investigator"]
    assert inv["tools"] == list(config.ROLE_TOOLS["investigator"])
    assert {"Edit", "Write", "NotebookEdit"} <= set(inv["disallowed_tools"])
    assert not ({"Edit", "Write", "NotebookEdit"} & set(_init(run_dir, "02_investigator")["tools"]))


def test_implementer_phase_has_the_implementation_policy(c1):
    run_dir, _, _ = c1
    imp = _sessions(run_dir)["04_implementer"]
    assert imp["tools"] == list(config.ROLE_TOOLS["implementer"])
    assert imp["disallowed_tools"] == []
    assert imp["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)
    assert {"Edit", "Write"} <= set(_init(run_dir, "04_implementer")["tools"])
    assert imp["append_system_prompt"] == c1_shared_worker.agent.ROLE_SYSTEM_APPENDIX["implementer"]


def test_a_failed_transition_makes_the_run_invalid(c1):
    _, _, r = c1
    rows = [dict(x) for x in r["topology"]["invocations"]]
    for x in rows:
        if x["logical_role"] == "implementer":
            x["init_tools"] = ["Read", "Grep", "Glob", "Bash"]     # resume kept the old policy
    bad = report_mod.worker_transition_check(rows)
    assert bad["verified"] is False and bad["checks"]["implementer_phase_can_edit"] is False
    broken = dict(r, topology={**r["topology"], "worker_transition": bad})
    assert report_mod.run_validity(broken)["valid"] is False


# --------------------------------------------------------------------------
# 11: Arm A / Arm B unchanged
# --------------------------------------------------------------------------


def _norm_sha(path):
    return hashlib.sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_arm_a_and_arm_b_orchestration_files_are_byte_identical_to_the_frozen_versions():
    assert _norm_sha("arms/single.py") == "128cb314d1c2098d607281810efe47cc37f559b7024b95a36e3c357be5f74c52"
    assert _norm_sha("arms/multi_nl.py") == "537bc435463f19e65f269a71510fcf916ecd54a65f1efb7769e63e6b6130864a"


def test_ab_configuration_and_arm_selection_are_unchanged():
    assert config.ARMS == ("A", "B")
    assert config.RunConfig().config_hash() == "9edbfb5d0d082d49a61969068fafd4ac"
    assert runner.SMOKE_ARMS["both"] == ("A", "B")
    assert runner.ARM_MODULES["A"] is single and runner.ARM_MODULES["B"] is multi_nl


def test_ab_mock_runs_keep_their_topology_and_metadata(mock_runs):
    for arm in ("A", "B"):
        run_dir, _ = mock_runs[arm]
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert not ({"topology", "arm_topology", "base_config_hash", "experiment_schema_version"} & set(meta))
        assert meta["config_hash"] == config.RunConfig(model="mock-sonnet", limits=config.SMOKE_LIMITS).config_hash()
    rb = report_mod.run_report(mock_runs["B"][0])
    t = rb["topology"]
    assert (t["cli_invocations"], t["fresh_sessions"], t["resumed_invocations"], t["physical_sessions"]) == (5, 3, 2, 3)
    assert "forwarded_investigation_report" in [h["label"] for h in rb["handoffs"]]
    assert "worker_transition" not in t


# --------------------------------------------------------------------------
# 12: reconstruction of the Worker transition (the prompt/argv gate for C1 is
# in tests/test_reconstruction.py, parametrized over A, B and C1)
# --------------------------------------------------------------------------


def test_worker_transition_record_is_reconstructible(c1):
    run_dir, _, _ = c1
    tr = [e["payload"] for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.WORKER_ROLE_TRANSITION]
    assert len(tr) == 1
    p = tr[0]
    s = _sessions(run_dir)
    inv_id = _init(run_dir, "02_investigator")["session_id"]
    assert p["physical_session_id"] == p["resume_target"] == inv_id
    assert (p["previous_logical_role"], p["new_logical_role"]) == ("investigator", "implementer")
    assert p["new_prompt"] == s["04_implementer"]["stdin_text"]
    assert p["new_tool_policy"]["tools"] == s["04_implementer"]["tools"]
    assert p["previous_tool_policy"]["disallowed_tools"] == s["02_investigator"]["disallowed_tools"]
    assert p["investigator_report_forwarded_to_worker"] is False and p["task_statement_resent"] is False
    topo = json.loads((run_dir / "topology.json").read_text(encoding="utf-8"))
    assert topo["worker_transition"]["session_id_preserved"] is True
    assert [x["step"] for x in topo["invocations"]] == [
        "coordinator_kickoff", "investigate", "coordinator_plan", "implement_resume", "coordinator_wrapup"]


# --------------------------------------------------------------------------
# 13: config identity
# --------------------------------------------------------------------------


def test_c1_metadata_has_its_own_config_identity(c1):
    run_dir, _, r = c1
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    cfg = config.RunConfig(model="mock-sonnet", limits=config.SMOKE_LIMITS)
    assert meta["arm"] == "C1" and meta["arm_topology"] == "C1_shared_worker_context"
    assert meta["topology_version"] == 1 and meta["stage"] == 1 and meta["experiment_schema_version"] == 2
    assert meta["base_config_hash"] == cfg.config_hash()
    assert meta["config_hash"] == config.topology_config_hash(cfg, config.C1_TOPOLOGY) != meta["base_config_hash"]
    assert meta["topology"]["investigator_report_forwarded_to_worker"] is False
    assert config.topology_config_hash(config.RunConfig(), config.C1_TOPOLOGY) == "5ca22e4846c07ca973ee4e891df059c1"
    assert r["base_config_hash"] == cfg.config_hash()


# --------------------------------------------------------------------------
# 14-16: exposure and quota safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", STAGE1_TASKS)
def test_analysis_only_fields_never_reach_a_c1_prompt(task_id):
    task = registry.get_task(task_id)
    prompts = [multi_nl.coordinator_kickoff_prompt(task), multi_nl.investigator_prompt(task, "instruction"),
               multi_nl.coordinator_after_investigation_prompt("report"),
               c1_shared_worker.implementer_resume_prompt("instruction")]
    for p in prompts:
        for rel in task.analysis_only_paths:
            assert rel not in p
        for word in ("symptom_paths", "supporting_paths", "expected_edit", "ANALYSIS ONLY"):
            assert word not in p
        assert task.notes not in p
    assert task.statement not in prompts[-1]


def test_held_out_protections_remain_active(c1):
    run_dir, summary, r = c1
    task = registry.get_task("palindrome_punctuation")
    prep = next(e for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.WORKSPACE_PREPARED)
    assert prep["payload"]["verifier_withheld"] == task.verifier_dest_name
    assert task.verifier_dest_name not in prep["payload"]["files"]
    assert r["isolation_check"]["available"] is True and r["isolation_check"]["breach_suspected"] is False
    assert r["held_out_content_check"]["available"] is True
    assert r["held_out_content_check"]["breach_suspected"] is False
    assert summary["solved_basis"] == "held-out verifier exit code only"
    assert summary["verification"]["verifier_paths_injected"] == [task.verifier_dest_name]


def test_no_real_claude_was_used(c1):
    run_dir, _, _ = c1
    for inv in _sessions(run_dir).values():
        assert inv["cli_path"].replace("\\", "/").endswith("tools/mock_claude.py")
        assert Path(inv["argv"][0]).name.lower().startswith("python")


# --------------------------------------------------------------------------
# Analysis on the C1 topology
# --------------------------------------------------------------------------


def test_decomposition_chains_the_worker_resume_by_physical_session(c1):
    run_dir, summary, r = c1
    per = {s["session_key"]: s for s in r["overhead_decomposition"]["per_session"]}
    assert per["04_implementer"]["resumed"] is True
    h = {x["label"]: x for x in summary["handoffs"]}
    instr = (run_dir / h["implementation_instruction"]["path"]).read_text(encoding="utf-8")
    # the resumed Worker carries its Investigator-phase history (instruction + own report)
    assert per["04_implementer"]["handoff_text_estimated_tokens_in_context"] > decomposition.est_tokens(instr)
    assert per["04_implementer"]["task_statement_occurrences"] == 1   # carried, not resent


def test_stage1_comparison_renders_every_endpoint(mock_runs, c1):
    _, _, rc = c1
    rb = report_mod.run_report(mock_runs["B"][0])
    text = report_mod.render_stage1_comparison(rb, rc)
    for s in ("[PASS] same base config", "[PASS] same base commit", "C1 worker transition verified: True",
              "E1 correctness", "E2 input", "E3 fresh context", "E4 cache write", "E5 handoff",
              "E6 rereads", "E7 wall / cost", "cache reads are billed input"):
        assert s in text, s
    rows = report_mod.stage1_rows([rb, rc], tasks=("palindrome_punctuation",))
    row = rows["rows"][0]
    assert row["complete"] and row["b"]["fresh_sessions"] == 3 and row["c1"]["fresh_sessions"] == 2
    assert row["b"]["forwarded_report_chars"] > 0 and row["c1"]["forwarded_report_chars"] == 0


def test_stage1_summary_waits_for_c1_runs():
    text = report_mod.render_stage1_summary([])
    for t in STAGE1_TASKS:
        assert f"{t}" in text
    assert "pending" in text


def test_task3_b_run_is_parity_compatible_only_through_the_amendment7_note():
    b = {"task_id": "shipping_inch_dimensions", "config_hash": "22ce9b7e5249dd497ee7c4c0318216b4",
         "session_terminations": ["completed"] * 5, "base_commit": "x", "session_cli_versions": ["2.1.260"],
         "resolved_models": ["m"], "workspace_integrity": {"read_only_files": []}}
    c = dict(b, config_hash="5ca22e4846c07ca973ee4e891df059c1",
             base_config_hash="9edbfb5d0d082d49a61969068fafd4ac")
    failures, notes = report_mod.stage1_parity(b, c)
    assert failures == [] and notes and "amendment 7" in notes[0]
    b_capped = dict(b, session_terminations=["turn_limit_exceeded"])
    assert "same base config" in report_mod.stage1_parity(b_capped, c)[0]


_PAIRS = {
    "palindrome_punctuation": ("20260910T125941Z_palindrome_punctuation_A_r1", "20260910T230448Z_palindrome_punctuation_B_r1"),
    "cart_invoice_rounding": ("20260911T004656Z_cart_invoice_rounding_A_r1", "20260911T004825Z_cart_invoice_rounding_B_r1"),
    "shipping_inch_dimensions": ("20260911T014034Z_shipping_inch_dimensions_A_r1", "20260911T014126Z_shipping_inch_dimensions_B_r1"),
    "settings_list_fields": ("20260911T015555Z_settings_list_fields_A_r2", "20260911T015737Z_settings_list_fields_B_r2"),
    "rename_max_connections": ("20260911T020645Z_rename_max_connections_A_r1", "20260911T020834Z_rename_max_connections_B_r1"),
    "sla_weekend_hours": ("20260911T021224Z_sla_weekend_hours_A_r1", "20260911T021334Z_sla_weekend_hours_B_r1"),
}


@pytest.mark.skipif(not all((ROOT / "runs" / r / "metadata.json").is_file() for p in _PAIRS.values() for r in p),
                    reason="Stage-0.5 runs not present locally")
def test_physical_session_chaining_leaves_every_stage05_decomposition_unchanged():
    frozen = {}
    for line in (ROOT / "results" / "stage05_pilot" / "pilot_tables.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        frozen[row["task"]] = row
    for task, (ra, rb) in _PAIRS.items():
        a = report_mod.run_report(ROOT / "runs" / ra)
        b = report_mod.run_report(ROOT / "runs" / rb)
        p = decomposition.pair_decomposition(a["overhead_decomposition"], b["overhead_decomposition"])
        assert {x["category"]: x["delta"] for x in p["rows"]} == frozen[task]["decomposition"], task
        assert p["unattributed_remainder_delta"] == frozen[task]["remainder"], task
