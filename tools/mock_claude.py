"""A mock `claude` CLI for instrumentation validation ONLY.

It speaks the same argv surface and emits the same stream-json event shapes as the
real Claude Code 2.1.260 (schema captured in tests/fixtures/), and it really
performs its file reads, searches and edits in the working directory, so the
whole harness -> telemetry -> metrics -> report pipeline runs end to end without
an authenticated CLI and without consuming any subscription quota.

This is NOT a model and NOT a substitute for a real run. Runs driven by it are
labelled `mock` and must never be reported as Arm A / Arm B results.

Used via STAGE0_CLAUDE_CLI. Never used by `runner.py smoke`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.1.260-mock"
MOCK_MODEL = "mock-sonnet"

# Stage 2 (model routing) only: requests for these model names behave like the
# real 2.1.260 CLI - `sonnet` resolves to `claude-sonnet-5`, a full id is echoed,
# every assistant message carries the resolved model, and modelUsage has one
# entry per model including Claude Code's own auxiliary Haiku call (which lands
# in the SAME entry when the session model is itself Haiku). Any other name
# (e.g. `mock-sonnet`, used by every A/B/C1 mock run) keeps the old behaviour.
MOCK_RESOLUTION = {
    "sonnet": "claude-sonnet-5",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
}
MOCK_AUX_MODEL = "claude-haiku-4-5-20251001"
MOCK_AUX_USAGE = {"inputTokens": 900, "outputTokens": 15, "cacheReadInputTokens": 0,
                  "cacheCreationInputTokens": 0}
# per MTok: input, output, cache write (5m), cache read - Claude Code 2.1.260 registry
MOCK_PRICES = {"claude-sonnet-5": (2.0, 10.0, 2.5, 0.2), "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.1)}
# Test hook: pretend the CLI served a different model than requested.
FORCE_RESOLVED_ENV = "STAGE0_MOCK_FORCE_RESOLVED_MODEL"


def _canonical(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model)


def _mock_cost(model: str, e: dict) -> float:
    p = MOCK_PRICES.get(_canonical(model), (0.0, 0.0, 0.0, 0.0))
    return (e["inputTokens"] * p[0] + e["outputTokens"] * p[1]
            + e["cacheCreationInputTokens"] * p[2] + e["cacheReadInputTokens"] * p[3]) / 1e6

TARGET_FILES = ("tinylib/palindrome.py", "tinylib/text.py", "tinylib/report.py")


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Emitter:
    def __init__(self, session_id: str, message_model: str = MOCK_MODEL, routing: bool = False):
        self.session_id = session_id
        self.message_model = message_model
        self.routing = routing
        self.turns = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_write = 0

    def _write(self, obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def init(self, tools: list[str], model: str, permission_mode: str, cwd: str) -> None:
        self._write(
            {
                "type": "system",
                "subtype": "init",
                "cwd": cwd,
                "session_id": self.session_id,
                "tools": tools,
                "mcp_servers": [],
                "model": model,
                "permissionMode": permission_mode,
                "slash_commands": [],
                "apiKeySource": "none",
                "claude_code_version": VERSION,
                "output_style": "default",
                "agents": [],
                "skills": [],
                "plugins": [],
                "uuid": str(uuid.uuid4()),
                "analytics_disabled": False,
            }
        )

    def _usage(self) -> dict:
        self.input_tokens += 1200
        self.output_tokens += 180
        self.cache_read += 800
        self.cache_write += 40
        return {
            "input_tokens": 1200,
            "output_tokens": 180,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 40,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 40,
            },
            "output_tokens_details": {"thinking_tokens": 0},
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            "service_tier": "standard",
        }

    def assistant(self, content: list[dict]) -> None:
        self.turns += 1
        self._write(
            {
                "type": "assistant",
                "message": {
                    "id": f"msg_{uuid.uuid4().hex[:16]}",
                    "model": self.message_model,
                    "role": "assistant",
                    "type": "message",
                    "stop_reason": None,
                    "content": content,
                    "usage": self._usage(),
                },
                "parent_tool_use_id": None,
                "session_id": self.session_id,
                "uuid": str(uuid.uuid4()),
                "timestamp": _ts(),
            }
        )

    def tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        self._write(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tid,
                            "content": [{"type": "text", "text": text}],
                            "is_error": err,
                        }
                        for tid, text, err in results
                    ],
                },
                "parent_tool_use_id": None,
                "session_id": self.session_id,
                "uuid": str(uuid.uuid4()),
                "timestamp": _ts(),
            }
        )

    def _routing_model_usage(self) -> dict:
        main = {"inputTokens": self.input_tokens, "outputTokens": self.output_tokens,
                "cacheReadInputTokens": self.cache_read, "cacheCreationInputTokens": self.cache_write}
        entries = {self.message_model: main}
        if self.message_model == MOCK_AUX_MODEL:
            for k, v in MOCK_AUX_USAGE.items():
                main[k] += v
        else:
            entries[MOCK_AUX_MODEL] = dict(MOCK_AUX_USAGE)
        for name, e in entries.items():
            e.update({"thinkingTokens": 0, "costUSD": _mock_cost(name, e),
                      "canonicalModel": _canonical(name), "provider": "firstParty"})
        return entries

    def result(self, text: str, duration_ms: int) -> None:
        if self.routing:
            model_usage = self._routing_model_usage()
            total_cost = sum(e["costUSD"] for e in model_usage.values())
        else:
            model_usage = {MOCK_MODEL: {"inputTokens": self.input_tokens}}
            total_cost = 0.0123
        self._write(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": duration_ms,
                "duration_api_ms": duration_ms - 5,
                "num_turns": self.turns,
                "result": text,
                "session_id": self.session_id,
                "total_cost_usd": total_cost,
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "cache_read_input_tokens": self.cache_read,
                    "cache_creation_input_tokens": self.cache_write,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 0,
                        "ephemeral_5m_input_tokens": self.cache_write,
                    },
                    "output_tokens_details": {"thinking_tokens": 0},
                    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
                    "service_tier": "standard",
                },
                "modelUsage": model_usage,
                "permission_denials": [],
                "subagent_stats": {"spawned": 0, "completed": 0, "failed": 0},
                "stop_reason": "end_turn",
                "terminal_reason": "success",
                "uuid": str(uuid.uuid4()),
                "timestamp": _ts(),
            }
        )


# --------------------------------------------------------------------------
# real tool behaviour
# --------------------------------------------------------------------------


def do_read(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        return f"Error: file not found: {path}"
    lines = p.read_text(encoding="utf-8").splitlines()
    return "\n".join(f"{i:6d}\t{l}" for i, l in enumerate(lines, start=1))


def do_grep(pattern: str, root: str = ".") -> str:
    rx = re.compile(pattern)
    hits = []
    for p in sorted(Path(root).rglob("*.py")):
        if ".git" in p.parts or "__pycache__" in p.parts:
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p.as_posix()}:{i}:{line}")
        except OSError:
            continue
    return "\n".join(hits) if hits else "No matches found"


def do_edit(path: str, old: str, new: str) -> tuple[str, bool]:
    p = Path(path)
    if not p.is_file():
        return f"Error: file not found: {path}", True
    text = p.read_text(encoding="utf-8")
    if old not in text:
        return f"Error: string not found in {path}", True
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"The file {path} has been updated.", False


def do_bash(command: str) -> str:
    import subprocess

    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=300
        )
        return ((r.stdout or "") + (r.stderr or ""))[:20000] or "(no output)"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"command failed: {exc}"


# --------------------------------------------------------------------------
# the scripted fix
# --------------------------------------------------------------------------

TEXT_OLD = '''def slugify(text):'''
TEXT_NEW = '''_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize_for_comparison(text):
    """Normalize for character-level comparison: drops punctuation and spacing."""
    return _PUNCT.sub("", normalize(text))


def slugify(text):'''

PAL_OLD = '''from .text import normalize


def is_palindrome(text):
    """Return True when `text` reads the same forwards and backwards."""
    cleaned = normalize(text).replace(" ", "")
    return bool(cleaned) and cleaned == cleaned[::-1]'''
PAL_NEW = '''from .text import normalize_for_comparison


def is_palindrome(text):
    """Return True when `text` reads the same forwards and backwards.

    Punctuation, spacing and casing are ignored.
    """
    cleaned = normalize_for_comparison(text)
    return bool(cleaned) and cleaned == cleaned[::-1]'''


# --------------------------------------------------------------------------
# roles
# --------------------------------------------------------------------------


def detect_role(prompt: str, allowed_tools: list[str]) -> str:
    if "Write the instruction you want to send to the Investigator" in prompt:
        return "coordinator_kickoff"
    if "Write the instruction you want to send to the Implementer" in prompt:
        return "coordinator_plan"
    if "Write the final result to return to the user" in prompt:
        return "coordinator_wrapup"
    if "Do not modify any file" in prompt:
        return "investigator"
    # C1 (Stage 1): the Worker session resumed for its Implementer phase. It is
    # given the Coordinator's instruction only, not a forwarded report.
    if "You are now the Implementer" in prompt:
        return "implementer"
    if "Investigation report from the Investigator" in prompt:
        return "implementer"
    if not allowed_tools:
        return "coordinator_kickoff"
    return "solo"


INVESTIGATION_REPORT = """Findings.

