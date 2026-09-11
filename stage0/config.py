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


# --------------------------------------------------------------------------
# Stage 1: C1_shared_worker_context (PREREGISTRATION section 16)
# --------------------------------------------------------------------------
#
# A separate experimental arm, NOT the old speculative "structured-state Arm C".
# It reuses the Investigator's Claude Code session as the Implementer's session
# (resumed), keeping the Coordinator and the logical three-role workflow. It is
# deliberately NOT part of ARMS or effective_config(), so the A/B config_hash is
# unchanged. C1 gets its own identity from topology_config_hash().

C1_ARM = "C1"
C1_ARM_TOPOLOGY = "C1_shared_worker_context"
C1_TOPOLOGY_VERSION = 1
EXPERIMENT_SCHEMA_VERSION_C1 = 2  # A/B metadata (schema 1) is left as written

C1_TOPOLOGY = {
    "arm": C1_ARM,
    "arm_topology": C1_ARM_TOPOLOGY,
    "topology_version": C1_TOPOLOGY_VERSION,
    "logical_roles": ["coordinator", "investigator", "implementer"],
    "physical_sessions": {"coordinator": ["coordinator"], "worker": ["investigator", "implementer"]},
    "invocations": [
        {"step": "coordinator_kickoff", "logical_role": "coordinator", "physical_session": "coordinator", "resume": False},
        {"step": "investigate", "logical_role": "investigator", "physical_session": "worker", "resume": False},
        {"step": "coordinator_plan", "logical_role": "coordinator", "physical_session": "coordinator", "resume": True},
        {"step": "implement_resume", "logical_role": "implementer", "physical_session": "worker", "resume": True},
        {"step": "coordinator_wrapup", "logical_role": "coordinator", "physical_session": "coordinator", "resume": True},
    ],
    # What the resumed Worker is given in its Implementer phase: the Coordinator's
    # instruction only. It already holds the task, its own investigation and its
    # own report in session history, so the report is NOT forwarded back and the
    # task statement is NOT resent.
    "implementer_phase_prompt_inputs": ["coordinator_instruction"],
    "investigator_report_forwarded_to_worker": False,
    "task_statement_resent_on_worker_resume": False,
    # Per-phase tool policy of the one physical Worker session: exactly the Arm-B
    # role policies, applied per invocation (--tools / --disallowedTools are
    # per-process flags; a resumed invocation receives its own).
    "worker_phase_tool_policy": {
        role: {
            "tools": list(ROLE_TOOLS[role]),
            "disallowed_tools": list(ROLE_DISALLOWED_TOOLS.get(role, ())),
            "allowed_tools": list(allowed_tools_for_role(role)),
        }
        for role in ("investigator", "implementer")
    },
}


