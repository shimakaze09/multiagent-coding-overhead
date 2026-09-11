"""Pair 2 discrimination: will the frozen analysis separate an edit-required
reread of shopcart/cart.py from a discretionary reread of a supporting file?

Synthetic events built from the REAL fixture file contents. No Claude session.
The normal temporal, priming, verification and `unknown` rules all still apply:
nothing here forces a supporting-file read into any bucket.
"""

from __future__ import annotations

import pytest

from analysis import metrics, report as report_mod
from harness import tools
from tasks import registry

CLI = "2.1.260"
WS = "D:/ws"
CART, DISC, INV, MONEY, CUR = (
    "shopcart/cart.py", "shopcart/discounts.py", "shopcart/invoice.py",
    "shopcart/money.py", "shopcart/currency.py",
)


@pytest.fixture(scope="module")
def task():
    return registry.get_task("cart_invoice_rounding")


@pytest.fixture(scope="module")
def texts(task):
    return {p: (task.source_tree / p).read_text(encoding="utf-8") for p in (CART, DISC, INV, MONEY, CUR)}


def mk(texts, *, agent, sidx, uid, path, start, klass=tools.STRUCTURED_READ):
    body = texts[path] if klass == tools.STRUCTURED_READ else "ok"
    return metrics.Acq(
        session_index=sidx, session_key=f"{sidx:02d}_{agent}", agent_id=agent,
        tool_use_id=uid, tool_name={tools.STRUCTURED_READ: "Read", tools.STRUCTURED_EDIT: "Edit",
                                    tools.BASH_VERIFICATION: "Bash"}[klass],
        acquisition_class=klass, target_path=f"{WS}/{path}", query=None,
        command="python -m pytest -q" if klass == tools.BASH_VERIFICATION else None,
        result_sha=tools.content_sha(body), result_shingles=tuple(tools.shingles(body)),
        result_chars=len(body), result_bytes=len(body.encode()),
        start_line=start, concurrency_group_line=start, end_line=start + 1,
        start_ts=None, end_ts=None, turn_id=1, completed=True,
    )


HANDOFF = metrics.Message(
    recipient="implementer", sender="coordinator", label="forwarded_investigation_report",
    delivered_before_session_index=4,
    text=(
        "The symptom is in shopcart/cart.py: line_total rounds the whole line.\n"
        "The per-unit rounding contract is documented in shopcart/discounts.py.\n"
        "shopcart/invoice.py computes invoice lines per unit, which the cart must match.\n"
        "Minor units per currency come from shopcart/currency.py.\n"
    ),
)


def scenario(texts):
    inv = [mk(texts, agent="investigator", sidx=2, uid=f"i{i}", path=p, start=5 + 2 * i)
           for i, p in enumerate((CART, DISC, INV, MONEY, CUR))]
    impl = [
        mk(texts, agent="implementer", sidx=4, uid="m_cart", path=CART, start=10),
        mk(texts, agent="implementer", sidx=4, uid="m_disc", path=DISC, start=12),
        mk(texts, agent="implementer", sidx=4, uid="m_inv", path=INV, start=14),
        mk(texts, agent="implementer", sidx=4, uid="m_money", path=MONEY, start=16),
        mk(texts, agent="implementer", sidx=4, uid="m_edit", path=CART, start=20,
           klass=tools.STRUCTURED_EDIT),
        mk(texts, agent="implementer", sidx=4, uid="m_test", path=CART, start=22,
           klass=tools.BASH_VERIFICATION),
        mk(texts, agent="implementer", sidx=4, uid="m_cur", path=CUR, start=24),
    ]
    return inv + impl


def by_consumer(rep):
    return {f["consumer_tool_use_id"]: f for f in rep.findings}


