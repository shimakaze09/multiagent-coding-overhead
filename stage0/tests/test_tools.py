"""Milestone 8 tests: tool/acquisition classification and coverage.

Bash is the honest weak point: these tests pin down what the classifier does and
does not claim.
"""

from __future__ import annotations

import pytest

from harness import tools


def _call(name, tool_input, result="a\nb\nc\nd"):
    return tools.classify_tool_call(
        tool_use_id="t",
        tool_name=name,
        tool_input=tool_input,
        result_text=result,
        agent_id="solo",
        session_key="01_solo",
        start_ts=None,
        end_ts=None,
        start_line=1,
        end_line=2,
        is_error=False,
    )


# --------------------------------------------------------------------------
# Structured tools
# --------------------------------------------------------------------------


def test_read_is_a_structured_read():
    a = _call("Read", {"file_path": "tinylib/text.py"})
    assert a.acquisition_class == tools.STRUCTURED_READ
    assert a.confidence == "high"
    assert a.target_path == "tinylib/text.py"
    assert a.is_acquisition


def test_windows_paths_are_normalized():
    a = _call("Read", {"file_path": r"tinylib\text.py"})
    assert a.target_path == "tinylib/text.py"


def test_grep_and_glob_are_structured_searches():
    for name in ("Grep", "Glob"):
        a = _call(name, {"pattern": "normalize", "path": "."})
        assert a.acquisition_class == tools.STRUCTURED_SEARCH
        assert a.query == "normalize"


def test_edit_and_write_are_not_acquisition():
    for name in ("Edit", "Write"):
        a = _call(name, {"file_path": "x.py"})
        assert a.acquisition_class == tools.STRUCTURED_EDIT
        assert not a.is_acquisition


def test_unknown_tool_is_flagged_not_guessed():
    a = _call("SomeFutureTool", {"x": 1})
    assert a.acquisition_class == tools.UNKNOWN_TOOL
    assert a.confidence == "none"
    assert a.is_opaque_acquisition


def test_search_results_have_paths_extracted():
    a = _call("Grep", {"pattern": "def"}, result="tinylib/text.py:3:def normalize(t):")
    assert "tinylib/text.py" in a.result_paths


def test_read_results_do_not_get_speculative_path_extraction():
    a = _call("Read", {"file_path": "x.py"}, result="import tinylib.text\n\nfoo\nbar")
    assert a.result_paths == []


# --------------------------------------------------------------------------
# Bash classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ("cat tinylib/text.py", tools.CLASSIFIED_BASH_READ),
        ("head -n 40 tinylib/text.py", tools.CLASSIFIED_BASH_READ),
        ("tail -20 log.txt", tools.CLASSIFIED_BASH_READ),
        ("sed -n '10,40p' tinylib/text.py", tools.CLASSIFIED_BASH_READ),
        ("git show HEAD:tinylib/text.py", tools.BASH_GIT_INSPECTION),
        ("git diff", tools.BASH_GIT_INSPECTION),
        ("git grep normalize", tools.BASH_GIT_INSPECTION),
        ("git log -S normalize", tools.BASH_GIT_INSPECTION),
        ("git status", tools.BASH_GIT_INSPECTION),
        ("grep -rn normalize .", tools.BASH_SEARCH),
        ("rg 'def is_palindrome'", tools.BASH_SEARCH),
        ("awk '/def/ {print}' x.py", tools.BASH_SEARCH),
        ("find . -name '*.py'", tools.BASH_DIRECTORY_LISTING),
        ("ls -R tinylib", tools.BASH_DIRECTORY_LISTING),
        ("tree tinylib", tools.BASH_DIRECTORY_LISTING),
        ("python -m pytest tests -q", tools.BASH_VERIFICATION),
        ("pytest -q", tools.BASH_VERIFICATION),
        ("npm test", tools.BASH_VERIFICATION),
        ("mypy tinylib", tools.BASH_VERIFICATION),
        ("python -c \"import tinylib; print(tinylib)\"", tools.BASH_VERIFICATION),
        ("make", tools.BASH_BUILD),
        ("gcc -c foo.c", tools.BASH_BUILD),
        ("cargo build --release", tools.BASH_BUILD),
        ("make test", tools.BASH_VERIFICATION),
        ("rm -rf build", tools.BASH_WORKSPACE_MANAGEMENT),
        ("sed -i 's/a/b/' f.py", tools.BASH_WORKSPACE_MANAGEMENT),
        ("git commit -m x", tools.BASH_WORKSPACE_MANAGEMENT),
        ("git checkout main", tools.BASH_WORKSPACE_MANAGEMENT),
        ("mkdir -p out", tools.BASH_WORKSPACE_MANAGEMENT),
        ("pwd", tools.BASH_NON_ACQUISITION),
        ("ls", tools.BASH_NON_ACQUISITION),
        ("stat tinylib/text.py", tools.BASH_NON_ACQUISITION),
        ("./scripts/mystery-tool --go", tools.BASH_UNKNOWN),
        ("python tools/whatever.py", tools.BASH_UNKNOWN),
    ],
)
def test_bash_commands_are_classified(command, expected):
    klass, _segments = tools.classify_bash_command(command)
    assert klass == expected, f"{command!r} -> {klass}"


