"""Provenance of a held-out CONTENT match (PREREGISTRATION amendment 11).

The frozen content check (`leakage.py`, v1, unchanged) reports a *suspected*
exposure whenever a tool RESULT carries at least MIN_PROTECTED_LINES lines of
the held-out verifier that are in neither the repository nor the statement.
Calibration showed that this fires on an agent that writes its own scratch
test, then reads it back: the lines are the agent's own, not the verifier's.

This module decides, mechanically and from raw telemetry only, whether a match
is that false positive. A match is `self_authored_false_positive` only when ALL
six conditions of amendment 11 hold:

1. the matched text appeared in an ordinary file inside the run workspace;
2. that file was written by the agent earlier in the same run (a file-writing
   tool call whose target resolves inside the workspace);
3. no protected path was read before the text appeared (no marker, and no
   out-of-workspace access, anywhere before it);
4. no held-out / reference / design file was accessed in the run;
5. no out-of-workspace source supplied the matched content - for every matched
   line the agent's own write comes strictly before the line's first
   appearance in any tool RESULT;
6. the provenance is reconstructible: the isolation check is available, the
   streams parsed completely, and every matched line has an identified author.

Anything else - a line whose first appearance is in a result, an author that
cannot be identified, an unresolvable path, incomplete telemetry - is
`unexplained` and remains a hard stop. The module never clears a run: it
classifies matches, and only the run's own validity decides what follows.

PROSPECTIVE. `applies_to(metadata)` is true only for runs whose metadata
records the rule (`exposure_provenance_rule.version`), which the harness began
writing when amendment 11 was committed. Attempts that ran before it keep the
classification they were given, including `s2t11_docpipe` repeat 3.
"""

from __future__ import annotations

from typing import Iterable, Optional

from analysis import isolation, overlap_v2

EXPOSURE_PROVENANCE_VERSION = 1
RULE_ID = "amendment_11_self_authored_content_match"
METADATA_KEY = "exposure_provenance_rule"

# Tool calls that put text into a file. A Bash call can also write a file, but
# its target cannot be identified mechanically from the command alone, so text
# that first appears in one is `unexplained`, not self-authored.
WRITE_TOOLS = ("write", "edit", "multiedit", "notebookedit")
# Where a write tool carries the text it is about to store.
_TEXT_KEYS = ("content", "new_string", "new_source", "text")
_EDIT_LIST_KEYS = ("edits",)

VERDICT_SELF_AUTHORED = "self_authored_false_positive"
VERDICT_UNEXPLAINED = "unexplained"


def rule_record() -> dict:
    """What the harness writes into a new run's metadata."""
    return {
        "version": EXPOSURE_PROVENANCE_VERSION,
        "rule_id": RULE_ID,
        "amendment": 11,
        "prospective_from": "amendment 11 commit",
    }


def applies_to(metadata: Optional[dict]) -> bool:
    rec = (metadata or {}).get(METADATA_KEY) or {}
    return rec.get("version") == EXPOSURE_PROVENANCE_VERSION


def _norm_lines(text: Optional[str]) -> set[str]:
    return {overlap_v2.normalize_v2(line) for line in (text or "").splitlines()}


def _authored_text(inp: dict) -> str:
    parts = [v for k in _TEXT_KEYS if isinstance(v := inp.get(k), str)]
    for k in _EDIT_LIST_KEYS:
        for item in inp.get(k) or []:
            if isinstance(item, dict):
                parts += [v for kk in _TEXT_KEYS if isinstance(v := item.get(kk), str)]
    return "\n".join(parts)


def ordered_calls(raw: dict) -> list:
    out = []
    for sa in sorted(raw.get("sessions") or [], key=lambda s: getattr(s, "session_index", 0)):
        for c in sa.parsed.tool_calls:
            out.append((sa.session_key, c))
    out.sort(key=lambda x: (x[0], getattr(x[1], "start_line", 0)))
    return out


def _write_target_inside(call, ws_norm: str) -> Optional[str]:
    """The in-workspace path a write tool targets, or None."""
    inp = call.input if isinstance(call.input, dict) else {}
    for key in ("file_path", "path", "notebook_path"):
        raw_path = inp.get(key)
        if not isinstance(raw_path, str):
            continue
        resolved = isolation.normalize_path(raw_path, base=ws_norm)
        if resolved is None:
            return None
        if not isolation._inside(resolved, ws_norm):
            return None
        blob = resolved.replace("\\", "/")
        if any(m in blob for m in isolation.EXPOSURE_MARKERS):
            return None
        return resolved
    return None


