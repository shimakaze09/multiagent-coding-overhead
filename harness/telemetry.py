"""Conservative parser for Claude Code `--output-format stream-json --verbose`.

Rules (from SPEC.md section 5):
  * every raw line is preserved by the caller; this module only reads
  * known fields are parsed conservatively
  * unknown event types survive parsing and are counted, never silently dropped
  * a new optional field must never crash the parser
  * this parser is versioned independently of the Claude Code version

Schema notes are grounded in output actually observed from Claude Code 2.1.260;
see tests/fixtures/. Fields not observed on that build are read defensively.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

# 4: rate_limit_event, system/permission_denied, system/thinking_tokens,
#    total_input_tokens, modelUsage totals, turn-accounting split. All grounded
#    in a real authenticated Claude Code 2.1.260 Arm A run.
PARSER_VERSION = 4

# availability vocabulary
REPORTED = "reported"
NOT_EXPOSED = "not_exposed"
ESTIMATED = "estimated"
UNRELIABLE = "unreliable"

SOURCE_STREAM = "claude_code_stream"

# Confirmed against a real authenticated Claude Code 2.1.260 run whose result
# event reported input_tokens=14, cache_read_input_tokens=126874,
# cache_creation_input_tokens=10369, output_tokens=2223.
TOKEN_SEMANTICS = {
    "input_tokens": (
        "UNCACHED newly-submitted input tokens ONLY. This is NOT the prompt or "
        "context length: in the observed run it was 14 while the submitted "
        "context was ~137k."
    ),
    "cache_read_input_tokens": "input tokens served from the prompt cache",
    "cache_creation_input_tokens": "input tokens written into the prompt cache",
    "total_input_tokens": (
        "input_tokens + cache_read_input_tokens + cache_creation_input_tokens. "
        "This is the only field that may be described as submitted context volume."
    ),
    "output_tokens": "generated output tokens (includes thinking_tokens)",
    "total_tokens": "total_input_tokens + output_tokens; a mixed input/output sum",
    "scope": (
        "result.usage covers the session's MAIN model only. Claude Code may also "
        "invoke auxiliary models (a real run showed claude-haiku-4-5 alongside "
        "claude-sonnet-5); those tokens appear only in result.modelUsage, and "
        "total_cost_usd covers both. See model_usage_totals."
    ),
}


# --------------------------------------------------------------------------
# Measurement: every numeric telemetry field is a value/source/availability triple
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Measurement:
    value: Optional[float]
    source: Optional[str]
    availability: str

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "availability": self.availability,
        }


def reported(value: Any, source: str = SOURCE_STREAM) -> Measurement:
    return Measurement(value=value, source=source, availability=REPORTED)


def not_exposed() -> Measurement:
    return Measurement(value=None, source=None, availability=NOT_EXPOSED)


def estimated(value: Any, source: str) -> Measurement:
    return Measurement(value=value, source=source, availability=ESTIMATED)


def _measure(container: Optional[dict], key: str) -> Measurement:
    """Read one numeric field, distinguishing 'absent' from 'present and zero'."""
    if not isinstance(container, dict) or key not in container:
        return not_exposed()
    v = container.get(key)
    if v is None:
        return not_exposed()
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return Measurement(value=None, source=SOURCE_STREAM, availability=UNRELIABLE)
    return reported(v)


# --------------------------------------------------------------------------
# Parsed records
# --------------------------------------------------------------------------


@dataclass
class ToolCall:
    tool_use_id: str
    name: str
    input: dict
    start_line: int
    start_ts: Optional[str]
    start_seq_hint: int
    agent_turn: int
    end_line: Optional[int] = None
    end_ts: Optional[str] = None
    result_text: Optional[str] = None
    result_is_error: Optional[bool] = None
    parent_tool_use_id: Optional[str] = None
    # Claude Code emits ONE stream event per content block, so a single API
    # assistant message carrying several tool_use blocks appears as several
    # events on consecutive lines. Those calls are genuinely CONCURRENT: they
    # were issued together. `api_message_start_line` is the first stream line of
    # the containing message and is the correct concurrency unit for temporal
    # availability; `start_line` remains the exact stream line for raw fidelity.
    api_message_id: Optional[str] = None
    api_message_start_line: Optional[int] = None

    @property
    def concurrency_group_line(self) -> int:
        return self.api_message_start_line if self.api_message_start_line else self.start_line

    @property
    def completed(self) -> bool:
        return self.end_line is not None


@dataclass
class UsageSnapshot:
    line_no: int
    ts: Optional[str]
    input_tokens: Measurement
    output_tokens: Measurement
    cache_read_tokens: Measurement
    cache_write_tokens: Measurement
    cache_ephemeral_1h_tokens: Measurement
    cache_ephemeral_5m_tokens: Measurement
    thinking_tokens: Measurement
    total_input_tokens: Measurement
    total_tokens: Measurement
    service_tier: Optional[str]

    def as_dict(self) -> dict:
        d = {
            k: v.as_dict()
            for k, v in self.__dict__.items()
            if isinstance(v, Measurement)
        }
        d["line_no"] = self.line_no
        d["ts"] = self.ts
        d["service_tier"] = self.service_tier
        return d


@dataclass
class AssistantText:
    line_no: int
    ts: Optional[str]
    agent_turn: int
    text: str


@dataclass
class RateLimitSnapshot:
    """A `rate_limit_event`, as observed on Claude Code 2.1.260.

    Real captured shape:
        {"type": "rate_limit_event", "session_id": ..., "uuid": ...,
         "rate_limit_info": {"status": "allowed_warning", "resetsAt": <epoch>,
            "rateLimitType": "seven_day", "utilization": 0.85,
            "isUsingOverage": false, "surpassedThreshold": 0.75,
            "unifiedWindows": {"five_hour": {"utilization", "resetsAt"},
                               "seven_day": {"utilization", "resetsAt"}}}}

    Only fields actually present in the captured event are extracted; the
    complete raw event is preserved alongside. This is subscription-quota
    telemetry, NOT an error: `status: "allowed_warning"` means the request was
    allowed. It is only treated as blocking if its own fields say so.
    """

    line_no: int
    status: Optional[str]
    rate_limit_type: Optional[str]
    utilization: Optional[float]
    resets_at_epoch: Optional[int]
    is_using_overage: Optional[bool]
    surpassed_threshold: Optional[float]
    windows: dict
    raw: dict

    # Statuses that mean the request was served. Anything else is reported as
    # unrecognized rather than silently assumed to be fine or to be a failure.
    ALLOWED_STATUSES = ("allowed", "allowed_warning")
    BLOCKED_STATUSES = ("rejected", "blocked", "exceeded", "denied")

    @property
    def indicates_execution_failure(self) -> bool:
        return (self.status or "").lower() in self.BLOCKED_STATUSES

    @property
    def status_recognized(self) -> bool:
        s = (self.status or "").lower()
        return s in self.ALLOWED_STATUSES or s in self.BLOCKED_STATUSES

    def as_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "status": self.status,
            "rate_limit_type": self.rate_limit_type,
            "utilization": self.utilization,
            "resets_at_epoch": self.resets_at_epoch,
            "is_using_overage": self.is_using_overage,
            "surpassed_threshold": self.surpassed_threshold,
            "windows": self.windows,
            "indicates_execution_failure": self.indicates_execution_failure,
            "status_recognized": self.status_recognized,
            "source": SOURCE_STREAM,
            "availability": REPORTED,
        }


@dataclass
class PermissionDenial:
    """A `system/permission_denied` event.

    Real captured shape: {"type":"system","subtype":"permission_denied",
      "tool_name","tool_use_id","decision_reason_type","decision_reason","message"}

    Claude Code's own message states "The action was NOT performed", so a denied
    tool call must never be counted as a read, a search or a test run.
    """

    line_no: int
    tool_name: Optional[str]
    tool_use_id: Optional[str]
    decision_reason_type: Optional[str]
    decision_reason: Optional[str]
    raw: dict

    def as_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "tool_name": self.tool_name,
            "tool_use_id": self.tool_use_id,
            "decision_reason_type": self.decision_reason_type,
            "decision_reason": self.decision_reason,
        }


@dataclass
class ParsedStream:
    parser_version: int = PARSER_VERSION
    init: Optional[dict] = None
    session_ids: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_texts: list[AssistantText] = field(default_factory=list)
    usage_snapshots: list[UsageSnapshot] = field(default_factory=list)
    result: Optional[dict] = None
    hook_events: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    unknown_events: list[dict] = field(default_factory=list)
    type_counts: Counter = field(default_factory=Counter)
    unknown_type_counts: Counter = field(default_factory=Counter)
    unparsable_lines: int = 0
    total_lines: int = 0
    turns: int = 0
    thinking_only_turns: int = 0
    api_message_count: int = 0
    rate_limit_events: list[RateLimitSnapshot] = field(default_factory=list)
    permission_denials: list[PermissionDenial] = field(default_factory=list)
    system_subtype_counts: Counter = field(default_factory=Counter)
    unrecognized_system_subtypes: list[dict] = field(default_factory=list)

    # -- turn accounting -------------------------------------------------
    #
    # `turns` counts ASSISTANT STREAM EVENTS. Claude Code emits a standalone
    # assistant event for a `thinking` block and another for the `tool_use` that
    # follows it, both carrying the SAME message usage, i.e. they are two stream
    # events for one API-level assistant message. So this is not the CLI's
    # `num_turns`, and the two must not be conflated. See
    # `assistant_events_excluding_thinking_only`.

    @property
    def assistant_stream_events(self) -> int:
        return self.turns

    @property
    def assistant_events_excluding_thinking_only(self) -> int:
        return self.turns - self.thinking_only_turns

    @property
    def api_assistant_messages(self) -> int:
        """Distinct API-level assistant messages, grouped by `message.id`.

        This is the count of model responses. It is NOT `num_turns`.
        """
        return self.api_message_count

    @property
    def tool_use_blocks(self) -> int:
        return len(self.tool_calls)

    @property
    def cli_reported_num_turns(self) -> Optional[int]:
        v = (self.result or {}).get("num_turns")
        return v if isinstance(v, int) else None

    @property
    def denied_tool_use_ids(self) -> set[str]:
        """Tool calls the CLI refused. Sourced from `system/permission_denied`
        events and corroborated by `result.permission_denials`."""
        out = {d.tool_use_id for d in self.permission_denials if d.tool_use_id}
        for entry in (self.result or {}).get("permission_denials") or []:
            if isinstance(entry, dict) and entry.get("tool_use_id"):
                out.add(entry["tool_use_id"])
        return out

    # -- convenience -----------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        if self.init and self.init.get("session_id"):
            return self.init["session_id"]
        return self.session_ids[0] if self.session_ids else None

    @property
    def api_key_source(self) -> Optional[str]:
        return (self.init or {}).get("apiKeySource")

    @property
    def cli_version(self) -> Optional[str]:
        return (self.init or {}).get("claude_code_version")

    @property
    def final_text(self) -> str:
        r = self.result or {}
        if isinstance(r.get("result"), str):
            return r["result"]
        return self.assistant_texts[-1].text if self.assistant_texts else ""

    def is_error(self) -> bool:
        if self.result and self.result.get("is_error"):
            return True
        return bool(self.errors)

    def unknown_event_type_report(self) -> dict:
        return dict(self.unknown_type_counts)


# --------------------------------------------------------------------------
# Known event vocabulary
# --------------------------------------------------------------------------

KNOWN_TOP_LEVEL_TYPES = {
    "system",
    "assistant",
    "user",
    "result",
    "stream_event",
    "hook_event",
    "control_response",
    "control_request",
    "prompt_suggestion",
    "summary",
    # Observed on 2.1.260 in a real authenticated Arm A run. Subscription-quota
    # telemetry, not an error.
    "rate_limit_event",
}

# `system` subtypes observed on 2.1.260. An unrecognized subtype is recorded in
# `unrecognized_system_subtypes` rather than silently swallowed.
KNOWN_SYSTEM_SUBTYPES = {
    "init",
    "thinking_tokens",
    "permission_denied",
}

# Substrings that indicate the CLI reported a subscription usage-limit condition.
USAGE_LIMIT_MARKERS = (
    "usage limit",
    "rate_limit",
    "rate limit",
    "quota",
    "resets at",
    "upgrade to",
)


def looks_like_usage_limit(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in USAGE_LIMIT_MARKERS)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_usage(usage: Optional[dict], line_no: int, ts: Optional[str]) -> UsageSnapshot:
    usage = usage if isinstance(usage, dict) else None
    cache_creation = (usage or {}).get("cache_creation")
    details = (usage or {}).get("output_tokens_details")

    inp = _measure(usage, "input_tokens")
    out = _measure(usage, "output_tokens")
    c_read = _measure(usage, "cache_read_input_tokens")
    c_write = _measure(usage, "cache_creation_input_tokens")

    # IMPORTANT SEMANTICS, confirmed against a real 2.1.260 run:
    #   input_tokens                = UNCACHED, newly-submitted input only
    #   cache_read_input_tokens     = input served from the prompt cache
    #   cache_creation_input_tokens = input written into the prompt cache
    # In the observed run these were 14 / 126874 / 10369. `input_tokens` is
    # therefore NOT the prompt/context length; the submitted context is the SUM
    # of the three. `total_input_tokens` below is that sum, and it is the only
    # field that may be described as context volume.
    in_vals = [m.value for m in (inp, c_read, c_write) if m.value is not None]
    total_input = reported(sum(in_vals)) if in_vals else not_exposed()

    vals = [m.value for m in (inp, out, c_read, c_write) if m.value is not None]
    total = reported(sum(vals)) if vals else not_exposed()

    return UsageSnapshot(
        line_no=line_no,
        ts=ts,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=c_read,
        cache_write_tokens=c_write,
        cache_ephemeral_1h_tokens=_measure(cache_creation, "ephemeral_1h_input_tokens"),
        cache_ephemeral_5m_tokens=_measure(cache_creation, "ephemeral_5m_input_tokens"),
        thinking_tokens=_measure(details, "thinking_tokens"),
        total_input_tokens=total_input,
        total_tokens=total,
        service_tier=(usage or {}).get("service_tier"),
    )


def _content_blocks(message: Any) -> list[dict]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def parse_stream(lines: Iterable[str]) -> ParsedStream:
    """Parse stream-json lines. Never raises on unexpected content."""
    ps = ParsedStream()
    open_calls: dict[str, ToolCall] = {}
    cur_msg_id: Optional[str] = None
    cur_msg_line: int = 0

    for line_no, raw in enumerate(lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        ps.total_lines += 1
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            ps.unparsable_lines += 1
            continue
        if not isinstance(ev, dict):
            ps.unparsable_lines += 1
            continue

        etype = ev.get("type")
        ps.type_counts[str(etype)] += 1
        ts = ev.get("timestamp")
        sid = ev.get("session_id")
        if sid and sid not in ps.session_ids:
            ps.session_ids.append(sid)

        if etype == "system":
            subtype = ev.get("subtype")
            ps.system_subtype_counts[str(subtype)] += 1
            if subtype == "init":
                ps.init = ev
            elif subtype == "permission_denied":
                ps.permission_denials.append(
                    PermissionDenial(
                        line_no=line_no,
                        tool_name=ev.get("tool_name"),
                        tool_use_id=ev.get("tool_use_id"),
                        decision_reason_type=ev.get("decision_reason_type"),
                        decision_reason=ev.get("decision_reason"),
                        raw=ev,
                    )
                )
            elif subtype == "thinking_tokens":
                # Claude Code's own field name is `estimated_tokens`; it is an
                # estimate by the provider's own admission, so it is never
                # promoted to a reported token count.
                pass
            elif subtype not in KNOWN_SYSTEM_SUBTYPES:
                ps.unrecognized_system_subtypes.append(
                    {"line_no": line_no, "subtype": subtype, "keys": sorted(ev.keys())}
                )
                ps.unknown_type_counts[f"system/{subtype}"] += 1
            continue

        if etype == "rate_limit_event":
            info = ev.get("rate_limit_info")
            info = info if isinstance(info, dict) else {}
            windows = info.get("unifiedWindows")
            ps.rate_limit_events.append(
                RateLimitSnapshot(
                    line_no=line_no,
                    status=info.get("status"),
                    rate_limit_type=info.get("rateLimitType"),
                    utilization=info.get("utilization"),
                    resets_at_epoch=info.get("resetsAt"),
                    is_using_overage=info.get("isUsingOverage"),
                    surpassed_threshold=info.get("surpassedThreshold"),
                    windows=windows if isinstance(windows, dict) else {},
                    raw=ev,
                )
            )
            continue

        if etype == "hook_event":
            ps.hook_events.append(ev)
            continue

        if etype == "assistant":
            msg = ev.get("message")
            msg_id = msg.get("id") if isinstance(msg, dict) else None
            if msg_id != cur_msg_id or msg_id is None:
                cur_msg_id = msg_id
                cur_msg_line = line_no
                ps.api_message_count += 1
            ps.turns += 1
            block_types = {b.get("type") for b in _content_blocks(msg)}
            if block_types == {"thinking"}:
                # A standalone thinking event: part of the same API-level
                # assistant message as the tool_use/text event that follows.
                ps.thinking_only_turns += 1
            if ev.get("error") or ev.get("is_api_error_message"):
                ps.errors.append(
                    {
                        "line_no": line_no,
                        "ts": ts,
                        "error": ev.get("error"),
                        "text": _first_text(msg),
                        "is_api_error_message": ev.get("is_api_error_message"),
                    }
                )
            usage = (msg or {}).get("usage") if isinstance(msg, dict) else None
            if isinstance(usage, dict):
                ps.usage_snapshots.append(parse_usage(usage, line_no, ts))
            for block in _content_blocks(msg):
                btype = block.get("type")
                if btype == "text" and isinstance(block.get("text"), str):
                    ps.assistant_texts.append(
                        AssistantText(line_no, ts, ps.turns, block["text"])
                    )
                elif btype == "tool_use":
                    tid = str(block.get("id") or f"line{line_no}")
                    call = ToolCall(
                        tool_use_id=tid,
                        name=str(block.get("name") or "?"),
                        input=block.get("input") if isinstance(block.get("input"), dict) else {},
                        start_line=line_no,
                        start_ts=ts,
                        start_seq_hint=line_no,
                        agent_turn=ps.turns,
                        parent_tool_use_id=ev.get("parent_tool_use_id"),
                        api_message_id=cur_msg_id,
                        api_message_start_line=cur_msg_line,
                    )
                    ps.tool_calls.append(call)
                    open_calls[tid] = call
            continue

        if etype == "user":
            msg = ev.get("message")
            for block in _content_blocks(msg):
                if block.get("type") != "tool_result":
                    continue
                tid = str(block.get("tool_use_id") or "")
                call = open_calls.pop(tid, None)
                if call is None:
                    # A result whose tool_use we never saw: keep it visible.
                    ps.unknown_events.append(
                        {
                            "line_no": line_no,
                            "reason": "orphan_tool_result",
                            "tool_use_id": tid,
                            "raw": ev,
                        }
                    )
                    ps.unknown_type_counts["orphan_tool_result"] += 1
                    continue
                call.end_line = line_no
                call.end_ts = ts
                call.result_text = _stringify_tool_result(block.get("content"))
                call.result_is_error = bool(block.get("is_error"))
            continue

        if etype == "result":
            ps.result = ev
            usage = ev.get("usage")
            if isinstance(usage, dict):
                ps.usage_snapshots.append(parse_usage(usage, line_no, ts))
            if ev.get("is_error"):
                ps.errors.append(
                    {
                        "line_no": line_no,
                        "ts": ts,
                        "error": ev.get("terminal_reason") or ev.get("subtype"),
                        "text": ev.get("result") if isinstance(ev.get("result"), str) else None,
                        "api_error_status": ev.get("api_error_status"),
                    }
                )
            continue

        if etype == "stream_event":
            # partial message chunks; only emitted with --include-partial-messages
            continue

        if etype in KNOWN_TOP_LEVEL_TYPES:
            continue

        # Genuinely unknown: preserve and count.
        ps.unknown_events.append({"line_no": line_no, "reason": "unknown_type", "raw": ev})
        ps.unknown_type_counts[str(etype)] += 1

    return ps


def _first_text(message: Any) -> Optional[str]:
    for block in _content_blocks(message):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
    return None


def _stringify_tool_result(content: Any) -> str:
    """tool_result content may be a string or a list of content blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif b.get("type"):
                    parts.append(f"<{b['type']}>")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return json.dumps(content, sort_keys=True)


