"""The Bash permission policy the harness generates must be exactly the intended
narrow allowlist - nothing wider, and identical for Arm A and Arm B.

Context: the first real Arm A run could not run the task's tests. Claude Code
refused two `python -m pytest` calls (`system/permission_denied`,
decision_reason_type="asyncAgent") because the session has no approval surface.
Read-only Bash in the same run was unaffected, so the only missing capability is
running the test suite.

These tests are the guard against that fix quietly becoming "allow arbitrary
Bash".
"""

from __future__ import annotations

import json
import re

import pytest

import config
from harness import agent, claude_cli

# The intended policy, written out literally. If the policy changes on purpose,
# this constant and the reason must change together.
EXPECTED_ALLOWLIST = (
    "Bash(python -m pytest *)",
    "Bash(python -m pytest)",
    "Bash(python -m pytest:*)",
)

ALL_ROLES = ("solo", "coordinator", "investigator", "implementer")


# --------------------------------------------------------------------------
# The allowlist itself
# --------------------------------------------------------------------------


def test_allowlist_is_exactly_the_intended_narrow_set():
    assert config.BASH_TEST_ALLOWLIST == EXPECTED_ALLOWLIST


def test_every_rule_targets_only_the_pytest_module_invocation():
    """Each rule must be Bash(...) whose content starts with `python -m pytest`."""
    for rule in config.BASH_TEST_ALLOWLIST:
        m = re.fullmatch(r"Bash\((.*)\)", rule)
        assert m, f"{rule!r} is not a Bash(content) rule"
        content = m.group(1)
        assert content.startswith("python -m pytest"), content
        remainder = content[len("python -m pytest"):]
        assert remainder in ("", " *", ":*"), (
            f"{rule!r} widens beyond the pytest invocation: {remainder!r}"
        )


def test_no_rule_grants_arbitrary_bash():
    forbidden = {
        "Bash",
        "Bash(*)",
        "Bash( *)",
        "Bash(:*)",
        "Bash(python *)",
        "Bash(python:*)",
        "Bash(python3 *)",
        "Bash(sh *)",
        "Bash(bash *)",
        "Bash(sudo *)",
    }
    assert not (set(config.BASH_TEST_ALLOWLIST) & forbidden)
    for rule in config.BASH_TEST_ALLOWLIST:
        assert rule != "Bash"
        # a bare interpreter prefix is arbitrary code execution
        assert not re.fullmatch(r"Bash\((python3?|sh|bash|pwsh|node|sudo|env)[ :]\*\)", rule)


def test_python_dash_c_is_not_allowed():
    """The previous agent attempted `python -c "..."`. The deterministic pytest
    path is sufficient for this fixture, so inline scripting stays denied."""
    for rule in config.BASH_TEST_ALLOWLIST:
        assert "-c" not in rule
    assert not any("python -c" in r for r in config.BASH_TEST_ALLOWLIST)


def test_pytest_console_script_is_not_allowed():
    """Only the `python -m pytest` entry point, which is what this fixture's
    README and held-out verifier both use."""
    assert not any(re.fullmatch(r"Bash\(pytest[ :]?\*?\)", r) for r in config.BASH_TEST_ALLOWLIST)


def test_no_write_read_or_network_command_is_allowed():
    joined = " ".join(config.BASH_TEST_ALLOWLIST)
    for dangerous in ("rm ", "mv ", "cp ", "curl", "wget", "cat ", "git ", "pip ",
                      "chmod", "ssh", ">", "|"):
        assert dangerous not in joined, dangerous


def test_rule_count_is_small_and_deliberate():
    assert len(config.BASH_TEST_ALLOWLIST) == 3
    assert len(set(config.BASH_TEST_ALLOWLIST)) == 3


def test_all_three_spellings_express_the_same_single_permission():
    contents = {
        re.fullmatch(r"Bash\((.*)\)", r).group(1).replace(" *", "").replace(":*", "")
        for r in config.BASH_TEST_ALLOWLIST
    }
    assert contents == {"python -m pytest"}


# --------------------------------------------------------------------------
# Identical across arms and roles
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_role_gets_the_identical_allowlist(role):
    assert config.allowed_tools_for_role(role) == EXPECTED_ALLOWLIST


def test_unknown_role_is_refused_rather_than_silently_defaulted():
    with pytest.raises(KeyError):
        config.allowed_tools_for_role("nonexistent_role")


