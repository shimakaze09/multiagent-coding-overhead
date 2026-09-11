"""Amendment 11: content-match provenance, and the infrastructure-unresolved
exclusion (PREREGISTRATION section 19.21).

Local only; no Claude session. The four detector cases the amendment requires
are in `test_true_*`, `test_self_authored_*`, `test_ambiguous_*` and
`test_protected_read_then_copied_*`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from analysis import difficulty, exposure_provenance as ep, isolation, leakage, quality_cost
from harness import telemetry
from tasks import registry

ROOT = Path(__file__).resolve().parent.parent
TASK_ID = "s2t11_docpipe"
S2T11_INVALID_RUN = "20260911T080058Z_s2t11_docpipe_S2_SS_r3"


# --------------------------------------------------------------------------
# synthetic runs
# --------------------------------------------------------------------------


def _raw(ws_path, calls, *, rule=True, unparsable=0):
    """calls: (tool, input, result_text). `rule=False` is a run that predates
    the amendment (no rule record in its metadata)."""
    lines = [json.dumps({"type": "system", "subtype": "init", "session_id": "s",
                         "apiKeySource": "none"})]
    for i, (name, inp, result) in enumerate(calls):
        lines.append(json.dumps({"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": result}]}}))
    lines += ["{ not json" for _ in range(unparsable)]
    meta = {"workspace": {"path": str(ws_path)}, "task_id": TASK_ID}
    if rule:
        meta[ep.METADATA_KEY] = ep.rule_record()
    parsed = telemetry.parse_stream(lines)
    return {"metadata": meta,
            "sessions": [SimpleNamespace(session_key="01_solo", session_index=1, parsed=parsed)]}


@pytest.fixture(scope="module")
def protected():
    task = registry.get_task(TASK_ID)
    repo = {p.relative_to(task.source_tree).as_posix(): p.read_text(encoding="utf-8")
            for p in task.source_tree.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    lines = leakage.protected_lines(task.verifier_path.read_text(encoding="utf-8"),
                                    repo, task.statement)
    assert len(lines) >= 10
    return lines, task, repo


@pytest.fixture
def secret_text(protected):
    """Three protected lines, as an agent would carry them in a file."""
    lines, _task, _repo = protected
    return "\n".join(sorted(lines)[:4]) + "\n"


def _classify(raw, protected_lines):
    iso = isolation.check_run(raw)
    content = leakage.check_run(raw, TASK_ID, {"x.py": "# nothing protected here\n"})
    # the repo texts above are a stand-in; use the real protected set
    content = {"version": 1, "available": True, "exposures": leakage.check_texts(
        [(sa.session_key, c.tool_use_id, c.result_text)
         for sa in raw["sessions"] for c in sa.parsed.tool_calls], protected_lines),
        "breach_suspected": None}
    content["breach_suspected"] = bool(content["exposures"])
    return iso, content, ep.classify_run(raw, iso, content, protected_lines)


# --------------------------------------------------------------------------
# the four required detector cases
# --------------------------------------------------------------------------


def test_true_protected_content_match_is_a_hard_stop(tmp_path, protected, secret_text):
    lines, _t, _r = protected
    ws = tmp_path / "runs" / "X_r1" / "workspace"
    raw = _raw(ws, [("Bash", {"command": "python -c 'print(held_out_body)'"}, secret_text)])
    iso, content, prov = _classify(raw, lines)
    assert content["breach_suspected"] is True
    assert prov["applies"] is True and prov["available"] is True
    assert [m["verdict"] for m in prov["matches"]] == [ep.VERDICT_UNEXPLAINED]
    assert prov["self_authored_false_positive"] is False
    assert prov["held_out_exposure"] is True and prov["hard_stop"] is True
    assert ep.clears_content_match({"exposure_provenance": prov}) is False


def test_self_authored_in_workspace_match_is_not_exposure(tmp_path, protected, secret_text):
    lines, _t, _r = protected
    ws = tmp_path / "runs" / "X_r1" / "workspace"
    scratch = str(ws / "_verify.py")
    raw = _raw(ws, [
        ("Write", {"file_path": scratch, "content": secret_text}, "File created"),
        ("Bash", {"command": "python _verify.py"}, "AssertionError"),
        ("Read", {"file_path": scratch}, secret_text),
    ])
    iso, content, prov = _classify(raw, lines)
    assert content["breach_suspected"] is True          # the frozen check still fires
    assert iso["breach_suspected"] is False
    (match,) = prov["matches"]
    assert match["verdict"] == ep.VERDICT_SELF_AUTHORED
    assert match["matched_lines"] == match["self_authored_lines"] >= 3
    assert match["reasons"] == [] and match["authored_by_call_indexes"] == [0]
    assert prov["self_authored_false_positive"] is True
    assert prov["held_out_exposure"] is False and prov["hard_stop"] is False
    assert ep.clears_content_match({"exposure_provenance": prov}) is True


def test_ambiguous_provenance_is_a_hard_stop(tmp_path, protected, secret_text):
    """Two shapes: a file written by a Bash command whose target cannot be
    identified, and a write to a path outside the workspace."""
    lines, _t, _r = protected
    ws = tmp_path / "runs" / "X_r1" / "workspace"
    heredoc = _raw(ws, [
        ("Bash", {"command": f"cat > _v.py <<'EOF'\n{secret_text}EOF"}, ""),
        ("Read", {"file_path": str(ws / "_v.py")}, secret_text),
    ])
    _iso, _c, prov = _classify(heredoc, lines)
    assert [m["verdict"] for m in prov["matches"]] == [ep.VERDICT_UNEXPLAINED]
    assert prov["hard_stop"] is True

    outside = _raw(ws, [
        ("Write", {"file_path": str(tmp_path / "elsewhere" / "_v.py"), "content": secret_text}, "ok"),
        ("Read", {"file_path": str(tmp_path / "elsewhere" / "_v.py")}, secret_text),
    ])
    iso, _c, prov = _classify(outside, lines)
    assert iso["breach_suspected"] is True             # and the run is invalid anyway
    assert [m["verdict"] for m in prov["matches"]] == [ep.VERDICT_UNEXPLAINED]
    assert "out-of-workspace access in the run" in prov["matches"][0]["reasons"]

    truncated = _raw(ws, [
        ("Write", {"file_path": str(ws / "_v.py"), "content": secret_text}, "ok"),
        ("Read", {"file_path": str(ws / "_v.py")}, secret_text),
    ], unparsable=2)
    _iso, _c, prov = _classify(truncated, lines)
    assert prov["hard_stop"] is True
    assert any("unparsable" in r for r in prov["matches"][0]["reasons"])


def test_protected_read_then_copied_content_is_a_hard_stop(tmp_path, protected, secret_text):
    """The text reached the agent first and was copied into the workspace
    afterwards: the write is not its origin, so the rule must not fire."""
    lines, _t, _r = protected
    ws = tmp_path / "runs" / "X_r1" / "workspace"
    raw = _raw(ws, [
        ("Bash", {"command": "python -c 'print(open(verifier).read())'"}, secret_text),
        ("Write", {"file_path": str(ws / "_v.py"), "content": secret_text}, "ok"),
        ("Read", {"file_path": str(ws / "_v.py")}, secret_text),
    ])
    _iso, content, prov = _classify(raw, lines)
    assert len(content["exposures"]) == 2              # the read and the copy back
    assert {m["verdict"] for m in prov["matches"]} == {ep.VERDICT_UNEXPLAINED}
    assert prov["self_authored_false_positive"] is False and prov["hard_stop"] is True


# --------------------------------------------------------------------------
# the rule is prospective, and detection is not weakened
# --------------------------------------------------------------------------


def test_rule_is_prospective_and_needs_the_metadata_record(tmp_path, protected, secret_text):
    lines, _t, _r = protected
    ws = tmp_path / "runs" / "X_r1" / "workspace"
    calls = [("Write", {"file_path": str(ws / "_v.py"), "content": secret_text}, "ok"),
             ("Read", {"file_path": str(ws / "_v.py")}, secret_text)]
    old = _raw(ws, calls, rule=False)
    _iso, _c, prov = _classify(old, lines)
    assert prov["applies"] is False
    assert prov["self_authored_false_positive"] is True     # classified, but not applied
    assert ep.clears_content_match({"exposure_provenance": prov}) is False
    assert ep.applies_to({}) is False and ep.applies_to(None) is False
    assert ep.applies_to({ep.METADATA_KEY: {"version": 999}}) is False
    assert ep.applies_to({ep.METADATA_KEY: ep.rule_record()}) is True


def test_frozen_detectors_are_unchanged():
    assert leakage.LEAKAGE_CONTENT_VERSION == 1 and leakage.MIN_PROTECTED_LINES == 3
    assert isolation.ISOLATION_CHECK_VERSION == 1
    assert isolation.EXPOSURE_MARKERS == ("tasks/holdout", "test_holdout_", "test_fixture_",
                                          "_reference_fix", "preregistration")
    assert ep.EXPOSURE_PROVENANCE_VERSION == 1


def test_protected_for_run_matches_the_frozen_check(protected):
    lines, task, repo = protected
    got_task, got = leakage.protected_for_run(TASK_ID, repo)
    assert got_task.task_id == task.task_id and got == lines
    assert leakage.protected_for_run(TASK_ID, {})[1] is None
    assert leakage.protected_for_run(None, repo)[1] is None


@pytest.mark.skipif(not (ROOT / "runs" / S2T11_INVALID_RUN).is_dir(),
                    reason="raw calibration run not present")
def test_s2t11_repeat_3_stays_invalid():
    """The attempt that motivated the amendment is not reclassified: its match
    is recognised as self-authored, and the run remains INVALID because it ran
    before the rule."""
    from analysis import report as report_mod

    r = report_mod.run_report(ROOT / "runs" / S2T11_INVALID_RUN)
    prov = r["exposure_provenance"]
    assert prov["applies"] is False
    assert prov["self_authored_false_positive"] is True
    assert r["held_out_content_check"]["breach_suspected"] is True
    assert r["validity"]["valid"] is False
    assert "held-out content exposure suspected" in r["validity"]["reasons"]


# --------------------------------------------------------------------------
# infrastructure-unresolved exclusion
# --------------------------------------------------------------------------


def test_exclusion_is_infrastructure_not_performance():
    assert config.STAGE2_INFRASTRUCTURE_UNRESOLVED == {
        "s2t03_ledgerly": "infrastructure incompatibility / repeated isolation invalidation"}
    reason = config.STAGE2_INFRASTRUCTURE_UNRESOLVED["s2t03_ledgerly"]
    assert "infrastructure" in reason and "difficult" not in reason.lower()
    assert difficulty.difficulty_pool() == tuple(
        t for t in config.STAGE2_CANDIDATES if t != "s2t03_ledgerly")
    assert difficulty.PROTOCOL["infrastructure_exclusion"]["basis"].startswith("infrastructure only")


def _entry(task, rid, repeat, solved, valid=True):
    return {"task_id": task, "arm": "S2_SS", "stage2_phase": "calibration", "run_id": rid,
            "repeat_id": repeat, "solved": solved, "valid": valid, "validity_reasons": []}


def test_excluded_task_has_no_stratum_and_keeps_descriptive_counts():
    entries = [_entry("s2t03_ledgerly", "r1v", 1, False),
               _entry("s2t03_ledgerly", "r1a", 1, False, valid=False),
               _entry("s2t03_ledgerly", "r3a", 3, False, valid=False),
               _entry("s2t03_ledgerly", "r1b", 4, False, valid=False)]
    s = difficulty.task_status("s2t03_ledgerly", entries)
    assert s["status"] == difficulty.INFRASTRUCTURE_UNRESOLVED_STATUS
    assert s["stratum"] is None
    assert s["included_in_difficulty_pool"] is False
    assert s["included_in_evaluation_benchmark"] is False
    assert s["exclusion_reason"] == config.STAGE2_INFRASTRUCTURE_UNRESOLVED["s2t03_ledgerly"]
    assert (s["valid_observations"], s["valid_successes"], s["invalid_attempts"]) == (1, 0, 3)
    assert s["next_repeat_ids"] == []          # no further replacement is granted


def _complete_entries():
    """Every pool task complete, the excluded one not."""
    entries = []
    for t in difficulty.difficulty_pool():
        solved = not t.endswith("flowq")
        for rep in (1, 2, 3) if solved else (1, 2, 3, 4, 5):
            entries.append(_entry(t, f"{t}_r{rep}", rep, solved))
    entries.append(_entry("s2t03_ledgerly", "s2t03_r1", 1, False))
    return entries


def test_excluded_task_cannot_enter_difficulty_aggregation():
    entries = _complete_entries()
    statuses = difficulty.calibration_summary(entries)
    dist = difficulty.distribution(statuses)
    assert dist["infrastructure_unresolved"] == 1
    assert sum(dist[s] for s in difficulty.STRATA) == len(difficulty.difficulty_pool())

    labels = difficulty.freeze_labels(entries)
    assert "s2t03_ledgerly" not in labels["tasks"]
    assert "s2t03_ledgerly" not in labels["difficulty_pool"]
    assert all("s2t03_ledgerly" not in ts for ts in labels["benchmark"].values())
    assert all("s2t03_ledgerly" not in ts for ts in labels["excluded_over_cap"].values())
    rec = labels["infrastructure_unresolved"]["s2t03_ledgerly"]
    assert rec["calibration_status"] == "INFRASTRUCTURE_UNRESOLVED"
    assert rec["included_in_difficulty_pool"] is False and rec["descriptive_records_only"] is True
    assert rec["exclusion_basis"] == "infrastructure"
    assert labels["distribution"]["infrastructure_unresolved"] == 1
    assert not any(r.startswith("s2t03") for r in labels["tasks"]
                   for r in labels["tasks"][r]["counted_run_ids"])

    # and no evaluation run can be planned for it
    plan = quality_cost.evaluation_plan([], labels)
    assert plan and all(p["task"] != "s2t03_ledgerly" for p in plan)

    with pytest.raises(ValueError, match="reached difficulty aggregation"):
        difficulty.assert_no_excluded_task(
            {**labels, "benchmark": {"easy": ["s2t03_ledgerly"]}})


def test_freeze_refuses_while_a_pool_task_is_incomplete():
    entries = [e for e in _complete_entries() if not e["task_id"].startswith("s2t05")]
    with pytest.raises(ValueError, match="calibration incomplete"):
        difficulty.freeze_labels(entries)


def test_amendment_11_is_recorded():
    text = (ROOT / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "### Amendment 11" in text
    assert "self_authored_false_positive" in text
    assert "INFRASTRUCTURE_UNRESOLVED" in text
