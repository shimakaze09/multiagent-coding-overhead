"""Stage 0 configuration, CLI discovery, and the subscription/billing guard.

Deliberately boring: plain dataclasses, no framework, no dependency injection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

STAGE0_ROOT = Path(__file__).resolve().parent
RUNS_DIR = STAGE0_ROOT / "runs"
TASKS_DIR = STAGE0_ROOT / "tasks"
FIXTURES_DIR = STAGE0_ROOT / "tests" / "fixtures"

# --------------------------------------------------------------------------
# Billing / authentication guard
# --------------------------------------------------------------------------

# Presence of any of these means a spawned `claude` could bill the Anthropic API
# (or a third-party provider) instead of using the Claude subscription.
# We test PRESENCE ONLY. We never read, print, log, unset, or replace values.
BILLING_GUARD_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY_HELPER",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

# `apiKeySource` values reported by the CLI's system/init event that indicate the
# session is NOT running on subscription OAuth.
API_KEY_SOURCES_MEANING_API_BILLING = (
    "ANTHROPIC_API_KEY",
    "apiKeyHelper",
    "ANTHROPIC_AUTH_TOKEN",
    "bedrock",
    "vertex",
)

# Flags we must never pass. `--bare` forces ANTHROPIC_API_KEY / apiKeyHelper auth
# and never reads OAuth, i.e. it is an API-billing path.
FORBIDDEN_CLI_FLAGS = ("--bare", "--max-budget-usd", "--betas")


class BillingGuardError(RuntimeError):
    """Raised when the environment could cause Anthropic API billing."""


@dataclass(frozen=True)
class BillingGuardReport:
    present_vars: tuple[str, ...]
    ok: bool

    def explain(self) -> str:
        if self.ok:
            return (
                "Billing guard PASS: no API-key/third-party-provider environment "
                "variables present. A spawned `claude` will use subscription OAuth."
            )
        listed = ", ".join(self.present_vars)
        return (
            "Billing guard FAIL.\n"
            f"  Present in the environment: {listed}\n"
            "  (presence only was tested; no value was read or logged)\n\n"
            "  Stage 0 must run on your Claude Pro/Max subscription through the\n"
            "  Claude Code CLI. With the variable(s) above set, a spawned `claude`\n"
            "  may authenticate against the Anthropic API and create API charges.\n\n"
            "  The harness will NOT unset or replace your credentials.\n"
            "  If you intend to run on the subscription, remove the variable(s)\n"
            "  from this shell yourself and re-run, e.g. in PowerShell:\n"
            "      Remove-Item Env:ANTHROPIC_API_KEY\n"
            "  or launch the harness from a shell that does not define them."
        )


def check_billing_guard(env: Optional[dict] = None) -> BillingGuardReport:
    """Presence-only check for API-billing credentials."""
    env = os.environ if env is None else env
    present = tuple(v for v in BILLING_GUARD_VARS if env.get(v))
    return BillingGuardReport(present_vars=present, ok=not present)


def preflight_billing_guard(env: Optional[dict] = None) -> BillingGuardReport:
    """Hard gate. Raises before any Claude workload if API billing is possible."""
    report = check_billing_guard(env)
    if not report.ok:
        raise BillingGuardError(report.explain())
    return report


# --------------------------------------------------------------------------
# Environment sanitation for child `claude` processes
# --------------------------------------------------------------------------

# The harness may itself be launched inside a Claude Code session, which exports
# session-scoped variables. Leaking them into an experiment child would let the
# child join/inherit the host session and would make the run irreproducible.
CHILD_ENV_STRIP_PREFIXES = ("CLAUDE_CODE_",)
CHILD_ENV_STRIP_EXACT = (
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_PREVIEW_CLASSIFIER_FLOOR",
    "CLAUDE_CONFIG_DIR",
)


# --------------------------------------------------------------------------
# CLI discovery
# --------------------------------------------------------------------------

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class ClaudeCli:
    path: str
    version: str
    discovered_via: str

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        m = _VERSION_RE.search(self.version or "")
        return tuple(int(g) for g in m.groups()) if m else (0, 0, 0)


def _bundled_cli_candidates() -> list[tuple[tuple[int, int, int], Path]]:
    """Claude Code builds bundled by the Claude desktop application."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    base = Path(appdata) / "Claude" / "claude-code"
    if not base.is_dir():
        return []
    out = []
    for child in base.iterdir():
        exe = child / ("claude.exe" if os.name == "nt" else "claude")
        m = _VERSION_RE.fullmatch(child.name)
        if exe.is_file() and m:
            out.append((tuple(int(g) for g in m.groups()), exe))
    out.sort(reverse=True)
    return out