def test_the_frozen_rules_separate_the_cases_the_fixture_is_built_for(texts):
    rep = metrics.analyze(scenario(texts), [HANDOFF], cli_version=CLI)
    f = by_consumer(rep)
    # cart.py: read, then edited -> tool-required, NOT discretionary (H2)
    assert f["m_cart"]["priming"] == metrics.PRIMED
    assert f["m_cart"]["reacquisition_subcategory"] == metrics.REACQ_EDIT_PRECONDITION
    # named supporting files, reread, never edited -> discretionary candidates (H1)
    assert f["m_disc"]["reacquisition_subcategory"] == metrics.REACQ_DISCRETIONARY
    assert f["m_inv"]["reacquisition_subcategory"] == metrics.REACQ_DISCRETIONARY
    # money.py was NOT named in the handoff -> unprimed, independent discovery
    assert f["m_money"]["priming"] == metrics.UNPRIMED
    assert f["m_money"]["reacquisition_subcategory"] == metrics.REACQ_NOT_APPLICABLE
    # currency.py reread AFTER the agent's own test run -> unknown, not discretionary
    assert f["m_cur"]["reacquisition_subcategory"] == metrics.REACQ_UNKNOWN
    assert f["m_cur"]["subcategory_evidence"]["rule"] == "follows_own_verification_result"
    assert rep.primed_reacquisitions == 4


def test_supporting_and_focal_file_breakdowns(texts, task):
    rep = metrics.analyze(scenario(texts), [HANDOFF], cli_version=CLI)
    sf = report_mod.file_breakdown(rep.findings, task.supporting_paths)
    assert sf["applicable"] is True
    assert sf["discretionary_rereads"] == 2
    assert sf["unknown"] == 1
    assert sf["unprimed_overlaps"] == 1
    assert sf["edit_precondition_associated"] == 0
    assert sf["per_file"][DISC]["discretionary_information_reacquisition"] == 1
    assert sf["per_file"][INV]["discretionary_information_reacquisition"] == 1
    assert sf["per_file"][MONEY]["unprimed_overlaps"] == 1
    assert sf["per_file"][CUR]["unknown"] == 1
    assert sf["discretionary_chars"] == len(texts[DISC]) + len(texts[INV])

    ff = report_mod.file_breakdown(rep.findings, task.expected_modified_paths)
    assert ff["edit_precondition_associated"] == 1
    assert ff["discretionary_rereads"] == 0


def test_a_test_outcome_request_keeps_a_supporting_read_out_of_discretionary(texts):
    msg = metrics.Message(
        recipient="implementer", sender="coordinator", label="implementation_instruction",
        delivered_before_session_index=4,
        text="Confirm that shopcart/discounts.py still passes its tests after your change.",
    )
    acqs = [mk(texts, agent="investigator", sidx=2, uid="i", path=DISC, start=5),
            mk(texts, agent="implementer", sidx=4, uid="m", path=DISC, start=10)]
    f = metrics.analyze(acqs, [msg], cli_version=CLI).findings[0]
    assert f["reacquisition_subcategory"] == metrics.REACQ_UNKNOWN


def test_the_task_declares_the_supporting_files(task):
    assert task.supporting_paths == (DISC, INV, MONEY, CUR)
    assert task.expected_modified_paths == (CART,)
    for p in task.supporting_paths:
        assert (task.source_tree / p).is_file()


def test_file_breakdown_is_not_applicable_without_declared_paths():
    assert report_mod.file_breakdown([], [])["applicable"] is False


def test_pair_summary_shows_every_frozen_section_and_parity(mock_runs):
    a = report_mod.run_report(mock_runs["A"][0])
    b = report_mod.run_report(mock_runs["B"][0])
    text = report_mod.render_pair_summary(a, b)
    for section in report_mod.PAIR_SUMMARY_SECTIONS:
        assert section in text, section
    for check in ("same task", "same config_hash", "same base commit",
                  "same Claude Code version", "same resolved main model", "same protected files"):
        assert f"[PASS] {check}" in text, check
    for row in ("solved (held-out verifier)", "total input tokens (main model)", "cache read",
                "cache write", "output tokens (main model)", "wall seconds",
                "repository acquisition chars", "model calls (API assistant messages)"):
        assert row in text, row
    assert "exploratory" in text


def test_pair_summary_flags_a_config_hash_mismatch(mock_runs):
    a = report_mod.run_report(mock_runs["A"][0])
    b = dict(report_mod.run_report(mock_runs["B"][0]), config_hash="different")
    assert "[FAIL] same config_hash" in report_mod.render_pair_summary(a, b)


def test_comparison_turns_row_is_no_longer_none(mock_runs):
    a = report_mod.run_report(mock_runs["A"][0])
    b = report_mod.run_report(mock_runs["B"][0])
    text = report_mod.render_comparison(a, b)
    row = next(l for l in text.splitlines() if l.startswith("Claude CLI num_turns (sum)"))
    assert "None" not in row
