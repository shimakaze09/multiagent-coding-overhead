"""Arm B - conventional natural-language multi-agent, fixed topology.

    User task
       |
    Coordinator
       |
    Investigator
       |
    Coordinator
       |
    Implementer
       |
    Coordinator
       |
    Final result

Exactly three logical agents. The Coordinator is one Claude Code session resumed
across its three turns (it is one logical agent with memory, as in conventional
multi-agent systems); the Investigator and Implementer each get their own
session, because they are logically independent.

Handoffs are ordinary natural language and are stored verbatim. The Implementer
is never told to avoid re-reading anything: repeated acquisition is the dependent
variable.
"""

from __future__ import annotations

from typing import Optional

import config
from arms import RunSession, finish_run, start_run
from harness import agent, events as ev
from tasks.registry import Task

ARM = "B"


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------


def coordinator_kickoff_prompt(task: Task) -> str:
    return agent.build_prompt(
        [
            ("Original task (verbatim, from the user)", task.statement),
            (
                "Your team",
                "You have two teammates:\n"
                "- Investigator: can read and search the repository, but cannot "
                "change any file.\n"
                "- Implementer: can read the repository, edit files, and run tests.\n\n"
                "You have no access to the repository yourself.",
            ),
            (
                "What to do now",
                "Write the instruction you want to send to the Investigator. "
                "Reply with that instruction and nothing else - your entire reply "
                "will be delivered to the Investigator as a message.",
            ),
        ]
    )


def investigator_prompt(task: Task, coordinator_instruction: str) -> str:
    return agent.build_prompt(
        [
            ("Original task (verbatim, from the user)", task.statement),
            ("Instruction from the Coordinator", coordinator_instruction),
            (
                "Working agreement",
                "You are in a checked-out copy of the repository at the current "
                "working directory. Investigate and then report back to the "
                "Coordinator. Do not modify any file. Reply with your "
                "investigation report and nothing else - your entire reply will be "
                "delivered to the Coordinator as a message.",
            ),
        ]
    )


def coordinator_after_investigation_prompt(investigator_report: str) -> str:
    return agent.build_prompt(
        [
            ("Investigation report from the Investigator", investigator_report),
            (
                "What to do now",
                "Write the instruction you want to send to the Implementer. "
                "Reply with that instruction and nothing else - your entire reply "
                "will be delivered to the Implementer as a message.",
            ),
        ]
    )


def implementer_prompt(
    task: Task, coordinator_instruction: str, investigator_report: str
) -> str:
    return agent.build_prompt(
        [
            ("Original task (verbatim, from the user)", task.statement),
            ("Instruction from the Coordinator", coordinator_instruction),
            ("Investigation report from the Investigator", investigator_report),
            (
                "Working agreement",
                "You are in a checked-out copy of the repository at the current "
                "working directory. Make the change and run the project's tests. "
                "Then reply with a report for the Coordinator and nothing else - "
                "your entire reply will be delivered to the Coordinator as a "
                "message.",
            ),
        ]
    )


