"""Stage-0.5 metrics (PREREGISTRATION section 15), all prospective:
handoff_repository_overlap_v2, the overhead decomposition, unique downstream
acquisition, the held-out content check, run validity and the cross-task
summary. Historical runs are only read; nothing about them is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis import decomposition, ingest, leakage, overlap_v2
from analysis import report as report_mod
from harness import telemetry, tools
from tasks import registry

ROOT = Path(__file__).resolve().parent.parent
PAIR1 = ("20260910T125941Z_palindrome_punctuation_A_r1", "20260910T230448Z_palindrome_punctuation_B_r1")
PAIR2 = ("20260911T004656Z_cart_invoice_rounding_A_r1", "20260911T004825Z_cart_invoice_rounding_B_r1")
HISTORICAL = ("20260910T122445Z_palindrome_punctuation_A_r1",) + PAIR1 + PAIR2


def _present(*runs):
    return all((ROOT / "runs" / r / "metadata.json").is_file() for r in runs)


@pytest.fixture(scope="module")
def reports():
    out = {}
    for r in HISTORICAL:
        if _present(r):
            out[r] = report_mod.run_report(ROOT / "runs" / r)
    return out


# --------------------------------------------------------------------------
# handoff_repository_overlap_v2
# --------------------------------------------------------------------------

DISCOUNTS = (ROOT / "tasks" / "repos" / "shopcart" / "shopcart" / "discounts.py").read_text(encoding="utf-8")
REPO = {
    "shopcart/discounts.py": DISCOUNTS,
    "shopcart/other.py": "from decimal import Decimal, ROUND_HALF_UP\n\nfrom .money import Money\n",
}
EXCERPT = (
    "```python\n"
    "def unit_discount(unit_price, percent):\n"
    "    raw = Decimal(unit_price.amount_minor) * Decimal(percent) / Decimal(100)\n"
    "\n"
    "def net_unit_price(unit_price, percent):\n"
    "    return unit_price - unit_discount(unit_price, percent)\n"
    "```\n"
)


def test_v2_counts_an_excerpt_with_omitted_lines_that_v1_misses():
    idx = overlap_v2.repository_line_index(REPO)
    q = overlap_v2.quoted_lines(EXCERPT, idx)
    assert {f: len(v) for f, v in q.items()} == {"shopcart/discounts.py": 4}
    repo_set = set().union(*(tools.content_chunk_set(t) for t in REPO.values()))
    assert tools.covered_by(EXCERPT, repo_set)[0] == 0   # frozen v1 sees nothing


def test_v2_ignores_common_lines_and_single_coincidences():
    idx = overlap_v2.repository_line_index(REPO)
    assert "from decimal import Decimal, ROUND_HALF_UP" not in idx       # in two files
    one_line = "The code does `def unit_discount(unit_price, percent):` somewhere."
    assert overlap_v2.quoted_lines("def unit_discount(unit_price, percent):\nprose", idx) == {}
    assert overlap_v2.quoted_lines(one_line, idx) == {}


def test_v2_normalizes_read_prefixes_and_diff_markers():
    idx = overlap_v2.repository_line_index(REPO)
    text = ("    17\tdef unit_discount(unit_price, percent):\n"
            "-    raw = Decimal(unit_price.amount_minor) * Decimal(percent) / Decimal(100)\n")
    assert len(overlap_v2.quoted_lines(text, idx)["shopcart/discounts.py"]) == 2


@pytest.mark.skipif(not _present(*PAIR2), reason="Pair-2 runs not present locally")
def test_v2_on_pair2_is_exploratory_and_leaves_v1_untouched(reports):
    b = reports[PAIR2[1]]
    assert b["stage05_metrics_status"] == "post_hoc_exploratory"
    assert b["handoff_repository_overlap_v2"]["summary"]["report_repository_lines_v2"] == 11
    assert b["handoff_repository_overlap_v2"]["summary"]["report_repository_chars_v2"] == 500
    # the frozen v1 numbers recorded in PREREGISTRATION section 14 are unchanged
    assert b["communication"]["communication_summary"]["report_repository_chunks"] == 2
    assert b["communication"]["communication_summary"]["report_repository_quote_fraction"] == 0.0469


# --------------------------------------------------------------------------
# Overhead decomposition on the real historical runs (post-hoc / exploratory)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _present(*HISTORICAL), reason="historical runs not present locally")
def test_per_call_usage_reconciles_exactly_with_the_session_totals(reports):
    for run_id, r in reports.items():
        d = r["overhead_decomposition"]
        assert d["observable"]["reconciles_with_result_usage"] is True, run_id
        assert d["observable"]["total_input_tokens"] == r["token_telemetry"]["reported_total_input_tokens"]
        assert d["unattributed_remainder_tokens"] == (
            d["observable"]["total_input_tokens"] - d["attributed_context_weighted_tokens"])


@pytest.mark.skipif(not _present(*PAIR2), reason="Pair-2 runs not present locally")
def test_pair2_decomposition_uses_the_frozen_classifier_counts(reports):
    a, b = reports[PAIR2[0]], reports[PAIR2[1]]
    db = b["overhead_decomposition"]
    assert db["discretionary_reacquisition"]["count"] == 2
    assert db["discretionary_reacquisition"]["chars"] == 2996
    assert db["tool_required_reacquisition"]["count"] == 1
    assert db["tool_required_reacquisition"]["chars"] == 2340
    p = decomposition.pair_decomposition(a["overhead_decomposition"], db)
    assert p["observable_delta"] == 381293 - 129667
    fan = next(r for r in p["rows"] if r["key"] == "fanout")
    assert fan["delta"] > 0
    assert p["first_call_input_exact"] == {"arm_a": 17703, "arm_b": 76637}


@pytest.mark.skipif(not _present(*PAIR2), reason="Pair-2 runs not present locally")
def test_decomposition_section_has_every_required_line(reports):
    text = report_mod.render_decomposition(reports[PAIR2[0]], reports[PAIR2[1]])
    for label in ("Arm A total input:", "Arm B total input:", "B - A observable input delta:",
                  "session/context fanout:", "handoff transmission:", "discretionary reacquisition:",
                  "tool-required reacquisition:", "unique downstream acquisition:",
                  "unattributed / hidden remainder:", "POST-HOC / EXPLORATORY",
                  "NOT forced to sum"):
        assert label in text, label


@pytest.mark.skipif(not _present(*PAIR2), reason="Pair-2 runs not present locally")
def test_pair2_unique_downstream_is_only_self_generated_verification(reports):
    u = reports[PAIR2[1]]["unique_downstream_acquisition"]
    assert u["informational_unique_chars_estimate"] == 0
    assert [r["kind"] for r in u["acquisitions"]] == ["self_generated_after_own_edit"]
    assert u["material"]["merely_verified"]["determination"] == "yes"
    # the scratch file written and deleted by the Implementer is not an implementation change
    ci = u["material"]["changed_implementation"]
    assert ci["determination"] == "no"
    assert ci["evidence"]["edited_then_removed_or_reverted"] == ["_verify_manual.py"]


# --------------------------------------------------------------------------
# Unique downstream acquisition: synthetic, mechanical rules
# --------------------------------------------------------------------------

A_TEXT = "def a():\n    first = 1\n    second = 2\n    return first + second\n"
B_TEXT = "def b():\n    alpha = 10\n    beta = 20\n    return alpha * beta\n"
DIFF = "diff --git a/a.py b/a.py\n-    first = 1\n+    first = 100\n     second = 2\n"


def _session(key, index, role, calls):
    lines = [json.dumps({"type": "system", "subtype": "init", "session_id": key, "apiKeySource": "none"})]
    for i, (name, inp, result) in enumerate(calls):
        lines.append(json.dumps({"type": "assistant", "message": {"id": f"{key}m{i}", "content": [
            {"type": "tool_use", "id": f"{key}t{i}", "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"{key}t{i}", "content": result}]}}))
    return SimpleNamespace(session_key=key, session_index=index,
                           invocation={"role": role, "agent_id": role},
                           parsed=telemetry.parse_stream(lines))


def _raw(tmp_path, impl_calls, handoff_text="Fix a() in a.py.", changed=("a.py",)):
    (tmp_path / "handoffs").mkdir(exist_ok=True)
    (tmp_path / "handoffs" / "h.txt").write_text(handoff_text, encoding="utf-8")
    ws = "D:/x/workspace"
    inv = _session("02_investigator", 2, "investigator",
                   [("Read", {"file_path": f"{ws}/a.py"}, A_TEXT)])
    imp = _session("04_implementer", 4, "implementer",
                   [(n, {k: (v.replace("WS", ws) if isinstance(v, str) else v) for k, v in i.items()}, r)
                    for n, i, r in impl_calls])
    raw = {"run_dir": tmp_path, "sessions": [inv, imp],
           "summary": {"handoffs": [{"index": 1, "sender": "coordinator", "recipient": "implementer",
                                     "label": "implementation_instruction", "path": "handoffs/h.txt"}],
                       "workspace_integrity": {"changed_paths": [{"path": p, "change": "modified"}
                                                                 for p in changed]}}}
    acqs = {s.session_key: ingest.acquisitions_for_session(s) for s in raw["sessions"]}
    return raw, acqs


def test_rereading_known_content_is_not_unique(tmp_path):
    raw, acqs = _raw(tmp_path, [("Read", {"file_path": "WS/a.py"}, A_TEXT)])
    u = decomposition.unique_downstream(raw, acqs)
    assert u["applicable"] is True and u["acquisitions"] == []
    assert u["material"]["merely_verified"]["determination"] == "yes"


def test_a_file_the_investigator_never_read_is_a_new_constraint_candidate(tmp_path):
    raw, acqs = _raw(tmp_path, [
        ("Read", {"file_path": "WS/b.py"}, B_TEXT),
        ("Edit", {"file_path": "WS/a.py", "old_string": "1", "new_string": "100"}, "ok"),
        ("Bash", {"command": "git diff"}, DIFF),
    ])
    u = decomposition.unique_downstream(raw, acqs)
    kinds = {r["target"]: r["kind"] for r in u["acquisitions"]}
    assert kinds["b.py"] == "file_upstream_never_read"
    assert "self_generated_after_own_edit" in kinds.values()
    m = u["material"]
    assert m["added_new_constraint"]["determination"] == "candidate"
    assert m["added_new_constraint"]["evidence"]["files"] == ["b.py"]
    assert m["changed_implementation"]["determination"] == "no"
    assert m["merely_verified"]["determination"] == "no"
    assert m["changed_diagnosis"]["determination"] == "undetermined_without_judgement"
    assert m["corrected_earlier_finding"]["determination"] == "undetermined_without_judgement"


def test_changing_a_file_no_handoff_names_is_an_implementation_change(tmp_path):
    raw, acqs = _raw(tmp_path, [
        ("Edit", {"file_path": "WS/c.py", "old_string": "x", "new_string": "y"}, "ok"),
    ], changed=("c.py",))
    u = decomposition.unique_downstream(raw, acqs)
    assert u["material"]["changed_implementation"]["determination"] == "yes"
    assert u["material"]["changed_implementation"]["evidence"]["changed_files_not_named_in_any_handoff"] == ["c.py"]


def test_single_agent_runs_have_no_unique_downstream(tmp_path):
    raw = {"run_dir": tmp_path, "sessions": [_session("01_solo", 1, "solo", [])], "summary": {}}
    assert decomposition.unique_downstream(raw, {})["applicable"] is False


# --------------------------------------------------------------------------
# Held-out content check and validity
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _present(*HISTORICAL), reason="historical runs not present locally")
def test_no_historical_run_received_held_out_content(reports):
    for run_id, r in reports.items():
        c = r["held_out_content_check"]
        assert c["available"] is True and c["breach_suspected"] is False, run_id


@pytest.mark.skipif(not _present(*PAIR1, *PAIR2), reason="historical runs not present locally")
def test_historical_pairs_are_valid_under_the_mechanical_rules(reports):
    for run_id in PAIR1 + PAIR2:
        assert reports[run_id]["validity"] == {"valid": True, "reasons": []}, run_id


def test_validity_rules():
    ok = {"solved": True, "low_observability": False, "unknown_tool_calls": 0,
          "arm_label_valid": True, "all_sessions_subscription_ok": True,
          "session_terminations": ["completed"], "isolation_check": {"available": True,
                                                                     "breach_suspected": False},
          "held_out_content_check": {"breach_suspected": False},
          "workspace_integrity": {"changed_outside_expected_scope": ["other.py"]}}
    assert report_mod.run_validity(ok)["valid"] is True     # editing another file is not an exclusion
    for key, value in (("session_terminations", ["turn_limit_exceeded"]),
                       ("held_out_content_check", {"breach_suspected": True}),
                       ("isolation_check", {"available": True, "breach_suspected": True}),
                       ("all_sessions_subscription_ok", None), ("low_observability", True)):
        assert report_mod.run_validity({**ok, key: value})["valid"] is False, key


# --------------------------------------------------------------------------
# Cross-task summary
# --------------------------------------------------------------------------


def _fake(task, arm, run_id, inp, out, wall, valid=True):
    return {"task_id": task, "repeat_id": 1, "arm": arm, "run_id": run_id, "task_shape": "s",
            "solved": True, "validity": {"valid": valid, "reasons": [] if valid else ["x"]},
            "config_hash": "h", "base_commit": "c", "session_cli_versions": ["2.1.260"],
            "resolved_models": ["m"], "workspace_integrity": {"read_only_files": []},
            "token_telemetry": {"reported_total_input_tokens": inp, "reported_output_tokens": out},
            "wall_seconds": wall}


def test_invalid_pairs_never_enter_aggregates():
    s = report_mod.cross_task_rows([
        _fake("t1", "A", "1", 100, 10, 10), _fake("t1", "B", "2", 300, 30, 40),
        _fake("t2", "A", "3", 100, 10, 10), _fake("t2", "B", "4", 900, 90, 90, valid=False),
        _fake("t3", "A", "5", 100, 10, 10), _fake("t3", "B", "6", 200, 20, 20),
    ])
    assert s["valid_pairs"] == 2 and s["invalid_pairs"] == 1
    agg = s["aggregates_over_valid_pairs"]["input_ratio"]
    assert agg == {"n": 2, "mean": 2.5, "median": 2.5, "min": 2.0, "max": 3.0}


def test_a_parity_failure_invalidates_the_pair():
    b = _fake("t1", "B", "2", 300, 30, 40)
    b["config_hash"] = "other"
    s = report_mod.cross_task_rows([_fake("t1", "A", "1", 100, 10, 10), b])
    assert s["valid_pairs"] == 0
    assert any("same config_hash" in x for x in s["rows"][0]["reasons"])


def test_the_latest_run_per_arm_is_used_and_earlier_ones_are_listed():
    s = report_mod.cross_task_rows([
        _fake("t1", "A", "20260101T000000Z", 100, 10, 10),
        _fake("t1", "A", "20260102T000000Z", 200, 10, 10),
        _fake("t1", "B", "20260103T000000Z", 400, 30, 40),
    ])
    assert s["rows"][0]["input_ratio"] == 2.0
    assert s["superseded_runs"] == ["20260101T000000Z"]


@pytest.mark.skipif(not _present(*HISTORICAL), reason="historical runs not present locally")
def test_cross_task_summary_on_the_historical_pairs(reports):
    s = report_mod.cross_task_rows(list(reports.values()))
    by_task = {r["task"]: r for r in s["rows"]}
    assert set(by_task) == {"palindrome_punctuation", "cart_invoice_rounding"}
    assert all(r["valid"] and r["post_hoc"] for r in by_task.values())
    assert s["superseded_runs"] == ["20260910T122445Z_palindrome_punctuation_A_r1"]
    assert by_task["cart_invoice_rounding"]["input_ratio"] == round(381293 / 129667, 3)
    text = report_mod.render_cross_task_summary(list(reports.values()))
    for col in ("B/A in", "ovl v1", "ovl v2", "uniq dn ch", "edit-req", "valid pairs: 2"):
        assert col in text
    assert "cart_invoice_rounding*" in text


def test_every_new_task_has_a_shape():
    for t in ("shipping_inch_dimensions", "settings_list_fields",
              "rename_max_connections", "sla_weekend_hours"):
        assert registry.get_task(t).shape
