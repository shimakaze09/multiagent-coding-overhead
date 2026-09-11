"""Reacquisition subcategories (PREREGISTRATION amendment 5).

The first real Arm B run exposed a confound: both of its primed reacquisitions
were files the Implementer then edited, and Claude Code's Edit tool refuses a
file that has not been Read in the current session. The gross
`primed_reacquisition` count is kept; underneath it every primed reacquisition
is placed in exactly one of:

    edit_precondition_associated | verification_associated |
    discretionary_information_reacquisition | unknown
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from analysis import ingest, metrics, report as report_mod
from harness import tools

CLI = "2.1.260"

A_TEXT = "def area(w, h):\n    return w * h\n\ndef perimeter(w, h):\n    return 2 * (w + h)\n\nUNITS = 'metric'"
B_TEXT = "ROUNDING = 'half_up'\nSCALE = 100\n\ndef round_minor(x):\n    return int(x + 0.5)\n\nVERSION = 3"
A_EDITED = A_TEXT.replace("UNITS = 'metric'", "UNITS = 'imperial'")

_NAMES = {
    tools.STRUCTURED_READ: "Read",
    tools.STRUCTURED_EDIT: "Edit",
    tools.STRUCTURED_SEARCH: "Grep",
    tools.BASH_VERIFICATION: "Bash",
}


def mk(
    *,
    agent,
    session_index,
    tool_use_id,
    klass=tools.STRUCTURED_READ,
    path="pkg/a.py",
    start=10,
    end=11,
    text=A_TEXT,
    group=None,
    denied=False,
    error=False,
    query=None,
) -> metrics.Acq:
    text = text if klass in (tools.STRUCTURED_READ, tools.STRUCTURED_SEARCH) else "ok"
    return metrics.Acq(
        session_index=session_index,
        session_key=f"{session_index:02d}_{agent}",
        agent_id=agent,
        tool_use_id=tool_use_id,
        tool_name=_NAMES[klass],
        acquisition_class=klass,
        target_path=path,
        query=query,
        command="python -m pytest -q" if klass == tools.BASH_VERIFICATION else None,
        result_sha=tools.content_sha(text),
        result_shingles=tuple(tools.shingles(text)),
        result_chars=len(text),
        result_bytes=len(text.encode()),
        start_line=start,
        concurrency_group_line=group if group is not None else start,
        end_line=end,
        start_ts=None,
        end_ts=None,
        turn_id=1,
        completed=end is not None,
        permission_denied=denied,
        is_error=error,
    )


def msg(text, recipient="implementer", before=4, label="implementation_instruction"):
    return metrics.Message(
        recipient=recipient,
        sender="coordinator",
        label=label,
        text=text,
        delivered_before_session_index=before,
    )


def investigator_reads():
    return [
        mk(agent="investigator", session_index=2, tool_use_id="i_a", path="pkg/a.py",
           start=5, end=6, text=A_TEXT),
        mk(agent="investigator", session_index=2, tool_use_id="i_b", path="pkg/b.py",
           start=7, end=8, text=B_TEXT),
    ]


HANDOFF = msg("The bug is in pkg/a.py. The rounding rule it must follow lives in pkg/b.py.")


def subcat(rep, tool_use_id):
    f = next(f for f in rep.findings if f["consumer_tool_use_id"] == tool_use_id)
    return f["reacquisition_subcategory"], f["subcategory_evidence"]


# --------------------------------------------------------------------------
# The four subcategories
# --------------------------------------------------------------------------


def test_read_then_edit_of_same_file_is_edit_precondition_associated():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    sub, ev = subcat(rep, "m_a")
    assert sub == metrics.REACQ_EDIT_PRECONDITION
    assert ev["edit_tool_use_id"] == "m_edit"
    assert ev["rule"] == "first_read_before_first_successful_edit_of_same_file"
    assert "File has not been read yet" in ev["tool_semantics"]
    assert rep.edit_precondition_associated == 1
    assert rep.primed_reacquisitions == 1


def test_primed_reread_of_a_non_edited_supporting_file_is_discretionary():
    """The signal the second fixture is designed to measure."""
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=12, end=13, text=B_TEXT),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    assert subcat(rep, "m_a")[0] == metrics.REACQ_EDIT_PRECONDITION
    assert subcat(rep, "m_b")[0] == metrics.REACQ_DISCRETIONARY
    assert rep.discretionary_information_reacquisition == 1
    assert rep.discretionary_upper_bound["tool_calls"] == 1
    assert rep.discretionary_upper_bound["chars"] == len(B_TEXT)


def test_read_before_edit_is_not_silently_called_avoidable():
    """A read the Edit tool demands must never land in the discretionary bucket."""
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    assert rep.discretionary_information_reacquisition == 0
    assert rep.discretionary_upper_bound["tool_calls"] == 0
    # the gross measure is untouched
    assert rep.primed_reacquisitions == 1
    assert rep.oracle_upper_bound["duplicate_tool_calls"] == 1


def test_edit_precondition_is_unknown_on_an_unverified_cli_version():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version="9.9.9")
    sub, ev = subcat(rep, "m_a")
    assert sub == metrics.REACQ_UNKNOWN
    assert ev["rule"] == "edit_precondition_unverified_for_this_cli_version"
    assert rep.edit_precondition_verified is False


def test_no_cli_version_at_all_is_also_unknown():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    assert subcat(metrics.analyze(acqs, [HANDOFF]), "m_a")[0] == metrics.REACQ_UNKNOWN


def test_a_failed_or_refused_edit_does_not_make_the_read_edit_required():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21, error=True),
    ]
    sub, ev = subcat(metrics.analyze(acqs, [HANDOFF], cli_version=CLI), "m_a")
    assert sub == metrics.REACQ_UNKNOWN
    assert ev["rule"] == "same_file_edit_attempted_but_did_not_execute"


def test_a_second_read_before_the_edit_was_not_required_by_the_tool():
    """Only the first read satisfies the precondition; a repeat is discretionary."""
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a1", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_a2", path="pkg/a.py", start=12, end=13),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    assert subcat(rep, "m_a1")[0] == metrics.REACQ_EDIT_PRECONDITION
    assert subcat(rep, "m_a2")[0] == metrics.REACQ_DISCRETIONARY


def test_rereading_a_file_after_editing_it_is_verification_associated():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=12, end=13),
        mk(agent="implementer", session_index=4, tool_use_id="m_a_after", path="pkg/a.py",
           start=20, end=21, text=A_EDITED),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    sub, ev = subcat(rep, "m_a_after")
    assert sub == metrics.REACQ_VERIFICATION
    assert ev["rule"] == "post_edit_reread"


def test_an_explicit_verification_request_is_verification_associated():
    request = msg("Please verify that pkg/b.py still rounds half up before you change anything.")
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
    ]
    sub, ev = subcat(metrics.analyze(acqs, [request], cli_version=CLI), "m_b")
    assert sub == metrics.REACQ_VERIFICATION
    assert ev["rule"] == "explicit_verification_request"
    assert ev["verb"].lower() == "verify"
    assert "pkg/b.py" in ev["sentence"]


def test_a_verification_verb_in_a_different_sentence_does_not_count():
    text = "Please verify the tests pass. Background: pkg/b.py has the rounding rule."
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
    ]
    assert subcat(metrics.analyze(acqs, [msg(text)], cli_version=CLI), "m_b")[0] == (
        metrics.REACQ_DISCRETIONARY
    )


def test_a_reread_after_the_agents_own_test_run_is_unknown_not_discretionary():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_test",
           klass=tools.BASH_VERIFICATION, path=None, start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=12, end=13, text=B_TEXT),
    ]
    sub, ev = subcat(metrics.analyze(acqs, [HANDOFF], cli_version=CLI), "m_b")
    assert sub == metrics.REACQ_UNKNOWN
    assert ev["rule"] == "follows_own_verification_result"


def test_an_edit_of_a_different_file_does_not_make_a_read_edit_required():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    assert subcat(metrics.analyze(acqs, [HANDOFF], cli_version=CLI), "m_b")[0] == (
        metrics.REACQ_DISCRETIONARY
    )


def test_an_edit_in_another_session_does_not_satisfy_current_session_semantics():
    """Claude Code's rule is per session: an edit by a later session of the same
    file does not make this session's read edit-required."""
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=6, tool_use_id="later_edit",
           klass=tools.STRUCTURED_EDIT, path="pkg/a.py", start=5, end=6),
    ]
    assert subcat(metrics.analyze(acqs, [HANDOFF], cli_version=CLI), "m_a")[0] == (
        metrics.REACQ_DISCRETIONARY
    )


