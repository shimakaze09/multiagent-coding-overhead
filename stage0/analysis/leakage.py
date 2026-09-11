"""Held-out CONTENT exposure check: prospective, Stage 0.5 (PREREGISTRATION section 15).

Complements the frozen path/marker check (`isolation.py`, v1, unchanged, the
basis of the Pair-2 exclusion rule). That check sees a held-out file only when
its path or a marker appears. Building the Stage-0.5 fixtures showed it cannot
see held-out CONTENT that reaches an agent without them, e.g. printed by a
command whose own text carries no marker.

Rule, conservative on purpose:

* protected lines are the substantive lines (v2 normalization, at least
  V2_MIN_ALNUM alphanumeric characters) of the task's held-out verifier that
  appear NEITHER in the repository at the base commit (visible tests included)
  NOR in the task statement, both of which agents legitimately see;
* a tool RESULT exposes held-out content if it contains at least
  MIN_PROTECTED_LINES distinct protected lines. Tool inputs are not checked:
  an agent writing an assertion that happens to match the verifier is not
  leakage, and one coincidental line is not evidence.

Detection only: nothing is prevented. A Stage-0.5 run with
`breach_suspected = true` is excluded, like an isolation breach.
"""

from __future__ import annotations

from typing import Optional

from analysis import overlap_v2

LEAKAGE_CONTENT_VERSION = 1
MIN_PROTECTED_LINES = 3


def protected_lines(verifier_text: str, repo_texts: dict[str, str], statement: str) -> set[str]:
    seen: set[str] = set()
    for text in list(repo_texts.values()) + [statement or ""]:
        for line in (text or "").splitlines():
            seen.add(overlap_v2.normalize_v2(line))
    out = set()
    for line in (verifier_text or "").splitlines():
        n = overlap_v2.normalize_v2(line)
        if overlap_v2._alnum(n) >= overlap_v2.V2_MIN_ALNUM and n not in seen:
            out.add(n)
    return out


def check_texts(results: list[tuple[str, str, Optional[str]]], protected: set[str]) -> list[dict]:
    """results: (session_key, tool_use_id, result_text)."""
    hits = []
    for session_key, tool_use_id, text in results:
        found = {overlap_v2.normalize_v2(l) for l in (text or "").splitlines()} & protected
        if len(found) >= MIN_PROTECTED_LINES:
            hits.append({"session_key": session_key, "tool_use_id": tool_use_id,
                         "protected_lines_matched": len(found),
                         "examples": sorted(found)[:3]})
    return hits


def protected_for_run(task_id: Optional[str], repo_texts: dict[str, str]):
    """`(task, protected lines)` for one run; the lines are None when the task
    verifier or the base-commit repository is not available. Same inputs and
    same rule as `check_run`, exposed so the amendment-11 provenance layer sees
    exactly the set that was matched."""
    from tasks import registry

    try:
        task = registry.get_task(task_id) if task_id else None
        verifier_text = task.verifier_path.read_text(encoding="utf-8") if task else None
    except (registry.TaskError, OSError):
        task, verifier_text = None, None
    if task is None or verifier_text is None or not repo_texts:
        return task, None
    return task, protected_lines(verifier_text, repo_texts, task.statement)


def check_run(raw: dict, task_id: Optional[str], repo_texts: dict[str, str]) -> dict:
    task, protected = protected_for_run(task_id, repo_texts)
    if protected is None:
        return {"version": LEAKAGE_CONTENT_VERSION, "available": False,
                "reason": "task verifier or base-commit repository not available",
                "breach_suspected": None}
    results = [(sa.session_key, c.tool_use_id, c.result_text)
               for sa in raw["sessions"] for c in sa.parsed.tool_calls]
    hits = check_texts(results, protected)
    return {
        "version": LEAKAGE_CONTENT_VERSION,
        "available": True,
        "protected_lines": len(protected),
        "min_protected_lines_per_result": MIN_PROTECTED_LINES,
        "exposures": hits,
        "breach_suspected": bool(hits),
        "prevention": "none - detection from raw telemetry only",
    }