def test_pipeline_takes_the_most_information_bearing_class():
    klass, segs = tools.classify_bash_command("cat text.py | grep normalize | wc -l")
    assert klass == tools.BASH_SEARCH
    assert tools.BASH_READ in segs
    assert tools.NEUTRAL in segs, "wc reads stdin; it is a neutral filter"


# --------------------------------------------------------------------------
# Neutral stream filters (regression: a real run was mis-binned because of one)
# --------------------------------------------------------------------------


def test_a_trailing_sort_does_not_make_a_find_unclassifiable():
    """Observed in the first real Arm A run:
        cd "<ws>" && find . -type f -not -path "*/.git/*" | sort
    was binned as unclassified purely because of the `sort`."""
    klass, segs = tools.classify_bash_command(
        'cd "/ws" && find . -type f -not -path "*/.git/*" | sort'
    )
    assert klass == tools.BASH_DIRECTORY_LISTING
    assert segs == [
        tools.BASH_NON_ACQUISITION,
        tools.BASH_DIRECTORY_LISTING,
        tools.NEUTRAL,
    ]


def test_a_trailing_head_does_not_make_a_find_unclassifiable():
    """Also observed: find ... | head -50"""
    klass, _ = tools.classify_bash_command('find "/ws" -iname "*tinylib*" | head -50')
    assert klass == tools.BASH_DIRECTORY_LISTING


@pytest.mark.parametrize("cmd", ["sort", "wc -l", "uniq", "cut -d: -f1", "jq .", "tr a b"])
def test_neutral_filters_alone_acquire_nothing(cmd):
    klass, _ = tools.classify_bash_command(cmd)
    assert klass == tools.BASH_NON_ACQUISITION


def test_head_with_a_file_operand_is_a_read_not_a_filter():
    assert tools.classify_bash_segment("head -50 tinylib/text.py") == tools.BASH_READ
    assert tools.classify_bash_segment("head -50") == tools.NEUTRAL
    assert tools.classify_bash_segment("cat") == tools.NEUTRAL
    assert tools.classify_bash_segment("cat foo.py") == tools.BASH_READ


def test_sed_range_print_needs_a_file_to_be_a_read():
    assert tools.classify_bash_segment("sed -n '10,40p' x.py") == tools.BASH_READ
    assert tools.classify_bash_segment("sed -n '10,40p'") == tools.NEUTRAL


def test_neutral_is_never_a_reported_category():
    """It may appear in the per-segment audit trail, never as the verdict."""
    assert tools.NEUTRAL not in tools.ACQUISITION_CLASSES
    assert tools.NEUTRAL not in tools.OPAQUE_ACQUISITION_CLASSES
    assert tools.NEUTRAL not in tools.NON_ACQUISITION_CLASSES
    assert tools.NEUTRAL not in tools._COVERAGE_COUNT_ATTR


# --------------------------------------------------------------------------
# Permission-denied calls
# --------------------------------------------------------------------------


def _denied(name, tool_input, result="Permission for this tool use was denied."):
    return tools.classify_tool_call(
        tool_use_id="t",
        tool_name=name,
        tool_input=tool_input,
        result_text=result,
        agent_id="solo",
        session_key="01_solo",
        start_ts=None,
        end_ts=None,
        start_line=1,
        end_line=2,
        is_error=True,
        permission_denied=True,
    )


def test_a_denied_call_is_reclassified_as_tool_denied():
    a = _denied("Bash", {"command": "python -m pytest tests -q"})
    assert a.acquisition_class == tools.TOOL_DENIED
    assert a.permission_denied is True
    assert a.attempted_class == tools.BASH_VERIFICATION, "what it would have been"


def test_a_denied_read_is_not_counted_as_a_read():
    a = _denied("Read", {"file_path": "tinylib/text.py"})
    assert a.acquisition_class == tools.TOOL_DENIED
    assert a.is_acquisition is False
    assert a.is_acquisition_candidate is False
    assert a.attempted_class == tools.STRUCTURED_READ