def coordinator_wrapup_prompt(implementer_report: str) -> str:
    return agent.build_prompt(
        [
            ("Report from the Implementer", implementer_report),
            (
                "What to do now",
                "Write the final result to return to the user. Reply with that "
                "and nothing else.",
            ),
        ]
    )


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
    )

    coordinator = agent.AgentSpec.for_role("coordinator")
    investigator = agent.AgentSpec.for_role("investigator")
    implementer = agent.AgentSpec.for_role("implementer")

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
                "note": "Arm B chain stopped early; the run is recorded as unsolved.",
            },
        )
        return finish_run(session)

    def must_stop(result: agent.AgentResult) -> Optional[str]:
        """A failed session, or quota telemetry saying the next session would
        enter paid overage. Checked BEFORE the next session is spawned."""
        if not result.ok:
            return "agent session did not complete successfully"
        return agent.quota_stop_reason(result.cli)

    # -- 1. Coordinator: kickoff ------------------------------------------
    r1 = agent.run_agent(
        session.ctx,
        coordinator,
        prompt=coordinator_kickoff_prompt(task),
        workspace=session.workspace,
        session_index=1,
        prompt_inputs={
            "task_statement_verbatim": task.statement,
            "upstream_reports": [],
            "step": "coordinator_kickoff",
        },
    )
    session.agent_results.append(r1)
    stop = must_stop(r1)
    if stop:
        return abort("coordinator_kickoff", r1, stop)
    coordinator_session_id = r1.session_id

    investigation_instruction = r1.final_text
    session.record_handoff(
        index=1,
        sender="coordinator",
        recipient="investigator",
        label="investigation_instruction",
        body=investigation_instruction,
    )

    # -- 2. Investigator ---------------------------------------------------
    r2 = agent.run_agent(
        session.ctx,
        investigator,
        prompt=investigator_prompt(task, investigation_instruction),
        workspace=session.workspace,
        session_index=2,
        prompt_inputs={
            "task_statement_verbatim": task.statement,
            "coordinator_instruction": investigation_instruction,
            "upstream_reports": [],
            "step": "investigate",
        },
    )
    session.agent_results.append(r2)
    stop = must_stop(r2)
    if stop:
        return abort("investigate", r2, stop)

    investigation_report = r2.final_text
    session.record_handoff(
        index=2,
        sender="investigator",
        recipient="coordinator",
        label="investigation_report",
        body=investigation_report,
    )

    # -- 3. Coordinator: plan implementation ------------------------------
    r3 = agent.run_agent(
        session.ctx,
        coordinator,
        prompt=coordinator_after_investigation_prompt(investigation_report),
        workspace=session.workspace,
        session_index=3,
        prompt_inputs={
            "investigator_report": investigation_report,
            "step": "coordinator_plan",
        },
        resume_session_id=coordinator_session_id,
    )
    session.agent_results.append(r3)
    stop = must_stop(r3)
    if stop:
        return abort("coordinator_plan", r3, stop)

    implementation_instruction = r3.final_text
    session.record_handoff(
        index=3,
        sender="coordinator",
        recipient="implementer",
        label="implementation_instruction",
        body=implementation_instruction,
    )
    # Recorded separately from the instruction so carried context can be measured
    # on its own: this is the Investigator's findings being relayed onward.
    session.record_handoff(
        index=4,
        sender="coordinator",
        recipient="implementer",
        label="forwarded_investigation_report",
        body=investigation_report,
    )

    # -- 4. Implementer ----------------------------------------------------
    r4 = agent.run_agent(
        session.ctx,
        implementer,
        prompt=implementer_prompt(
            task, implementation_instruction, investigation_report
        ),
        workspace=session.workspace,
        session_index=4,
        prompt_inputs={
            "task_statement_verbatim": task.statement,
            "coordinator_instruction": implementation_instruction,
            "investigator_report": investigation_report,
            "step": "implement",
        },
    )
    session.agent_results.append(r4)
    stop = must_stop(r4)
    if stop:
        return abort("implement", r4, stop)

    implementation_report = r4.final_text
    session.record_handoff(
        index=5,
        sender="implementer",
        recipient="coordinator",
        label="implementation_report",
        body=implementation_report,
    )

    # -- 5. Coordinator: wrap up ------------------------------------------
    r5 = agent.run_agent(
        session.ctx,
        coordinator,
        prompt=coordinator_wrapup_prompt(implementation_report),
        workspace=session.workspace,
        session_index=5,
        prompt_inputs={
            "implementer_report": implementation_report,
            "step": "coordinator_wrapup",
        },
        resume_session_id=coordinator_session_id,
    )
    session.agent_results.append(r5)
    if r5.ok:
        session.record_handoff(
            index=6,
            sender="coordinator",
            recipient="user",
            label="final_result",
            body=r5.final_text,
        )

    return finish_run(session)