# --------------------------------------------------------------------------
# Roll-ups
# --------------------------------------------------------------------------


def aggregate_usage(ps: ParsedStream) -> dict:
    """Prefer the final `result.usage` (authoritative session total) and fall back
    to summing per-message snapshots. Never invents a value."""
    if ps.result and isinstance(ps.result.get("usage"), dict):
        snap = parse_usage(ps.result["usage"], -1, None)
        return {
            "input_tokens": snap.input_tokens.as_dict(),
            "output_tokens": snap.output_tokens.as_dict(),
            "cache_read_tokens": snap.cache_read_tokens.as_dict(),
            "cache_write_tokens": snap.cache_write_tokens.as_dict(),
            "cache_ephemeral_1h_tokens": snap.cache_ephemeral_1h_tokens.as_dict(),
            "cache_ephemeral_5m_tokens": snap.cache_ephemeral_5m_tokens.as_dict(),
            "thinking_tokens": snap.thinking_tokens.as_dict(),
            "total_input_tokens": snap.total_input_tokens.as_dict(),
            "total_tokens": snap.total_tokens.as_dict(),
            "service_tier": snap.service_tier,
            "aggregation": "cli_result_event",
            "semantics": TOKEN_SEMANTICS,
        }

    if not ps.usage_snapshots:
        return {
            k: not_exposed().as_dict()
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "cache_ephemeral_1h_tokens",
                "cache_ephemeral_5m_tokens",
                "thinking_tokens",
                "total_input_tokens",
                "total_tokens",
            )
        } | {
            "service_tier": None,
            "aggregation": "none_available",
            "semantics": TOKEN_SEMANTICS,
        }

    def _sum(attr: str) -> dict:
        vals = [
            getattr(s, attr).value
            for s in ps.usage_snapshots
            if getattr(s, attr).value is not None
        ]
        return reported(sum(vals)).as_dict() if vals else not_exposed().as_dict()

    out = {
        a: _sum(a)
        for a in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "cache_ephemeral_1h_tokens",
            "cache_ephemeral_5m_tokens",
            "thinking_tokens",
            "total_input_tokens",
            "total_tokens",
        )
    }
    out["service_tier"] = ps.usage_snapshots[-1].service_tier
    out["aggregation"] = "summed_message_snapshots"
    out["semantics"] = TOKEN_SEMANTICS
    return out


