"""Stage 2A - heterogeneous model routing (PREREGISTRATION section 18).

Local/mocked validation only: tools/mock_claude.py stands in for Claude Code
(it resolves `sonnet` and the Haiku id the way 2.1.260 does, and reports
Claude Code's auxiliary Haiku call), so no real Claude session is opened and no
quota is used.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

import config
import runner
from analysis import ingest, report as report_mod, routing, stage2
from arms import c1_shared_worker, multi_nl, s2_routing, single
from harness import agent, claude_cli, events as ev, model_routing
from tasks import registry

ROOT = Path(__file__).resolve().parent.parent
MOCK_CLI = ROOT / "tools" / "mock_claude.py"
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"

EXPECTED_REQUESTS = {
    "S2_R1": {"coordinator": HAIKU, "investigator": "sonnet", "implementer": HAIKU},
    "S2_R2": {"coordinator": HAIKU, "investigator": HAIKU, "implementer": "sonnet"},
    "S2_R3": {"coordinator": HAIKU, "investigator": HAIKU, "implementer": HAIKU},
    "S2_S": {"solo": HAIKU},
    "S2_SS": {"solo": "sonnet"},
    "S2_M": {"coordinator": "sonnet", "investigator": "sonnet", "implementer": "sonnet"},
}
# topology_config_hash(RunConfig(), stage2_topology(arm)), frozen with section 18.
PINNED_S2_HASHES = {
    "S2_R1": "f1a4b7f32952ee7618e3062c0f0a5ebd",
    "S2_R2": "073ebc97390854d8331531a681a8374d",
    "S2_R3": "4b75b94f619fe83c93aa3582a3da0aa3",
    "S2_S": "d9e92534b466f8793b390b222c18fe76",
    "S2_SS": "d2cd1cf2024412d57d685340261f5fe5",
    "S2_M": "d5459739f84ac4c5202bb144b45507da",   # added by section 19; the five above are unchanged
}


def _sessions(run_dir):
    return {sd.name: json.loads((sd / "invocation.json").read_text(encoding="utf-8"))
            for sd in sorted((run_dir / "sessions").iterdir())}


def _init(run_dir, key):
    for line in (run_dir / "sessions" / key / "claude_stdout.jsonl").read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        if o.get("type") == "system" and o.get("subtype") == "init":
            return o
    raise AssertionError(f"no init event in {key}")


def _result(run_dir, key):
    return json.loads((run_dir / "sessions" / key / "result.json").read_text(encoding="utf-8"))


def _routing_file(run_dir):
    return json.loads((run_dir / "routing.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def s2(mock_s2_runs):
    return {arm: (mock_s2_runs[arm][0], mock_s2_runs[arm][1], report_mod.run_report(mock_s2_runs[arm][0]))
            for arm in config.STAGE2_ARMS}


@pytest.fixture(scope="module")
def s2_entries(mock_s2_runs):
    return {arm: stage2.load_entry(mock_s2_runs[arm][0]) for arm in config.STAGE2_ARMS}


@pytest.fixture(scope="module")
def mismatch_run(tmp_path_factory):
    """S2_R3 with the mock pretending the CLI served claude-sonnet-5 instead."""
    os.environ["STAGE0_CLAUDE_CLI"] = str(MOCK_CLI)
    os.environ["STAGE0_MOCK_FORCE_RESOLVED_MODEL"] = SONNET
    try:
        cli = config.find_claude_cli()
        caps = claude_cli.detect_capabilities(cli)
        runs_dir = tmp_path_factory.mktemp("runs_s2_mismatch")
        summary = s2_routing.ARMS["S2_R3"].run(
            task=registry.get_task("palindrome_punctuation"), repeat_id=1, cfg=config.RunConfig(),
            cli=cli, capability_report=caps.as_dict(), runs_dir=runs_dir)
        return runs_dir / summary["run_id"], summary
    finally:
        os.environ.pop("STAGE0_CLAUDE_CLI", None)
        os.environ.pop("STAGE0_MOCK_FORCE_RESOLVED_MODEL", None)


# --------------------------------------------------------------------------
# 1-3: role -> model mapping; requested and resolved model are recorded
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_every_configuration_maps_each_role_to_its_preregistered_model(s2, arm):
    assert config.stage2_topology(arm)["role_models_requested"] == EXPECTED_REQUESTS[arm]
    run_dir, _, _ = s2[arm]
    starts = {e["payload"]["session_key"]: e["payload"]
              for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.CLAUDE_SESSION_START}
    for key, inv in _sessions(run_dir).items():
        want = EXPECTED_REQUESTS[arm][inv["role"]]
        assert inv["model"] == want
        assert inv["argv"][inv["argv"].index("--model") + 1] == want
        assert starts[key]["model"] == want


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_requested_model_is_recorded_per_invocation(s2, arm):
    run_dir, summary, r = s2[arm]
    rec = _routing_file(run_dir)["invocations"]
    sessions = _sessions(run_dir)
    assert [x["session_key"] for x in rec] == list(sessions)
    for x in rec:
        assert x["requested_model"] == sessions[x["session_key"]]["model"]
        assert x["logical_role"] == sessions[x["session_key"]]["role"]
        assert x["physical_session_id"]
        assert x["reasoning_effort"] == {"requested": "not_passed", "policy": "cli_default_not_passed",
                                         "resolved": "not_reported_by_cli_telemetry"}
    events = [e["payload"] for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.MODEL_ROUTING]
    assert [e["requested_model"] for e in events] == [x["requested_model"] for x in rec]
    assert [x["requested_model"] for x in r["model_routing"]["invocations"]] == [x["requested_model"] for x in rec]


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_resolved_model_is_recorded_from_the_invocations_own_telemetry(s2, arm):
    run_dir, _, r = s2[arm]
    classes = config.STAGE2_CONFIGS[arm]["role_classes"]
    for x in _routing_file(run_dir)["invocations"]:
        init = _init(run_dir, x["session_key"])
        assert x["resolved_model"] == init["model"]
        assert x["resolved_model"] == (HAIKU if classes[x["logical_role"]] == "CHEAP" else SONNET)
        assert x["verification"]["verified"] is True and all(x["verification"]["checks"].values())
        assert model_routing.canonical_model(x["verification"]["assistant_message_models"][0]) == \
            config.MODEL_CLASSES[classes[x["logical_role"]]]["canonical"]
    mr = r["model_routing"]
    assert mr["verified"] and mr["complete"] and not mr["failed_checks"]
    assert [x["resolved_model"] for x in mr["invocations"]] == \
        [x["resolved_model"] for x in _routing_file(run_dir)["invocations"]]


# --------------------------------------------------------------------------
# 4: a resolved-model mismatch invalidates the run
# --------------------------------------------------------------------------


def test_a_resolved_model_mismatch_fails_the_invocation_check():
    chk = model_routing.check_invocation(
        requested=HAIKU, expected_requested=HAIKU, expected_resolved=HAIKU,
        init={"model": SONNET}, assistant_models=[SONNET],
        result={"usage": {"input_tokens": 1}, "modelUsage": {SONNET: {"inputTokens": 1, "costUSD": 0.0}}})
    assert chk["verified"] is False
    assert chk["checks"]["init_model_matches_assignment"] is False
    assert chk["checks"]["assistant_messages_match_assignment"] is False


def test_a_mismatch_record_makes_the_run_invalid(s2):
    _, _, r = s2["S2_R1"]
    assert report_mod.run_validity(r)["valid"] is True
    broken = copy.deepcopy(r)
    broken["model_routing"]["failed_checks"] = ["02_investigator: init_model_matches_assignment"]
    v = report_mod.run_validity(broken)
    assert v["valid"] is False and any("model routing not verified" in x for x in v["reasons"])
    assert report_mod.run_validity(dict(r, model_routing=None))["valid"] is False


def test_a_mismatch_stops_the_chain_before_the_next_session(mismatch_run):
    run_dir, summary = mismatch_run
    assert summary["session_count"] == 1
    assert list(_sessions(run_dir)) == ["01_coordinator"]
    stopped = _routing_file(run_dir)["stopped"]
    assert stopped["step"] == "coordinator_kickoff" and "does not match" in stopped["reason"]
    r = report_mod.run_report(run_dir)
    assert r["validity"]["valid"] is False
    assert any("model routing not verified" in x for x in r["validity"]["reasons"])
    assert stage2.load_entry(run_dir)["valid"] is False


# --------------------------------------------------------------------------
# 5-8: the configurations
# --------------------------------------------------------------------------


def _by_role(r):
    out = {}
    for x in r["model_routing"]["invocations"]:
        out.setdefault(x["logical_role"], []).append(x)
    return out


def test_strong_investigator_hybrid_is_cheap_coordinator_strong_investigator_cheap_implementer(s2):
    roles = _by_role(s2["S2_R1"][2])
    coord = roles["coordinator"]
    assert [x["requested_model"] for x in coord] == [HAIKU] * 3
    assert [x["resolved_model"] for x in coord] == [HAIKU] * 3
    assert [x["is_resume"] for x in coord] == [False, True, True]
    assert len({x["physical_session_id"] for x in coord}) == 1   # model never changes on --resume
    assert [(x["requested_model"], x["resolved_model"]) for x in roles["investigator"]] == [("sonnet", SONNET)]
    assert [(x["requested_model"], x["resolved_model"]) for x in roles["implementer"]] == [(HAIKU, HAIKU)]
    assert roles["investigator"][0]["physical_session_id"] != roles["implementer"][0]["physical_session_id"]


def test_strong_implementer_hybrid_is_the_inverse(s2):
    roles = _by_role(s2["S2_R2"][2])
    assert [x["resolved_model"] for x in roles["coordinator"]] == [HAIKU] * 3
    assert [(x["requested_model"], x["resolved_model"]) for x in roles["investigator"]] == [(HAIKU, HAIKU)]
    assert [(x["requested_model"], x["resolved_model"]) for x in roles["implementer"]] == [("sonnet", SONNET)]
    r1, r2 = config.STAGE2_CONFIGS["S2_R1"]["role_classes"], config.STAGE2_CONFIGS["S2_R2"]["role_classes"]
    assert (r1["investigator"], r1["implementer"]) == (r2["implementer"], r2["investigator"]) == ("STRONG", "CHEAP")


def test_all_cheap_never_requests_the_strong_model(s2, s2_entries):
    run_dir, _, r = s2["S2_R3"]
    for inv in _sessions(run_dir).values():
        assert inv["model"] == HAIKU
        assert "sonnet" not in inv["argv"]
    assert {x["resolved_model"] for x in r["model_routing"]["invocations"]} == {HAIKU}
    m = s2_entries["S2_R3"]["metrics"]
    assert m["strong"]["invocations"] == 0 and m["strong"]["cost_usd"] == 0
    assert m["cost_by_model"]["strong"]["total"] == 0


def test_single_cheap_is_one_cheap_model_session(s2):
    run_dir, summary, r = s2["S2_S"]
    task = registry.get_task("palindrome_punctuation")
    (key, inv), = _sessions(run_dir).items()
    assert key == "01_solo" and summary["session_count"] == 1 and summary["handoffs"] == []
    assert inv["model"] == HAIKU and _init(run_dir, key)["model"] == HAIKU
    assert inv["tools"] == list(config.ROLE_TOOLS["solo"])
    assert inv["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST) and inv["disallowed_tools"] == []
    assert inv["stdin_text"] == single.build_solo_prompt(task)
    assert r["topology"]["cli_invocations"] == 1


def test_single_strong_fresh_baseline_is_arm_a_with_the_historical_request(s2):
    run_dir, _, _ = s2["S2_SS"]
    (key, inv), = _sessions(run_dir).items()
    assert inv["model"] == "sonnet" and _init(run_dir, key)["model"] == SONNET
    assert inv["stdin_text"] == single.build_solo_prompt(registry.get_task("palindrome_punctuation"))
    assert inv["prompt_inputs"].keys() == {"task_statement_verbatim", "upstream_reports"}
    assert inv["append_system_prompt"] is None


@pytest.mark.parametrize("arm", ("S2_R1", "S2_R2", "S2_R3"))
def test_multi_configurations_use_arm_b_topology_and_handoffs(s2, arm):
    run_dir, summary, r = s2[arm]
    t = r["topology"]
    assert (t["cli_invocations"], t["fresh_sessions"], t["resumed_invocations"], t["physical_sessions"]) == (5, 3, 2, 3)
    assert [h["label"] for h in summary["handoffs"]] == [
        "investigation_instruction", "investigation_report", "implementation_instruction",
        "forwarded_investigation_report", "implementation_report", "final_result"]
    for key, inv in _sessions(run_dir).items():
        spec = agent.AgentSpec.for_role(inv["role"])
        assert inv["tools"] == list(spec.tools) and inv["disallowed_tools"] == list(spec.disallowed_tools)
        assert inv["allowed_tools"] == list(config.BASH_TEST_ALLOWLIST)
        assert inv["append_system_prompt"] == (agent.ROLE_SYSTEM_APPENDIX[inv["role"]] or None)
    assert "worker_transition" not in t


# --------------------------------------------------------------------------
# 9: A / B / C1 unchanged
# --------------------------------------------------------------------------


def _norm_sha(rel):
    return hashlib.sha256((ROOT / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_arm_a_b_c1_orchestration_sources_are_unchanged():
    assert _norm_sha("arms/single.py") == "128cb314d1c2098d607281810efe47cc37f559b7024b95a36e3c357be5f74c52"
    assert _norm_sha("arms/multi_nl.py") == "537bc435463f19e65f269a71510fcf916ecd54a65f1efb7769e63e6b6130864a"
    assert _norm_sha("arms/c1_shared_worker.py") == "0c7f9d5dbe0097fbbbb24731f48b62bc930c0bc7317f8ba70c3900d1767ab92d"
    assert config.STAGE2_PROMPT_SOURCES == {
        "arms/single.py": _norm_sha("arms/single.py"), "arms/multi_nl.py": _norm_sha("arms/multi_nl.py")}
    assert s2_routing.role_appendix_sha() == config.STAGE2_ROLE_APPENDIX_SHA256


def test_ab_c1_identity_and_arm_selection_are_unchanged():
    assert config.ARMS == ("A", "B")
    assert config.RunConfig().config_hash() == "9edbfb5d0d082d49a61969068fafd4ac"
    assert config.topology_config_hash(config.RunConfig(), config.C1_TOPOLOGY) == "5ca22e4846c07ca973ee4e891df059c1"
    assert runner.SMOKE_ARMS["both"] == ("A", "B") and runner.SMOKE_ARMS["C1"] == ("C1",)
    assert not set(runner.SMOKE_ARMS["both"]) & set(config.STAGE2_ARMS)
    assert runner.ARM_MODULES["A"] is single and runner.ARM_MODULES["B"] is multi_nl
    assert runner.ARM_MODULES["C1"] is c1_shared_worker
    assert all(runner.SMOKE_ARMS[a] == (a,) and runner.ARM_MODULES[a] is s2_routing.ARMS[a]
               for a in config.STAGE2_ARMS)


def test_run_agent_model_override_defaults_to_the_run_model():
    p = inspect.signature(agent.run_agent).parameters["model"]
    assert p.default is None and p.kind is inspect.Parameter.KEYWORD_ONLY


def test_ab_c1_mock_runs_keep_their_model_metadata_and_legacy_mock_output(all_mock_runs):
    for arm in ("A", "B", "C1"):
        run_dir, _ = all_mock_runs[arm]
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert not ({"stage2_identity", "model_routing_env_guard", "prompt_sources_checked"} & set(meta))
        for key, inv in _sessions(run_dir).items():
            assert inv["model"] == "mock-sonnet"
            assert inv["argv"][inv["argv"].index("--model") + 1] == "mock-sonnet"
            res = _result(run_dir, key)
            assert set(res["modelUsage"]) == {"mock-sonnet"} and set(res["modelUsage"]["mock-sonnet"]) == {"inputTokens"}
            assert res["total_cost_usd"] == 0.0123
        assert not (run_dir / "routing.json").exists()
        assert "model_routing" not in report_mod.run_report(run_dir)
    meta_c1 = json.loads((all_mock_runs["C1"][0] / "metadata.json").read_text(encoding="utf-8"))
    assert (meta_c1["stage"], meta_c1["experiment_schema_version"]) == (1, 2)


# --------------------------------------------------------------------------
# 10-11: held-out isolation; analysis-only fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_held_out_protections_remain_active(s2, arm):
    run_dir, summary, r = s2[arm]
    task = registry.get_task("palindrome_punctuation")
    prep = next(e for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.WORKSPACE_PREPARED)
    assert prep["payload"]["verifier_withheld"] == task.verifier_dest_name
    assert task.verifier_dest_name not in prep["payload"]["files"]
    assert r["isolation_check"]["available"] is True and r["isolation_check"]["breach_suspected"] is False
    assert r["held_out_content_check"]["breach_suspected"] is False
    assert summary["solved_basis"] == "held-out verifier exit code only"
    assert summary["verification"]["verifier_paths_injected"] == [task.verifier_dest_name]


@pytest.mark.parametrize("task_id", config.STAGE2_TASKS)
def test_analysis_only_fields_never_reach_a_stage2_prompt(task_id):
    task = registry.get_task(task_id)
    prompts = [single.build_solo_prompt(task), multi_nl.coordinator_kickoff_prompt(task),
               multi_nl.investigator_prompt(task, "instruction"),
               multi_nl.coordinator_after_investigation_prompt("report"),
               multi_nl.implementer_prompt(task, "instruction", "report"),
               multi_nl.coordinator_wrapup_prompt("report")]
    for p in prompts:
        for rel in task.analysis_only_paths:
            assert rel not in p
        for word in ("symptom_paths", "supporting_paths", "expected_edit", "ANALYSIS ONLY", "haiku", "sonnet"):
            assert word.lower() not in p.lower()
        assert task.notes not in p
    s2_routing.check_prompt_sources()   # the frozen prompts are the ones in use


# --------------------------------------------------------------------------
# 12-13: cost by role and model; auxiliary usage kept apart
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_cost_is_aggregated_by_role_and_by_model(s2_entries, arm):
    m = s2_entries[arm]["metrics"]
    approx = lambda v: pytest.approx(v, abs=1e-5)  # noqa: E731
    assert sum(v["cost"] for v in m["cost_by_role"].values()) == approx(m["cost_usd"])
    assert sum(v["total"] for v in m["cost_by_model"].values()) == approx(m["cost_usd"])
    assert m["role_assigned_cost_usd"] + m["auxiliary_cost_usd"] == approx(m["cost_usd"])
    assert m["cli_total_cost_usd"] == approx(m["cost_usd"])      # includes auxiliary usage
    assert m["cost_exact"] is True and m["cost_usd"] > 0
    classes = config.STAGE2_CONFIGS[arm]["role_classes"]
    strong_roles = [r for r, c in classes.items() if c == "STRONG"]
    assert m["cost_by_model"]["strong"]["role_assigned"] == approx(
        sum(m["cost_by_role"][r]["role_assigned_cost"] for r in strong_roles))
    assert m["strong"]["cost_share"] == pytest.approx(
        m["cost_by_model"]["strong"]["total"] / m["cost_usd"], abs=1e-3)
    assert m["strong"]["invocations"] == sum(m["cost_by_role"][r]["invocations"] for r in strong_roles)


def test_auxiliary_usage_is_distinguished_from_an_assigned_cheap_role(s2):
    run_dir, _, r = s2["S2_R3"]
    res = _result(run_dir, "01_coordinator")
    x = next(i for i in r["model_routing"]["invocations"] if i["session_key"] == "01_coordinator")
    a = x["accounting"]
    # the role model IS Haiku: Claude Code's auxiliary Haiku call lands in the same entry
    assert set(res["modelUsage"]) == {HAIKU}
    assert res["modelUsage"][HAIKU]["inputTokens"] == res["usage"]["input_tokens"] + 900
    assert a["role_model"] == "claude-haiku-4-5"
    assert a["role_usage"]["input"] == res["usage"]["input_tokens"]
    assert a["auxiliary"]["claude-haiku-4-5"]["input"] == 900 and a["auxiliary"]["claude-haiku-4-5"]["output"] == 15
    assert a["role_cost_usd"] + a["auxiliary_cost_usd"] == pytest.approx(res["total_cost_usd"], abs=1e-9)


def test_auxiliary_usage_is_distinguished_from_an_assigned_strong_role(s2, s2_entries):
    run_dir, _, r = s2["S2_R1"]
    x = next(i for i in r["model_routing"]["invocations"] if i["logical_role"] == "investigator")
    res = _result(run_dir, x["session_key"])
    assert set(res["modelUsage"]) == {SONNET, HAIKU}
    a = x["accounting"]
    assert a["role_model"] == SONNET and a["role_usage"]["input"] == res["usage"]["input_tokens"]
    assert set(a["auxiliary"]) == {"claude-haiku-4-5"}
    m = s2_entries["S2_R1"]["metrics"]
    assert m["strong"]["input"] == a["role_usage"]["input"]     # auxiliary never counted as strong
    assert m["cost_by_model"]["cheap"]["auxiliary"] > 0 and m["cost_by_model"]["strong"]["auxiliary"] == 0


def test_pricing_reproduces_the_cli_cost_of_every_stored_real_session():
    results = sorted((ROOT / "runs").glob("2026*/sessions/*/result.json"))
    if not results:
        pytest.skip("real runs not present locally")
    n = 0
    for f in results:
        res = json.loads(f.read_text(encoding="utf-8"))
        usage = model_routing.result_usage_classes(res["usage"])
        for name, entry in res["modelUsage"].items():
            m = model_routing.model_usage_classes(entry)
            same_as_main = all(m[k] == usage[k] for k in model_routing.TOKEN_CLASSES)
            priced = model_routing.api_equivalent_cost(
                model_routing.canonical_model(name), usage if same_as_main else m)
            assert priced == pytest.approx(entry["costUSD"], abs=1e-9), (f, name)
            n += 1
    assert n >= 90


# --------------------------------------------------------------------------
# 14: invalid runs never enter success counts or cost comparisons
# --------------------------------------------------------------------------


def _fake_entry(run_id, solved=True, valid=True, cost=1.0, exact=True):
    return {"run_id": run_id, "task_id": "t", "arm": "S2_R1", "solved": solved, "valid": valid,
            "metrics": {"cost_usd": cost, "cost_exact": exact, "wall_seconds": 10.0}}


def test_invalid_runs_never_enter_cost_comparisons():
    ss = _fake_entry("ss", cost=2.0)
    good = _fake_entry("r1", cost=1.0)
    bad = _fake_entry("r1_bad", cost=0.1, valid=False)
    ok = stage2.compare({"t": {"SINGLE_STRONG": {"entry": ss}, "S2_R1": {"entry": good}}}, "SINGLE_STRONG", "S2_R1")
    assert ok["pooled_cost_ratio"] == 0.5 and ok["jointly_solved_with_exact_cost"] == 1
    excl = stage2.compare({"t": {"SINGLE_STRONG": {"entry": ss}, "S2_R1": {"entry": bad}}}, "SINGLE_STRONG", "S2_R1")
    assert excl["tasks_compared"] == 0 and excl["pooled_cost_ratio"] is None and excl["y_solved"] == 0
    failed = stage2.compare({"t": {"SINGLE_STRONG": {"entry": ss},
                                   "S2_R1": {"entry": _fake_entry("r1f", solved=False, cost=0.1)}}},
                            "SINGLE_STRONG", "S2_R1")
    assert failed["tasks_compared"] == 1 and failed["jointly_solved_with_exact_cost"] == 0
    lower = stage2.compare({"t": {"SINGLE_STRONG": {"entry": ss},
                                  "S2_R1": {"entry": _fake_entry("r1l", exact=False)}}}, "SINGLE_STRONG", "S2_R1")
    assert lower["jointly_solved_with_exact_cost"] == 0


def test_the_first_valid_run_is_used_and_invalid_runs_are_skipped():
    entries = [dict(_fake_entry("20260101T000000Z_a", valid=False), task_id="t"),
               dict(_fake_entry("20260101T000100Z_b"), task_id="t"),
               dict(_fake_entry("20260101T000200Z_c"), task_id="t")]
    pick = stage2.select_config(entries, "t", "S2_R1")
    assert pick["entry"]["run_id"] == "20260101T000100Z_b"


def test_an_invalid_real_run_is_excluded_from_the_summary(mismatch_run):
    e = stage2.load_entry(mismatch_run[0])
    picks = {"t": {"SINGLE_STRONG": {"entry": _fake_entry("ss")}, "S2_R3": {"entry": e}}}
    assert stage2.compare(picks, "SINGLE_STRONG", "S2_R3")["tasks_compared"] == 0


# --------------------------------------------------------------------------
# 15: no real Claude
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_no_real_claude_was_used(s2, arm):
    run_dir, _, _ = s2[arm]
    for inv in _sessions(run_dir).values():
        assert inv["cli_path"].replace("\\", "/").endswith("tools/mock_claude.py")
        assert Path(inv["argv"][0]).name.lower().startswith("python")


def test_the_launch_tripwire_is_armed():
    with pytest.raises(RuntimeError, match="refused"):
        subprocess.Popen(["claude.exe", "--version"])


# --------------------------------------------------------------------------
# Reconstruction, identity, guards
# --------------------------------------------------------------------------


def _handoffs(run_dir):
    return {e["payload"]["label"]: (run_dir / e["payload"]["path"]).read_text(encoding="utf-8")
            for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.HANDOFF_SENT}


def _rebuild_prompt(run_dir, arm, step, task):
    h = _handoffs(run_dir)
    if config.STAGE2_CONFIGS[arm]["topology"] == "single_agent_arm_a":
        return single.build_solo_prompt(task)
    return {
        "coordinator_kickoff": lambda: multi_nl.coordinator_kickoff_prompt(task),
        "investigate": lambda: multi_nl.investigator_prompt(task, h["investigation_instruction"]),
        "coordinator_plan": lambda: multi_nl.coordinator_after_investigation_prompt(h["investigation_report"]),
        "implement": lambda: multi_nl.implementer_prompt(
            task, h["implementation_instruction"], h["forwarded_investigation_report"]),
        "coordinator_wrapup": lambda: multi_nl.coordinator_wrapup_prompt(h["implementation_report"]),
    }[step]()


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_every_stage2_invocation_is_reconstructible_from_the_logs(s2, arm):
    run_dir, _, _ = s2[arm]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    task = registry.get_task(meta["task_id"])
    starts = [e for e in ev.iter_events(run_dir / "events.jsonl") if e["type"] == ev.CLAUDE_SESSION_START]
    assert len(starts) == len(_sessions(run_dir))
    for start in starts:
        p = start["payload"]
        stored = json.loads((run_dir / "sessions" / p["session_key"] / "invocation.json").read_text(encoding="utf-8"))
        prompt = _rebuild_prompt(run_dir, arm, stored["prompt_inputs"].get("step", "solo"), task)
        rebuilt = claude_cli.build_invocation(
            cli_path=stored["cli_path"], session_key=p["session_key"], agent_id=start["agent_id"],
            role=p["role"], prompt=prompt, cwd=p["cwd"], model=p["model"], tools=p["tools"],
            allowed_tools=p["allowed_tools"], disallowed_tools=p["disallowed_tools"],
            permission_mode=p["permission_mode"], permission_prompts=p["permission_prompts"],
            max_turns=p["max_turns"], max_wall_seconds=p["max_wall_seconds"],
            session_id=p["assigned_session_id"], resume_session_id=p["resumed_session_id"],
            append_system_prompt=stored["append_system_prompt"],
            include_hook_events=meta["config"]["include_hook_events"],
            prompt_inputs=stored["prompt_inputs"], base_env={"PATH": "x"})
        assert not claude_cli.compare_reconstruction(
            claude_cli.reconstruct_from_log(stored), claude_cli.reconstruct_from_log(rebuilt.as_dict()))


def test_stage2_config_identities_are_new_distinct_and_pinned():
    hashes = {a: config.topology_config_hash(config.RunConfig(), config.stage2_topology(a))
              for a in config.STAGE2_ARMS}
    assert hashes == PINNED_S2_HASHES
    assert len(set(hashes.values())) == len(hashes)
    assert not set(hashes.values()) & {"9edbfb5d0d082d49a61969068fafd4ac", "22ce9b7e5249dd497ee7c4c0318216b4",
                                       "5ca22e4846c07ca973ee4e891df059c1"}


@pytest.mark.parametrize("arm", config.STAGE2_ARMS)
def test_stage2_metadata_carries_the_full_identity(s2, arm):
    run_dir, _, r = s2[arm]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    topo = meta["topology"]
    assert (meta["stage"], meta["experiment_schema_version"], meta["topology_version"]) == (2, 3, 1)
    assert meta["config_hash"] == PINNED_S2_HASHES[arm]
    assert meta["base_config_hash"] == "9edbfb5d0d082d49a61969068fafd4ac"
    assert topo["role_models_requested"] == EXPECTED_REQUESTS[arm]
    assert set(topo["role_models_expected_resolved"].values()) <= {HAIKU, SONNET}
    assert set(topo["role_reasoning_effort"].values()) == {"cli_default_not_passed"}
    assert topo["prompt_version"]["model_specific_prompt_changes"] is False
    assert topo["permission_policy_id"] == "narrow_pytest_v1"
    ident = meta["stage2_identity"]
    assert ident["config_hash"] == meta["config_hash"]
    assert ident["task_base_commit"] == meta["workspace"]["base_commit"]
    assert len(ident["held_out_verifier_sha256"]) == 64 and len(ident["identity_hash"]) == 32
    assert ident["classifier_versions"]["bash_classifier"] == 2
    assert ident["classifier_versions"]["model_routing_check"] == model_routing.MODEL_ROUTING_CHECK_VERSION
    assert meta["model_routing_env_guard"]["present_reaching_child"] == []
    assert r["stage2_identity"] == ident


def test_effort_is_never_passed_to_any_role(s2):
    for arm in config.STAGE2_ARMS:
        for inv in _sessions(s2[arm][0]).values():
            assert "--effort" not in inv["argv"]
            assert not any("effort" in k.lower() for k in inv["env_manifest"].get("set_by_harness", {}))
    assert config.STAGE2_EFFORT_POLICY["effort_flag_passed"] is False


def test_model_routing_env_guard_tests_presence_of_variables_that_reach_the_child():
    assert config.model_routing_env_present({}) == ()
    assert config.model_routing_env_present({"CLAUDE_CODE_EFFORT_LEVEL": "max"}) == ()   # stripped
    assert config.model_routing_env_present({"ANTHROPIC_DEFAULT_HAIKU_MODEL": "x"}) == ("ANTHROPIC_DEFAULT_HAIKU_MODEL",)
    with pytest.raises(config.ModelRoutingEnvError) as exc:
        config.preflight_model_routing_env({"MAX_THINKING_TOKENS": "secret-value-123"})
    assert "secret-value-123" not in str(exc.value)


def test_a_stage2_run_refuses_to_start_with_a_routing_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "x")
    cli = config.ClaudeCli(path=str(MOCK_CLI), version="", discovered_via="test")
    with pytest.raises(config.ModelRoutingEnvError):
        s2_routing.run(arm="S2_R1", task=registry.get_task("palindrome_punctuation"), repeat_id=1,
                       cfg=config.RunConfig(), cli=cli, capability_report={}, runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_stage2_refuses_a_different_base_model_and_a_prompt_drift(tmp_path, monkeypatch):
    cli = config.ClaudeCli(path=str(MOCK_CLI), version="", discovered_via="test")
    with pytest.raises(ValueError):
        s2_routing.run(arm="S2_R1", task=registry.get_task("palindrome_punctuation"), repeat_id=1,
                       cfg=config.RunConfig(model="opus"), cli=cli, capability_report={}, runs_dir=tmp_path)
    with pytest.raises(SystemExit, match="not allowed"):
        runner.main(["smoke", "--task", "sla_weekend_hours", "--arm", "S2_R1", "--model", "opus"])
    monkeypatch.setitem(config.STAGE2_PROMPT_SOURCES, "arms/single.py", "0" * 64)
    with pytest.raises(s2_routing.Stage2PromptDrift):
        s2_routing.check_prompt_sources()
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# Baseline reuse, E6/E7 rules, interpretation cases, reports
# --------------------------------------------------------------------------


_HISTORICAL = {
    "settings_list_fields": "20260911T015555Z_settings_list_fields_A_r2",
    "rename_max_connections": "20260911T020645Z_rename_max_connections_A_r1",
    "sla_weekend_hours": "20260911T021224Z_sla_weekend_hours_A_r1",
}


@pytest.mark.skipif(not (ROOT / "runs" / "20260911T014034Z_shipping_inch_dimensions_A_r1").is_dir(),
                    reason="Stage-0.5 runs not present locally")
def test_preregistered_single_strong_reuse_decision():
    entries = stage2.load_entries(ingest.discover_runs(ROOT / "runs"))
    for task, run_id in _HISTORICAL.items():
        pick = stage2.select_single_strong(entries, task)
        assert pick["entry"]["run_id"] == run_id and pick["source"] == "historical Arm A", task
    ship = stage2.select_single_strong(entries, "shipping_inch_dimensions")
    assert ship["entry"] is None and "fresh Single-Strong" in ship["source"]
    (c,) = ship["considered"]
    failed = [n for n, ok in c["parity"]["checks"].items() if not ok]
    assert failed == ["base config identical (model request, tools, permission policy, limits, turn counting)"]


def _fe(arm="S2_R1", solved=False, changed=(), visible=None, report="", instruction="", terms=None):
    roles = (("solo",) if arm in ("A", "S2_S", "S2_SS")
             else ("coordinator", "investigator", "coordinator", "implementer", "coordinator"))
    terms = terms or ("completed",) * len(roles)
    rows = [{"session_key": f"{i + 1:02d}_{r}", "logical_role": r, "termination_reason": t, "is_error": False}
            for i, (r, t) in enumerate(zip(roles, terms))]
    texts = {} if len(roles) == 1 else {"investigation_report": report, "implementation_instruction": instruction}
    return {"arm": arm, "task_id": "shipping_inch_dimensions", "solved": solved, "valid": True, "run_id": arm,
            "workspace_integrity": {"changed_paths": [{"path": p} for p in changed]},
            "visible_tests": {"passed": visible} if visible is not None else None,
            "handoff_texts": texts, "model_routing": {"invocations": rows},
            "last_agent_test_run": {"ran": False, "outcome": None, "role": None}}


def test_failure_location_rules_are_mechanical():
    exp = registry.get_task("shipping_inch_dimensions").expected_edit_paths[0]
    loc = lambda **k: stage2.failure_location(_fe(**k))  # noqa: E731
    assert loc(solved=True) is None
    r = loc(terms=("completed", "completed", "completed", "turn_limit_exceeded", "completed"))
    assert (r["location"], r["role"]) == ("implementer_coding", "implementer") and r["rule"].startswith("R1")
    assert loc(report="nothing relevant")["location"] == "investigator_diagnosis"
    assert loc(report=f"fix {exp}", instruction="do it")["location"] == "coordinator_routing"
    assert loc(report=f"fix {exp}", instruction=f"edit {exp}")["rule"].startswith("R4")
    assert loc(report=exp, instruction=exp, changed=(exp,), visible=False)["rule"].startswith("R5")
    v = loc(report=exp, instruction=exp, changed=(exp,), visible=True)
    assert v["location"] == "verification" and v["rule"].startswith("R6")
    s = loc(arm="S2_S")
    assert (s["location"], s["role"]) == ("implementer_coding", "solo")


def test_escalation_evidence_uses_the_strong_in_role_counterpart():
    e = _fe(arm="S2_R3", report="nothing")
    f = stage2.failure_location(e)
    solved = {"S2_R1": {"entry": {"valid": True, "solved": True, "run_id": "r1"}}}
    failed = {"S2_R1": {"entry": {"valid": True, "solved": False, "run_id": "r1"}}}
    assert stage2.escalation_evidence("S2_R3", e, f, solved)["verdict"].startswith("potentially_recoverable")
    assert stage2.escalation_evidence("S2_R3", e, f, failed)["verdict"].startswith("not_indicated")
    assert stage2.escalation_evidence("S2_R3", e, f, {})["verdict"].startswith("undetermined")
    strong = _fe(arm="S2_R1", report="nothing")
    assert stage2.escalation_evidence("S2_R1", strong, stage2.failure_location(strong), {})["verdict"].startswith(
        "not_applicable")
    single_cheap = _fe(arm="S2_S")
    ev_s = stage2.escalation_evidence("S2_S", single_cheap, stage2.failure_location(single_cheap),
                                      {"SINGLE_STRONG": {"entry": {"valid": True, "solved": True, "run_id": "a"}}})
    assert ev_s["failure_role_model_class"] == "CHEAP" and ev_s["verdict"].startswith("potentially_recoverable")


def _ce(solved, cost):
    return {"entry": {"valid": True, "solved": solved, "run_id": "x",
                      "metrics": {"cost_usd": cost, "cost_exact": True, "wall_seconds": 1.0}}}


def test_interpretation_cases_are_evaluated_mechanically():
    picks = {
        "t1": {"SINGLE_STRONG": _ce(True, 1.0), "S2_S": _ce(True, 0.2), "S2_R3": _ce(True, 0.3),
               "S2_R1": _ce(True, 0.5), "S2_R2": _ce(True, 0.9)},
        "t2": {"SINGLE_STRONG": _ce(True, 1.0), "S2_S": _ce(False, 0.2), "S2_R3": _ce(True, 0.3),
               "S2_R1": _ce(True, 0.6), "S2_R2": _ce(False, 0.9)},
    }
    c = stage2.interpretation_cases(picks)
    assert c["complete"]
    assert c["cases"]["A"]["holds"] is True          # same success, pooled 0.55
    assert c["cases"]["B"]["holds"] is False         # Single Cheap fails t2
    assert c["cases"]["C"]["holds"] is False
    assert c["cases"]["D"]["holds"] is False
    assert c["cases"]["E"]["holds"] is True          # All-Cheap Multi 2 > Single Cheap 1
    picks["t2"]["S2_R1"] = _ce(False, 0.6)
    c = stage2.interpretation_cases(picks)
    assert c["cases"]["C"]["holds"] is True and c["cases"]["A"]["holds"] is False
    picks["t2"]["S2_R1"] = {"entry": dict(picks["t2"]["S2_R1"]["entry"], valid=False)}
    assert stage2.interpretation_cases(picks)["complete"] is False


def test_stage2_task_report_renders_every_configuration_and_endpoint(s2_entries):
    text = stage2.render_task_report(list(s2_entries.values()), "palindrome_punctuation")
    for s in ("Single Strong", "Single Cheap", "All-Cheap Multi", "Strong-Investigator Hybrid",
              "Strong-Implementer Hybrid", "Arm B, reference only", "E2 cost by role", "E2 cost by model",
              "E3 strong model", "E4 tokens", "E5 wall", f"requested {HAIKU} -> resolved {HAIKU}",
              "requested sonnet -> resolved claude-sonnet-5"):
        assert s in text, s


def test_stage2_commands_run_without_claude(tmp_path, capsys):
    assert runner.main(["stage2-summary", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Stage 2A summary" in out and "pending" in out
    for name in ("A:", "B:", "C:", "D:"):
        assert name in out
    assert runner.main(["stage2-report", "--task", "sla_weekend_hours", "--runs-dir", str(tmp_path)]) == 0
    assert "Strong-Investigator Hybrid" in capsys.readouterr().out


def test_preregistration_freezes_stage2_before_any_run():
    text = (ROOT / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "## 18. Stage 2A: heterogeneous model routing" in text
    for s in (HAIKU, "`sonnet`", SONNET, "cli_default_not_passed", "S2_R1", "S2_R2", "S2_R3", "S2_S",
              "S2_SS", "17 runs", "No model-specific prompt tuning", *PINNED_S2_HASHES.values()):
        assert s in text, s