def test_agent_specs_for_both_arms_carry_the_same_allowlist():
    specs = {r: agent.AgentSpec.for_role(r) for r in ALL_ROLES}
    assert len({s.allowed_tools for s in specs.values()}) == 1
    for s in specs.values():
        assert s.allowed_tools == EXPECTED_ALLOWLIST
    # Arm A's solo agent and Arm B's implementer must be indistinguishable in
    # both capability pool and permission policy.
    assert specs["solo"].tools == specs["implementer"].tools
    assert specs["solo"].allowed_tools == specs["implementer"].allowed_tools


def test_role_policy_differences_are_only_tools_not_permissions():
    solo = agent.AgentSpec.for_role("solo")
    inv_spec = agent.AgentSpec.for_role("investigator")
    coord = agent.AgentSpec.for_role("coordinator")
    # permission policy identical
    assert solo.allowed_tools == inv_spec.allowed_tools == coord.allowed_tools
    # tool sets differ by the pre-existing role design
    assert coord.tools == ()
    assert "Edit" not in inv_spec.tools
    assert "Edit" in inv_spec.disallowed_tools


# --------------------------------------------------------------------------
# The generated argv
# --------------------------------------------------------------------------


def _inv(role="solo", tmp_path=None):
    spec = agent.AgentSpec.for_role(role)
    return claude_cli.build_invocation(
        cli_path="claude",
        session_key=f"01_{role}",
        agent_id=role,
        role=role,
        prompt="p",
        cwd=str(tmp_path or "."),
        model="sonnet",
        tools=spec.tools,
        allowed_tools=spec.allowed_tools,
        disallowed_tools=spec.disallowed_tools,
        base_env={"PATH": "x"},
    )


def test_argv_passes_each_rule_as_its_own_argument(tmp_path):
    """The rules contain spaces, so a comma-joined single argument would be
    ambiguous. `--allowedTools <tools...>` is variadic."""
    argv = _inv(tmp_path=tmp_path).argv
    i = argv.index("--allowedTools")
    assert argv[i + 1 : i + 1 + 3] == list(EXPECTED_ALLOWLIST)
    for rule in EXPECTED_ALLOWLIST:
        assert rule in argv, "each rule must appear verbatim as one argv element"
    assert not any("," in a and a.startswith("Bash(") for a in argv)


def test_argv_uses_the_flag_spelling_the_installed_cli_documents(fixtures_dir, tmp_path):
    help_text = (fixtures_dir / "cli_help_2.1.260.txt").read_text(encoding="utf-8")
    assert claude_cli._help_mentions_flag(help_text, "--allowedTools")
    assert "--allowedTools" in _inv(tmp_path=tmp_path).argv


def test_argv_still_denies_everything_else(tmp_path):
    """The allowlist auto-approves; `--permission-prompts none` denies the rest.
    Both must be present or the policy is not narrow."""
    argv = _inv(tmp_path=tmp_path).argv
    i = argv.index("--permission-prompts")
    assert argv[i + 1] == "none"
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "bypassPermissions" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--allow-dangerously-skip-permissions" not in argv


def test_bypass_permissions_is_never_used(tmp_path):
    for role in ALL_ROLES:
        argv = _inv(role, tmp_path).argv
        assert "bypassPermissions" not in " ".join(argv)


def test_coordinator_argv_has_no_bash_so_the_allowlist_is_inert(tmp_path):
    argv = _inv("coordinator", tmp_path).argv
    assert argv[argv.index("--tools") + 1] == ""
    # the policy is still passed, so the configuration is literally identical
    assert "--allowedTools" in argv


def test_argv_is_byte_identical_between_arm_a_solo_and_arm_b_implementer(tmp_path):
    a = _inv("solo", tmp_path).argv
    b = _inv("implementer", tmp_path).argv

    def strip_volatile(argv):
        out = list(argv)
        for flag in ("--session-id",):
            if flag in out:
                i = out.index(flag)
                del out[i : i + 2]
        return out

    assert strip_volatile(a) == strip_volatile(b)


def test_invocation_records_the_policy_for_reconstruction(tmp_path):
    inv = _inv(tmp_path=tmp_path)
    assert inv.allowed_tools == list(EXPECTED_ALLOWLIST)
    assert inv.permission_prompts == "none"
    assert "allowed_tools" in claude_cli.RECONSTRUCTION_FIELDS
    assert "permission_prompts" in claude_cli.RECONSTRUCTION_FIELDS
    d = inv.as_dict()
    assert d["allowed_tools"] == list(EXPECTED_ALLOWLIST)


