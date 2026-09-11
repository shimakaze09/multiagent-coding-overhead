"""handoff_repository_overlap_v2: prospective, Stage 0.5 (PREREGISTRATION section 15).

WHY v2 EXISTS. Pair 2 showed that the frozen v1 metric
(`handoff_repository_overlap_v1`: indentation-insensitive 3-line content windows,
analysis/handoff.py) undercounts an excerpt when the author keeps selected source
lines but drops the lines between them. The Investigator quoted 12 source lines,
11 of them verbatim, but left out adjacent docstring and validation lines, and
v1 matched only 2 windows.

v1 is unchanged. It remains the preregistered H3 metric for Pair 1 and Pair 2,
and v2 never replaces or reinterprets those results. v2 applies prospectively to
the Stage-0.5 pilot. On a run listed in run_manifests/ (historical), any v2
figure is post-hoc and exploratory, and reports label it so.

DEFINITION (line level, deliberately conservative; no embeddings, no LLM):

* normalize a line: strip a Read-tool line-number prefix (`NNN\\t`, `NNN|`),
  then surrounding whitespace, then ONE leading diff/quote marker (`-`, `+`,
  `>`) that is followed by whitespace, then whitespace again;
* a repository line is SUBSTANTIVE if its normalized text has at least
  V2_MIN_ALNUM alphanumeric characters AND occurs in exactly one repository file
  at the base commit. A line that occurs in two or more files (imports,
  boilerplate) is COMMON and never counts;
* a text QUOTES file F if it contains at least V2_MIN_LINES_PER_FILE distinct
  substantive lines of F. Only lines of files meeting that threshold count, so
  one coincidental line cannot register as a quotation;
* tool results are acquisitions, not quotations, so no per-file threshold
  applies to them. Every substantive repository line they contain counts.

Characters are exact: the summed length of the distinct matched normalized
lines.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from harness import tools

OVERLAP_V2_VERSION = 1
V2_MIN_ALNUM = 12
V2_MIN_LINES_PER_FILE = 2

UPSTREAM_ROLE = "investigator"
DOWNSTREAM_ROLE = "implementer"

_ALNUM = re.compile(r"[A-Za-z0-9]")
_MARKER = re.compile(r"^[-+>]\s+")


def normalize_v2(line: str) -> str:
    s = tools._normalize_line(line).strip()
    m = _MARKER.match(s)
    if m:
        s = s[m.end():].strip()
    return s


def _alnum(s: str) -> int:
    return len(_ALNUM.findall(s))


def repository_line_index(repo_texts: dict[str, str]) -> dict[str, str]:
    """normalized substantive line -> the ONE repository file containing it."""
    owners: dict[str, set[str]] = defaultdict(set)
    for rel, text in repo_texts.items():
        for line in (text or "").splitlines():
            n = normalize_v2(line)
            if _alnum(n) >= V2_MIN_ALNUM:
                owners[n].add(rel)
    return {n: next(iter(fs)) for n, fs in owners.items() if len(fs) == 1}


def repository_lines_in(text: Optional[str], index: dict[str, str]) -> dict[str, set[str]]:
    """file -> distinct substantive repository lines present in `text` (no threshold)."""
    per: dict[str, set[str]] = defaultdict(set)
    for line in (text or "").splitlines():
        n = normalize_v2(line)
        f = index.get(n)
        if f:
            per[f].add(n)
    return dict(per)


def quoted_lines(text: Optional[str], index: dict[str, str],
                 min_lines_per_file: int = V2_MIN_LINES_PER_FILE) -> dict[str, set[str]]:
    """file -> quoted substantive lines, only for files meeting the threshold."""
    return {f: ls for f, ls in repository_lines_in(text, index).items()
            if len(ls) >= min_lines_per_file}


def _chars(per_file: dict[str, set[str]]) -> int:
    return sum(len(l) for ls in per_file.values() for l in ls)


def _flat(per_file: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {(f, l) for f, ls in per_file.items() for l in ls}


def analyze_loaded(raw: dict, acquisitions_by_session: dict,
                   repo_texts: dict[str, str], repo_availability: str) -> dict:
    run_dir = Path(raw["run_dir"])
    sessions = raw["sessions"]
    handoffs = (raw.get("summary") or {}).get("handoffs") or []
    index = repository_line_index(repo_texts)

    bodies = {
        h["index"]: (run_dir / h["path"]).read_text(encoding="utf-8")
        if (run_dir / h.get("path", "")).is_file() else ""
        for h in handoffs
    }

    per_handoff = []
    for h in handoffs:
        body = bodies[h["index"]]
        q = quoted_lines(body, index)
        chars = _chars(q)
        per_handoff.append({
            "index": h["index"],
            "sender": h.get("sender"),
            "recipient": h.get("recipient"),
            "label": h.get("label"),
            "handoff_chars": len(body),
            "repository_lines_v2": sum(len(v) for v in q.values()),
            "repository_chars_v2": chars,
            "quote_fraction_v2": round(chars / len(body), 4) if body else None,
            "files_quoted_v2": {f: len(v) for f, v in sorted(q.items())},
        })

    report = next((p for p in per_handoff if p["label"] == "investigation_report"), None)
    total_chars = sum(p["handoff_chars"] for p in per_handoff)
    total_v2 = sum(p["repository_chars_v2"] for p in per_handoff)

    return {
        "metric": "handoff_repository_overlap_v2",
        "version": OVERLAP_V2_VERSION,
        "parameters": {
            "min_alnum_per_line": V2_MIN_ALNUM,
            "min_distinct_lines_per_file": V2_MIN_LINES_PER_FILE,
            "common_line_rule": "occurs in >= 2 repository files at the base commit",
            "normalization": "read-prefix, whitespace, one leading -/+/> marker",
        },
        "repository_content_source": repo_availability,
        "substantive_repository_lines": len(index),
        "per_handoff": per_handoff,
        "summary": {
            "report_repository_lines_v2": report["repository_lines_v2"] if report else None,
            "report_repository_chars_v2": report["repository_chars_v2"] if report else None,
            "report_quote_fraction_v2": report["quote_fraction_v2"] if report else None,
            "all_handoffs_repository_chars_v2": total_v2,
            "all_handoffs_quote_fraction_v2": round(total_v2 / total_chars, 4) if total_chars else None,
        },
        "handoff_reacquisition_overlap_v2": _reacquisition_overlap_v2(
            sessions, acquisitions_by_session, handoffs, bodies, index),
    }


def _role_texts(sessions, acquisitions_by_session, role) -> list[tuple[object, str]]:
    results = {c.tool_use_id: c.result_text or "" for sa in sessions for c in sa.parsed.tool_calls}
    out = []
    for sa in sessions:
        if sa.invocation.get("role") != role:
            continue
        for a in acquisitions_by_session.get(sa.session_key, []):
            if a.is_acquisition and a.completed and not a.permission_denied:
                out.append((a, results.get(a.tool_use_id, "")))
    return out


def _reacquisition_overlap_v2(sessions, acquisitions_by_session, handoffs, bodies, index) -> dict:
    up = _role_texts(sessions, acquisitions_by_session, UPSTREAM_ROLE)
    down = _role_texts(sessions, acquisitions_by_session, DOWNSTREAM_ROLE)
    if not up or not any(sa.invocation.get("role") == DOWNSTREAM_ROLE for sa in sessions):
        return {"applicable": False, "reason": "no investigator/implementer pair in this run"}
    I: set = set()
    for _, text in up:
        I |= _flat(repository_lines_in(text, index))
    H: set = set()
    for h in handoffs:
        if h.get("recipient") == DOWNSTREAM_ROLE:
            H |= _flat(quoted_lines(bodies[h["index"]], index))
    R: set = set()
    for _, text in down:
        R |= _flat(repository_lines_in(text, index))
    IHR = I & H & R
    by_file: dict[str, dict] = {}
    for f, l in IHR:
        slot = by_file.setdefault(f, {"lines": 0, "chars": 0})
        slot["lines"] += 1
        slot["chars"] += len(l)
    return {
        "applicable": True,
        "lines_in_investigator_acquisition": len(I),
        "lines_copied_into_handoffs_to_implementer": len(I & H),
        "lines_later_reacquired_by_implementer": len(I & R),
        "lines_in_all_three": len(IHR),
        "chars_in_all_three": sum(len(l) for _, l in IHR),
        "by_file": dict(sorted(by_file.items())),
    }
