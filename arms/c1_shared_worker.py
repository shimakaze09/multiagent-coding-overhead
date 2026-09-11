"""Stage 1 - C1_shared_worker_context (PREREGISTRATION section 16).

    User task
       |
    Coordinator                    (physical session 1, fresh)
       |
    Worker - Investigator phase    (physical session 2, fresh, read-only)
       |
    Coordinator                    (physical session 1, resumed)
       |
    Worker - Implementer phase     (physical session 2, RESUMED, may edit/test)
       |
    Coordinator                    (physical session 1, resumed)

Still three logical roles and the same five CLI invocations as Arm B, but only
TWO fresh physical sessions instead of three: the Investigator's Claude Code
session is resumed as the Implementer. The Coordinator is identical to Arm B
(same prompt functions). The Investigator phase uses Arm B's investigator prompt
and read-only tool policy.

What the resumed Worker receives in its Implementer phase: the Coordinator's
implementation instruction, and nothing else. It already holds the task
statement, its own investigation and its own report in session history (Claude
Code --resume), so the report is NOT forwarded back to it and the task is NOT
resent. Nothing tells it to avoid rereading; repeated acquisition stays a
dependent variable.

Tool policy is per CLI invocation (--tools / --disallowedTools are per-process
flags), so the resumed invocation carries the Implementer policy. Every run
verifies the transition from each invocation's own `init` event
(report.worker_transition_check). A run where it cannot be verified is invalid.
"""

from __future__ import annotations

import json
from typing import Optional

import config
from arms import RunSession, finish_run, multi_nl, start_run
from harness import agent, events as ev, tools
from tasks.registry import Task

ARM = config.C1_ARM
ARM_TOPOLOGY = config.C1_ARM_TOPOLOGY
TOPOLOGY_VERSION = config.C1_TOPOLOGY_VERSION
IMPLEMENTER_RESUME_MARKER = "You are now the Implementer"


# --------------------------------------------------------------------------
# Prompts. The Coordinator and Investigator prompts are Arm B's, unchanged.
# --------------------------------------------------------------------------


def implementer_resume_prompt(coordinator_instruction: str) -> str:
    """The Worker's Implementer-phase prompt: the instruction only."""
    return agent.build_prompt(
        [
            ("Instruction from the Coordinator", coordinator_instruction),
            (
                "Working agreement",
                f"{IMPLEMENTER_RESUME_MARKER}. You are continuing in the same "
                "checked-out copy of the repository at the current working "
                "directory, and you may now edit files and run the project's "
                "tests. Make the change and run the project's tests. Then reply "
                "with a report for the Coordinator and nothing else - your entire "
                "reply will be delivered to the Coordinator as a message.",
            ),
        ]
    )


