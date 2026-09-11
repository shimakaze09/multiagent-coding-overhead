"""One agent = one Claude Code session, plus normalization into our event log.

Role policy lives here (which tools a role may use, what its prompt contains).
Process mechanics live in claude_cli.py; schema parsing lives in telemetry.py.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import config
from harness import claude_cli, events as ev, telemetry, tools
from harness.workspace import Workspace


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...] = ()
    # The Bash permission allowlist. Identical for every role by design, so Arm A
    # and Arm B receive byte-identical permission configuration.
    allowed_tools: tuple[str, ...] = ()

    @staticmethod
    def for_role(role: str, agent_id: Optional[str] = None) -> "AgentSpec":
        return AgentSpec(
            agent_id=agent_id or role,
            role=role,
            tools=tuple(config.ROLE_TOOLS[role]),
            disallowed_tools=tuple(config.ROLE_DISALLOWED_TOOLS.get(role, ())),
            allowed_tools=tuple(config.allowed_tools_for_role(role)),
        )


@dataclass
class RunContext:
    run_id: str
    task_id: str
    arm: str
    run_dir: Path
    log: ev.EventLog
    cli_path: str
    model: str
    limits: config.Limits
    include_hook_events: bool = False
    permission_mode: str = "acceptEdits"
    permission_prompts: str = "none"
    sessions_spawned: int = 0

    def budget_check(self) -> None:
        """Subscription usage protection: enforced before spawning, never after."""
        if self.sessions_spawned >= self.limits.max_sessions_per_invocation:
            raise RuntimeError(
                f"session budget exhausted: {self.sessions_spawned} sessions already "
                f"spawned, limit is {self.limits.max_sessions_per_invocation}. "
                "Refusing to start another Claude session."
            )


@dataclass
class AgentResult:
    spec: AgentSpec
    session_key: str
    invocation: claude_cli.Invocation
    cli: claude_cli.CliRunResult
    acquisitions: list[tuple]
    final_text: str

    @property
    def session_id(self) -> Optional[str]:
        return self.cli.parsed.session_id

    @property
    def ok(self) -> bool:
        return self.cli.ok


# --------------------------------------------------------------------------
# Prompt assembly — recorded verbatim so runs are reconstructible
# --------------------------------------------------------------------------

# Kept minimal on purpose: Stage 0 measures existing behavior. Nothing here tells
# an agent to avoid re-reading, and nothing hints at shared state or artifact
# references (that would be Arm C, which is out of scope).
ROLE_SYSTEM_APPENDIX = {
    "solo": "",
    "coordinator": (
        "You are the Coordinator of a small engineering team. You do not have "
        "access to the repository yourself. You delegate work to teammates and "
        "relay information between them in plain natural language."
    ),
    "investigator": (
        "You are the Investigator on a small engineering team. You may inspect "
        "the repository but you must not modify any file. You report findings to "
        "the Coordinator in plain natural language."
    ),
    "implementer": (
        "You are the Implementer on a small engineering team. You make the code "
        "change and run tests. You may inspect the repository as much as you need."
    ),
}


def quota_stop_reason(cli_result: "claude_cli.CliRunResult") -> Optional[str]:
    """Why a multi-session run must not spawn its NEXT session, or None.

    Subscription usage protection only: it never alters what Claude is given,
    so it is not part of config_hash. Triggers on the CLI's own telemetry:
      * a rate_limit_event reporting isUsingOverage=true (paid overage), or a
        status that indicates the request was blocked;
      * a usage-limit condition in the result text.
    """
    parsed = cli_result.parsed
    for rl in parsed.rate_limit_events:
        if rl.is_using_overage:
            return (
                f"rate_limit_event reports isUsingOverage=true "
                f"(type={rl.rate_limit_type}, utilization={rl.utilization}); "
                "continuing would consume paid overage"
            )
        if rl.indicates_execution_failure:
            return f"rate_limit_event status={rl.status!r} indicates a blocked request"
    if cli_result.summary.get("usage_limit_suspected"):
        return "Claude Code reported a usage-limit condition"
    return None


def build_prompt(sections: Sequence[tuple[str, str]]) -> str:
    """Join labelled sections. The result is stored byte-for-byte as stdin."""
    parts = []
    for title, body in sections:
        body = (body or "").strip()
        if not body:
            continue
        parts.append(f"## {title}\n\n{body}")
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# Running one agent
# --------------------------------------------------------------------------


def run_agent(
    ctx: RunContext,
    spec: AgentSpec,
    *,
    prompt: str,
    workspace: Workspace,
    session_index: int,
    prompt_inputs: Optional[dict] = None,
    resume_session_id: Optional[str] = None,
) -> AgentResult:
    ctx.budget_check()

    session_key = f"{session_index:02d}_{spec.agent_id}"
    session_dir = ctx.run_dir / "sessions" / session_key

    inv = claude_cli.build_invocation(
        cli_path=ctx.cli_path,
        session_key=session_key,
        agent_id=spec.agent_id,
        role=spec.role,
        prompt=prompt,
        cwd=workspace.path,
        model=ctx.model,
        tools=spec.tools,
        allowed_tools=spec.allowed_tools,
        disallowed_tools=spec.disallowed_tools,
        permission_mode=ctx.permission_mode,
        permission_prompts=ctx.permission_prompts,
        max_turns=ctx.limits.max_turns_per_session,
        max_wall_seconds=ctx.limits.max_wall_seconds_per_session,
        append_system_prompt=ROLE_SYSTEM_APPENDIX.get(spec.role) or None,
        include_hook_events=ctx.include_hook_events,
        prompt_inputs=prompt_inputs or {},
        resume_session_id=resume_session_id,
    )

    start_ev = ctx.log.append(
        ev.CLAUDE_SESSION_START,
        agent_id=spec.agent_id,
        payload={
            "session_key": session_key,
            "role": spec.role,
            "tools": list(spec.tools),
            "allowed_tools": list(spec.allowed_tools),
            "disallowed_tools": list(spec.disallowed_tools),
            "permission_mode": inv.permission_mode,
            "permission_prompts": inv.permission_prompts,
            "model": ctx.model,
            "assigned_session_id": inv.session_id,
            "resumed_session_id": inv.resume_session_id,
            "is_resume": bool(inv.resume_session_id),
            "prompt_sizes": tools.handoff_size(prompt),
            "cwd": str(workspace.path),
            "max_turns": inv.max_turns,
            "max_wall_seconds": inv.max_wall_seconds,
            "turn_limit_enforcement": inv.turn_limit_enforcement,
        },
    )

    ctx.sessions_spawned += 1
    cli_result = claude_cli.run_invocation(inv, session_dir)

    acquisitions = _emit_stream_events(
        ctx,
        spec,
        cli_result,
        session_key=session_key,
        parent_event_id=start_ev.event_id,
    )

    ctx.log.append(
        ev.CLAUDE_SESSION_END,
        agent_id=spec.agent_id,
        parent_event_id=start_ev.event_id,
        payload={
            "session_key": session_key,
            "exit": cli_result.exit_record(),
            "summary": cli_result.summary,
            "coverage": tools.coverage(acquisitions).as_dict(),
        },
    )

    if cli_result.termination_reason == claude_cli.TERM_TURN_LIMIT:
        ctx.log.append(
            ev.TURN_LIMIT_EXCEEDED,
            agent_id=spec.agent_id,
            parent_event_id=start_ev.event_id,
            payload={
                "session_key": session_key,
                "max_turns": inv.max_turns,
                "enforcement": "harness_side_process_termination",
                "note": "Claude Code 2.1.260 has no --max-turns flag",
            },
        )

    if cli_result.summary.get("usage_limit_suspected"):
        ctx.log.append(
            ev.USAGE_LIMIT_REPORTED,
            agent_id=spec.agent_id,
            parent_event_id=start_ev.event_id,
            payload={
                "session_key": session_key,
                "cli_result_text": (cli_result.parsed.result or {}).get("result"),
            },
        )

    return AgentResult(
        spec=spec,
        session_key=session_key,
        invocation=inv,
        cli=cli_result,
        acquisitions=acquisitions,
        final_text=cli_result.parsed.final_text,
    )


# --------------------------------------------------------------------------
# Stream -> normalized events
# --------------------------------------------------------------------------

# Events derived from the CLI stream are emitted in stream order. Our `seq`
# records that order but is marked `cli_reported`, because parallel tool calls
# inside Claude Code are genuinely concurrent and stream position does not prove
# causal order (SPEC.md section 5).


def _emit_stream_events(
    ctx: RunContext,
    spec: AgentSpec,
    cli_result: claude_cli.CliRunResult,
    *,
    session_key: str,
    parent_event_id: str,
) -> list[tools.Acquisition]:
    parsed = cli_result.parsed
    raw_lines = Path(cli_result.stdout_path).read_text(encoding="utf-8").splitlines()

    by_start: dict[int, list[telemetry.ToolCall]] = defaultdict(list)
    by_end: dict[int, list[telemetry.ToolCall]] = defaultdict(list)
    for call in parsed.tool_calls:
        by_start[call.start_line].append(call)
        if call.end_line is not None:
            by_end[call.end_line].append(call)

    tool_start_event: dict[str, str] = {}
    acquisitions: list[tools.Acquisition] = []
    denied_ids = parsed.denied_tool_use_ids
    pending_turn: Optional[tuple[int, str]] = None

    def emit(type_: str, *, payload: dict, line_no: int, cli_ts: Optional[str],
             turn_id: Optional[int] = None, parent: Optional[str] = None) -> ev.Event:
        payload = dict(payload)
        payload["cli_timestamp"] = cli_ts
        return ctx.log.append(
            type_,
            agent_id=spec.agent_id,
            turn_id=turn_id,
            payload=payload,
            source="claude_code",
            parent_event_id=parent or parent_event_id,
            raw_ref={"session_key": session_key, "line_no": line_no},
            order_confidence=ev.ORDER_CLI_REPORTED,
            ts=cli_ts or None,
        )

    turn_counter = 0
    for line_no, raw in enumerate(raw_lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            emit(
                ev.UNKNOWN_CLAUDE_EVENT,
                payload={"reason": "unparsable_line", "bytes": len(raw)},
                line_no=line_no,
                cli_ts=None,
            )
            continue
        if not isinstance(obj, dict):
            continue

        etype = obj.get("type")
        cli_ts = obj.get("timestamp")

        # Pointer back to the raw event; the content itself stays in the raw log.
        emit(
            ev.RAW_CLAUDE_EVENT,
            payload={
                "cli_type": etype,
                "cli_subtype": obj.get("subtype"),
                "bytes": len(raw),
                "cli_uuid": obj.get("uuid"),
            },
            line_no=line_no,
            cli_ts=cli_ts,
        )

        if etype == "system" and obj.get("subtype") == "permission_denied":
            emit(
                ev.PERMISSION_DENIED,
                payload={
                    "tool_name": obj.get("tool_name"),
                    "tool_use_id": obj.get("tool_use_id"),
                    "decision_reason_type": obj.get("decision_reason_type"),
                    "decision_reason": obj.get("decision_reason"),
                    "note": "the action was NOT performed; it acquired nothing",
                },
                line_no=line_no,
                cli_ts=cli_ts,
            )
            continue

        if etype == "system" and obj.get("subtype") == "init":
            emit(
                ev.USAGE_REPORT,
                payload={
                    "kind": "session_init",
                    "session_id": obj.get("session_id"),
                    "model": obj.get("model"),
                    "api_key_source": obj.get("apiKeySource"),
                    "tools_offered": obj.get("tools"),
                    "permission_mode": obj.get("permissionMode"),
                    "cli_version": obj.get("claude_code_version"),
                    "billing_ok": cli_result.billing_ok,
                    "billing_note": cli_result.billing_note,
                },
                line_no=line_no,
                cli_ts=cli_ts,
            )
            continue

        if etype == "assistant":
            if pending_turn is not None:
                emit(
                    ev.AGENT_TURN_END,
                    payload={"turn_id": pending_turn[0]},
                    line_no=line_no,
                    cli_ts=cli_ts,
                    turn_id=pending_turn[0],
                    parent=pending_turn[1],
                )
            turn_counter += 1
            turn_ev = emit(
                ev.AGENT_TURN_START,
                payload={"turn_id": turn_counter},
                line_no=line_no,
                cli_ts=cli_ts,
                turn_id=turn_counter,
            )
            pending_turn = (turn_counter, turn_ev.event_id)

            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            usage = msg.get("usage")
            if isinstance(usage, dict):
                snap = telemetry.parse_usage(usage, line_no, cli_ts)
                emit(
                    ev.USAGE_REPORT,
                    payload={"kind": "assistant_message", **snap.as_dict()},
                    line_no=line_no,
                    cli_ts=cli_ts,
                    turn_id=turn_counter,
                    parent=turn_ev.event_id,
                )

            if obj.get("error") or obj.get("is_api_error_message"):
                emit(
                    ev.ERROR,
                    payload={
                        "error": obj.get("error"),
                        "is_api_error_message": obj.get("is_api_error_message"),
                        "text": telemetry._first_text(msg),
                    },
                    line_no=line_no,
                    cli_ts=cli_ts,
                    turn_id=turn_counter,
                    parent=turn_ev.event_id,
                )

            for block in telemetry._content_blocks(msg):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    text = block["text"]
                    emit(
                        ev.AGENT_MESSAGE,
                        payload={
                            "direction": "assistant_text",
                            "text": text,
                            "sizes": tools.handoff_size(text),
                        },
                        line_no=line_no,
                        cli_ts=cli_ts,
                        turn_id=turn_counter,
                        parent=turn_ev.event_id,
                    )

            for call in by_start.get(line_no, []):
                start_type = _start_event_type(call.name, call.input)
                e = emit(
                    start_type,
                    payload={
                        "tool_use_id": call.tool_use_id,
                        "tool_name": call.name,
                        "tool_input": call.input,
                    },
                    line_no=line_no,
                    cli_ts=cli_ts,
                    turn_id=turn_counter,
                    parent=turn_ev.event_id,
                )
                tool_start_event[call.tool_use_id] = e.event_id
                if start_type != ev.TOOL_CALL_START:
                    # Always keep a generic TOOL_CALL_START too, so tool activity
                    # can be counted without knowing every specialization.
                    emit(
                        ev.TOOL_CALL_START,
                        payload={
                            "tool_use_id": call.tool_use_id,
                            "tool_name": call.name,
                            "specialized_as": start_type,
                        },
                        line_no=line_no,
                        cli_ts=cli_ts,
                        turn_id=turn_counter,
                        parent=e.event_id,
                    )
            continue

        if etype == "user":
            for call in by_end.get(line_no, []):
                acq = tools.classify_tool_call(
                    tool_use_id=call.tool_use_id,
                    tool_name=call.name,
                    tool_input=call.input,
                    result_text=call.result_text,
                    agent_id=spec.agent_id,
                    session_key=session_key,
                    start_ts=call.start_ts,
                    end_ts=call.end_ts,
                    start_line=call.start_line,
                    concurrency_group_line=call.concurrency_group_line,
                    end_line=call.end_line,
                    is_error=call.result_is_error,
                    turn_id=call.agent_turn,
                    permission_denied=call.tool_use_id in denied_ids,
                )
                acquisitions.append(acq)

                parent = tool_start_event.get(call.tool_use_id)
                compact = {
                    k: v
                    for k, v in acq.as_dict().items()
                    # shingles stay out of the event log; ingest recomputes them
                    # from the raw stream, which is the source of truth.
                    if k != "result_shingles"
                }
                end_type = _end_event_type(acq)
                emit(
                    end_type,
                    payload=compact,
                    line_no=line_no,
                    cli_ts=cli_ts,
                    turn_id=call.agent_turn,
                    parent=parent,
                )
                if end_type != ev.TOOL_CALL_END:
                    emit(
                        ev.TOOL_CALL_END,
                        payload={
                            "tool_use_id": acq.tool_use_id,
                            "tool_name": acq.tool_name,
                            "acquisition_class": acq.acquisition_class,
                            "specialized_as": end_type,
                            "result_chars": acq.result_chars,
                            "result_sha": acq.result_sha,
                            "is_error": acq.is_error,
                        },
                        line_no=line_no,
                        cli_ts=cli_ts,
                        turn_id=call.agent_turn,
                        parent=parent,
                    )
            continue

        if etype == "result":
            if pending_turn is not None:
                emit(
                    ev.AGENT_TURN_END,
                    payload={"turn_id": pending_turn[0]},
                    line_no=line_no,
                    cli_ts=cli_ts,
                    turn_id=pending_turn[0],
                    parent=pending_turn[1],
                )
                pending_turn = None
            emit(
                ev.USAGE_REPORT,
                payload={"kind": "session_result", **telemetry.session_summary(parsed)},
                line_no=line_no,
                cli_ts=cli_ts,
            )
            continue

        if etype == "rate_limit_event":
            info = obj.get("rate_limit_info")
            info = info if isinstance(info, dict) else {}
            emit(
                ev.RATE_LIMIT_REPORTED,
                payload={
                    "status": info.get("status"),
                    "rate_limit_type": info.get("rateLimitType"),
                    "utilization": info.get("utilization"),
                    "resets_at_epoch": info.get("resetsAt"),
                    "is_using_overage": info.get("isUsingOverage"),
                    "surpassed_threshold": info.get("surpassedThreshold"),
                    "windows": info.get("unifiedWindows"),
                    "note": (
                        "subscription-quota telemetry; not an execution failure "
                        "unless its own status says so"
                    ),
                },
                line_no=line_no,
                cli_ts=cli_ts,
            )
            continue

        if etype == "hook_event":
            emit(
                ev.RAW_CLAUDE_EVENT,
                payload={"kind": "hook_event", "hook": obj.get("hook_event_name")},
                line_no=line_no,
                cli_ts=cli_ts,
            )
            continue

        if etype not in telemetry.KNOWN_TOP_LEVEL_TYPES:
            emit(
                ev.UNKNOWN_CLAUDE_EVENT,
                payload={"cli_type": etype, "keys": sorted(obj.keys())},
                line_no=line_no,
                cli_ts=cli_ts,
            )

    if pending_turn is not None:
        ctx.log.append(
            ev.AGENT_TURN_END,
            agent_id=spec.agent_id,
            turn_id=pending_turn[0],
            payload={"turn_id": pending_turn[0], "note": "stream ended without result event"},
            source="claude_code",
            parent_event_id=pending_turn[1],
            order_confidence=ev.ORDER_CLI_REPORTED,
        )

    # Tool calls that never returned a result (e.g. process terminated mid-call)
    # are still recorded, with completed=False.
    seen = {a.tool_use_id for a in acquisitions}
    for call in parsed.tool_calls:
        if call.tool_use_id in seen:
            continue
        acq = tools.classify_tool_call(
            tool_use_id=call.tool_use_id,
            tool_name=call.name,
            tool_input=call.input,
            result_text=None,
            agent_id=spec.agent_id,
            session_key=session_key,
            start_ts=call.start_ts,
            end_ts=None,
            start_line=call.start_line,
            concurrency_group_line=call.concurrency_group_line,
            end_line=None,
            is_error=None,
            turn_id=call.agent_turn,
            permission_denied=call.tool_use_id in denied_ids,
        )
        acquisitions.append(acq)
        ctx.log.append(
            ev.TOOL_CALL_END,
            agent_id=spec.agent_id,
            turn_id=call.agent_turn,
            payload={
                "tool_use_id": acq.tool_use_id,
                "tool_name": acq.tool_name,
                "acquisition_class": acq.acquisition_class,
                "completed": False,
                "note": "no tool_result observed; stream ended or call was interrupted",
            },
            source="harness",
            parent_event_id=tool_start_event.get(call.tool_use_id, parent_event_id),
            raw_ref={"session_key": session_key, "line_no": call.start_line},
            order_confidence=ev.ORDER_CLI_REPORTED,
        )

    return acquisitions


def _start_event_type(tool_name: str, tool_input: dict) -> str:
    low = (tool_name or "").lower()
    if low in tools.READ_TOOLS:
        return ev.FILE_READ_START
    if low in tools.SEARCH_TOOLS:
        return ev.SEARCH_START
    if low in tools.BASH_TOOLS:
        cmd = (tool_input or {}).get("command") or ""
        klass, _ = tools.classify_bash_command(cmd)
        if klass == tools.CLASSIFIED_BASH_READ:
            return ev.FILE_READ_START
        if klass == tools.CLASSIFIED_BASH_SEARCH:
            return ev.SEARCH_START
    return ev.TOOL_CALL_START


def _end_event_type(acq: tools.Acquisition) -> str:
    if acq.acquisition_class in (tools.STRUCTURED_READ, tools.CLASSIFIED_BASH_READ):
        return ev.FILE_READ_END
    if acq.acquisition_class in (tools.STRUCTURED_SEARCH, tools.CLASSIFIED_BASH_SEARCH):
        return ev.SEARCH_END
    if acq.acquisition_class == tools.STRUCTURED_EDIT:
        return ev.EDIT
    if acq.acquisition_class == tools.CLASSIFIED_TEST_RUN:
        return ev.TEST_RUN
    return ev.TOOL_CALL_END
