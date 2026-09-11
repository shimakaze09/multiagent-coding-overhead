"""Amendment 8 (2026-09-11): bash classifier v2.

POST-RUN INSTRUMENTATION DEFECT found during the Stage-0.5 pilot. The frozen
splitter treated the `&` in `2>&1` as a command separator, leaving an
unclassifiable segment `1`, and had no category for `python --version`. Six of
the Implementer's own test/version commands in
20260911T015737Z_settings_list_fields_B_r2 therefore counted as opaque
acquisitions (coverage 0.70, invalid). None of those commands acquired
repository content. No other real run contains either form.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import tools

ROOT = Path(__file__).resolve().parent.parent
T4B = ROOT / "runs" / "20260911T015737Z_settings_list_fields_B_r2"


def test_version_is_pinned():
    assert tools.BASH_CLASSIFIER_VERSION == 2


@pytest.mark.parametrize("cmd,segments", [
    ("python -m pytest -q 2>&1", ["python -m pytest -q 2>&1"]),
    ("pytest >&2", ["pytest >&2"]),
    ("cat x <&3", ["cat x <&3"]),
    ("pytest &>out.txt", ["pytest &>out.txt"]),
    ('cd "D:\\w" && python -m pytest -q 2>&1', ['cd "D:\\w" ', " python -m pytest -q 2>&1"]),
])
def test_ampersand_inside_a_redirection_does_not_split(cmd, segments):
    assert tools.split_bash_segments(cmd) == segments


@pytest.mark.parametrize("cmd,n", [("sleep 1 & cat x", 2), ("a && b", 2), ("a || b", 2), ("a | b", 2)])
def test_real_separators_still_split(cmd, n):
    assert len(tools.split_bash_segments(cmd)) == n


@pytest.mark.parametrize("cmd,cls", [
    ("python -m pytest -q 2>&1", tools.BASH_VERIFICATION),
    ('cd "D:\\w" && python -m pytest -q _scratch_check.py 2>&1', tools.BASH_VERIFICATION),
    ('cd "D:\\w" && python --version', tools.BASH_NON_ACQUISITION),
    ("node --version", tools.BASH_NON_ACQUISITION),
    ("go version", tools.BASH_NON_ACQUISITION),
    ("python -V", tools.BASH_NON_ACQUISITION),
    ("python -v", tools.BASH_UNKNOWN),               # verbose mode, not a version query
    ("python script.py", tools.BASH_UNKNOWN),         # still an admitted gap
    ("grep -rn foo . 2>&1 | head", tools.BASH_SEARCH),
])
def test_classification(cmd, cls):
    assert tools.classify_bash_command(cmd)[0] == cls


@pytest.mark.skipif(not (T4B / "metadata.json").is_file(), reason="Task 4 rerun Arm B not present")
def test_the_affected_run_is_fully_observable_under_v2():
    from analysis import report as R

    r = R.run_report(T4B)
    assert r["acquisition_unknown"] == 0
    assert r["acquisition_coverage"]["acquisition_coverage"] == 1.0
    assert r["validity"] == {"valid": True, "reasons": []}
    assert r["analysis_versions"]["bash_classifier"] == 2


def test_no_other_run_recorded_before_the_amendment_contains_the_affected_forms():
    """The amendment's claim: re-analysis changed no run except T4B. Scope is the
    runs that existed when it was made (named before T4B's timestamp); later runs
    may contain `2>&1` and are simply classified under v2."""
    from analysis import ingest

    for rd in sorted((ROOT / "runs").glob("2026*_r[0-9]")):
        raw = ingest.load_run(rd)
        if raw is None or rd == T4B or rd.name >= T4B.name:
            continue
        for sa in raw["sessions"]:
            for a in ingest.acquisitions_for_session(sa):
                c = a.command or ""
                if a.permission_denied:
                    continue
                assert not (">&" in c or "&>" in c or "--version" in c), (rd.name, c)


def test_no_real_run_has_opaque_acquisitions_under_v2():
    from analysis import ingest

    for rd in sorted((ROOT / "runs").glob("2026*_r[0-9]")):
        raw = ingest.load_run(rd)
        if raw is None:
            continue
        for sa in raw["sessions"]:
            for a in ingest.acquisitions_for_session(sa):
                assert not (a.is_opaque_acquisition and not a.permission_denied), (rd.name, a.command)
