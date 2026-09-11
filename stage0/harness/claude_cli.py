"""Execute and observe the local Claude Code CLI. No agent-specific policy here.

Responsibilities:
  construct invocation -> record it BEFORE spawning -> spawn -> capture stdout,
  stderr, exit status, timing -> parse stream events -> retain raw stream
  unchanged -> return a normalized result.

Harness-side turn limiting lives here because Claude Code 2.1.260 has no
`--max-turns` flag (see SPEC.md section 3).
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import config
from harness import telemetry

WRAPPER_VERSION = 2

TERM_COMPLETED = "completed"
TERM_TURN_LIMIT = "turn_limit_exceeded"
TERM_WALL_LIMIT = "wall_limit_exceeded"
TERM_SPAWN_FAILED = "spawn_failed"


# --------------------------------------------------------------------------
# Capability detection
# --------------------------------------------------------------------------

# Flags the experiment design depends on. Verified against the installed build's
# --help text, never assumed from memory.
REQUIRED_FLAGS = (
    "-p",
    "--output-format",
    "--verbose",
    "--model",
    "--allowedTools",
    "--disallowedTools",
    "--tools",
    "--session-id",
    "--permission-mode",
)
OPTIONAL_FLAGS = (
    "--input-format",
    "--resume",
    "--include-hook-events",
    "--permission-prompts",
    "--no-session-persistence",
    "--strict-mcp-config",
    "--setting-sources",
    "--add-dir",
    "--append-system-prompt",
    "--max-turns",  # expected ABSENT on 2.1.260
    "--fork-session",
    "--include-partial-messages",
)
REQUIRED_OUTPUT_FORMATS = ("json", "stream-json")


@dataclass
class CapabilityReport:
    cli_path: Optional[str]
    cli_version: Optional[str]
    discovered_via: Optional[str]
    help_ok: bool
    flags: dict[str, bool] = field(default_factory=dict)
    output_formats: dict[str, bool] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.help_ok and not self.missing_required

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def launcher_for(cli_path: str) -> list[str]:
    """How to launch this CLI.

    Real Claude Code is an executable. A `.py` path is the test mock in
    tools/mock_claude.py and is run through this interpreter.
    """
    return [sys.executable, cli_path] if cli_path.endswith(".py") else [cli_path]


def detect_capabilities(cli: Optional[config.ClaudeCli] = None) -> CapabilityReport:
    cli = cli or config.find_claude_cli()
    if cli is None:
        return CapabilityReport(
            cli_path=None,
            cli_version=None,
            discovered_via=None,
            help_ok=False,
            missing_required=list(REQUIRED_FLAGS),
            notes=[
                "No `claude` executable found. Set STAGE0_CLAUDE_CLI to its full path, "
                "or install Claude Code so that `claude` is on PATH."
            ],
        )

    try:
        proc = subprocess.run(
            [*launcher_for(cli.path), "--help"], capture_output=True, text=True, timeout=120
        )
        help_text = (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return CapabilityReport(
            cli_path=cli.path,
            cli_version=cli.version,
            discovered_via=cli.discovered_via,
            help_ok=False,
            missing_required=list(REQUIRED_FLAGS),
            notes=[f"`claude --help` failed: {type(exc).__name__}"],
        )

    rep = CapabilityReport(
        cli_path=cli.path,
        cli_version=cli.version,
        discovered_via=cli.discovered_via,
        help_ok=bool(help_text.strip()),
    )
    for flag in REQUIRED_FLAGS + OPTIONAL_FLAGS:
        rep.flags[flag] = _help_mentions_flag(help_text, flag)
    for fmt in REQUIRED_OUTPUT_FORMATS:
        rep.output_formats[fmt] = f'"{fmt}"' in help_text or f"'{fmt}'" in help_text

    rep.missing_required = [f for f in REQUIRED_FLAGS if not rep.flags[f]]
    rep.missing_required += [
        f"--output-format={f}" for f in REQUIRED_OUTPUT_FORMATS if not rep.output_formats[f]
    ]

    if not rep.flags.get("--max-turns"):
        rep.notes.append(
            "`--max-turns` is NOT available on this build; turn limiting is "
            "enforced harness-side by terminating the child process."
        )
    if not rep.flags.get("--include-hook-events"):
        rep.notes.append("`--include-hook-events` unavailable; hook enrichment disabled.")
    return rep


def _help_mentions_flag(help_text: str, flag: str) -> bool:
    """Match a flag as a whole token so `-p` does not match inside `--print`."""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


# --------------------------------------------------------------------------
# Child environment
# --------------------------------------------------------------------------


def build_child_env(base: Optional[dict] = None) -> tuple[dict, dict]:
    """Return (child_env, manifest).

    Strips parent Claude Code session variables so an experiment child does not
    join/inherit the host session. Never touches credentials. The manifest records
    KEY NAMES ONLY for inherited variables; no values are captured.
    """
    base = dict(os.environ if base is None else base)
    stripped: list[str] = []
    child: dict[str, str] = {}
    for k, v in base.items():
        if k in config.CHILD_ENV_STRIP_EXACT or any(
            k.startswith(p) for p in config.CHILD_ENV_STRIP_PREFIXES
        ):
            stripped.append(k)
            continue
        child[k] = v

    manifest = {
        "stripped_keys": sorted(stripped),
        "inherited_keys": sorted(child.keys()),
        "set_by_harness": {},
        "billing_guard_vars_present": sorted(
            k for k in config.BILLING_GUARD_VARS if base.get(k)
        ),
        "note": "values of inherited variables are intentionally not recorded",
    }
    return child, manifest


# --------------------------------------------------------------------------
# Invocation
# --------------------------------------------------------------------------


@dataclass
class Invocation:
    """Everything OUR harness supplies to one `claude` process.

    Written to invocation.json BEFORE execution. This is the object the
    reconstruction gate compares against (SPEC.md section 15).
    """

    session_key: str
    agent_id: str
    role: str
    cli_path: str
    argv: list[str]
    cwd: str
    stdin_text: str
    model: str
    tools: list[str]
    allowed_tools: list[str]
    disallowed_tools: list[str]
    permission_mode: str
    permission_prompts: str
    session_id: str
    resume_session_id: Optional[str]
    output_format: str
    max_turns: int
    max_wall_seconds: int
    turn_limit_enforcement: str
    append_system_prompt: Optional[str]
    env_manifest: dict
    prompt_inputs: dict
    wrapper_version: int = WRAPPER_VERSION
    forbidden_flags_present: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )


def build_invocation(
    *,
    cli_path: str,
    session_key: str,
    agent_id: str,
    role: str,
    prompt: str,
    cwd: str | os.PathLike,
    model: str,
    tools: Sequence[str],
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    permission_mode: str = "acceptEdits",
    permission_prompts: str = "none",
    max_turns: int = 25,
    max_wall_seconds: int = 900,
    session_id: Optional[str] = None,
    resume_session_id: Optional[str] = None,
    append_system_prompt: Optional[str] = None,
    include_hook_events: bool = False,
    prompt_inputs: Optional[dict] = None,
    base_env: Optional[dict] = None,
) -> Invocation:
    """Construct the invocation. Pure: spawns nothing.

    `resume_session_id` continues an existing Claude Code session (used so Arm B's
    Coordinator is one logical agent across its three turns rather than three
    amnesiac agents). `--resume` and `--session-id` are mutually exclusive.
    """
    session_id = session_id or str(uuid.uuid4())
    # Test support: a `.py` CLI path (the mock in tools/mock_claude.py) is run
    # through this interpreter. Real Claude Code is an executable and takes the
    # first branch.
    argv: list[str] = [
        *launcher_for(cli_path),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
    ]
    if resume_session_id:
        argv += ["--resume", resume_session_id]
    else:
        argv += ["--session-id", session_id]
    argv += [
        "--permission-mode",
        permission_mode,
        # Nobody is at a terminal: anything that would prompt is denied rather
        # than hanging the run. Combined with --allowedTools this gives a narrow
        # allowlist: listed patterns execute, everything else is denied and
        # instrumented as tool_denied.
        "--permission-prompts",
        permission_prompts,
        # Keep experiment sessions out of the user's normal MCP/settings surface.
        "--strict-mcp-config",
        "--setting-sources",
        "project",
    ]

    if tools:
        argv += ["--tools", ",".join(tools)]
    else:
        # Coordinator baseline: no repository tools at all.
        argv += ["--tools", ""]
    if allowed_tools:
        # Space-separated, one argument per rule: the rules contain spaces
        # themselves (e.g. "Bash(python -m pytest *)"), so a comma-joined single
        # argument would be ambiguous. `--allowedTools <tools...>` is variadic.
        argv += ["--allowedTools", *allowed_tools]
    if disallowed_tools:
        argv += ["--disallowedTools", ",".join(disallowed_tools)]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]
    if include_hook_events:
        argv += ["--include-hook-events"]

    # The prompt goes on stdin, not argv: task statements are long and must be
    # recorded byte-for-byte without shell quoting concerns.
    child_env, manifest = build_child_env(base_env)

    forbidden = [f for f in config.FORBIDDEN_CLI_FLAGS if f in argv]

    return Invocation(
        session_key=session_key,
        agent_id=agent_id,
        role=role,
        cli_path=cli_path,
        argv=argv,
        cwd=str(cwd),
        stdin_text=prompt,
        model=model,
        tools=list(tools),
        allowed_tools=list(allowed_tools),
        disallowed_tools=list(disallowed_tools),
        permission_mode=permission_mode,
        permission_prompts=permission_prompts,
        session_id=session_id,
        resume_session_id=resume_session_id,
        output_format="stream-json",
        max_turns=max_turns,
        max_wall_seconds=max_wall_seconds,
        turn_limit_enforcement="harness_side",
        append_system_prompt=append_system_prompt,
        env_manifest=manifest,
        prompt_inputs=prompt_inputs or {},
        forbidden_flags_present=forbidden,
    )


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@dataclass
class CliRunResult:
    session_key: str
    exit_code: Optional[int]
    started_at: str
    ended_at: str
    wall_seconds: float
    termination_reason: str
    stdout_path: str
    stderr_path: str
    raw_stdout_lines: int
    parsed: telemetry.ParsedStream
    summary: dict
    billing_ok: bool
    billing_note: str

    @property
    def ok(self) -> bool:
        return (
            self.exit_code == 0
            and self.termination_reason == TERM_COMPLETED
            and not self.parsed.is_error()
        )

    def exit_record(self) -> dict:
        return {
            "session_key": self.session_key,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "wall_seconds": self.wall_seconds,
            "termination_reason": self.termination_reason,
            "raw_stdout_lines": self.raw_stdout_lines,
            "billing_ok": self.billing_ok,
            "billing_note": self.billing_note,
            "wrapper_version": WRAPPER_VERSION,
        }


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def run_invocation(
    inv: Invocation,
    out_dir: str | os.PathLike,
    *,
    on_raw_line=None,
    base_env: Optional[dict] = None,
) -> CliRunResult:
    """Spawn the CLI, stream and persist raw output, enforce harness-side limits.

    The raw stream is written to disk verbatim as it arrives and is never
    rewritten. Parsing happens on a copy of the same lines.
    """
    if inv.forbidden_flags_present:
        raise config.BillingGuardError(
            f"Invocation contains forbidden flags: {inv.forbidden_flags_present}"
        )
    # Guard again immediately before spawning: the environment may have changed.
    config.preflight_billing_guard(base_env)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stdout_path = out / "claude_stdout.jsonl"
    stderr_path = out / "claude_stderr.txt"

    inv.write(out / "invocation.json")  # BEFORE execution

    child_env, _manifest = build_child_env(base_env)
    started = _utc()
    t0 = time.monotonic()

    try:
        proc = subprocess.Popen(
            inv.argv,
            cwd=inv.cwd,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        ended = _utc()
        stderr_path.write_text(f"spawn failed: {exc}\n", encoding="utf-8")
        stdout_path.write_text("", encoding="utf-8")
        parsed = telemetry.parse_stream([])
        return CliRunResult(
            session_key=inv.session_key,
            exit_code=None,
            started_at=started,
            ended_at=ended,
            wall_seconds=time.monotonic() - t0,
            termination_reason=TERM_SPAWN_FAILED,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            raw_stdout_lines=0,
            parsed=parsed,
            summary=telemetry.session_summary(parsed),
            billing_ok=False,
            billing_note=f"spawn failed: {type(exc).__name__}",
        )

    lines: list[str] = []
    line_q: "queue.Queue[Optional[str]]" = queue.Queue()
    stderr_chunks: list[str] = []

    def _pump_stdout() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line_q.put(line)
        finally:
            line_q.put(None)

    def _pump_stderr() -> None:
        try:
            for line in proc.stderr:  # type: ignore[union-attr]
                stderr_chunks.append(line)
        except Exception:
            pass

    t_out = threading.Thread(target=_pump_stdout, daemon=True)
    t_err = threading.Thread(target=_pump_stderr, daemon=True)
    t_out.start()
    t_err.start()

    # Write the prompt to stdin and close it so the CLI knows input is complete.
    try:
        proc.stdin.write(inv.stdin_text)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except (OSError, ValueError):
        pass

    termination = TERM_COMPLETED
    turns_seen = 0
    deadline = t0 + inv.max_wall_seconds

    with open(stdout_path, "w", encoding="utf-8", newline="\n") as raw_fh:
        while True:
            timeout = max(0.1, deadline - time.monotonic())
            try:
                item = line_q.get(timeout=timeout)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    termination = TERM_WALL_LIMIT
                    _terminate(proc)
                    break
                continue
            if item is None:
                break

            raw_fh.write(item if item.endswith("\n") else item + "\n")
            raw_fh.flush()
            lines.append(item)
            if on_raw_line is not None:
                try:
                    on_raw_line(len(lines), item)
                except Exception:
                    pass  # observation must never break execution

            # Harness-side turn limit (no --max-turns on this build).
            if '"type":"assistant"' in item or '"type": "assistant"' in item:
                turns_seen += 1
                if turns_seen > inv.max_turns:
                    termination = TERM_TURN_LIMIT
                    _terminate(proc)
                    break

    try:
        exit_code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        _terminate(proc, hard=True)
        exit_code = proc.poll()

    # Drain anything still queued after termination so the raw log is complete.
    with open(stdout_path, "a", encoding="utf-8", newline="\n") as raw_fh:
        while True:
            try:
                item = line_q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                break
            raw_fh.write(item if item.endswith("\n") else item + "\n")
            lines.append(item)

    t_err.join(timeout=5)
    stderr_path.write_text("".join(stderr_chunks), encoding="utf-8")

    ended = _utc()
    wall = time.monotonic() - t0
    parsed = telemetry.parse_stream(lines)
    summary = telemetry.session_summary(parsed)

    billing_ok, billing_note = _assess_billing(parsed)

    if parsed.result is not None:
        (out / "result.json").write_text(
            json.dumps(parsed.result, indent=2, sort_keys=True), encoding="utf-8"
        )

    result = CliRunResult(
        session_key=inv.session_key,
        exit_code=exit_code,
        started_at=started,
        ended_at=ended,
        wall_seconds=wall,
        termination_reason=termination,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        raw_stdout_lines=len(lines),
        parsed=parsed,
        summary=summary,
        billing_ok=billing_ok,
        billing_note=billing_note,
    )
    (out / "exit.json").write_text(
        json.dumps(result.exit_record(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return result


def _assess_billing(parsed: telemetry.ParsedStream) -> tuple[bool, str]:
    """Verify from the CLI's own init event that this was not API-key billing."""
    src = parsed.api_key_source
    if src is None:
        return False, "apiKeySource not reported by CLI (init event missing)"
    if src in config.API_KEY_SOURCES_MEANING_API_BILLING:
        return False, f"apiKeySource={src!r} indicates API-key billing, not subscription"
    return True, f"apiKeySource={src!r} (not an API-key source)"