def _policy(spec: agent.AgentSpec) -> dict:
    return {"tools": list(spec.tools), "disallowed_tools": list(spec.disallowed_tools),
            "allowed_tools": list(spec.allowed_tools)}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run(
    *,
    task: Task,
    repeat_id: int,
    cfg: config.RunConfig,
    cli: config.ClaudeCli,
    capability_report: dict,
    runs_dir=None,
) -> dict:
    session: RunSession = start_run(
        task=task,
        arm=ARM,
        repeat_id=repeat_id,
        cfg=cfg,
        cli=cli,
        capability_report=capability_report,
        runs_dir=runs_dir,
        topology=config.C1_TOPOLOGY,
    )

    coordinator = agent.AgentSpec.for_role("coordinator")
    investigator = agent.AgentSpec.for_role("investigator")
    implementer = agent.AgentSpec.for_role("implementer")
    invocations: list[dict] = []
    transition: dict = {}

    def note(result: agent.AgentResult, step: str, physical: str) -> None:
        invocations.append({
            "session_key": result.session_key,
            "step": step,
            "logical_role": result.spec.role,
            "physical_session": physical,
            "physical_session_id": result.session_id,
            "is_resume": bool(result.invocation.resume_session_id),
            "resume_target": result.invocation.resume_session_id,
            "tool_policy": _policy(result.spec),
            "prompt_sha": tools.content_sha(result.invocation.stdin_text),
            "prompt_chars": len(result.invocation.stdin_text),
        })

    def write_topology() -> None:
        record = {
            "arm": ARM,
            "arm_topology": ARM_TOPOLOGY,
            "topology_version": TOPOLOGY_VERSION,
            "invocations": invocations,
            "worker_transition": transition,
        }
        (session.run_dir / "topology.json").write_text(
            json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")

    def abort(step: str, result: agent.AgentResult, reason: Optional[str] = None) -> dict:
        session.ctx.log.append(
            ev.ERROR,
            agent_id=result.spec.agent_id,
            payload={
                "step": step,
                "reason": reason or "agent session did not complete successfully",
                "exit_code": result.cli.exit_code,
                "termination_reason": result.cli.termination_reason,
                "cli_errors": result.cli.parsed.errors[:5],
                "note": "C1 chain stopped early; the run is recorded as unsolved.",
            },
        )
        write_topology()
        return finish_run(session)

    def must_stop(result: agent.AgentResult) -> Optional[str]:
        if not result.ok:
            return "agent session did not complete successfully"
        return agent.quota_stop_reason(result.cli)

    # -- 1. Coordinator: kickoff (identical to Arm B) ----------------------
    r1 = agent.run_agent(
        session.ctx, coordinator,
        prompt=multi_nl.coordinator_kickoff_prompt(task),
        workspace=session.workspace, session_index=1,
        prompt_inputs={"task_statement_verbatim": task.statement, "upstream_reports": [],
                       "step": "coordinator_kickoff", "logical_role": "coordinator",
                       "physical_session": "coordinator"},
    )
    session.agent_results.append(r1)
    note(r1, "coordinator_kickoff", "coordinator")
    stop = must_stop(r1)
    if stop:
        return abort("coordinator_kickoff", r1, stop)
    coordinator_session_id = r1.session_id
    investigation_instruction = r1.final_text
    session.record_handoff(index=1, sender="coordinator", recipient="investigator",
                           label="investigation_instruction", body=investigation_instruction)

    # -- 2. Worker, Investigator phase (Arm B's investigator prompt/policy) --
    r2 = agent.run_agent(
        session.ctx, investigator,
        prompt=multi_nl.investigator_prompt(task, investigation_instruction),
        workspace=session.workspace, session_index=2,
        prompt_inputs={"task_statement_verbatim": task.statement,
                       "coordinator_instruction": investigation_instruction,
                       "upstream_reports": [], "step": "investigate",
                       "logical_role": "investigator", "physical_session": "worker"},
    )
    session.agent_results.append(r2)
    note(r2, "investigate", "worker")
    stop = must_stop(r2)
    if stop:
        return abort("investigate", r2, stop)
    worker_session_id = r2.session_id
    investigation_report = r2.final_text
    session.record_handoff(index=2, sender="investigator", recipient="coordinator",
                           label="investigation_report", body=investigation_report)

    # -- 3. Coordinator: plan (identical to Arm B) -------------------------
    r3 = agent.run_agent(
        session.ctx, coordinator,
        prompt=multi_nl.coordinator_after_investigation_prompt(investigation_report),
        workspace=session.workspace, session_index=3,
        prompt_inputs={"investigator_report": investigation_report, "step": "coordinator_plan",
                       "logical_role": "coordinator", "physical_session": "coordinator"},
        resume_session_id=coordinator_session_id,
    )
    session.agent_results.append(r3)
    note(r3, "coordinator_plan", "coordinator")
    stop = must_stop(r3)
    if stop:
        return abort("coordinator_plan", r3, stop)
    implementation_instruction = r3.final_text
    session.record_handoff(index=3, sender="coordinator", recipient="implementer",
                           label="implementation_instruction", body=implementation_instruction)
    # Deliberately NO forwarded_investigation_report: the Worker wrote it.

    # -- 4. Worker, Implementer phase: RESUME of the Investigator session ----
    if not worker_session_id:
        return abort("implement_resume", r2, "the Worker session id was not reported; cannot resume")
    resume_prompt = implementer_resume_prompt(implementation_instruction)
    transition.update({
        "physical_session_id": worker_session_id,
        "previous_logical_role": "investigator",
        "new_logical_role": "implementer",
        "previous_session_key": r2.session_key,
        "new_session_key": "04_implementer",
        "resume_target": worker_session_id,
        "new_prompt": resume_prompt,
        "new_prompt_sha": tools.content_sha(resume_prompt),
        "previous_tool_policy": _policy(investigator),
        "new_tool_policy": _policy(implementer),
        "investigator_report_forwarded_to_worker": False,
        "task_statement_resent": False,
    })
    session.ctx.log.append(ev.WORKER_ROLE_TRANSITION, agent_id="implementer",
                           payload=dict(transition))
    r4 = agent.run_agent(
        session.ctx, implementer,
        prompt=resume_prompt,
        workspace=session.workspace, session_index=4,
        prompt_inputs={"coordinator_instruction": implementation_instruction,
                       "step": "implement_resume", "logical_role": "implementer",
                       "physical_session": "worker"},
        resume_session_id=worker_session_id,
    )
    session.agent_results.append(r4)
    note(r4, "implement_resume", "worker")
    transition["resumed_session_id_reported"] = r4.session_id
    transition["session_id_preserved"] = r4.session_id == worker_session_id
    stop = must_stop(r4)
    if stop:
        return abort("implement_resume", r4, stop)
    implementation_report = r4.final_text
    session.record_handoff(index=4, sender="implementer", recipient="coordinator",
                           label="implementation_report", body=implementation_report)

    # -- 5. Coordinator: wrap up (identical to Arm B) ----------------------
    r5 = agent.run_agent(
        session.ctx, coordinator,
        prompt=multi_nl.coordinator_wrapup_prompt(implementation_report),
        workspace=session.workspace, session_index=5,
        prompt_inputs={"implementer_report": implementation_report, "step": "coordinator_wrapup",
                       "logical_role": "coordinator", "physical_session": "coordinator"},
        resume_session_id=coordinator_session_id,
    )
    session.agent_results.append(r5)
    note(r5, "coordinator_wrapup", "coordinator")
    if r5.ok:
        session.record_handoff(index=5, sender="coordinator", recipient="user",
                               label="final_result", body=r5.final_text)

    write_topology()
    return finish_run(session)
