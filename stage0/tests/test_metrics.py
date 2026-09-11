"""Milestone 10 tests: duplication metrics.

The temporal-availability rule is the one that is easiest to get wrong, so it is
asserted directly and in both directions.
"""

from __future__ import annotations

import pytest

from analysis import metrics
from harness import tools


def _acq(
    *,
    agent="investigator",
    session_index=2,
    tool_use_id="t1",
    path="tinylib/text.py",
    start=10,
    end=11,
    text="alpha\nbeta\ngamma\ndelta\nepsilon",
    klass=tools.STRUCTURED_READ,
    query=None,
    group=None,
) -> metrics.Acq:
    return metrics.Acq(
        session_index=session_index,
        session_key=f"{session_index:02d}_{agent}",
        agent_id=agent,
        tool_use_id=tool_use_id,
        tool_name="Read" if klass == tools.STRUCTURED_READ else "Grep",
        acquisition_class=klass,
        target_path=path,
        query=query,
        command=None,
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
        completed=True,
    )


# --------------------------------------------------------------------------
# K(t): temporal availability
# --------------------------------------------------------------------------


def test_kt_sequential_acquisition_is_attributable():
    """A finished before B started -> B's read IS attributable to A."""
    a = _acq(agent="investigator", session_index=2, tool_use_id="A", start=10, end=11)
    b = _acq(agent="implementer", session_index=4, tool_use_id="B", start=10, end=11)
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 1
    assert rep.inter_agent_duplicate_acquisitions == 1
    assert rep.concurrency_excluded_pairs == 0
    f = rep.findings[0]
    assert f["consumer_tool_use_id"] == "B"
    assert f["producer_tool_use_id"] == "A"
    assert f["globally_previously_available"] is True


def test_kt_interleaved_acquisition_is_NOT_attributable():
    """The mandated case:

        A READ START
        B READ START
        A READ END
        B READ END

    B could not have reused A's result when B started, so B's read is not
    avoidable because of A.
    """
    a = _acq(agent="investigator", session_index=2, tool_use_id="A", start=10, end=12)
    b = _acq(agent="implementer", session_index=2, tool_use_id="B", start=11, end=13)
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 0, "must not attribute concurrent reads"
    assert rep.potentially_avoidable_acquisitions == 0
    assert rep.concurrency_excluded_pairs > 0, "the rejected pair must be counted"
    assert rep.findings == []


def test_kt_uses_producer_end_before_consumer_start_not_end():
    """producer.end < consumer.END would wrongly accept this pair."""
    a = _acq(tool_use_id="A", session_index=2, start=10, end=20)
    b = _acq(tool_use_id="B", session_index=2, start=11, end=99, agent="implementer")
    assert a.end_key < b.end_key, "the weaker (wrong) condition holds here"
    assert not (a.end_key < b.start_key), "the correct condition does not hold"
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 0


def test_kt_parallel_calls_in_one_message_are_mutually_unattributable():
    """Reads issued in a single assistant message share a start line."""
    a = _acq(tool_use_id="A", start=10, end=11, path="a.py", text="x\ny\nz\nw")
    b = _acq(tool_use_id="B", start=10, end=11, path="b.py", text="x\ny\nz\nw")
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 0
    assert rep.concurrency_excluded_pairs > 0


def test_incomplete_acquisition_never_becomes_available():
    a = _acq(tool_use_id="A", start=10, end=None)
    a.completed = False
    b = _acq(tool_use_id="B", agent="implementer", session_index=4)
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 0
    assert rep.completed_acquisitions == 1


# --------------------------------------------------------------------------
# inter- vs intra-agent
# --------------------------------------------------------------------------


def test_intra_agent_repeat_is_separated_from_inter_agent_duplication():
    a = _acq(agent="implementer", session_index=4, tool_use_id="A", start=10, end=11)
    b = _acq(agent="implementer", session_index=4, tool_use_id="B", start=20, end=21)
    rep = metrics.analyze([a, b], [])
    assert rep.intra_agent_repeat_acquisitions == 1
    assert rep.inter_agent_duplicate_acquisitions == 0
    assert rep.potentially_avoidable_acquisitions == 0, (
        "an agent re-reading its own earlier read is not inter-agent coordination "
        "redundancy"
    )


def test_nothing_is_labelled_wasted():
    a = _acq(tool_use_id="A")
    b = _acq(tool_use_id="B", agent="implementer", session_index=4)
    rep = metrics.analyze([a, b], [])
    text = str(rep.as_dict()).lower()
    assert "wasted" not in text, "Stage 0 uses neutral terminology only"


# --------------------------------------------------------------------------
# Priming
# --------------------------------------------------------------------------


def _msg(text, recipient="implementer", before=4, label="forwarded_investigation_report"):
    return metrics.Message(
        recipient=recipient,
        sender="coordinator",
        label=label,
        text=text,
        delivered_before_session_index=before,
    )


