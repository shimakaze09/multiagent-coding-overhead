"""Milestone 0 tests: capability detection and the subscription/billing guard.

The CLI-schema assertions run against the `--help` text actually captured from
the installed build, so a future Claude Code that drops a flag Stage 0 depends on
fails here rather than mid-experiment.
"""

from __future__ import annotations

import pytest

import config
from harness import claude_cli


@pytest.fixture
def real_help(fixtures_dir) -> str:
    return (fixtures_dir / "cli_help_2.1.260.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# CLI schema, as observed on 2.1.260
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flag", claude_cli.REQUIRED_FLAGS)
def test_required_flags_exist_on_the_captured_build(real_help, flag):
    assert claude_cli._help_mentions_flag(real_help, flag), (
        f"{flag} is required by Stage 0 but is absent from the captured --help"
    )


def test_required_output_formats_exist(real_help):
    for fmt in claude_cli.REQUIRED_OUTPUT_FORMATS:
        assert f'"{fmt}"' in real_help


def test_max_turns_does_NOT_exist_on_this_build(real_help):
    """A documented false assumption in the original specification.

    Claude Code 2.1.260 has no --max-turns, so turn limiting is harness-side.
    """
    assert not claude_cli._help_mentions_flag(real_help, "--max-turns")


def test_flags_stage0_relies_on_optionally(real_help):
    for flag in (
        "--input-format",
        "--resume",
        "--include-hook-events",
        "--permission-prompts",
        "--strict-mcp-config",
        "--setting-sources",
        "--append-system-prompt",
        "--no-session-persistence",
    ):
        assert claude_cli._help_mentions_flag(real_help, flag), flag


def test_flag_matching_is_token_exact(real_help):
    """`-p` must not match inside `--print`, and a made-up flag must not match."""
    assert claude_cli._help_mentions_flag("  -p, --print", "-p")
    assert not claude_cli._help_mentions_flag("  --print", "-p")
    assert not claude_cli._help_mentions_flag(real_help, "--not-a-real-flag")


def test_capability_report_notes_the_missing_turn_limit(real_help, monkeypatch):
    class FakeProc:
        stdout = real_help
        stderr = ""

    monkeypatch.setattr(claude_cli.subprocess, "run", lambda *a, **k: FakeProc())
    rep = claude_cli.detect_capabilities(config.ClaudeCli("x", "2.1.260", "test"))
    assert rep.ok
    assert rep.missing_required == []
    assert any("--max-turns" in n for n in rep.notes)


def test_missing_required_flag_fails_the_gate(monkeypatch):
    class FakeProc:
        stdout = "Usage: claude\n  --verbose\n"
        stderr = ""

    monkeypatch.setattr(claude_cli.subprocess, "run", lambda *a, **k: FakeProc())
    rep = claude_cli.detect_capabilities(config.ClaudeCli("x", "9.9.9", "test"))
    assert not rep.ok
    assert "-p" in rep.missing_required


def test_no_cli_found_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(config, "find_claude_cli", lambda: None)
    rep = claude_cli.detect_capabilities(None)
    assert not rep.ok
    assert rep.cli_path is None
    assert any("STAGE0_CLAUDE_CLI" in n for n in rep.notes)


# --------------------------------------------------------------------------
# Billing guard
# --------------------------------------------------------------------------


def test_guard_passes_with_a_clean_environment():
    rep = config.check_billing_guard({"PATH": "/usr/bin"})
    assert rep.ok
    assert rep.present_vars == ()
    assert "PASS" in rep.explain()


@pytest.mark.parametrize("var", config.BILLING_GUARD_VARS)
def test_guard_fails_for_each_billing_variable(var):
    rep = config.check_billing_guard({var: "some-secret-value"})
    assert not rep.ok
    assert var in rep.present_vars


def test_preflight_raises_before_any_workload():
    with pytest.raises(config.BillingGuardError) as exc:
        config.preflight_billing_guard({"ANTHROPIC_API_KEY": "sk-ant-SECRET"})
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_guard_never_reveals_the_credential_value():
    secret = "sk-ant-DO-NOT-LEAK-THIS"
    rep = config.check_billing_guard({"ANTHROPIC_API_KEY": secret})
    text = rep.explain()
    assert secret not in text
    assert "presence only" in text


def test_guard_does_not_modify_the_environment():
    env = {"ANTHROPIC_API_KEY": "x"}
    config.check_billing_guard(env)
    assert env == {"ANTHROPIC_API_KEY": "x"}, "the harness must never unset credentials"


def test_guard_treats_empty_string_as_absent():
    assert config.check_billing_guard({"ANTHROPIC_API_KEY": ""}).ok


# --------------------------------------------------------------------------
# Forbidden flags
# --------------------------------------------------------------------------


def test_bare_is_forbidden_because_it_forces_api_key_auth():
    assert "--bare" in config.FORBIDDEN_CLI_FLAGS


def test_built_invocation_contains_no_forbidden_flag(tmp_path):
    inv = claude_cli.build_invocation(
        cli_path="claude",
        session_key="01_solo",
        agent_id="solo",
        role="solo",
        prompt="hi",
        cwd=tmp_path,
        model="sonnet",
        tools=("Read",),
        base_env={"PATH": "/usr/bin"},
    )
    assert inv.forbidden_flags_present == []
    for flag in config.FORBIDDEN_CLI_FLAGS:
        assert flag not in inv.argv


def test_run_invocation_refuses_a_forbidden_flag(tmp_path):
    inv = claude_cli.build_invocation(
        cli_path="claude",
        session_key="01_solo",
        agent_id="solo",
        role="solo",
        prompt="hi",
        cwd=tmp_path,
        model="sonnet",
        tools=("Read",),
        base_env={"PATH": "/usr/bin"},
    )
    inv.argv.append("--bare")
    inv.forbidden_flags_present = ["--bare"]
    with pytest.raises(config.BillingGuardError):
        claude_cli.run_invocation(inv, tmp_path / "out", base_env={"PATH": "/usr/bin"})


def test_run_invocation_refuses_when_api_key_present(tmp_path):
    inv = claude_cli.build_invocation(
        cli_path="claude",
        session_key="01_solo",
        agent_id="solo",
        role="solo",
        prompt="hi",
        cwd=tmp_path,
        model="sonnet",
        tools=("Read",),
        base_env={"PATH": "/usr/bin"},
    )
    with pytest.raises(config.BillingGuardError):
        claude_cli.run_invocation(
            inv, tmp_path / "out", base_env={"ANTHROPIC_API_KEY": "sk-ant-x"}
        )


# --------------------------------------------------------------------------
# apiKeySource verdict
# --------------------------------------------------------------------------


def test_api_key_source_none_is_accepted_as_subscription():
    from harness import telemetry

    ps = telemetry.parse_stream(
        ['{"type":"system","subtype":"init","session_id":"s","apiKeySource":"none"}']
    )
    ok, note = claude_cli._assess_billing(ps)
    assert ok
    assert "none" in note


@pytest.mark.parametrize("src", config.API_KEY_SOURCES_MEANING_API_BILLING)
def test_api_key_source_indicating_api_billing_is_rejected(src):
    from harness import telemetry

    ps = telemetry.parse_stream(
        [f'{{"type":"system","subtype":"init","session_id":"s","apiKeySource":"{src}"}}']
    )
    ok, note = claude_cli._assess_billing(ps)
    assert not ok
    assert "API-key billing" in note


def test_missing_init_event_is_not_assumed_to_be_subscription():
    from harness import telemetry

    ok, note = claude_cli._assess_billing(telemetry.parse_stream([]))
    assert not ok
    assert "not reported" in note


# --------------------------------------------------------------------------
# Child environment sanitation
# --------------------------------------------------------------------------


def test_host_session_variables_are_stripped_from_children():
    base = {
        "PATH": "/usr/bin",
        "CLAUDE_CODE_SESSION_ID": "host-session",
        "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
        "CLAUDECODE": "1",
        "CLAUDE_EFFORT": "high",
    }
    child, manifest = claude_cli.build_child_env(base)
    assert child == {"PATH": "/usr/bin"}
    assert "CLAUDE_CODE_SESSION_ID" in manifest["stripped_keys"]
    assert "CLAUDECODE" in manifest["stripped_keys"]
    assert "CLAUDE_EFFORT" in manifest["stripped_keys"]


def test_env_manifest_records_key_names_only_never_values():
    base = {"PATH": "/usr/bin", "MY_SECRET": "hunter2"}
    _child, manifest = claude_cli.build_child_env(base)
    assert "MY_SECRET" in manifest["inherited_keys"]
    assert "hunter2" not in str(manifest)


def test_manifest_flags_billing_variables_without_leaking_them():
    _child, manifest = claude_cli.build_child_env({"ANTHROPIC_API_KEY": "sk-ant-SECRET"})
    assert manifest["billing_guard_vars_present"] == ["ANTHROPIC_API_KEY"]
    assert "sk-ant-SECRET" not in str(manifest)


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


def test_smoke_limits_cannot_launch_many_sessions():
    assert config.SMOKE_LIMITS.max_tasks_per_invocation == 1
    assert config.SMOKE_LIMITS.max_sessions_per_invocation <= 6


def test_pilot_requires_an_explicit_flag():
    assert config.PILOT_LIMITS.require_explicit_pilot_flag is True


def test_arm_c_is_not_implemented():
    assert config.ARMS == ("A", "B")


def test_agent_tool_is_excluded_from_the_shared_pool():
    for role_tools in config.ROLE_TOOLS.values():
        assert "Agent" not in role_tools
        assert "Task" not in role_tools


def test_arms_draw_from_the_same_capability_pool():
    """Arm A must not have a capability Arm B's implementer lacks, or vice versa."""
    assert set(config.ROLE_TOOLS["solo"]) == set(config.ROLE_TOOLS["implementer"])
    assert set(config.ROLE_TOOLS["investigator"]) <= set(config.ROLE_TOOLS["solo"])
    assert set(config.ROLE_TOOLS["solo"]) == set(config.TOOL_POOL)


def test_investigator_cannot_write():
    assert not (set(config.ROLE_TOOLS["investigator"]) & {"Edit", "Write", "NotebookEdit"})
    assert "Edit" in config.ROLE_DISALLOWED_TOOLS["investigator"]


def test_coordinator_has_no_repository_access_in_the_baseline():
    assert config.ROLE_TOOLS["coordinator"] == ()