def probe_cli_version(path: str) -> str:
    # A `.py` path is the test mock (tools/mock_claude.py); real Claude Code is
    # an executable.
    launcher = [sys.executable, path] if path.endswith(".py") else [path]
    try:
        r = subprocess.run(
            [*launcher, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout or "").strip()


def find_claude_cli() -> Optional[ClaudeCli]:
    """Locate a `claude` executable. Explicit override wins, then PATH, then the
    build bundled by the Claude desktop app."""
    override = os.environ.get("STAGE0_CLAUDE_CLI")
    if override:
        if not Path(override).is_file():
            return None
        return ClaudeCli(override, probe_cli_version(override), "STAGE0_CLAUDE_CLI")

    on_path = shutil.which("claude")
    if on_path:
        return ClaudeCli(on_path, probe_cli_version(on_path), "PATH")

    for _ver, exe in _bundled_cli_candidates():
        return ClaudeCli(str(exe), probe_cli_version(str(exe)), "desktop_app_bundle")

    return None


# --------------------------------------------------------------------------
# Tool capability sets (identical capability pool across arms)
# --------------------------------------------------------------------------

# The pool both arms draw from. `Agent`/`Task` is intentionally excluded from the
# pool so Arm A is a genuine single agent and Arm B's decomposition is exactly the
# three logical agents under study.
TOOL_POOL = ("Read", "Grep", "Glob", "Edit", "Write", "Bash")

ROLE_TOOLS = {
    # Arm A: the full shared pool.
    "solo": ("Read", "Grep", "Glob", "Edit", "Write", "Bash"),
    # Arm B: same pool, partitioned by role policy only.
    "coordinator": (),  # delegation only; no repository access in the baseline
    "investigator": ("Read", "Grep", "Glob", "Bash"),  # inspect, never modify
    "implementer": ("Read", "Grep", "Glob", "Edit", "Write", "Bash"),
}

# --------------------------------------------------------------------------
# Bash permission policy
# --------------------------------------------------------------------------
#
# Why this exists
# ---------------
# The first real Arm A run could not run the task's tests. Claude Code refused
# two `python -m pytest` calls with `system/permission_denied`,
# decision_reason_type="asyncAgent" ("no approval surface in this session"),
# because the harness runs non-interactively with `--permission-prompts none`.
# Read-only Bash was unaffected: `cd "<ws>" && find . -type f ... | sort`
# executed normally in the same run. So the ONLY missing capability is running
# the test suite, and `cd`/`find`/pipes need no rule of their own.
#
# Syntax, verified against the installed 2.1.260 binary (not assumed)
# -------------------------------------------------------------------
# `claude --help` documents:
#     --allowedTools, --allowed-tools <tools...>
#         Comma or space-separated list of tool names to allow
#         (e.g. "Bash(git *)" "Edit")
# and the binary's own rule validator distinguishes two forms:
#     "Bash(npm run:*) - prefix matching (legacy)"
#     "Bash(npm run *) - wildcard matching"
# rejecting `:*` anywhere but at the end, and an empty prefix before `:*`.
# We use the current wildcard form.
#
# Deliberately NOT allowed
# ------------------------
#   * bare `Bash` or `Bash(*)`               - that is arbitrary shell
#   * `Bash(python:*)` / `Bash(python *)`    - a bare interpreter prefix is
#                                              arbitrary code execution, and the
#                                              CLI itself calls such rules out
#   * `Bash(python -c *)`                    - the previous agent attempted an
#                                              inline script, but the
#                                              deterministic pytest path is
#                                              sufficient for this fixture
#   * `Bash(pytest *)`                       - the console-script entry point is
#                                              not needed: this fixture's README
#                                              and verifier both use
#                                              `python -m pytest`
#
# Compound commands are decomposed by Claude Code and each part is checked, so
# `cd <ws> && python -m pytest tests -q` needs the pytest rule only, and a third
# unlisted subcommand in the same line would still be denied.
# Empirically established: `--allowedTools` rules are NOT validated at startup.
# Two deliberately malformed rules ("Bash(:*)" and a ":*" not at the end) were
# accepted silently and the session ran normally. So a mis-spelled rule does not
# raise - it simply fails to match and the tool call is denied. All three
# spellings below therefore express exactly ONE permission ("run the project's
# test suite via `python -m pytest`"), so that the policy does not depend on
# which spelling this build's matcher honours.
BASH_TEST_ALLOWLIST = (
    "Bash(python -m pytest *)",   # wildcard matching (current form, per --help)
    "Bash(python -m pytest)",     # exact, no arguments
    "Bash(python -m pytest:*)",   # prefix matching (legacy form) - same family
)

# Identifier for the policy, recorded per run so a change is visible in the data.
ALLOWED_TOOLS_POLICY_ID = "narrow_pytest_v1"
ALLOWED_TOOLS_POLICY_NOTE = (
    "Narrow allowlist: the project's test suite via `python -m pytest` only. "
    "Applied identically to every session in Arm A and Arm B. Anything else that "
    "would require approval is still denied automatically and instrumented as "
    "tool_denied."
)

# The policy is a single list applied to EVERY role, so Arm A and Arm B receive
# byte-identical permission configuration. Roles still differ in `--tools` and
# `--disallowedTools`, which is the pre-existing role design and is unchanged:
# the Coordinator has no Bash at all, so the allowlist is inert for it, and the
# Investigator remains unable to modify files (Edit/Write/NotebookEdit denied).
def allowed_tools_for_role(role: str) -> tuple[str, ...]:
    """Identical for every role, by design. `role` is accepted so that any future
    divergence has to be written here explicitly rather than happening by
    accident."""
    if role not in ROLE_TOOLS:
        raise KeyError(f"unknown role: {role!r}")
    return BASH_TEST_ALLOWLIST


# Investigator must not modify the repository. Enforced with --disallowedTools in
# addition to the --tools restriction (belt and braces).
ROLE_DISALLOWED_TOOLS = {
    "solo": (),
    "coordinator": (),
    "investigator": ("Edit", "Write", "NotebookEdit"),
    "implementer": (),
}


# --------------------------------------------------------------------------
# Limits — subscription usage protection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """All enforced before spawning. Never a workaround for a usage limit."""

    max_tasks_per_invocation: int
    max_sessions_per_invocation: int
    max_turns_per_session: int
    max_wall_seconds_per_session: int
    require_explicit_pilot_flag: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


# A developer/smoke command must never launch dozens of Claude sessions.
SMOKE_LIMITS = Limits(
    max_tasks_per_invocation=1,
    # Arm B is 3 logical agents but 5 process invocations (the Coordinator's
    # session is resumed twice). One spare.
    max_sessions_per_invocation=6,
    max_turns_per_session=25,
    max_wall_seconds_per_session=900,
)

PILOT_LIMITS = Limits(
    max_tasks_per_invocation=12,
    max_sessions_per_invocation=12 * 2 * 3 * 4,
    max_turns_per_session=40,
    max_wall_seconds_per_session=1800,
)


@dataclass(frozen=True)
class RunConfig:
    model: str = "sonnet"
    limits: Limits = SMOKE_LIMITS
    include_hook_events: bool = False
    permission_mode: str = "acceptEdits"
    # Nobody can answer a prompt in a non-interactive run, so anything not
    # covered by the allowlist is denied rather than left hanging.
    permission_prompts: str = "none"
    # Harness-side turn limiting, because --max-turns does not exist in 2.1.260.
    # Amendment 7: a turn is a distinct API message (was: every assistant stream
    # line). Part of config_hash, so runs under either rule are distinguishable.
    turn_limit_enforcement: str = "harness_side_api_messages"
    min_acquisition_coverage: float = 0.90

    def as_dict(self) -> dict:
        d = asdict(self)
        d["limits"] = self.limits.as_dict()
        d["allowed_tools_policy_id"] = ALLOWED_TOOLS_POLICY_ID
        d["allowed_tools"] = list(BASH_TEST_ALLOWLIST)
        d["allowed_tools_note"] = ALLOWED_TOOLS_POLICY_NOTE
        d["config_hash"] = self.config_hash()
        return d

    # -- reproducibility identity -------------------------------------------

    def effective_config(self) -> dict:
        """Everything our harness chooses that can affect agent behavior.

        Deliberately excludes analysis-side versions (parser, coverage formula):
        those are recorded separately and change when we fix *measurement*, not
        when we change what the agent was allowed to do.
        """
        return {
            "model": self.model,
            "permission_mode": self.permission_mode,
            "permission_prompts": self.permission_prompts,
            "allowed_tools_policy_id": ALLOWED_TOOLS_POLICY_ID,
            "allowed_tools": list(BASH_TEST_ALLOWLIST),
            "role_tools": {r: list(t) for r, t in sorted(ROLE_TOOLS.items())},
            "role_disallowed_tools": {
                r: list(t) for r, t in sorted(ROLE_DISALLOWED_TOOLS.items())
            },
            "role_allowed_tools": {
                r: list(allowed_tools_for_role(r)) for r in sorted(ROLE_TOOLS)
            },
            "tool_pool": list(TOOL_POOL),
            "limits": self.limits.as_dict(),
            "include_hook_events": self.include_hook_events,
            "turn_limit_enforcement": self.turn_limit_enforcement,
            "min_acquisition_coverage": self.min_acquisition_coverage,
            "setting_sources": "project",
            "strict_mcp_config": True,
            "forbidden_cli_flags": list(FORBIDDEN_CLI_FLAGS),
            "arms": list(ARMS),
        }

    def config_hash(self) -> str:
        """Stable hash of `effective_config`. Two runs sharing this hash were
        given the same harness configuration, including the permission policy."""
        blob = json.dumps(self.effective_config(), sort_keys=True, separators=(",", ":"))
        return hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()


# Stage 0 explicitly does not implement Arm C or any structured-state transfer.
ARMS = ("A", "B")