def test_primed_when_a_delivered_message_named_the_path():
    a = _acq(agent="investigator", session_index=2, tool_use_id="A")
    b = _acq(agent="implementer", session_index=4, tool_use_id="B")
    rep = metrics.analyze(
        [a, b], [_msg("The root cause is in tinylib/text.py: normalize preserves punctuation.")]
    )
    f = rep.findings[0]
    assert f["priming"] == metrics.PRIMED
    assert f["priming_evidence"]["matched_token"] == "tinylib/text.py"
    assert f["priming_evidence"]["match_tier"] == "full_path"
    assert f["priming_evidence"]["excerpt"]
    assert rep.primed_reacquisitions == 1
    assert rep.unprimed_overlapping_discoveries == 0


def test_unprimed_when_no_message_named_the_target():
    a = _acq(agent="investigator", session_index=2, tool_use_id="A")
    b = _acq(agent="implementer", session_index=4, tool_use_id="B")
    rep = metrics.analyze([a, b], [_msg("Please fix the bug and run the tests.")])
    f = rep.findings[0]
    assert f["priming"] == metrics.UNPRIMED
    assert rep.unprimed_overlapping_discoveries == 1
    assert rep.primed_reacquisitions == 0


def test_a_bare_filename_stem_does_not_count_as_priming():
    """'report headings' must not prime a read of tinylib/report.py."""
    a = _acq(agent="investigator", session_index=2, tool_use_id="A", path="tinylib/report.py")
    b = _acq(agent="implementer", session_index=4, tool_use_id="B", path="tinylib/report.py")
    rep = metrics.analyze([a, b], [_msg("Do not change slugs or report headings.")])
    assert rep.findings[0]["priming"] == metrics.UNPRIMED


def test_a_message_delivered_AFTER_the_acquisition_cannot_prime_it():
    a = _acq(agent="investigator", session_index=2, tool_use_id="A")
    b = _acq(agent="implementer", session_index=4, tool_use_id="B")
    late = _msg("tinylib/text.py is the root cause", before=9)  # after session 4
    rep = metrics.analyze([a, b], [late])
    assert rep.findings[0]["priming"] == metrics.UNPRIMED


def test_priming_undetermined_for_an_opaque_acquisition():
    a = metrics.Acq(
        session_index=2,
        session_key="02_investigator",
        agent_id="investigator",
        tool_use_id="A",
        tool_name="Bash",
        acquisition_class=tools.CLASSIFIED_BASH_READ,
        target_path=None,
        query=None,
        command="cat tinylib/text.py",
        result_sha=tools.content_sha("a\nb\nc\nd"),
        result_shingles=tuple(tools.shingles("a\nb\nc\nd")),
        result_chars=7,
        result_bytes=7,
        start_line=10,
        concurrency_group_line=10,
        end_line=11,
        start_ts=None,
        end_ts=None,
        turn_id=1,
        completed=True,
    )
    b = metrics.Acq(**{**a.__dict__, "agent_id": "implementer",
                       "session_key": "04_implementer", "session_index": 4,
                       "tool_use_id": "B"})
    rep = metrics.analyze([a, b], [_msg("tinylib/text.py is the cause")])
    assert rep.findings[0]["priming"] == metrics.PRIMING_UNKNOWN
    assert rep.priming_undetermined == 1


# --------------------------------------------------------------------------
# Overlap kinds and oracle bound
# --------------------------------------------------------------------------


def test_identical_content_is_detected_across_agents():
    a = _acq(tool_use_id="A", agent="investigator", session_index=2)
    b = _acq(tool_use_id="B", agent="implementer", session_index=4)
    rep = metrics.analyze([a, b], [])
    assert rep.findings[0]["overlap_kind"] == metrics.IDENTICAL
    assert rep.findings[0]["overlap_ratio"] == 1.0


def test_repeated_search_with_same_query_is_labelled():
    text = "hit1\nhit2\nhit3\nhit4"
    a = _acq(
        tool_use_id="A", agent="investigator", session_index=2, klass=tools.STRUCTURED_SEARCH,
        query="normalize\\(", path=".", text=text,
    )
    b = _acq(
        tool_use_id="B", agent="implementer", session_index=4, klass=tools.STRUCTURED_SEARCH,
        query="normalize\\(", path=".", text=text,
    )
    rep = metrics.analyze([a, b], [])
    assert rep.repeated_search_results_inter_agent == 1
    assert rep.searches == 2


def test_unrelated_content_produces_no_finding():
    a = _acq(tool_use_id="A", path="a.py", text="one\ntwo\nthree\nfour")
    b = _acq(
        tool_use_id="B", agent="implementer", session_index=4, path="b.py",
        text="alpha\nbravo\ncharlie\ndelta",
    )
    rep = metrics.analyze([a, b], [])
    assert rep.findings == []