The symptom is in `tinylib/palindrome.py`: `is_palindrome` only strips spaces
after normalizing, so punctuation survives and "A man, a plan, a canal: Panama!"
compares unequal to its reverse.

The root cause is in `tinylib/text.py`: `normalize` collapses whitespace and
lowercases but deliberately preserves punctuation.

The constraint is in `tinylib/report.py`: `render_heading` and `render_report`
call `normalize` and depend on punctuation being preserved, and `slugify` in
`tinylib/text.py` also builds on it. So `normalize` itself must not change.

Recommendation: add a separate comparison-only normalizer in `tinylib/text.py`
and have `tinylib/palindrome.py` use that, leaving `normalize` untouched.
"""

INVESTIGATION_INSTRUCTION = """Please investigate why palindrome detection fails
for strings containing punctuation. Find the function that performs the check,
identify the root cause, and tell me which other parts of the library depend on
the same code path so we do not break them.
"""

IMPLEMENTATION_INSTRUCTION = """Please implement the fix. Add a comparison-only
normalizer alongside the existing one and use it for the palindrome check. Do not
change the behaviour of the existing normalizer, slugs or report headings. Run the
test suite when you are done.
"""


def emit_reads(em: Emitter, paths: list[str]) -> None:
    """One assistant message issuing several parallel reads, then their results.

    This is the concurrency shape temporal availability must handle: the calls
    share a start line, so none of them can be attributed to the others.
    """
    calls = []
    for path in paths:
        calls.append(
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:20]}",
                "name": "Read",
                "input": {"file_path": path},
            }
        )
    em.assistant(calls)
    em.tool_results([(c["id"], do_read(c["input"]["file_path"]), False) for c in calls])


def emit_single_read(em: Emitter, path: str) -> None:
    tid = f"toolu_{uuid.uuid4().hex[:20]}"
    em.assistant([{"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}])
    em.tool_results([(tid, do_read(path), False)])


def emit_parallel_read_and_cat(em: Emitter, path: str) -> None:
    """Acquire one file twice in a single message, via Read and via `cat`."""
    rid = f"toolu_{uuid.uuid4().hex[:20]}"
    bid = f"toolu_{uuid.uuid4().hex[:20]}"
    em.assistant(
        [
            {"type": "tool_use", "id": rid, "name": "Read", "input": {"file_path": path}},
            {"type": "tool_use", "id": bid, "name": "Bash", "input": {"command": f"cat {path}"}},
        ]
    )
    em.tool_results(
        [(rid, do_read(path), False), (bid, do_bash(f"cat {path}"), False)]
    )


def emit_grep(em: Emitter, pattern: str) -> None:
    tid = f"toolu_{uuid.uuid4().hex[:20]}"
    em.assistant(
        [{"type": "tool_use", "id": tid, "name": "Grep", "input": {"pattern": pattern, "path": "."}}]
    )
    em.tool_results([(tid, do_grep(pattern), False)])


def emit_bash(em: Emitter, command: str) -> None:
    tid = f"toolu_{uuid.uuid4().hex[:20]}"
    em.assistant([{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}])
    em.tool_results([(tid, do_bash(command), False)])


def emit_edit(em: Emitter, path: str, old: str, new: str) -> None:
    tid = f"toolu_{uuid.uuid4().hex[:20]}"
    em.assistant(
        [
            {
                "type": "tool_use",
                "id": tid,
                "name": "Edit",
                "input": {"file_path": path, "old_string": old, "new_string": new},
            }
        ]
    )
    text, err = do_edit(path, old, new)
    em.tool_results([(tid, text, err)])


def apply_fix(em: Emitter) -> None:
    emit_edit(em, "tinylib/text.py", TEXT_OLD, TEXT_NEW)
    emit_edit(em, "tinylib/palindrome.py", PAL_OLD, PAL_NEW)


def run_role(em: Emitter, role: str) -> str:
    if role == "coordinator_kickoff":
        em.assistant([{"type": "text", "text": INVESTIGATION_INSTRUCTION}])
        return INVESTIGATION_INSTRUCTION
    if role == "coordinator_plan":
        em.assistant([{"type": "text", "text": IMPLEMENTATION_INSTRUCTION}])
        return IMPLEMENTATION_INSTRUCTION
    if role == "coordinator_wrapup":
        text = "Fixed: palindrome checks now ignore punctuation; report headings unchanged."
        em.assistant([{"type": "text", "text": text}])
        return text

    if role == "investigator":
        emit_grep(em, r"def is_palindrome")
        emit_reads(em, list(TARGET_FILES))
        emit_grep(em, r"normalize\(")
        em.assistant([{"type": "text", "text": INVESTIGATION_REPORT}])
        return INVESTIGATION_REPORT

    if role == "implementer":
        # Deliberately re-acquires what the Investigator already reported. This is
        # the behaviour Stage 0 exists to measure; it is not told to avoid it.
        emit_reads(em, list(TARGET_FILES))
        # One assistant message that acquires the SAME file twice, through two
        # different tools, concurrently. This exercises two things end to end:
        # cross-tool content identity (Read's numbered output vs cat's plain
        # output must hash alike), and temporal availability - neither call can
        # be attributed to the other because they started together.
        emit_parallel_read_and_cat(em, "tinylib/text.py")
        emit_grep(em, r"normalize\(")
        apply_fix(em)
        emit_bash(em, "python -m pytest tests -q")
        text = "Added normalize_for_comparison in tinylib/text.py and used it in tinylib/palindrome.py. Tests pass."
        em.assistant([{"type": "text", "text": text}])
        return text

    # solo (Arm A)
    emit_grep(em, r"def is_palindrome")
    emit_reads(em, list(TARGET_FILES))
    apply_fix(em)
    emit_bash(em, "python -m pytest tests -q")
    text = "Fixed by adding a comparison-only normalizer; existing normalize/slugify/report behaviour untouched."
    em.assistant([{"type": "text", "text": text}])
    return text


# --------------------------------------------------------------------------
# argv
# --------------------------------------------------------------------------

HELP_TEXT = """Usage: claude [options] [command] [prompt]

