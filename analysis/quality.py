"""Stage 2 amendment: mechanical quality metrics Q1-Q5 (PREREGISTRATION 19.8).

Everything comes from the harness's own records of a run: the held-out
verifier output (``pytest -rA``), the visible-test output, the verifier
source, the workspace diff, and the task's analysis-only design metadata. No
LLM judge. Q6 (reliability) is a property of repeated runs and is computed by
``analysis.quality_cost``.

    Q1 held-out success           verifier exit code (SOLVED)
    Q2 held-out test fraction     held-out tests passed / held-out tests defined
    Q3 regression preservation    visible tests, and held-out ``test_r_*`` tests
    Q4 patch scope                files changed, lines added/removed, unexpected files
    Q5 constraint satisfaction    constraints whose ``test_c<k>_*`` tests all passed

Held-out tests are named ``test_c<k>_...`` (constraint k) or ``test_r_...``
(regression). A test the verifier did not report (e.g. a collection error)
counts as not passed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

QUALITY_VERSION = 1

_OUTCOME = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+\S+?::(\S+)")
_SUMMARY_PART = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")
_TEST_DEF = re.compile(r"^def (test_\w+)\s*\(", re.M)
_SIDE_EFFECT = re.compile(r"(^|/)(__pycache__|\.pytest_cache)(/|$)|\.py[co]$")


def defined_tests(source: str) -> list[str]:
    """Module-level test function names, in file order."""
    return _TEST_DEF.findall(source or "")


def parse_pytest(stdout: str) -> dict:
    """Per-test outcomes from ``-rA`` summary lines plus the final counts."""
    per_test: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        m = _OUTCOME.match(line.strip())
        if m:
            per_test[m.group(2).split("[")[0]] = m.group(1).lower()
    counts: dict[str, int] = {}
    for line in reversed((stdout or "").splitlines()):
        parts = _SUMMARY_PART.findall(line)
        if parts and " in " in line:
            for n, kind in parts:
                counts["error" if kind.startswith("error") else kind] = int(n)
            break
    return {"per_test": per_test, "counts": counts}


def _fraction(a: int, b: int) -> Optional[float]:
    return round(a / b, 4) if b else None


def held_out_scores(verifier_stdout: str, verifier_source: str, design: Optional[dict]) -> dict:
    names = defined_tests(verifier_source)
    parsed = parse_pytest(verifier_stdout)
    per = parsed["per_test"]
    if names and per:
        status = {n: ("passed" if per.get(n) == "passed" else per.get(n, "not_reported")) for n in names}
        passed = sum(1 for s in status.values() if s == "passed")
        total, basis = len(names), "per_test"
    else:  # no -rA lines (historical verifier commands): the summary counts only
        c = parsed["counts"]
        passed = c.get("passed", 0)
        total = passed + c.get("failed", 0) + c.get("error", 0)
        status, basis = {}, "summary_counts"
    constraints = {}
    for cid, description in ((design or {}).get("heldout") or {}).get("constraints", {}).items():
        tests = [n for n in names if n.startswith(f"test_{cid.lower()}_")]
        constraints[cid] = {"tests": tests, "satisfied": bool(tests) and all(
            status.get(t) == "passed" for t in tests), "description_hidden": True}
    regression = [n for n in names if n.startswith("test_r_")]
    reg_passed = sum(1 for n in regression if status.get(n) == "passed")
    return {
        "basis": basis,
        "tests_defined": len(names),
        "tests_passed": passed,
        "q2_heldout_fraction": _fraction(passed, total),
        "per_test": status,
        "q5_constraints_satisfied": sum(1 for c in constraints.values() if c["satisfied"]) if constraints else None,
        "q5_constraints_total": len(constraints) if constraints else None,
        "q5_constraint_fraction": _fraction(sum(1 for c in constraints.values() if c["satisfied"]),
                                            len(constraints)) if constraints else None,
        "constraints": {k: {"tests": v["tests"], "satisfied": v["satisfied"]} for k, v in constraints.items()},
        "heldout_regression_passed": reg_passed if regression else None,
        "heldout_regression_total": len(regression) if regression else None,
    }


def visible_scores(visible_stdout: Optional[str]) -> dict:
    if visible_stdout is None:
        return {"available": False}
    parsed = parse_pytest(visible_stdout)
    if parsed["per_test"]:
        passed = sum(1 for s in parsed["per_test"].values() if s == "passed")
        total = len(parsed["per_test"])
    else:
        c = parsed["counts"]
        passed = c.get("passed", 0)
        total = passed + c.get("failed", 0) + c.get("error", 0)
    return {"available": True, "passed": passed, "total": total, "fraction": _fraction(passed, total)}


def patch_scope(diff_text: str, expected_paths=()) -> dict:
    """Q4, report only: smaller is not assumed to be better."""
    files: dict[str, dict] = {}
    current = None
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            current = m.group(2) if m else None
            if current and not _SIDE_EFFECT.search(current):
                files.setdefault(current, {"added": 0, "removed": 0})
            else:
                current = None
        elif current is None or line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            files[current]["added"] += 1
        elif line.startswith("-"):
            files[current]["removed"] += 1
    expected = set(expected_paths or ())
    return {
        "files_changed": len(files),
        "loc_added": sum(f["added"] for f in files.values()),
        "loc_removed": sum(f["removed"] for f in files.values()),
        "files": sorted(files),
        "unexpected_files": sorted(p for p in files if expected and p not in expected),
    }


def _read(path: Path) -> Optional[str]:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def run_quality(run_dir, task=None, design: Optional[dict] = None) -> dict:
    """Q1-Q5 for one run, from its stored artifacts."""
    run_dir = Path(run_dir)
    ver = json.loads(_read(run_dir / "verify" / "verification.json") or "{}")
    if task is None:
        from tasks import registry
        meta = json.loads(_read(run_dir / "metadata.json") or "{}")
        task = registry.get_task(meta.get("task_id"))
    if design is None:
        from tasks import stage2_design
        design = stage2_design.load_design(task.task_id)
    source = task.verifier_path.read_text(encoding="utf-8") if task.verifier_path.is_file() else ""
    held = held_out_scores(_read(run_dir / "verify" / "verifier_stdout.txt") or "", source, design)
    vis = visible_scores(_read(run_dir / "verify" / "visible_tests_stdout.txt"))
    scope = patch_scope(_read(run_dir / "workspace_diff.patch") or "", task.expected_edit_paths)
    reg_parts = [x for x in ((vis.get("passed"), vis.get("total")) if vis.get("available") else (None, None),
                             (held["heldout_regression_passed"], held["heldout_regression_total"]))
                 if x[1]]
    return {
        "version": QUALITY_VERSION,
        "q1_solved": ver.get("solved"),
        "q2_heldout_fraction": held["q2_heldout_fraction"],
        "q3_regression": {
            "visible": vis,
            "heldout_regression_passed": held["heldout_regression_passed"],
            "heldout_regression_total": held["heldout_regression_total"],
            "preserved": all(p == t for p, t in reg_parts) if reg_parts else None,
        },
        "q4_patch_scope": scope,
        "q5_constraint_fraction": held["q5_constraint_fraction"],
        "q5_constraints_satisfied": held["q5_constraints_satisfied"],
        "q5_constraints_total": held["q5_constraints_total"],
        "heldout": held,
        "llm_judge_used": False,
    }