def model_usage_totals(ps: ParsedStream) -> dict:
    """Per-model token totals from `result.modelUsage`.

    `result.usage` covers only the main model. A real run showed Claude Code also
    invoking claude-haiku-4-5 (1104 input / 18 output tokens) alongside
    claude-sonnet-5, and `total_cost_usd` covered both. Without this, a run's
    token accounting silently omits auxiliary model use.
    """
    mu = (ps.result or {}).get("modelUsage")
    if not isinstance(mu, dict) or not mu:
        return {
            "availability": NOT_EXPOSED,
            "models": {},
            "all_models_input_tokens": not_exposed().as_dict(),
            "all_models_output_tokens": not_exposed().as_dict(),
        }
    per_model = {}
    tot_in = tot_out = 0
    for name, u in mu.items():
        if not isinstance(u, dict):
            continue
        vals = {
            "input_tokens": u.get("inputTokens"),
            "output_tokens": u.get("outputTokens"),
            "cache_read_input_tokens": u.get("cacheReadInputTokens"),
            "cache_creation_input_tokens": u.get("cacheCreationInputTokens"),
            "thinking_tokens": u.get("thinkingTokens"),
            "cost_usd": u.get("costUSD"),
            "canonical_model": u.get("canonicalModel"),
            "provider": u.get("provider"),
        }
        per_model[name] = vals
        for key, acc in (("input_tokens", "in"), ("output_tokens", "out")):
            v = vals[key]
            if isinstance(v, (int, float)):
                if acc == "in":
                    tot_in += v
                else:
                    tot_out += v
    return {
        "availability": REPORTED,
        "source": SOURCE_STREAM,
        "models": per_model,
        "model_count": len(per_model),
        "all_models_input_tokens": reported(tot_in).as_dict(),
        "all_models_output_tokens": reported(tot_out).as_dict(),
        "note": (
            "Includes auxiliary models Claude Code invoked itself. result.usage "
            "reflects the main model only."
        ),
    }