def topology_config_hash(cfg: "RunConfig", topology: dict) -> str:
    """Config identity for a Stage-1 arm: the unchanged base effective config
    plus the topology. Never equal to any A/B config_hash."""
    blob = json.dumps(
        {"base_effective_config": cfg.effective_config(), "topology": topology},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()


# --------------------------------------------------------------------------
# Stage 2A: heterogeneous model routing (PREREGISTRATION section 18)
# --------------------------------------------------------------------------
#
# A new experiment, not a continuation of C1. The variable is WHICH MODEL serves
# each logical role. Topology, prompts, tools, permissions, limits and tasks are
# Arm A's / Arm B's, unchanged. Like C1, nothing here touches ARMS,
# effective_config() or the A/B config_hash.
#
# Model identifiers, resolved from local evidence only (no inference):
#   * `claude --help` (2.1.260): "--model <model>  Model for the current session.
#     Provide an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet')
#     or a model's full name".
#   * The 2.1.260 binary's model registry lists `claude-haiku-4-5` with
#     first-party id `claude-haiku-4-5-20251001`, and `claude-sonnet-5`.
#   * The alias `haiku` resolves indirectly (ANTHROPIC_DEFAULT_HAIKU_MODEL, then
#     a remotely configurable lookup, then a built-in). The full id does not
#     depend on that chain, so CHEAP requests the full id.
#   * This subscription already served `claude-haiku-4-5-20251001` (Claude
#     Code's own auxiliary calls, 33 stored sessions) and resolved `sonnet` to
#     `claude-sonnet-5` in all 58 stored init events.
# STRONG keeps the historical request `sonnet` byte for byte, so the Single-
# Strong baseline and every strong-role invocation get the historical argv.
STRONG_MODEL = "sonnet"
STRONG_MODEL_RESOLVED = "claude-sonnet-5"
CHEAP_MODEL = "claude-haiku-4-5-20251001"
CHEAP_MODEL_RESOLVED = "claude-haiku-4-5-20251001"

MODEL_CLASSES = {
    "CHEAP": {"requested": CHEAP_MODEL, "expected_resolved": CHEAP_MODEL_RESOLVED,
              "canonical": "claude-haiku-4-5"},
    "STRONG": {"requested": STRONG_MODEL, "expected_resolved": STRONG_MODEL_RESOLVED,
               "canonical": "claude-sonnet-5"},
}

# Claude Code's own internal calls (not role-assigned). Stored telemetry shows
# only this model in that role. Any other extra model in an invocation's
# modelUsage invalidates the run (it would be unassigned capability).
AUXILIARY_MODELS_CANONICAL = ("claude-haiku-4-5",)

# API-equivalent list prices, USD per million tokens, as bundled in the Claude
# Code 2.1.260 model registry (pricing keys `tier_2_10` and `haiku_45`).
# Verified against stored telemetry: recomputing modelUsage.costUSD from these
# tables and the reported tokens reproduces all 90 stored per-model entries
# exactly. Not the amount paid: execution is on the subscription.
MODEL_PRICING_USD_PER_MTOK = {
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_write_5m": 2.5,
                        "cache_write_1h": 4.0, "cache_read": 0.2},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write_5m": 1.25,
                         "cache_write_1h": 2.0, "cache_read": 0.1},
}

# Reasoning effort. The CLI has `--effort <level>` (low, medium, high, xhigh,
# max), the env var CLAUDE_CODE_EFFORT_LEVEL and the setting `effortLevel`. But
# the registry gives `claude-sonnet-5` the capabilities effort/max_effort/
# xhigh_effort (default_effort "high") and `claude-haiku-4-5` none of them, and
# no init/result field reports the effort used. The control is therefore not
# comparable across the two models and not verifiable: Stage 2A passes no
# effort control to any role (every historical run did the same).
STAGE2_EFFORT_POLICY = {
    "policy": "cli_default_not_passed",
    "effort_flag_passed": False,
    "cheap": "not passed; the claude-haiku-4-5 registry entry lists no effort capability",
    "strong": "not passed; CLI default for claude-sonnet-5 (registry default_effort 'high'), "
              "identical to every historical Arm A/B/C1 run",
    "observable_in_telemetry": False,
}

STAGE2_TOPOLOGY_VERSION = 1
EXPERIMENT_SCHEMA_VERSION_S2 = 3  # A/B (1) and C1 (2) metadata are left as written

# Prompts are Arm A's / Arm B's, unchanged. Their sources are pinned here and
# checked before every Stage-2 run; no model-specific prompt exists.
STAGE2_PROMPT_SOURCES = {
    "arms/single.py": "128cb314d1c2098d607281810efe47cc37f559b7024b95a36e3c357be5f74c52",
    "arms/multi_nl.py": "537bc435463f19e65f269a71510fcf916ecd54a65f1efb7769e63e6b6130864a",
}
# sha256 of json.dumps(harness.agent.ROLE_SYSTEM_APPENDIX, sort_keys=True)
STAGE2_ROLE_APPENDIX_SHA256 = "e2e6b8418e1a9b828294bfb639b15334a1f6949ab5bdcdda86b00b57b147b1a8"

STAGE2_TASKS = ("shipping_inch_dimensions", "settings_list_fields",
                "rename_max_connections", "sla_weekend_hours")

