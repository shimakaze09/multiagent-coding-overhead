"""Arm A - one Claude Code agent, one session.

Receives the original task statement verbatim, the same repository at the same
base commit, the same success criterion, and the same tool capability pool as
Arm B's workers. It is not told to behave unusually.
"""

from __future__ import annotations

import config
from arms import RunSession, finish_run, start_run
from harness import agent
from tasks.registry import Task

ARM = "A"


def build_solo_prompt(task: Task) -> str:
    """The task statement verbatim, plus the minimum operational framing."""
    return agent.build_prompt(
        [
            ("Task", task.statement),
            (
                "Working agreement",
                "You are working in a checked-out copy of the repository at the "
                "current working directory. Make the change directly in these "
                "files and run the project's tests to check your work. When you "
                "are finished, briefly summarise what you changed and why.",
            ),
        ]
    )


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

    spec = agent.AgentSpec.for_role("solo")
    prompt = build_solo_prompt(task)

    result = agent.run_agent(
        session.ctx,
        spec,
        prompt=prompt,
        workspace=session.workspace,
        session_index=1,
        prompt_inputs={
            "task_statement_verbatim": task.statement,
            "upstream_reports": [],
        },
    )
    session.agent_results.append(result)

    return finish_run(session)