def rate_limit_report(ps: ParsedStream) -> dict:
    """Subscription-quota telemetry. Never an error on its own."""
    if not ps.rate_limit_events:
        return {"availability": NOT_EXPOSED, "events": [], "count": 0}
    last = ps.rate_limit_events[-1]
    return {
        "availability": REPORTED,
        "source": SOURCE_STREAM,
        "count": len(ps.rate_limit_events),
        "events": [e.as_dict() for e in ps.rate_limit_events],
        "latest_status": last.status,
        "latest_utilization": last.utilization,
        "latest_rate_limit_type": last.rate_limit_type,
        "is_using_overage": last.is_using_overage,
        "windows": last.windows,
        "indicates_execution_failure": any(
            e.indicates_execution_failure for e in ps.rate_limit_events
        ),
        "any_status_unrecognized": any(
            not e.status_recognized for e in ps.rate_limit_events
        ),
        "note": (
            "A rate_limit_event is quota telemetry, not a failure. Only a status "
            "in RateLimitSnapshot.BLOCKED_STATUSES indicates execution failure."
        ),
    }


def cost_report(ps: ParsedStream) -> dict:
    """Preserve CLI cost telemetry exactly, without claiming it is what was paid."""
    r = ps.result or {}
    cost = _measure(r, "total_cost_usd")
    return {
        "subscription_execution": True,
        "api_charge": "false_expected",
        "cli_reported_cost_usd": cost.as_dict(),
        "cli_cost_interpretation": "api_equivalent_not_amount_paid",
        "model_usage": r.get("modelUsage") if isinstance(r.get("modelUsage"), dict) else None,
    }