_SEPARATED = "separated_roles_arm_b"
_SINGLE = "single_agent_arm_a"
STAGE2_CONFIGS = {
    "S2_R1": {"label": "Strong-Investigator Hybrid", "topology": _SEPARATED,
              "role_classes": {"coordinator": "CHEAP", "investigator": "STRONG", "implementer": "CHEAP"}},
    "S2_R2": {"label": "Strong-Implementer Hybrid", "topology": _SEPARATED,
              "role_classes": {"coordinator": "CHEAP", "investigator": "CHEAP", "implementer": "STRONG"}},
    "S2_R3": {"label": "All-Cheap Multi", "topology": _SEPARATED,
              "role_classes": {"coordinator": "CHEAP", "investigator": "CHEAP", "implementer": "CHEAP"}},
    "S2_S": {"label": "Single Cheap", "topology": _SINGLE,
             "role_classes": {"solo": "CHEAP"}},
    # Only where no historical Arm A run has exact parity (section 18.6).
    "S2_SS": {"label": "Single Strong (fresh baseline)", "topology": _SINGLE,
              "role_classes": {"solo": "STRONG"}},
    # Section 19 (difficulty amendment): the multi-agent quality reference. Arm B's
    # topology and prompts with every role STRONG, under Stage-2 identity and
    # routing verification. Not historical Arm B unless exact parity (19.10).
    "S2_M": {"label": "All-Strong Multi", "topology": _SEPARATED,
             "role_classes": {"coordinator": "STRONG", "investigator": "STRONG", "implementer": "STRONG"}},
}
STAGE2_ARMS = tuple(STAGE2_CONFIGS)
STAGE2_PILOT_ARMS = ("S2_R1", "S2_R2", "S2_R3", "S2_S")

_ARM_B_STEPS = (
    ("coordinator_kickoff", "coordinator", False),
    ("investigate", "investigator", False),
    ("coordinator_plan", "coordinator", True),
    ("implement", "implementer", False),
    ("coordinator_wrapup", "coordinator", True),
)


def stage2_topology(arm: str) -> dict:
    """The Stage-2 identity of one configuration (hashed with the base config)."""
    c = STAGE2_CONFIGS[arm]
    classes = dict(c["role_classes"])
    separated = c["topology"] == _SEPARATED
    steps = _ARM_B_STEPS if separated else (("solo", "solo", False),)
    return {
        "arm": arm,
        "arm_topology": f"stage2_{c['topology']}",
        "label": c["label"],
        "stage": 2,
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION_S2,
        "topology_version": STAGE2_TOPOLOGY_VERSION,
        "base_arm_topology": "B" if separated else "A",
        "logical_roles": list(classes),
        "invocations": [
            {"step": s, "logical_role": r, "physical_session": r, "resume": res,
             "model_class": classes[r]}
            for s, r, res in steps
        ],
        "role_model_classes": classes,
        "role_models_requested": {r: MODEL_CLASSES[k]["requested"] for r, k in classes.items()},
        "role_models_expected_resolved": {r: MODEL_CLASSES[k]["expected_resolved"] for r, k in classes.items()},
        "role_reasoning_effort": {r: STAGE2_EFFORT_POLICY["policy"] for r in classes},
        "model_classes": MODEL_CLASSES,
        "auxiliary_models_allowed": list(AUXILIARY_MODELS_CANONICAL),
        "effort_policy": STAGE2_EFFORT_POLICY,
        "prompt_version": {
            "prompts": "arm_b_v1_verbatim" if separated else "arm_a_v1_verbatim",
            "sources_sha256": dict(STAGE2_PROMPT_SOURCES),
            "role_system_appendix_sha256": STAGE2_ROLE_APPENDIX_SHA256,
            "model_specific_prompt_changes": False,
        },
        "permission_policy_id": ALLOWED_TOOLS_POLICY_ID,
        "pricing_usd_per_mtok": MODEL_PRICING_USD_PER_MTOK,
    }


# Variables that would change which model or effort a spawned `claude` uses.
# Presence only is tested; values are never read. CLAUDE_CODE_* is already
# stripped from every child, so only variables that would REACH a child count.
MODEL_ROUTING_ENV_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "MAX_THINKING_TOKENS",
)


class ModelRoutingEnvError(RuntimeError):
    """Raised when the environment could silently change a Stage-2 model/effort."""


