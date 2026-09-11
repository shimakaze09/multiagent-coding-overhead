"""Raw real-run artifacts must stay byte-identical to the frozen manifest.

Run directories are not committed; this checks whichever listed runs are present
locally and skips the rest.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

import pytest

MANIFEST = "run_manifests/raw_run_checksums.sha256"
PAIR2_MANIFEST = "run_manifests/pair2_raw_run_checksums.sha256"
PAIR2_RUNS = {
    "20260911T004656Z_cart_invoice_rounding_A_r1": 15,
    "20260911T004825Z_cart_invoice_rounding_B_r1": 41,
}


def _manifest(root: Path, name: str = MANIFEST) -> dict[str, str]:
    out = {}
    for line in (root / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, path = line.split(None, 1)
            out[path.strip()] = digest
    return out


def _runs(entries: dict[str, str]) -> dict[str, list[str]]:
    runs = defaultdict(list)
    for path in entries:
        runs[path.split("/")[1]].append(path)
    return runs


def test_manifest_covers_the_three_pre_freeze_real_runs(request):
    runs = _runs(_manifest(request.config.rootpath))
    assert {k: len(v) for k, v in runs.items()} == {
        "20260910T122445Z_palindrome_punctuation_A_r1": 12,
        "20260910T125941Z_palindrome_punctuation_A_r1": 12,
        "20260910T230448Z_palindrome_punctuation_B_r1": 38,
    }


def test_no_workspace_files_are_in_the_manifest(request):
    assert not any("/workspace/" in p for p in _manifest(request.config.rootpath))


@pytest.mark.parametrize("run", [
    "20260910T122445Z_palindrome_punctuation_A_r1",
    "20260910T125941Z_palindrome_punctuation_A_r1",
    "20260910T230448Z_palindrome_punctuation_B_r1",
])
def test_present_raw_runs_are_byte_identical_to_the_manifest(request, run):
    root = request.config.rootpath
    run_dir = root / "runs" / run
    if not run_dir.is_dir():
        pytest.skip(f"{run} not present locally")
    entries = {p: d for p, d in _manifest(root).items() if p.split("/")[1] == run}
    for rel, digest in entries.items():
        assert hashlib.sha256((root / rel).read_bytes()).hexdigest() == digest, rel
    present = {
        p.relative_to(root).as_posix()
        for p in run_dir.rglob("*")
        if p.is_file() and "workspace" not in p.relative_to(run_dir).parts
    }
    assert present == set(entries), "a raw file was added to or removed from the run"


# --------------------------------------------------------------------------
# Pair 2 (second controlled A/B pair) - immutable historical data
# --------------------------------------------------------------------------


def test_pair2_manifest_covers_exactly_the_two_pair2_runs(request):
    entries = _manifest(request.config.rootpath, PAIR2_MANIFEST)
    assert {k: len(v) for k, v in _runs(entries).items()} == PAIR2_RUNS
    assert not any("/workspace/" in p for p in entries)


def test_pair2_manifest_does_not_overlap_the_pre_freeze_manifest(request):
    root = request.config.rootpath
    assert not set(_manifest(root)) & set(_manifest(root, PAIR2_MANIFEST))


@pytest.mark.parametrize("run", sorted(PAIR2_RUNS))
def test_present_pair2_runs_are_byte_identical_to_the_manifest(request, run):
    root = request.config.rootpath
    run_dir = root / "runs" / run
    if not run_dir.is_dir():
        pytest.skip(f"{run} not present locally")
    entries = {p: d for p, d in _manifest(root, PAIR2_MANIFEST).items() if p.split("/")[1] == run}
    for rel, digest in entries.items():
        assert hashlib.sha256((root / rel).read_bytes()).hexdigest() == digest, rel
    present = {
        p.relative_to(root).as_posix()
        for p in run_dir.rglob("*")
        if p.is_file() and "workspace" not in p.relative_to(run_dir).parts
    }
    assert present == set(entries), "a raw file was added to or removed from the run"


def test_pair2_result_record_is_the_frozen_pair_summary(request):
    text = (request.config.rootpath / "results" / "pair2_cart_invoice_rounding_report.txt"
            ).read_text(encoding="utf-8")
    assert "=== Stage 0 controlled pair: cart_invoice_rounding ===" in text
    for run in PAIR2_RUNS:
        assert run in text
    assert text.count("[PASS]") == 6 and "[FAIL]" not in text