def test_absolute_and_relative_paths_to_the_same_file_match():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a",
           path="C:/ws/pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    assert subcat(metrics.analyze(acqs, [HANDOFF], cli_version=CLI), "m_a")[0] == (
        metrics.REACQ_EDIT_PRECONDITION
    )


# --------------------------------------------------------------------------
# What must NOT change
# --------------------------------------------------------------------------


def test_an_unprimed_overlap_remains_independent_and_is_not_subcategorised():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
    ]
    rep = metrics.analyze(acqs, [msg("Please fix the bug.")], cli_version=CLI)
    f = rep.findings[0]
    assert f["priming"] == metrics.UNPRIMED
    assert f["reacquisition_subcategory"] == metrics.REACQ_NOT_APPLICABLE
    assert rep.unprimed_overlapping_discoveries == 1
    assert rep.primed_reacquisitions == 0
    assert rep.discretionary_information_reacquisition == 0


def test_concurrent_acquisitions_remain_excluded_and_unclassified():
    """K(t) is applied before any subcategory: concurrent calls produce no
    finding at all, so they cannot be counted in any bucket."""
    a = mk(agent="investigator", session_index=2, tool_use_id="i_a", start=10, end=12)
    b = mk(agent="implementer", session_index=2, tool_use_id="m_a", start=11, end=13)
    rep = metrics.analyze([a, b], [msg("see pkg/a.py", before=1)], cli_version=CLI)
    assert rep.findings == []
    assert rep.concurrency_excluded_pairs > 0
    assert rep.primed_reacquisitions == 0
    assert rep.edit_precondition_associated == 0
    assert rep.discretionary_information_reacquisition == 0


