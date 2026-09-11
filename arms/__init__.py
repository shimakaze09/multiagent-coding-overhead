"""Shared run scaffolding for the arms.

Everything both arms must do identically lives here: run directory layout,
metadata, workspace preparation, verification, finalization. Arm-specific
orchestration lives in single.py (Arm A) and multi_nl.py (Arm B).
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import config
from analysis import exposure_provenance
from harness import agent, claude_cli, events as ev, telemetry, tools, workspace as ws_mod
from tasks.registry import Task

RUN_ID_TIME_FMT = "%Y%m%dT%H%M%SZ"


def make_run_id(task: Task, arm: str, repeat_id: int) -> str:
    stamp = datetime.now(timezone.utc).strftime(RUN_ID_TIME_FMT)
    return f"{stamp}_{task.task_id}_{arm}_r{repeat_id}"


@dataclass
class RunSession:
    run_id: str
    run_dir: Path
    task: Task
    arm: str
    repeat_id: int
    cfg: config.RunConfig
    ctx: agent.RunContext
    workspace: ws_mod.Workspace
    metadata: dict
    handoffs: list[dict] = field(default_factory=list)
    agent_results: list[agent.AgentResult] = field(default_factory=list)

    # -- handoffs -------------------------------------------------------

    def record_handoff(
        self, *, index: int, sender: str, recipient: str, label: str, body: str
    ) -> dict:
        """Store a natural-language handoff exactly, plus descriptive sizes."""
        fname = f"{index:02d}_{sender}_to_{recipient}.txt"
        path = self.run_dir / "handoffs" / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

        rec = {
            "index": index,
            "sender": sender,
            "recipient": recipient,
            "label": label,
            "path": str(path.relative_to(self.run_dir)).replace("\\", "/"),
            "sizes": tools.handoff_size(body),
            "sha": tools.content_sha(body),
        }
        self.handoffs.append(rec)
        self.ctx.log.append(
            ev.HANDOFF_SENT,
            agent_id=sender,
            payload={**rec, "text": body},
        )
        self.ctx.log.append(
            ev.AGENT_MESSAGE,
            agent_id=recipient,
            payload={
                "direction": "received_handoff",
                "from": sender,
                "label": label,
                "text": body,
                "sizes": rec["sizes"],
            },
        )
        return rec


def start_run(
    *,
    task: Task,
    arm: str,
    repeat_id: int,
    cfg: config.RunConfig,
    cli: config.ClaudeCli,
    capability_report: dict,
    runs_dir: Optional[Path] = None,
    topology: Optional[dict] = None,
) -> RunSession:
    """Create the run directory, prepare an isolated workspace, open the log.

    `topology` is given only by the Stage-1 arm (C1) and the Stage-2 arms. Arm A
    and Arm B never pass it, so their metadata and config_hash are exactly as
    before. A topology may name its own `stage` and `experiment_schema_version`
    (Stage 2 does); C1_TOPOLOGY names neither and keeps stage 1 / schema 2."""
    billing = config.preflight_billing_guard()

    run_id = make_run_id(task, arm, repeat_id)
    base = Path(runs_dir or config.RUNS_DIR)
    run_dir = base / run_id
    if run_dir.exists():
        run_id = f"{run_id}_{uuid.uuid4().hex[:6]}"
        run_dir = base / run_id
    run_dir.mkdir(parents=True)

    log = ev.EventLog(
        run_dir / "events.jsonl", run_id, default_task_id=task.task_id, default_arm=arm
    )

    # The held-out verifier must not exist in the workspace during the agent phase.
    # Protected paths (e.g. the visible benchmark tests) are made read-only here,
    # in shared code, so Arm A and Arm B get identical protection.
    workspace = ws_mod.prepare_workspace(
        task.source_tree,
        run_dir / "workspace",
        repo_name=task.repo,
        exclude=(task.verifier_dest_name,),
        read_only_paths=task.read_only_paths,
    )

    _child_env, env_manifest = claude_cli.build_child_env()

    metadata = {
        "run_id": run_id,
        "task_id": task.task_id,
        "task_category": task.category,
        "arm": arm,
        "repeat_id": repeat_id,
        "created_at": ev.utc_now_iso(),
        "stage": 0,
        "arm_c_implemented": False,
        "config": cfg.as_dict(),
        "config_hash": cfg.config_hash(),
        "effective_config": cfg.effective_config(),
        "permission_policy": {
            "allowed_tools_policy_id": config.ALLOWED_TOOLS_POLICY_ID,
            "allowed_tools": list(config.BASH_TEST_ALLOWLIST),
            "permission_mode": cfg.permission_mode,
            "permission_prompts": cfg.permission_prompts,
            "applied_to_roles": {
                r: list(config.allowed_tools_for_role(r)) for r in sorted(config.ROLE_TOOLS)
            },
            "identical_across_arms": True,
            "note": config.ALLOWED_TOOLS_POLICY_NOTE,
        },
        "task": task.as_dict(),
        "workspace": workspace.as_dict(),
        "cli": {
            "path": cli.path,
            "version": cli.version,
            "discovered_via": cli.discovered_via,
        },
        "capability_report": capability_report,
        "billing_guard": {
            "ok": billing.ok,
            "vars_present": list(billing.present_vars),
            "subscription_execution": True,
            "api_charge": "false_expected",
            "note": "presence-only check; no credential value was read or logged",
        },
        "env_manifest": env_manifest,
        "versions": {
            "event_schema_version": ev.SCHEMA_VERSION,
            "parser_version": telemetry.PARSER_VERSION,
            "cli_wrapper_version": claude_cli.WRAPPER_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "git": _git_version(),
        },
        # PREREGISTRATION amendment 11, prospective from its commit: this run's
        # content matches are classified by provenance before they can count as
        # held-out exposure. Runs without this record predate the amendment and
        # keep the frozen classification. Analysis-side only; no config identity
        # and no prompt depends on it.
        "exposure_provenance_rule": exposure_provenance.rule_record(),
        "context_residency_level": 2,
        "context_residency_note": (
            "Level 2 (reconstructed from session event history). Level 1 is not "
            "achievable: Claude Code's hidden system prompt and the provider HTTP "
            "request are not observable."
        ),
    }
    if topology is not None:
        # Stage-1 arm: its own config identity. base_config_hash is the unchanged
        # A/B harness configuration it runs on.
        metadata.update({
            "stage": topology.get("stage", 1),
            "experiment_schema_version": topology.get(
                "experiment_schema_version", config.EXPERIMENT_SCHEMA_VERSION_C1),
            "arm_topology": topology["arm_topology"],
            "topology_version": topology["topology_version"],
            "topology": topology,
            "base_config_hash": cfg.config_hash(),
            "config_hash": config.topology_config_hash(cfg, topology),
        })
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )

    ctx = agent.RunContext(
        run_id=run_id,
        task_id=task.task_id,
        arm=arm,
        run_dir=run_dir,
        log=log,
        cli_path=cli.path,
        model=cfg.model,
        limits=cfg.limits,
        include_hook_events=cfg.include_hook_events,
        permission_mode=cfg.permission_mode,
        permission_prompts=cfg.permission_prompts,
    )

    log.append(
        ev.TASK_START,
        payload={
            "task_id": task.task_id,
            "category": task.category,
            "arm": arm,
            "repeat_id": repeat_id,
            "statement": task.statement,
            "statement_sizes": tools.handoff_size(task.statement),
            "model": cfg.model,
            "limits": cfg.limits.as_dict(),
        },
    )
    log.append(
        ev.WORKSPACE_PREPARED,
        payload={
            **workspace.as_dict(),
            "files": workspace.files(),
            "verifier_withheld": task.verifier_dest_name,
        },
    )
    return RunSession(
        run_id=run_id,
        run_dir=run_dir,
        task=task,
        arm=arm,
        repeat_id=repeat_id,
        cfg=cfg,
        ctx=ctx,
        workspace=workspace,
        metadata=metadata,
    )


def _git_version() -> str:
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=30)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def finish_run(session: RunSession) -> dict:
    """Capture the diff, run the held-out verifier, write the run summary."""
    task = session.task
    ctx = session.ctx

    diff = session.workspace.diff_vs_base()
    (session.run_dir / "workspace_diff.patch").write_text(diff, encoding="utf-8")
    final_tree = session.workspace.working_tree_hash()

    # Workspace integrity, measured BEFORE the verifier is injected. Reported
    # alongside SOLVED, never folded into it.
    changes = session.workspace.changed_paths()
    expected = list(task.expected_modified_paths)
    integrity = {
        "changed_paths": changes,
        "read_only_patterns": list(session.workspace.read_only_patterns),
        "read_only_files": list(session.workspace.read_only_files),
        "protected_paths_changed": [
            c for c in changes if session.workspace.is_protected(c["path"])
        ],
        "expected_modified_paths": expected,
        "changed_outside_expected_scope": sorted(
            c["path"] for c in changes if expected and c["path"] not in expected
        ),
    }

    # Visible tests, run by the harness on the workspace exactly as the agent left
    # it and BEFORE the held-out verifier exists in it. Reported separately:
    # SOLVED comes from the held-out verifier only.
    visible = None
    if task.visible_test_command:
        vt = ws_mod.run_command(session.workspace, task.visible_test_command)
        visible = {
            "ran": True,
            "passed": vt.exit_code == 0,
            "exit_code": vt.exit_code,
            "argv": vt.argv,
            "wall_seconds": vt.wall_seconds,
            "stdout_tail": vt.stdout[-2000:],
        }
        vis_dir = session.run_dir / "verify"
        vis_dir.mkdir(parents=True, exist_ok=True)
        (vis_dir / "visible_tests_stdout.txt").write_text(vt.stdout, encoding="utf-8")
        (vis_dir / "visible_tests_stderr.txt").write_text(vt.stderr, encoding="utf-8")
        (vis_dir / "visible_tests.json").write_text(
            json.dumps(visible, indent=2, sort_keys=True), encoding="utf-8"
        )
        ctx.log.append(
            ev.VISIBLE_TESTS,
            payload={
                "passed": visible["passed"],
                "exit_code": visible["exit_code"],
                "argv": visible["argv"],
                "wall_seconds": visible["wall_seconds"],
                "note": "reported separately; SOLVED comes from the held-out verifier only",
            },
        )

    verification = ws_mod.verify(
        session.workspace,
        verifier_source=task.verifier_path,
        verifier_dest_name=task.verifier_dest_name,
        command=task.verifier_command,
    )
    vdir = session.run_dir / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "verifier_stdout.txt").write_text(verification.stdout, encoding="utf-8")
    (vdir / "verifier_stderr.txt").write_text(verification.stderr, encoding="utf-8")
    (vdir / "verification.json").write_text(
        json.dumps(verification.as_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )

    ctx.log.append(
        ev.VERIFICATION,
        payload={
            "solved": verification.solved,
            "exit_code": verification.exit_code,
            "argv": verification.argv,
            "wall_seconds": verification.wall_seconds,
            "judge": "deterministic_held_out_tests",
            "llm_judge_used": False,
            "stdout_tail": verification.stdout[-4000:],
        },
    )

    all_acqs = [a for r in session.agent_results for a in r.acquisitions]
    coverage = tools.coverage(all_acqs, minimum=session.cfg.min_acquisition_coverage)

    sessions_summary = [
        {
            "session_key": r.session_key,
            "agent_id": r.spec.agent_id,
            "role": r.spec.role,
            "session_id": r.session_id,
            "exit_code": r.cli.exit_code,
            "termination_reason": r.cli.termination_reason,
            "wall_seconds": r.cli.wall_seconds,
            "billing_ok": r.cli.billing_ok,
            "billing_note": r.cli.billing_note,
            "summary": r.cli.summary,
        }
        for r in session.agent_results
    ]

    subagent_spawned = [
        s["summary"].get("subagent_spawned") for s in sessions_summary
    ]
    arm_label_valid = all(not v for v in subagent_spawned if v is not None)

    summary = {
        "run_id": session.run_id,
        "task_id": task.task_id,
        "arm": session.arm,
        "repeat_id": session.repeat_id,
        "solved": verification.solved,
        "verification": verification.as_dict(),
        "sessions": sessions_summary,
        "session_count": len(sessions_summary),
        "handoffs": session.handoffs,
        "handoff_total_chars": sum(h["sizes"]["chars"] for h in session.handoffs),
        "handoff_total_estimated_tokens": sum(
            h["sizes"]["estimated_tokens"] for h in session.handoffs
        ),
        "acquisition_coverage": coverage.as_dict(),
        "final_tree_hash": final_tree,
        "base_commit": session.workspace.base_commit,
        "diff_bytes": len(diff.encode("utf-8")),
        "solved_basis": "held-out verifier exit code only",
        "visible_tests": visible,
        "workspace_integrity": integrity,
        "all_sessions_subscription_ok": all(s["billing_ok"] for s in sessions_summary)
        if sessions_summary
        else False,
        "arm_label_valid": arm_label_valid,
        "arm_label_note": (
            "No internal subagent fan-out observed."
            if arm_label_valid
            else "subagent_stats.spawned was non-zero: the arm label for this run is invalid."
        ),
        "ended_at": ev.utc_now_iso(),
    }
    (session.run_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )

    ctx.log.append(
        ev.TASK_END,
        payload={
            "solved": verification.solved,
            "session_count": len(sessions_summary),
            "acquisition_coverage": coverage.acquisition_coverage,
            "meets_minimum_coverage": coverage.meets_minimum,
            "arm_label_valid": arm_label_valid,
        },
    )
    ctx.log.close()
    return summary