def test_a_changed_allowlist_is_caught_by_the_reconstruction_gate(tmp_path):
    stored = _inv(tmp_path=tmp_path).as_dict()
    tampered = dict(stored, allowed_tools=["Bash"])
    diffs = claude_cli.compare_reconstruction(
        claude_cli.reconstruct_from_log(stored),
        claude_cli.reconstruct_from_log(tampered),
    )
    assert any("allowed_tools" in d for d in diffs)


# --------------------------------------------------------------------------
# config_hash
# --------------------------------------------------------------------------


def test_config_hash_is_stable_and_covers_the_permission_policy():
    a = config.RunConfig().config_hash()
    b = config.RunConfig().config_hash()
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{32}", a)

    eff = config.RunConfig().effective_config()
    assert eff["allowed_tools"] == list(EXPECTED_ALLOWLIST)
    assert eff["allowed_tools_policy_id"] == "narrow_pytest_v1"
    assert eff["permission_prompts"] == "none"
    assert eff["permission_mode"] == "acceptEdits"
    for role in ALL_ROLES:
        assert eff["role_allowed_tools"][role] == list(EXPECTED_ALLOWLIST)


def test_config_hash_changes_if_the_allowlist_changes(monkeypatch):
    before = config.RunConfig().config_hash()
    monkeypatch.setattr(config, "BASH_TEST_ALLOWLIST", ("Bash",))
    after = config.RunConfig().config_hash()
    assert before != after, "widening the allowlist must change config_hash"


def test_config_hash_changes_if_permission_mode_changes():
    a = config.RunConfig().config_hash()
    b = config.RunConfig(permission_mode="bypassPermissions").config_hash()
    assert a != b


def test_config_hash_is_independent_of_analysis_side_versions():
    """A parser or coverage-formula fix must not change what the agent was
    allowed to do, so it must not change config_hash."""
    eff = config.RunConfig().effective_config()
    blob = json.dumps(eff, sort_keys=True)
    assert "parser_version" not in blob
    assert "coverage_formula" not in blob


def test_config_as_dict_exposes_policy_and_hash():
    d = config.RunConfig().as_dict()
    assert d["allowed_tools"] == list(EXPECTED_ALLOWLIST)
    assert d["allowed_tools_policy_id"] == "narrow_pytest_v1"
    assert d["config_hash"] == config.RunConfig().config_hash()


# --------------------------------------------------------------------------
# Observability requirements are not weakened
# --------------------------------------------------------------------------


def test_coverage_minimum_is_unchanged():
    assert config.RunConfig().min_acquisition_coverage == 0.90


def test_denial_instrumentation_is_preserved():
    """The allowlist must not remove our ability to see a denial: anything not
    allowlisted is still denied, and must still be captured."""
    from harness import telemetry, tools

    ps = telemetry.parse_stream(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s",
                        "apiKeySource": "none"}),
            json.dumps({"type": "system", "subtype": "permission_denied",
                        "tool_name": "Bash", "tool_use_id": "t1",
                        "decision_reason_type": "asyncAgent",
                        "decision_reason": "no approval surface in this session"}),
        ]
    )
    assert ps.denied_tool_use_ids == {"t1"}
    assert telemetry.session_summary(ps)["permission_denied_count"] == 1
    assert tools.TOOL_DENIED in tools.NON_ACQUISITION_CLASSES


def test_verification_is_still_a_non_acquisition_category():
    """Allowing pytest must not turn test output into 'acquisition'."""
    from harness import tools

    assert tools.BASH_VERIFICATION in tools.NON_ACQUISITION_CLASSES
    assert tools.BASH_VERIFICATION not in tools.ACQUISITION_CLASSES
    a = tools.classify_tool_call(
        tool_use_id="t", tool_name="Bash",
        tool_input={"command": "python -m pytest tests -q"},
        result_text="12 passed", agent_id="solo", session_key="01_solo",
        start_ts=None, end_ts=None, start_line=1, end_line=2, is_error=False,
    )
    assert a.acquisition_class == tools.BASH_VERIFICATION
    assert a.is_acquisition_candidate is False