def test_intra_agent_repeats_are_not_subcategorised():
    acqs = [
        mk(agent="implementer", session_index=4, tool_use_id="m1", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m2", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    assert rep.intra_agent_repeat_acquisitions == 1
    assert rep.findings[0]["reacquisition_subcategory"] == metrics.REACQ_NOT_APPLICABLE


def test_subcategories_partition_the_gross_count_exactly():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=12, end=13, text=B_TEXT),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    rep = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    parts = (
        rep.edit_precondition_associated
        + rep.verification_associated
        + rep.discretionary_information_reacquisition
        + rep.reacquisition_unknown
    )
    assert parts == rep.primed_reacquisitions == 2
    assert sum(v["tool_calls"] for v in rep.subcategory_physical.values()) == 2


def test_nothing_is_labelled_wasted():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
    ]
    assert "wasted" not in str(metrics.analyze(acqs, [HANDOFF], cli_version=CLI).as_dict()).lower()


def test_preregistered_oracle_bound_is_unchanged_by_the_subcategories():
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_a", path="pkg/a.py", start=10, end=11),
        mk(agent="implementer", session_index=4, tool_use_id="m_edit", klass=tools.STRUCTURED_EDIT,
           path="pkg/a.py", start=20, end=21),
    ]
    with_edit = metrics.analyze(acqs, [HANDOFF], cli_version=CLI)
    without_edit = metrics.analyze(acqs[:-1], [HANDOFF], cli_version=CLI)
    assert with_edit.oracle_upper_bound == without_edit.oracle_upper_bound
    assert with_edit.primed_reacquisitions == without_edit.primed_reacquisitions == 1


# --------------------------------------------------------------------------
# The real Arm B run
# --------------------------------------------------------------------------

REAL_B = "20260910T230448Z_palindrome_punctuation_B_r1"


@pytest.fixture(scope="module")
def real_b(request):
    d = request.config.rootpath / "runs" / REAL_B
    if not (d / "metadata.json").is_file():
        pytest.skip(f"real Arm B run {REAL_B} not present")
    return d


def _hash_raw(d: Path) -> dict:
    return {
        str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.rglob("*"))
        if p.is_file() and "workspace" not in p.relative_to(d).parts
    }


def test_real_arm_b_both_duplicate_reads_remain_gross_primed_reacquisitions(real_b):
    r = report_mod.run_report(real_b)
    rc = r["reacquisition_classification"]
    assert rc["gross_primed_reacquisitions"] == 2
    assert r["primed_reacquisitions"] == 2
    assert r["oracle_upper_bound"]["duplicate_tool_calls"] == 2


