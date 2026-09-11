"""Communication duplication (PREREGISTRATION amendment 5).

How much repository / tool content is copied into natural-language handoffs,
and how much of it the downstream agent then acquires again with tools.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from analysis import handoff
from harness import tools

CODE = (
    "def is_palindrome(text):\n"
    "    cleaned = normalize(text).replace(' ', '')\n"
    "    return bool(cleaned) and cleaned == cleaned[::-1]\n"
    "\n"
    "def longest_palindrome(words):\n"
    "    best = None\n"
    "    return best\n"
)


# --------------------------------------------------------------------------
# Content windows
# --------------------------------------------------------------------------


def test_syntax_only_windows_are_not_informative():
    assert tools.content_windows(")\n]\n}\n)\n") == []
    assert tools.content_windows("else:\n    pass\n)\n") == []


def test_windows_are_indentation_insensitive():
    reindented = "\n".join("        " + l for l in CODE.splitlines())
    assert tools.content_chunk_set(CODE) == tools.content_chunk_set(reindented) != set()


def test_read_tool_line_number_prefixes_are_normalized_away():
    numbered = "\n".join(f"{i:6d}\t{l}" for i, l in enumerate(CODE.splitlines(), 1))
    assert tools.content_chunk_set(CODE) == tools.content_chunk_set(numbered)


def test_quoted_code_inside_prose_is_attributed_and_prose_is_not():
    ref = tools.content_chunk_set(CODE)
    handoff_text = "Here is the function:\n```python\n" + CODE + "```\nPlease fix it."
    n, chars, matched = tools.covered_by(handoff_text, ref)
    assert n == len(tools.content_windows(CODE))
    assert matched == ref
    assert chars == sum(len(l.strip()) for l in CODE.splitlines() if l.strip())
    assert tools.covered_by("A paragraph of prose.\nWith no code.\nAt all here.", ref)[0] == 0


def test_windows_and_coverage_are_deterministic():
    assert tools.content_windows(CODE) == tools.content_windows(CODE)
    ref = tools.content_chunk_set(CODE)
    assert tools.covered_by(CODE, ref) == tools.covered_by(CODE, ref)


def test_preregistered_shingles_are_untouched_by_the_new_windows():
    """`shingles` (used by every preregistered overlap metric) keeps indentation
    and keeps short windows; the new windows are a separate measurement."""
    assert tools.shingles("a\nb\nc\nd") != []
    assert tools.content_windows("a\nb\nc\nd") == []


# --------------------------------------------------------------------------
# The real Arm B run
# --------------------------------------------------------------------------

REAL_B = "20260910T230448Z_palindrome_punctuation_B_r1"
REAL_A = "20260910T125941Z_palindrome_punctuation_A_r1"


@pytest.fixture(scope="module")
def real_b(request):
    d = request.config.rootpath / "runs" / REAL_B
    if not (d / "metadata.json").is_file():
        pytest.skip(f"real Arm B run {REAL_B} not present")
    return d


@pytest.fixture(scope="module")
def comm(real_b):
    return handoff.analyze_run(real_b)


def _by_label(comm):
    return {h["label"]: h for h in comm["per_handoff"]}


def test_every_handoff_is_measured(comm):
    assert [h["label"] for h in comm["per_handoff"]] == [
        "investigation_instruction",
        "investigation_report",
        "implementation_instruction",
        "forwarded_investigation_report",
        "implementation_report",
        "final_result",
    ]
    assert comm["repository_content_source"] == "base_commit"


def test_the_forwarded_report_is_entirely_relayed_content(comm):
    h = _by_label(comm)["forwarded_investigation_report"]
    assert h["informative_chunks"] > 0
    assert h["relayed_chunks_in_handoff"] == h["informative_chunks"]


def test_the_investigator_report_quotes_repository_content_it_had_read(comm):
    h = _by_label(comm)["investigation_report"]
    assert h["repository_content_chunks_in_handoff"] > 0
    assert h["tool_output_chunks_in_handoff"] > 0
    assert 0 < h["handoff_repository_quote_fraction"] < 1
    assert set(h["repository_files_quoted"]) <= {
        "tinylib/palindrome.py", "tinylib/report.py", "tinylib/text.py",
        "tinylib/__init__.py", "tests/test_basic.py",
    }


def test_the_coordinator_never_contributes_its_own_tool_output(comm):
    for h in comm["per_handoff"]:
        if h["sender"] == "coordinator":
            assert h["tool_output_chunks_in_handoff"] == 0


def test_the_first_instruction_relays_nothing(comm):
    h = _by_label(comm)["investigation_instruction"]
    assert h["relayed_chunks_in_handoff"] == 0


def test_handoff_reacquisition_overlap_is_consistent(comm):
    o = comm["handoff_reacquisition_overlap"]
    assert o["applicable"] is True
    assert o["chunks_in_all_three"] <= o["chunks_copied_into_handoffs_to_implementer"]
    assert o["chunks_in_all_three"] <= o["chunks_later_reacquired_by_implementer"]
    assert o["chunks_copied_into_handoffs_to_implementer"] <= o["chunks_in_investigator_acquisition"]
    assert o["chunks_in_all_three"] > 0
    assert set(o["by_target"]) <= {"tinylib/palindrome.py", "tests/test_basic.py"}


def test_real_arm_b_pinned_communication_numbers(comm):
    """Pinned so a later change to the windowing cannot silently move them."""
    cs = comm["communication_summary"]
    assert cs["investigator_acquired_chars"] == 3618
    assert cs["investigator_acquired_chunks"] == 80
    assert cs["investigator_report_chars"] == 5633
    assert cs["report_repository_chunks"] == 36
    assert cs["report_chunks_copied_from_investigator_acquisitions"] == 36
    assert cs["report_chars_copied_from_investigator_acquisitions_estimate"] == 1770
    assert cs["report_repository_quote_fraction"] == 0.3142
    assert cs["all_handoffs_chars"] == 17601
    assert cs["all_handoffs_repository_quote_fraction"] == 0.2328

    report = _by_label(comm)["investigation_report"]
    assert report["informative_chunks"] == 73
    assert report["repository_files_quoted"] == {
        "tests/test_basic.py": 14,
        "tinylib/palindrome.py": 7,
        "tinylib/report.py": 12,
        "tinylib/text.py": 3,
    }

    o = comm["handoff_reacquisition_overlap"]
    assert o["chunks_in_investigator_acquisition"] == 80
    assert o["chunks_copied_into_handoffs_to_implementer"] == 36
    assert o["chunks_later_reacquired_by_implementer"] == 36
    assert o["chunks_in_all_three"] == 21
    assert o["implementer_reacquired_chars_in_all_three_estimate"] == 1269
    assert o["by_target"] == {
        "tests/test_basic.py": {"chunks": 14, "chars_estimate": 788, "tool_calls": 1},
        "tinylib/palindrome.py": {"chunks": 7, "chars_estimate": 481, "tool_calls": 1},
    }


def test_handoff_content_overlap_is_deterministic(real_b, comm):
    assert handoff.analyze_run(real_b) == comm


def test_arm_a_has_no_handoffs_and_no_overlap(request):
    d = request.config.rootpath / "runs" / REAL_A
    if not (d / "metadata.json").is_file():
        pytest.skip("real Arm A run not present")
    c = handoff.analyze_run(d)
    assert c["per_handoff"] == []
    assert c["handoff_reacquisition_overlap"]["applicable"] is False


def test_raw_artifacts_unchanged_by_handoff_analysis(real_b):
    def digest(d: Path):
        return {
            str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*"))
            if p.is_file() and "workspace" not in p.relative_to(d).parts
        }

    before = digest(real_b)
    handoff.analyze_run(real_b)
    assert digest(real_b) == before