def test_denied_calls_are_excluded_from_the_coverage_denominator():
    acqs = [
        _call("Read", {"file_path": "a.py"}),
        _denied("Bash", {"command": "./mystery-tool"}),
    ]
    cov = tools.coverage(acqs)
    assert cov.acquisition_candidates == 1, "the denied call could acquire nothing"
    assert cov.denied_calls == 1
    assert cov.acquisition_coverage == 1.0
    assert cov.bash_unknown == 0


def test_an_undenied_opaque_bash_still_lowers_coverage():
    """Denial handling must not become a loophole for genuine opacity."""
    acqs = [_call("Read", {"file_path": "a.py"}), _call("Bash", {"command": "./mystery"})]
    cov = tools.coverage(acqs)
    assert cov.acquisition_candidates == 2
    assert cov.acquisition_unknown == 1
    assert cov.acquisition_coverage == 0.5


def test_unclassifiable_segment_dominates_so_coverage_is_never_overclaimed():
    klass, _ = tools.classify_bash_command("cat a.py && ./unknown-binary")
    assert klass == tools.UNCLASSIFIED_BASH


def test_env_prefixes_and_wrappers_are_stripped():
    assert tools.classify_bash_command("FOO=1 cat x.py")[0] == tools.CLASSIFIED_BASH_READ
    assert tools.classify_bash_command("time grep -r x .")[0] == tools.CLASSIFIED_BASH_SEARCH


def test_unbalanced_quotes_do_not_crash():
    klass, _ = tools.classify_bash_command("grep \"unclosed .")
    assert klass == tools.CLASSIFIED_BASH_SEARCH


def test_empty_command_is_metadata_not_unclassified():
    assert tools.classify_bash_command("")[0] == tools.BASH_METADATA


def test_bash_call_records_segment_classes_for_audit():
    a = _call("Bash", {"command": "cat a.py | grep x"})
    assert a.bash_segment_classes == [tools.CLASSIFIED_BASH_READ, tools.CLASSIFIED_BASH_SEARCH]
    assert a.command == "cat a.py | grep x"


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


def test_coverage_counts_only_acquisition_candidates():
    acqs = [
        _call("Read", {"file_path": "a.py"}),           # candidate, classified
        _call("Grep", {"pattern": "x"}),                # candidate, classified
        _call("Edit", {"file_path": "a.py"}),           # non-acquisition
        _call("Bash", {"command": "pytest -q"}),        # non-acquisition (verify)
        _call("Bash", {"command": "mkdir out"}),        # non-acquisition (workspace)
        _call("Bash", {"command": "pwd"}),              # non-acquisition (metadata)
        _call("Bash", {"command": "make"}),             # non-acquisition (build)
    ]
    cov = tools.coverage(acqs)
    assert cov.acquisition_candidates == 2
    assert cov.non_acquisition_calls == 5
    assert cov.acquisition_coverage == 1.0
    assert cov.meets_minimum is True


def test_a_non_acquisition_bash_command_does_not_reduce_observability():
    """The defect the first real run exposed: unrelated shell activity must not
    depress a number that measures attributable *acquisition*."""
    base = [_call("Read", {"file_path": "a.py"})]
    with_noise = base + [
        _call("Bash", {"command": "pytest -q"}),
        _call("Bash", {"command": "mkdir -p build"}),
        _call("Bash", {"command": "git commit -m x"}),
    ]
    assert tools.coverage(base).acquisition_coverage == 1.0
    assert tools.coverage(with_noise).acquisition_coverage == 1.0


def test_opaque_bash_lowers_coverage():
    acqs = [_call("Read", {"file_path": "a.py"})] * 9
    acqs.append(_call("Bash", {"command": "./mystery"}))
    cov = tools.coverage(acqs)
    assert cov.bash_unknown == 1
    assert cov.acquisition_unknown == 1
    assert cov.acquisition_coverage == pytest.approx(0.9)
    assert cov.meets_minimum is True  # exactly at the 0.90 threshold


def test_coverage_formula_is_recorded_in_every_report():
    cov = tools.coverage([_call("Read", {"file_path": "a.py"})])
    assert cov.formula_version == tools.COVERAGE_FORMULA_VERSION
    assert "acquisition_classified" in cov.formula
    assert "permission-denied" in cov.formula


def test_unknown_tool_always_fails_the_coverage_gate():
    acqs = [_call("Read", {"file_path": "a.py"})] * 99
    acqs.append(_call("MysteryTool", {}))
    cov = tools.coverage(acqs)
    assert cov.acquisition_coverage == pytest.approx(0.99)
    assert cov.meets_minimum is False, "any unknown tool must fail the gate"