def classify_run(raw: dict, isolation_result: dict, content_check: dict,
                 protected: Optional[Iterable[str]]) -> dict:
    """Classify every content match of one run. Detection only, like the checks
    it sits on top of: nothing here makes a run valid."""
    meta = raw.get("metadata") or {}
    out = {
        "version": EXPOSURE_PROVENANCE_VERSION,
        "rule_id": RULE_ID,
        "applies": applies_to(meta),
        "available": False,
        "matches": [],
        "self_authored_false_positive": False,
        "held_out_exposure": bool(content_check.get("breach_suspected")),
        "hard_stop": bool(content_check.get("breach_suspected")),
    }
    hits = list(content_check.get("exposures") or [])
    if not content_check.get("available") or protected is None:
        out["reason"] = "content check unavailable"
        return out
    out["available"] = True
    if not hits:
        out.update({"held_out_exposure": False, "hard_stop": False})
        return out

    protected = set(protected)
    ws = (meta.get("workspace") or {}).get("path")
    ws_norm = isolation.normalize_path(ws) if ws else None

    # Condition 3, 4 and 6 at run level.
    blockers: list[str] = []
    if ws_norm is None:
        blockers.append("workspace path not recorded")
    if not isolation_result.get("available"):
        blockers.append("isolation check unavailable")
    if isolation_result.get("held_out_or_reference_exposure"):
        blockers.append("held-out/reference marker seen in the run")
    if isolation_result.get("outside_workspace_accesses"):
        blockers.append("out-of-workspace access in the run")
    if isolation_result.get("unresolved_posix_absolute_paths"):
        blockers.append("unresolvable absolute path in the run")
    unparsable = sum(int(getattr(sa.parsed, "unparsable_lines", 0) or 0)
                     for sa in raw.get("sessions") or [])
    if unparsable:
        blockers.append(f"{unparsable} unparsable stream line(s)")

    calls = ordered_calls(raw)
    index = {c.tool_use_id: i for i, (_k, c) in enumerate(calls)}
    result_lines = [_norm_lines(c.result_text) for _k, c in calls]
    author_lines = [
        (_norm_lines(_authored_text(c.input if isinstance(c.input, dict) else {}))
         if (c.name or "").lower() in WRITE_TOOLS else set(),
         _write_target_inside(c, ws_norm) if ws_norm and (c.name or "").lower() in WRITE_TOOLS else None)
        for _k, c in calls
    ]

    def first_result(line: str) -> Optional[int]:
        for i, lines in enumerate(result_lines):
            if line in lines:
                return i
        return None

    def author_of(line: str, before: int) -> Optional[int]:
        for i in range(before):
            lines, target = author_lines[i]
            if target is not None and line in lines:
                return i
        return None

    for hit in hits:
        i = index.get(hit.get("tool_use_id"))
        record = {"tool_use_id": hit.get("tool_use_id"), "call_index": i,
                  "tool": (calls[i][1].name if i is not None else None),
                  "matched_lines": 0, "self_authored_lines": 0,
                  "unexplained_examples": [], "reasons": list(blockers)}
        if i is None:
            record["reasons"].append("matching tool call not found in telemetry")
            record["verdict"] = VERDICT_UNEXPLAINED
            out["matches"].append(record)
            continue
        matched = sorted(result_lines[i] & protected)
        record["matched_lines"] = len(matched)
        authored, unexplained = [], []
        for line in matched:
            a = author_of(line, i)
            fr = first_result(line)
            # Conditions 1, 2 and 5: the agent's own write of an in-workspace
            # file comes strictly before the line's first appearance anywhere
            # in a tool result.
            if a is not None and (fr is None or a < fr):
                authored.append(line)
            else:
                unexplained.append(line)
        record["self_authored_lines"] = len(authored)
        record["authored_by_call_indexes"] = sorted(
            {author_of(line, i) for line in authored} - {None})
        if unexplained:
            record["unexplained_examples"] = unexplained[:3]
            record["reasons"].append(
                f"{len(unexplained)} matched line(s) not authored in this run before first appearing")
        record["verdict"] = (VERDICT_SELF_AUTHORED
                             if not unexplained and not blockers and matched
                             else VERDICT_UNEXPLAINED)
        out["matches"].append(record)

    all_self = bool(out["matches"]) and all(
        m["verdict"] == VERDICT_SELF_AUTHORED for m in out["matches"])
    out["self_authored_false_positive"] = all_self
    # The verdict the run's validity uses is only taken up where the rule
    # applies; the classification itself is recorded for every run.
    out["held_out_exposure"] = not all_self
    out["hard_stop"] = not all_self
    return out


def clears_content_match(report: dict) -> bool:
    """True when amendment 11 applies to this run AND every match is the
    self-authored false positive. Used by `report.run_validity`."""
    prov = (report or {}).get("exposure_provenance") or {}
    return bool(prov.get("applies") and prov.get("available")
                and prov.get("self_authored_false_positive"))