def _terminate(proc: subprocess.Popen, hard: bool = False) -> None:
    try:
        if hard:
            proc.kill()
        else:
            proc.terminate()
    except OSError:
        pass


# --------------------------------------------------------------------------
# Reconstruction gate support
# --------------------------------------------------------------------------

# Fields that must be byte-identical for a run to be reproducible at the
# harness-input level. Claude Code's hidden system prompt and the provider HTTP
# request are NOT included and reconstruction of them is not claimed.
RECONSTRUCTION_FIELDS = (
    "argv",
    "cwd",
    "stdin_text",
    "model",
    "tools",
    "allowed_tools",
    "disallowed_tools",
    "permission_mode",
    "permission_prompts",
    "session_id",
    "resume_session_id",
    "output_format",
    "max_turns",
    "max_wall_seconds",
    "append_system_prompt",
    "prompt_inputs",
    "role",
    "agent_id",
)


def load_invocation(path: str | os.PathLike) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reconstruct_from_log(invocation_json: dict) -> dict:
    """Project a stored invocation onto the reconstruction-relevant fields."""
    return {k: invocation_json.get(k) for k in RECONSTRUCTION_FIELDS}


def compare_reconstruction(stored: dict, rebuilt: dict) -> list[str]:
    """Return a list of human-readable mismatches; empty means the gate passes."""
    diffs = []
    for key in RECONSTRUCTION_FIELDS:
        a, b = stored.get(key), rebuilt.get(key)
        if isinstance(a, tuple):
            a = list(a)
        if isinstance(b, tuple):
            b = list(b)
        if a != b:
            diffs.append(f"{key}: stored={a!r} rebuilt={b!r}")
    return diffs