def session_summary(ps: ParsedStream) -> dict:
    r = ps.result or {}
    subagents = r.get("subagent_stats") if isinstance(r.get("subagent_stats"), dict) else {}
    return {
        "parser_version": ps.parser_version,
        "session_id": ps.session_id,
        "cli_version": ps.cli_version,
        "api_key_source": ps.api_key_source,
        "model": (ps.init or {}).get("model"),
        "permission_mode": (ps.init or {}).get("permissionMode"),
        "tools_offered": (ps.init or {}).get("tools"),
        # --- turn accounting: three DIFFERENT things, deliberately named apart.
        # A real run reported 14 / 3 / 11 / 11: Claude Code emits a separate
        # assistant stream event for a standalone `thinking` block, so stream
        # events exceed API-level assistant messages.
        "assistant_stream_events": ps.assistant_stream_events,
        "thinking_only_stream_events": ps.thinking_only_turns,
        "assistant_events_excluding_thinking_only": ps.assistant_events_excluding_thinking_only,
        "cli_reported_num_turns": _measure(r, "num_turns").as_dict(),
        "api_assistant_messages": ps.api_assistant_messages,
        "tool_use_blocks": ps.tool_use_blocks,
        "turn_accounting_note": (
            "FOUR different quantities, none interchangeable. "
            "assistant_stream_events: Claude Code emits one stream event per "
            "content block, so this over-counts model responses. "
            "api_assistant_messages: distinct message.id values, i.e. actual "
            "model responses. "
            "tool_use_blocks: individual tool calls. "
            "cli_reported_num_turns: Claude Code's own counter, whose definition "
            "is not documented in the stream. OBSERVED RELATIONSHIP (real runs "
            "only, n=3): num_turns == tool_use_blocks + 1. That is an empirical "
            "fit on few runs, not a specification, and nothing in the analysis "
            "depends on it."
        ),
        # legacy field names, kept so older artifacts stay readable
        "turns_observed_by_parser": ps.turns,
        "num_turns_reported": _measure(r, "num_turns").as_dict(),
        "duration_ms_reported": _measure(r, "duration_ms").as_dict(),
        "duration_api_ms_reported": _measure(r, "duration_api_ms").as_dict(),
        "stop_reason": r.get("stop_reason"),
        "terminal_reason": r.get("terminal_reason"),
        "is_error": ps.is_error(),
        "permission_denials": r.get("permission_denials"),
        "permission_denied_events": [d.as_dict() for d in ps.permission_denials],
        "permission_denied_count": len(ps.denied_tool_use_ids),
        "denied_tool_use_ids": sorted(ps.denied_tool_use_ids),
        "subagent_spawned": subagents.get("spawned"),
        "tool_calls": len(ps.tool_calls),
        "tool_calls_incomplete": sum(1 for c in ps.tool_calls if not c.completed),
        "event_type_counts": dict(ps.type_counts),
        "system_subtype_counts": dict(ps.system_subtype_counts),
        "unrecognized_system_subtypes": ps.unrecognized_system_subtypes,
        "unknown_event_types": ps.unknown_event_type_report(),
        "unparsable_lines": ps.unparsable_lines,
        "usage": aggregate_usage(ps),
        "model_usage_totals": model_usage_totals(ps),
        "rate_limit": rate_limit_report(ps),
        "cost": cost_report(ps),
        "usage_limit_suspected": looks_like_usage_limit(
            r.get("result") if isinstance(r.get("result"), str) else None
        ),
    }