def test_coverage_is_none_when_nothing_acquisition_capable_happened():
    cov = tools.coverage([_call("Edit", {"file_path": "a.py"})])
    assert cov.acquisition_coverage is None
    assert cov.meets_minimum is None


def test_minimum_coverage_default_is_documented_value():
    assert tools.coverage([]).minimum_required == 0.90


# --------------------------------------------------------------------------
# Content identity
# --------------------------------------------------------------------------


def test_identical_content_hashes_identically():
    assert tools.content_sha("abc") == tools.content_sha("abc")


def test_different_content_hashes_differently():
    assert tools.content_sha("abc") != tools.content_sha("abd")


def test_no_result_means_no_identity_not_a_hash_of_empty():
    a = tools.classify_tool_call(
        tool_use_id="t", tool_name="Read", tool_input={"file_path": "x"},
        result_text=None, agent_id="a", session_key="01_a", start_ts=None,
        end_ts=None, start_line=1, end_line=None, is_error=None,
    )
    assert a.result_sha is None
    assert a.completed is False


def test_shingles_of_short_content_still_produce_one_identity():
    assert len(tools.shingles("only one line")) == 1
    assert tools.shingles("") == []
    assert tools.shingles(None) == []


# --------------------------------------------------------------------------
# Handoff sizing
# --------------------------------------------------------------------------


def test_handoff_size_reports_exact_counts_and_labels_estimates():
    s = tools.handoff_size("hello world\nsecond line")
    assert s["chars"] == 23
    assert s["utf8_bytes"] == 23
    assert s["words"] == 4
    assert s["lines"] == 2
    assert s["estimated_tokens_availability"] == "estimated"
    assert "heuristic" in s["estimated_tokens_method"]


def test_estimated_tokens_is_never_called_tokens():
    s = tools.handoff_size("some text")
    assert "tokens" not in s or "estimated_tokens" in s
    assert "tokens" not in [k for k in s if k == "tokens"]
    assert "estimated_tokens" in s


def test_multibyte_bytes_differ_from_chars():
    s = tools.handoff_size("café →")
    assert s["utf8_bytes"] > s["chars"]


def test_empty_handoff_is_zero_not_error():
    s = tools.handoff_size("")
    assert s["chars"] == 0 and s["estimated_tokens"] == 0


# --------------------------------------------------------------------------
# Quote-aware segment splitting
# --------------------------------------------------------------------------


def test_separators_inside_quotes_do_not_split_a_command():
    """Regression from the first real Arm A run: a `python -c` script body was
    chopped on `;` and newlines, producing bogus bash_unknown segments."""
    cmd = 'python -c "import tinylib; print(tinylib.normalize(\'A\'))"'
    assert tools.split_bash_segments(cmd) == [cmd]
    assert tools.classify_bash_command(cmd)[0] == tools.BASH_VERIFICATION


def test_newlines_inside_a_quoted_script_do_not_split():
    cmd = 'python -c "\nfrom tinylib import is_palindrome\nprint(is_palindrome(\'x\'))\n"'
    assert len(tools.split_bash_segments(cmd)) == 1
    assert tools.classify_bash_command(cmd)[0] == tools.BASH_VERIFICATION


def test_real_denied_command_from_arm_a_classifies_as_verification():
    """The exact command Claude Code refused in the first real Arm A run."""
    cmd = (
        'cd "/d/dev/subagent test/stage0/runs/x/workspace" '
        '&& python -m pytest tests/ -q '
        '&& python -c "\nfrom tinylib import is_palindrome\n'
        "print(is_palindrome('A man, a plan, a canal: Panama!'))\n\""
    )
    klass, segs = tools.classify_bash_command(cmd)
    assert klass == tools.BASH_VERIFICATION
    assert tools.BASH_UNKNOWN not in segs, "the script body must not create unknowns"


def test_real_separators_outside_quotes_still_split():
    segs = tools.split_bash_segments('cd "/a b" && find . | sort ; pwd')
    assert len(segs) == 4
    assert segs[0].strip() == 'cd "/a b"'


def test_escaped_quote_inside_a_double_quoted_string_is_handled():
    cmd = 'python -c "print(\\"No \'x\' in Nixon\\")"'
    assert tools.split_bash_segments(cmd) == [cmd]


def test_unbalanced_quote_does_not_hang_or_crash():
    segs = tools.split_bash_segments('grep "unclosed && pwd')
    assert segs and isinstance(segs, list)
    assert tools.classify_bash_command('grep "unclosed && pwd')[0] == tools.BASH_SEARCH


def test_empty_and_whitespace_commands():
    assert tools.split_bash_segments("") == []
    assert tools.split_bash_segments("   ") == []
    assert tools.classify_bash_command("   ")[0] == tools.BASH_NON_ACQUISITION