def test_oracle_upper_bound_counts_only_primed_inter_agent():
    a = _acq(tool_use_id="A", agent="investigator", session_index=2)
    b = _acq(tool_use_id="B", agent="implementer", session_index=4)
    primed = metrics.analyze([a, b], [_msg("see tinylib/text.py")])
    unprimed = metrics.analyze([a, b], [_msg("just fix it")])

    assert primed.oracle_upper_bound["primed_repeated_file_acquisitions"] == 1
    assert primed.oracle_upper_bound["duplicate_tool_calls"] == 1
    assert primed.oracle_upper_bound["duplicate_chars"] > 0
    assert "not dollars" in primed.oracle_upper_bound["unit"]

    assert unprimed.oracle_upper_bound["primed_repeated_file_acquisitions"] == 0
    assert unprimed.oracle_upper_bound["duplicate_tool_calls"] == 0


def test_oracle_upper_bound_is_not_in_dollars():
    rep = metrics.analyze([], [])
    assert "usd" not in str(rep.oracle_upper_bound).lower()
    assert "dollar" in rep.oracle_upper_bound["unit"]


# --------------------------------------------------------------------------
# Shingles
# --------------------------------------------------------------------------


def test_shingles_are_three_line_windows_not_per_line():
    text = "a\nb\nc\nd\ne"
    sh = tools.shingles(text)
    assert len(sh) == 3, "5 lines -> 3 windows of 3"
    assert len(set(sh)) == 3


def test_trivial_lines_do_not_create_false_overlap():
    """Two different files that share only trivial lines must not overlap."""
    f1 = "import os\n\ndef alpha():\n    return 1\n\n}\n"
    f2 = "import os\n\ndef zulu():\n    return 99\n\n}\n"
    s1, s2 = set(tools.shingles(f1)), set(tools.shingles(f2))
    ratio = len(s1 & s2) / len(s2) if s2 else 0
    assert ratio < metrics.SHINGLE_OVERLAP_THRESHOLD


def test_read_tool_line_number_prefixes_are_normalized_away():
    plain = "def f():\n    return 1\n\nx = f()"
    numbered = "     1\tdef f():\n     2\t    return 1\n     3\t\n     4\tx = f()"
    assert tools.shingles(plain) == tools.shingles(numbered)


def test_file_versions_are_different_information():
    """A read before an edit and a read after it are NOT the same information."""
    before = "def normalize(t):\n    return t.lower()\n\nx = 1"
    after = "def normalize(t):\n    return t.lower().strip()\n\nx = 1"
    a = _acq(tool_use_id="A", agent="investigator", session_index=2, text=before)
    b = _acq(tool_use_id="B", agent="implementer", session_index=4, text=after)
    assert a.result_sha != b.result_sha
    rep = metrics.analyze([a, b], [])
    assert rep.findings == [] or rep.findings[0]["overlap_kind"] != metrics.IDENTICAL


# --------------------------------------------------------------------------
# Concurrency grouping (parallel tool calls in one API message)
# --------------------------------------------------------------------------


def test_calls_in_one_api_message_are_concurrent_even_on_different_lines():
    """Claude Code emits one stream event per content block, so parallel tool
    calls land on consecutive lines. Ordering by raw line would make them look
    sequential; the concurrency group keeps them simultaneous."""
    a = _acq(tool_use_id="A", start=8, end=9, group=8)
    b = _acq(tool_use_id="B", agent="implementer", session_index=2, start=10, end=11, group=8)
    assert a.start_key == b.start_key
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 0, (
        "A finished at line 9 and B's raw line is 10, but they were issued "
        "together, so neither is attributable to the other"
    )
    assert rep.concurrency_excluded_pairs > 0


def test_raw_line_ordering_would_have_wrongly_attributed_them():
    """Guard on the guard: with group == raw line, the same pair IS attributed.
    This shows the fix changes the outcome rather than being decorative."""
    a = _acq(tool_use_id="A", start=8, end=9, group=8)
    b = _acq(tool_use_id="B", agent="implementer", session_index=2, start=10, end=11, group=10)
    rep = metrics.analyze([a, b], [])
    assert rep.globally_previously_available == 1


def test_concurrency_group_defaults_to_the_start_line():
    a = _acq(tool_use_id="A", start=42, end=43)
    assert a.concurrency_group_line == 42


def test_acq_from_dict_falls_back_when_group_is_absent():
    d = {
        "session_key": "01_solo", "agent_id": "solo", "tool_use_id": "t",
        "tool_name": "Read", "acquisition_class": tools.STRUCTURED_READ,
        "start_line": 7, "end_line": 8, "completed": True,
    }
    assert metrics.acq_from_dict(d, 1).concurrency_group_line == 7
    d["concurrency_group_line"] = 5
    assert metrics.acq_from_dict(d, 1).concurrency_group_line == 5