Options:
  -p, --print
  --output-format <format>   (choices: "text", "json", "stream-json")
  --input-format <format>    (choices: "text", "stream-json")
  --verbose
  --model <model>
  --allowedTools, --allowed-tools <tools...>
  --disallowedTools, --disallowed-tools <tools...>
  --tools <tools...>
  --session-id <uuid>
  --resume [value]
  --permission-mode <mode>
  --permission-prompts <target>
  --append-system-prompt <prompt>
  --include-hook-events
  --no-session-persistence
  --strict-mcp-config
  --setting-sources <sources>
  --add-dir <directories...>
  --fork-session
  --include-partial-messages
  -v, --version

Commands:
  auth      Manage authentication
"""


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(HELP_TEXT)
        return 0
    if "--version" in argv or "-v" in argv:
        sys.stdout.write(f"{VERSION} (Claude Code)\n")
        return 0
    if argv[:2] == ["auth", "status"]:
        sys.stdout.write(
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "mock",
                    "apiProvider": "firstParty",
                    "analyticsDisabled": False,
                },
                indent=2,
            )
            + "\n"
        )
        return 0

    def flag_value(*names: str) -> str | None:
        for i, a in enumerate(argv):
            if a in names and i + 1 < len(argv):
                return argv[i + 1]
        return None

    session_id = flag_value("--session-id") or flag_value("--resume") or str(uuid.uuid4())
    model = flag_value("--model") or MOCK_MODEL
    permission_mode = flag_value("--permission-mode") or "default"
    tools_arg = flag_value("--tools")
    allowed = [t for t in (tools_arg or "").split(",") if t]

    prompt = sys.stdin.read() if not sys.stdin.isatty() else ""

    t0 = time.monotonic()
    if model in MOCK_RESOLUTION:
        resolved = os.environ.get(FORCE_RESOLVED_ENV) or MOCK_RESOLUTION[model]
        em = Emitter(session_id, message_model=resolved, routing=True)
        em.init(allowed, resolved, permission_mode, os.getcwd())
    else:
        em = Emitter(session_id)
        em.init(allowed, model, permission_mode, os.getcwd())
    role = detect_role(prompt, allowed)
    final = run_role(em, role)
    em.result(final, int((time.monotonic() - t0) * 1000))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