def _reaches_child(name: str) -> bool:
    return name not in CHILD_ENV_STRIP_EXACT and not any(
        name.startswith(p) for p in CHILD_ENV_STRIP_PREFIXES)


def model_routing_env_present(env: Optional[dict] = None) -> tuple[str, ...]:
    env = os.environ if env is None else env
    return tuple(v for v in MODEL_ROUTING_ENV_VARS if env.get(v) and _reaches_child(v))


def preflight_model_routing_env(env: Optional[dict] = None) -> dict:
    present = model_routing_env_present(env)
    if present:
        raise ModelRoutingEnvError(
            "Stage-2 guard: these variables would change the model or effort of a "
            f"spawned `claude`: {', '.join(present)} (presence only was tested). "
            "The harness will not unset them; remove them from this shell and re-run.")
    return {"vars_tested": list(MODEL_ROUTING_ENV_VARS), "present_reaching_child": [],
            "note": "presence-only; no value was read or logged"}


# --------------------------------------------------------------------------
# Stage 2 amendment: difficulty-calibrated quality-cost experiment
# (PREREGISTRATION section 19). Supersedes the unexecuted 17-run plan of 18.11.
# --------------------------------------------------------------------------
#
# Phase C (calibration) runs Single Strong only, on the candidate pool, to
# assign empirical difficulty strata. Phase E (evaluation) re-measures every
# architecture - Single Strong included - on the frozen benchmark with fresh,
# independent runs, so no run both selects a task and scores an architecture.
# Phase labels and strata are run metadata only: they never reach a prompt.

STAGE2_PHASES = ("calibration", "evaluation")

# Conceptual architecture -> Stage-2 configuration (section 19.6).
STAGE2_ARCHITECTURES = {
    "A": "S2_SS",   # Single Strong
    "M": "S2_M",    # All-Strong Multi (primary comparison with A)
    "H1": "S2_R1",  # Strong-Investigator Hybrid
    "H2": "S2_R2",  # Strong-Implementer Hybrid
    "CM": "S2_R3",  # All-Cheap Multi
    "CS": "S2_S",   # Single Cheap
}
STAGE2_CALIBRATION_ARM = "S2_SS"
STAGE2_E1_ARMS = ("S2_SS", "S2_M")
STAGE2_E2_ARMS = ("S2_R1", "S2_R2", "S2_R3", "S2_S")

# The candidate pool (section 19.3): 12 tasks, 4 families, 3 codebases. Task ids
# are neutral on purpose; family and design features are analysis-only
# (tasks/holdout/<task>/design.json).
STAGE2_CANDIDATES = (
    "s2t01_ledgerly", "s2t02_ledgerly", "s2t03_ledgerly", "s2t04_ledgerly",
    "s2t05_flowq", "s2t06_flowq", "s2t07_flowq", "s2t08_flowq",
    "s2t09_docpipe", "s2t10_docpipe", "s2t11_docpipe", "s2t12_docpipe",
)
# The four Stage-0.5 tasks: an EASY control stratum only (section 19.11).
STAGE2_EASY_CONTROLS = STAGE2_TASKS

STAGE2_RESULTS_DIR = STAGE0_ROOT / "results" / "stage2"
STAGE2_DIFFICULTY_LABELS = STAGE2_RESULTS_DIR / "difficulty_labels.json"

# Per-session limits for calibration and evaluation on the candidate pool
# (section 19.7). The smoke limits (25 turns, 900 s) were sized for the Stage-0.5
# tasks, where Single Strong used at most 18 model turns. On deliberately harder
# tasks a 25-turn cap would bind first for the single agent (one session) and
# could manufacture a multi-agent advantage (five sessions). Every architecture
# in the calibrated strata gets these same limits; limit terminations remain
# outcomes. The EASY controls keep SMOKE_LIMITS for historical parity.
STAGE2_LIMITS = Limits(
    max_tasks_per_invocation=1,
    max_sessions_per_invocation=6,
    max_turns_per_session=60,
    max_wall_seconds_per_session=1800,
)


def stage2_limits_for(task_id: str) -> Limits:
    return SMOKE_LIMITS if task_id in STAGE2_EASY_CONTROLS else STAGE2_LIMITS
