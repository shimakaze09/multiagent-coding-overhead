"""Held-out isolation check (PREREGISTRATION amendment 6, recorded before Pair 2).

What the harness guarantees: the held-out verifier is not in the workspace
during the agent phase, runs alone afterwards with --noconftest, and SOLVED is
its exit code only.

What it does NOT guarantee: filesystem isolation. The workspace lives at
stage0/runs/<run_id>/workspace, so the held-out tests (stage0/tasks/holdout/),
the fixture validation tests (which contain a reference fix), the task
definitions and the preregistration are reachable at known relative paths such
as ../../../tasks/holdout. Whether Claude Code 2.1.260 would read outside its
working directory in a non-interactive session with --permission-prompts none
has not been verified; verifying it would need a Claude session.

So this module DETECTS, from raw telemetry, any tool call that reached outside
the workspace or surfaced held-out/reference material. It prevents nothing. A
run with `breach_suspected` is excluded from the pre-registered comparison.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from typing import Optional

from harness import tools

ISOLATION_CHECK_VERSION = 1

# Material that must never reach an agent. Matched case-insensitively, after
# normalising backslashes to "/", against every tool input and tool result.
EXPOSURE_MARKERS = (
    "tasks/holdout",
    "test_holdout_",
    "test_fixture_",
    "_reference_fix",
    "preregistration",
)

_PATH_KEYS = ("file_path", "path", "notebook_path")
_GITBASH_DRIVE = re.compile(r"^/([A-Za-z])(?=/|$)")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:(?=/|$)")


def normalize_path(p: Optional[str], base: Optional[str] = None) -> Optional[str]:
    """Absolute, forward-slash, case-folded path; None if it cannot be resolved.

    Handles Windows drive paths, git-bash drive paths (/d/...), and relative
    paths (joined to `base`). A home-relative path (~) is returned as-is and is
    outside any workspace by definition. A POSIX-absolute path with no drive
    letter cannot be resolved on this host and returns None (reported
    separately as unresolved rather than guessed)."""
    p = (p or "").strip().strip("\"'").replace("\\", "/")
    if not p:
        return None
    m = _GITBASH_DRIVE.match(p)
    if m:
        p = f"{m.group(1)}:{p[2:]}"
    if p.startswith("~"):
        return p.casefold()
    if not _WIN_DRIVE.match(p):
        if p.startswith("/") or base is None:
            return None
        p = base.rstrip("/") + "/" + p
    drive, tail = p[:2], p[2:] or "/"
    return (drive + posixpath.normpath(tail)).casefold()


def _inside(path_norm: str, ws_norm: str) -> bool:
    return path_norm == ws_norm or path_norm.startswith(ws_norm.rstrip("/") + "/")


def _bash_path_tokens(command: str) -> list[str]:
    out = []
    for segment in tools.split_bash_segments(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        for t in tokens:
            if t.startswith("-") or "://" in t:
                continue
            if "/" in t or "\\" in t or t.startswith("~") or t == "..":
                out.append(t)
    return out


def check_run(raw: dict) -> dict:
    """raw: ingest.load_run output (or anything with the same shape)."""
    meta = raw.get("metadata") or {}
    ws = (meta.get("workspace") or {}).get("path")
    ws_norm = normalize_path(ws) if ws else None
    if ws_norm is None:
        return {
            "version": ISOLATION_CHECK_VERSION,
            "available": False,
            "reason": "workspace path not recorded",
            "breach_suspected": None,
        }

    outside: list[dict] = []
    exposure: list[dict] = []
    unresolved: list[dict] = []
    for sa in raw.get("sessions") or []:
        for c in sa.parsed.tool_calls:
            inp = c.input if isinstance(c.input, dict) else {}
            candidates = [inp[k] for k in _PATH_KEYS if isinstance(inp.get(k), str)]
            if (c.name or "").lower() == "glob" and isinstance(inp.get("pattern"), str):
                candidates.append(inp["pattern"])
            if isinstance(inp.get("command"), str):
                candidates += _bash_path_tokens(inp["command"])

            for cand in candidates:
                rec = {"session_key": sa.session_key, "tool_use_id": c.tool_use_id,
                       "tool": c.name, "path": cand}
                resolved = normalize_path(cand, base=ws_norm)
                if resolved is None:
                    if cand.replace("\\", "/").startswith("/"):
                        unresolved.append(rec)
                    continue
                if not _inside(resolved, ws_norm):
                    outside.append({**rec, "resolved": resolved})

            blob = (json.dumps(inp, sort_keys=True) + "\n" + (c.result_text or ""))
            blob = blob.replace("\\\\", "/").replace("\\", "/").casefold()
            for marker in EXPOSURE_MARKERS:
                if marker in blob:
                    exposure.append({"session_key": sa.session_key,
                                     "tool_use_id": c.tool_use_id, "tool": c.name,
                                     "marker": marker})

    return {
        "version": ISOLATION_CHECK_VERSION,
        "available": True,
        "workspace": ws,
        "outside_workspace_accesses": outside,
        "held_out_or_reference_exposure": exposure,
        "unresolved_posix_absolute_paths": unresolved,
        "breach_suspected": bool(outside or exposure),
        "prevention": "none - detection from raw telemetry only",
    }