def test_real_arm_b_both_are_edit_precondition_associated(real_b):
    r = report_mod.run_report(real_b)
    rc = r["reacquisition_classification"]
    assert rc["edit_precondition_associated"] == 2
    assert rc["discretionary_information_reacquisition"] == 0
    assert rc["verification_associated"] == 0
    assert rc["unknown"] == 0
    assert rc["edit_precondition_verified"] is True
    assert rc["discretionary_upper_bound"]["tool_calls"] == 0


def test_real_arm_b_evidence_points_at_a_real_later_edit_of_the_same_file(real_b):
    raw = ingest.load_run(real_b)
    impl = next(sa for sa in raw["sessions"] if sa.invocation["role"] == "implementer")
    calls = {c.tool_use_id: c for c in impl.parsed.tool_calls}
    r = report_mod.run_report(real_b)
    for f in r["findings"]:
        ev = f["subcategory_evidence"]
        read = calls[f["consumer_tool_use_id"]]
        edit = calls[ev["edit_tool_use_id"]]
        assert read.name == "Read" and edit.name == "Edit"
        assert read.input["file_path"] == edit.input["file_path"]
        assert edit.start_line > read.end_line
        assert edit.tool_use_id not in impl.parsed.denied_tool_use_ids


def test_real_arm_b_classification_is_derived_from_events_not_hardcoded(real_b):
    acqs = ingest.run_acquisitions(real_b)
    msgs = ingest.run_messages(real_b)
    # an unverified CLI version turns both into unknown
    assert metrics.analyze(acqs, msgs, cli_version="9.9.9").reacquisition_unknown == 2

    # Without the Implementer's edits neither read is edit-required. Each then
    # meets the verification-request rule: the Coordinator's instruction names
    # both files in sentences asking to "re-verify it still passes" and "confirm
    # every test still passes". Those ask for a test RUN, so the reads are not
    # attributable to them and are `unknown`, not verification.
    no_edits = [a for a in acqs if a.acquisition_class != tools.STRUCTURED_EDIT]
    rep = metrics.analyze(no_edits, msgs, cli_version=CLI)
    assert rep.edit_precondition_associated == 0
    assert rep.verification_associated == 0
    assert rep.reacquisition_unknown == 2
    rules = sorted(f["subcategory_evidence"]["rule"] for f in rep.findings)
    assert rules == ["verification_request_about_test_outcome"] * 2
    verbs = sorted(f["subcategory_evidence"]["verb"].lower() for f in rep.findings)
    assert verbs == ["confirm", "re-verify"]

    # With the verification verbs also removed from the handoffs, only the
    # residual is left: discretionary. Nothing in the classifier names these files.
    neutral = [
        metrics.Message(m.recipient, m.sender, m.label,
                        metrics._VERIFICATION_RE.sub("", m.text),
                        m.delivered_before_session_index)
        for m in msgs
    ]
    rep2 = metrics.analyze(no_edits, neutral, cli_version=CLI)
    assert rep2.discretionary_information_reacquisition == 2


def test_a_verification_request_about_test_outcomes_is_unknown():
    text = "Run the suite and confirm that pkg/b.py still passes all its checks."
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
    ]
    sub, ev = subcat(metrics.analyze(acqs, [msg(text)], cli_version=CLI), "m_b")
    assert sub == metrics.REACQ_UNKNOWN
    assert ev["rule"] == "verification_request_about_test_outcome"
    assert ev["about_test_outcome"] is True


def test_an_inspection_request_is_preferred_over_a_test_outcome_request():
    text = ("Confirm pkg/b.py still passes its tests.\n"
            "Also double-check that pkg/b.py keeps the half-up rounding rule.")
    acqs = investigator_reads() + [
        mk(agent="implementer", session_index=4, tool_use_id="m_b", path="pkg/b.py",
           start=10, end=11, text=B_TEXT),
    ]
    sub, ev = subcat(metrics.analyze(acqs, [msg(text)], cli_version=CLI), "m_b")
    assert sub == metrics.REACQ_VERIFICATION
    assert ev["verb"].lower() == "double-check"


def test_real_arm_b_raw_artifacts_unchanged_by_reanalysis(real_b):
    before = _hash_raw(real_b)
    report_mod.run_report(real_b)
    report_mod.human_trace(real_b)
    after = _hash_raw(real_b)
    assert before == after
