"""Tool-call classification, information identity, and acquisition coverage.

Identity is at RETURNED-CONTENT granularity, not byte ranges: Claude Code does
not expose read ranges in a form we can rely on, and SPEC.md forbids fabricating
them. What we hash is exactly the text the agent received.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional, Sequence

# --------------------------------------------------------------------------
# Acquisition classes
# --------------------------------------------------------------------------

STRUCTURED_READ = "structured_read"
STRUCTURED_SEARCH = "structured_search"
STRUCTURED_EDIT = "structured_edit"

# Bash categories. Derived data only: the raw command, stdout/stderr and exit
# status are always preserved unchanged in the raw stream.
BASH_READ = "bash_read"
BASH_SEARCH = "bash_search"
BASH_DIRECTORY_LISTING = "bash_directory_listing"
BASH_GIT_INSPECTION = "bash_git_inspection"
BASH_VERIFICATION = "bash_verification"
BASH_BUILD = "bash_build"
BASH_WORKSPACE_MANAGEMENT = "bash_workspace_management"
BASH_NON_ACQUISITION = "bash_non_acquisition"
BASH_UNKNOWN = "bash_unknown"

UNKNOWN_TOOL = "unknown_tool"
NON_ACQUISITION = "non_acquisition"

# A tool call the CLI refused. Claude Code states explicitly: "The action was NOT
# performed." It therefore acquired nothing, ran nothing, and changed nothing, and
# must never be counted as a read, a search or a test run.
TOOL_DENIED = "tool_denied"

# Backwards-compatible aliases for the pre-taxonomy names.
CLASSIFIED_BASH_READ = BASH_READ
CLASSIFIED_BASH_SEARCH = BASH_SEARCH
CLASSIFIED_TEST_RUN = BASH_VERIFICATION
BASH_MUTATE = BASH_WORKSPACE_MANAGEMENT
BASH_METADATA = BASH_NON_ACQUISITION
UNCLASSIFIED_BASH = BASH_UNKNOWN

CONFIDENCE = {
    STRUCTURED_READ: "high",
    STRUCTURED_SEARCH: "high",
    STRUCTURED_EDIT: "high",
    BASH_READ: "medium",
    BASH_SEARCH: "medium",
    BASH_DIRECTORY_LISTING: "medium",
    BASH_GIT_INSPECTION: "medium",
    BASH_VERIFICATION: "medium",
    BASH_BUILD: "medium",
    BASH_WORKSPACE_MANAGEMENT: "medium",
    BASH_NON_ACQUISITION: "medium",
    BASH_UNKNOWN: "none",
    UNKNOWN_TOOL: "none",
    NON_ACQUISITION: "high",
    TOOL_DENIED: "high",
}

# Classes that bring repository information into an agent's context AND whose
# information source is mechanically identified.
ACQUISITION_CLASSES = (
    STRUCTURED_READ,
    STRUCTURED_SEARCH,
    BASH_READ,
    BASH_SEARCH,
    BASH_DIRECTORY_LISTING,
    BASH_GIT_INSPECTION,
)

# Acquisition-capable but NOT mechanically attributable. These are the classes
# that make observability incomplete, and the only ones that lower coverage.
OPAQUE_ACQUISITION_CLASSES = (BASH_UNKNOWN, UNKNOWN_TOOL)

# Could not have delivered repository content to the model, so irrelevant to
# acquisition observability. `bash_verification` and `bash_build` are here
# deliberately: their output is *generated* by running code, not *acquired* from
# the repository, and it is captured verbatim in the raw stream either way. See
# SPEC.md section 7 for the full rationale.
NON_ACQUISITION_CLASSES = (
    STRUCTURED_EDIT,
    BASH_VERIFICATION,
    BASH_BUILD,
    BASH_WORKSPACE_MANAGEMENT,
    BASH_NON_ACQUISITION,
    NON_ACQUISITION,
    TOOL_DENIED,
)

READ_TOOLS = {"read", "notebookread"}
SEARCH_TOOLS = {"grep", "glob"}
EDIT_TOOLS = {"edit", "write", "notebookedit", "multiedit"}
BASH_TOOLS = {"bash", "powershell", "shell"}
KNOWN_NON_ACQUISITION_TOOLS = {"todowrite", "todoread", "exitplanmode", "askuserquestion"}

# --------------------------------------------------------------------------
# Bash command classification
# --------------------------------------------------------------------------

# Commands that read named file content. With no file operand they read stdin and
# are pure stream filters instead (see NEUTRAL_FILTER_CMDS / _has_file_operand).
BASH_READ_CMDS = {
    "cat", "head", "tail", "nl", "more", "less", "type", "od", "xxd", "strings",
    "bat", "gc", "get-content",
}
BASH_SEARCH_CMDS = {
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "select-string",
    "sls", "awk",
}
# Structure discovery rather than content search.
BASH_LISTING_CMDS = {"find", "tree", "fd", "locate", "dir"}
BASH_TEST_CMDS = {
    "pytest", "tox", "nose2", "jest", "vitest", "mocha", "ctest", "unittest",
    "ruff", "flake8", "mypy", "pyright", "eslint", "pylint", "black", "isort",
}
BASH_BUILD_CMDS = {
    "make", "cmake", "ninja", "gcc", "g++", "clang", "clang++", "cl", "tsc",
    "webpack", "rollup", "vite", "msbuild", "javac", "rustc",
}
BASH_WORKSPACE_CMDS = {
    "mv", "cp", "rm", "mkdir", "rmdir", "touch", "tee", "chmod", "chown", "ln",
    "patch", "install", "truncate", "unzip", "tar",
}
BASH_NON_ACQUISITION_CMDS = {
    "pwd", "whoami", "date", "echo", "which", "where", "env", "printenv",
    "uname", "hostname", "du", "df", "true", "false", "exit", "cd", "test",
    "sleep", "clear", "export", "set", "type-p",
}

# Pure stream filters: they consume stdin and acquire nothing themselves, so they
# must not make an otherwise-classified pipeline unknown. `cd ... && find . | sort`
# is a directory listing, not an unclassifiable command.
NEUTRAL_FILTER_CMDS = {
    "sort", "uniq", "wc", "cut", "tr", "column", "rev", "tac", "paste", "join",
    "jq", "xargs", "fmt", "expand", "unexpand", "shuf", "numfmt", "pr", "split",
    "out-string", "measure-object", "sort-object", "select-object",
}

# `git` needs subcommand-level classification.
GIT_INSPECTION_SUBCMDS = {
    "show", "diff", "blame", "cat-file", "grep", "log", "ls-files", "ls-tree",
    "status", "describe", "shortlog", "rev-list",
}
GIT_MUTATE_SUBCMDS = {
    "commit", "add", "checkout", "reset", "revert", "merge", "rebase", "push",
    "pull", "apply", "clean", "rm", "mv", "stash", "tag", "branch", "init",
    "clone", "switch", "restore", "cherry-pick",
}
GIT_NON_ACQUISITION_SUBCMDS = {"rev-parse", "config", "remote", "version"}

# Separators that end one command and start another. Only honoured OUTSIDE
# quotes: `python -c "import x; print(x)"` is ONE command, and a real run showed
# a naive split turning such a script body into bogus unknown segments.
_SEPARATORS = ("||", "&&", "|", ";", "&", "\n")

# 1: frozen through the Stage-0.5 freeze (5a756bf).
# 2: amendment 8 (2026-09-11): `&` inside a redirection is not a separator, and
#    interpreter version queries are non-acquisition metadata.
BASH_CLASSIFIER_VERSION = 2
_VERSION_QUERY_TOKENS = {"--version", "-V", "version"}


def split_bash_segments(command: str) -> list[str]:
    """Split a shell command into segments, respecting quotes.

    Deliberately simple: it tracks single/double quotes and backslash escapes.
    It is not a shell parser and does not pretend to be - see BASH_UNKNOWN.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: Optional[str] = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]

        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue

        # Amendment 8: `&` inside a redirection (`2>&1`, `>&2`, `<&3`, `&>file`)
        # is not a command separator. v1 split `pytest -q 2>&1` into `... 2>`
        # and an unclassifiable `1`.
        if ch == "&" and (command.startswith("&>", i) or (buf and buf[-1] in "<>")):
            buf.append(ch)
            i += 1
            continue

        matched = next((sep for sep in _SEPARATORS if command.startswith(sep, i)), None)
        if matched:
            segments.append("".join(buf))
            buf = []
            i += len(matched)
            continue

        buf.append(ch)
        i += 1

    segments.append("".join(buf))
    return [s for s in segments if s.strip()]

# Interpreters whose `-c`/`-e` inline-script form runs code rather than reading
# repository text. Treated as verification, not acquisition: the script's output
# is generated, and the script body itself is captured verbatim in the raw stream.
_INLINE_SCRIPT_FLAGS = {"-c", "-e", "-m"}


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _strip_env_prefix(tokens: list[str]) -> list[str]:
    """Drop `VAR=value` prefixes and common wrappers to reach the real command."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t):
            i += 1
            continue
        if t in ("env", "command", "nohup", "time", "sudo", "xargs", "nice"):
            i += 1
            continue
        break
    return tokens[i:]


NEUTRAL = "_neutral_filter"  # internal only; never a reported category

_FLAGLIKE = re.compile(r"^-")


def _has_file_operand(tokens: list[str]) -> bool:
    """True when a command was given something to open, rather than reading stdin.

    `head -50` is a stream filter; `head -50 foo.py` reads a file. Flags and their
    obvious numeric values are ignored.
    """
    for t in tokens:
        if _FLAGLIKE.match(t):
            continue
        if t.isdigit():
            continue
        return True
    return False


def classify_bash_segment(segment: str) -> str:
    """Classify one pipeline/`&&` segment of a shell command.

    Every branch is a deterministic decision on the observed command text. When
    the command family is not recognized the answer is BASH_UNKNOWN: false
    precision is worse than an admitted gap.
    """
    tokens = _strip_env_prefix(_tokenize(segment.strip()))
    if not tokens:
        return BASH_NON_ACQUISITION

    head = tokens[0].lower()
    if head.endswith(".exe"):
        head = head[: -len(".exe")]
    head = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    rest = tokens[1:]

    if head in NEUTRAL_FILTER_CMDS:
        return NEUTRAL

    # ls / dir: a recursive listing discovers structure; a plain one is trivial.
    if head in ("ls", "dir", "get-childitem", "gci"):
        flags = " ".join(rest)
        return (
            BASH_DIRECTORY_LISTING
            if re.search(r"-\w*[Rr]|-recurse", flags)
            else BASH_NON_ACQUISITION
        )

    # `stat`/`file` report metadata about a path, not its content.
    if head in ("stat", "file"):
        return BASH_NON_ACQUISITION

    if head == "sed":
        if "-i" in rest:
            return BASH_WORKSPACE_MANAGEMENT
        joined = " ".join(rest)
        looks_like_range_print = "-n" in rest or re.search(r"\d+,\d+p", joined)
        if not looks_like_range_print:
            return BASH_UNKNOWN
        # The first non-flag token is sed's SCRIPT, not a file. `sed -n '1,5p'`
        # filters stdin; `sed -n '1,5p' foo.py` reads a file.
        operands = [t for t in rest if not _FLAGLIKE.match(t)]
        return BASH_READ if len(operands) > 1 else NEUTRAL

    if head == "git":
        sub = next((t.lower() for t in rest if not t.startswith("-")), "")
        if sub in GIT_INSPECTION_SUBCMDS:
            return BASH_GIT_INSPECTION
        if sub in GIT_MUTATE_SUBCMDS:
            return BASH_WORKSPACE_MANAGEMENT
        if sub in GIT_NON_ACQUISITION_SUBCMDS:
            return BASH_NON_ACQUISITION
        return BASH_UNKNOWN

    # Interpreters and build drivers: distinguish a test/lint run from an inline
    # script from an unrecognized invocation.
    if head in ("python", "python3", "py", "node", "npm", "yarn", "pnpm", "cargo",
                "go", "dotnet", "mvn", "gradle", "uv", "deno", "ruby", "perl",
                "pwsh", "powershell"):
        joined = " ".join(t.lower() for t in rest)
        if re.search(r"\b(test|pytest|unittest|nose2|ruff|flake8|mypy|pyright|lint|check)\b", joined):
            return BASH_VERIFICATION
        if re.search(r"\b(build|compile|bundle|dist)\b", joined):
            return BASH_BUILD
        # `python -c "..."` / `node -e "..."` runs generated code, not a file read.
        if any(t in _INLINE_SCRIPT_FLAGS for t in rest):
            return BASH_VERIFICATION
        # Amendment 8: `python --version` / `go version` report the tool's own
        # version, never repository content. Exact tokens only: `python -v` is
        # verbose mode, not a version query.
        if rest and set(rest) <= _VERSION_QUERY_TOKENS:
            return BASH_NON_ACQUISITION
        return BASH_UNKNOWN

    if head == "make":
        joined = " ".join(t.lower() for t in rest)
        return BASH_VERIFICATION if re.search(r"\b(test|check|lint)\b", joined) else BASH_BUILD

    if head in BASH_TEST_CMDS:
        return BASH_VERIFICATION
    if head in BASH_BUILD_CMDS:
        return BASH_BUILD
    if head in BASH_READ_CMDS:
        return BASH_READ if _has_file_operand(rest) else NEUTRAL
    if head in BASH_SEARCH_CMDS:
        return BASH_SEARCH
    if head in BASH_LISTING_CMDS:
        return BASH_DIRECTORY_LISTING
    if head in BASH_WORKSPACE_CMDS:
        return BASH_WORKSPACE_MANAGEMENT
    if head in BASH_NON_ACQUISITION_CMDS:
        return BASH_NON_ACQUISITION
    return BASH_UNKNOWN


# Precedence when a command has several segments: an unrecognized segment wins, so
# coverage is never over-claimed; then the most information-bearing acquisition
# category; then non-acquisition work.
_PRECEDENCE = [
    BASH_UNKNOWN,
    BASH_SEARCH,
    BASH_READ,
    BASH_GIT_INSPECTION,
    BASH_DIRECTORY_LISTING,
    BASH_VERIFICATION,
    BASH_BUILD,
    BASH_WORKSPACE_MANAGEMENT,
    BASH_NON_ACQUISITION,
]


def classify_bash_command(command: str) -> tuple[str, list[str]]:
    """Classify a full shell command. Returns (class, per_segment_classes).

    Neutral stream filters are recorded in the per-segment list as
    `_neutral_filter` for auditability but never decide the overall category.
    """
    if not command or not command.strip():
        return BASH_NON_ACQUISITION, []
    segments = split_bash_segments(command)
    classes = [classify_bash_segment(s) for s in segments] or [BASH_NON_ACQUISITION]
    decisive = [c for c in classes if c != NEUTRAL]
    if not decisive:
        # Nothing but stream filters: it read stdin, which came from nowhere.
        return BASH_NON_ACQUISITION, classes
    for candidate in _PRECEDENCE:
        if candidate in decisive:
            return candidate, classes
    return BASH_UNKNOWN, classes


# --------------------------------------------------------------------------
# Content identity
# --------------------------------------------------------------------------

SHINGLE_LINES = 3  # 3-line windows, not per-line hashes (SPEC.md section 7)


def content_sha(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=16).hexdigest()


def _normalize_line(line: str) -> str:
    """Strip a `NNN\\t` / `NNN|` read-tool line prefix, then trailing whitespace.

    Order matters: rstrip first would eat the tab that delimits the prefix, and a
    blank numbered line would survive as the bare line number.
    """
    line = re.sub(r"^\s*\d+[\t|]", "", line)
    return line.rstrip()


def shingles(text: Optional[str], window: int = SHINGLE_LINES) -> list[str]:
    """Stable BLAKE2b hashes over sliding `window`-line windows of non-empty lines."""
    if not text:
        return []
    lines = [_normalize_line(l) for l in text.splitlines()]
    lines = [l for l in lines if l.strip()]
    if len(lines) < window:
        if not lines:
            return []
        joined = "\n".join(lines)
        return [hashlib.blake2b(joined.encode("utf-8", "replace"), digest_size=12).hexdigest()]
    out = []
    for i in range(len(lines) - window + 1):
        joined = "\n".join(lines[i : i + window])
        out.append(hashlib.blake2b(joined.encode("utf-8", "replace"), digest_size=12).hexdigest())
    return out


# --------------------------------------------------------------------------
# Acquisition records
# --------------------------------------------------------------------------


@dataclass
class Acquisition:
    """One observed tool call, normalized for duplication analysis."""

    tool_use_id: str
    agent_id: str
    session_key: str
    tool_name: str
    acquisition_class: str
    confidence: str
    start_ts: Optional[str]
    end_ts: Optional[str]
    start_line: int
    # First stream line of the API message that issued this call. Tool calls
    # sharing it were issued together and are genuinely concurrent.
    concurrency_group_line: int
    end_line: Optional[int]
    target_path: Optional[str]
    query: Optional[str]
    command: Optional[str]
    bash_segment_classes: list[str]
    result_chars: int
    result_bytes: int
    result_sha: Optional[str]
    result_shingles: list[str]
    result_paths: list[str]
    is_error: Optional[bool]
    completed: bool
    turn_id: Optional[int] = None
    path_version_hint: Optional[str] = None
    permission_denied: bool = False
    attempted_class: Optional[str] = None  # what it would have been, had it run

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def is_acquisition(self) -> bool:
        return self.acquisition_class in ACQUISITION_CLASSES

    @property
    def is_opaque_acquisition(self) -> bool:
        return self.acquisition_class in OPAQUE_ACQUISITION_CLASSES

    @property
    def is_acquisition_candidate(self) -> bool:
        """Could this operation plausibly have delivered repository/task
        information to the model? Denied calls could not: nothing ran."""
        if self.permission_denied:
            return False
        return self.is_acquisition or self.is_opaque_acquisition


_PATHLIKE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,6}")


# --------------------------------------------------------------------------
# Content windows for communication-duplication measurement (amendment 5)
# --------------------------------------------------------------------------
#
# Deliberately separate from `shingles`, which is preregistered and unchanged.
# Two differences, both needed to compare prose handoffs with tool output:
#   * indentation is stripped, so code quoted inside a markdown handoff still
#     matches the file it came from even if re-indented;
#   * uninformative windows are dropped. A window must carry at least
#     WINDOW_MIN_ALNUM alphanumeric characters AND at least
#     WINDOW_MIN_CONTENT_LINES lines containing an alphanumeric character, so runs
#     of `)`, `]`, `}` or `else:` cannot register as duplication on their own.

WINDOW_MIN_ALNUM = 16
WINDOW_MIN_CONTENT_LINES = 2
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class ContentWindow:
    digest: str
    line_indexes: tuple[int, ...]  # indexes into text.splitlines()


def content_windows(text: Optional[str], window: int = SHINGLE_LINES) -> list[ContentWindow]:
    if not text:
        return []
    kept: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines()):
        norm = _normalize_line(line).strip()
        if norm:
            kept.append((i, norm))
    out: list[ContentWindow] = []
    for j in range(len(kept) - window + 1):
        chunk = kept[j : j + window]
        norm = [n for _, n in chunk]
        alnum = sum(len(_ALNUM_RE.findall(n)) for n in norm)
        content_lines = sum(1 for n in norm if _ALNUM_RE.search(n))
        if alnum < WINDOW_MIN_ALNUM or content_lines < WINDOW_MIN_CONTENT_LINES:
            continue
        digest = hashlib.blake2b(
            "\n".join(norm).encode("utf-8", "replace"), digest_size=12
        ).hexdigest()
        out.append(ContentWindow(digest, tuple(i for i, _ in chunk)))
    return out


def content_chunk_set(text: Optional[str]) -> set[str]:
    return {w.digest for w in content_windows(text)}


def covered_by(text: Optional[str], reference: set[str]) -> tuple[int, int, set[str]]:
    """(matched_windows, chars_estimate, matched_digests).

    `chars_estimate` is the stripped length of every line of `text` covered by at
    least one window whose digest is in `reference`. It is an estimate: lines in
    a quoted block shorter than one window cannot be attributed.
    """
    if not text:
        return 0, 0, set()
    lines = text.splitlines()
    covered: set[int] = set()
    matched: set[str] = set()
    count = 0
    for w in content_windows(text):
        if w.digest in reference:
            count += 1
            matched.add(w.digest)
            covered.update(w.line_indexes)
    return count, sum(len(lines[i].strip()) for i in covered), matched


def extract_result_paths(text: Optional[str], limit: int = 400) -> list[str]:
    """Best-effort path extraction from a search result body."""
    if not text:
        return []
    seen: list[str] = []
    for m in _PATHLIKE.finditer(text):
        cand = m.group(0).replace("\\", "/").strip("./")
        if cand and cand not in seen:
            seen.append(cand)
        if len(seen) >= limit:
            break
    return seen


def classify_tool_call(
    *,
    tool_use_id: str,
    tool_name: str,
    tool_input: dict,
    result_text: Optional[str],
    agent_id: str,
    session_key: str,
    start_ts: Optional[str],
    end_ts: Optional[str],
    start_line: int,
    end_line: Optional[int],
    is_error: Optional[bool],
    concurrency_group_line: Optional[int] = None,
    turn_id: Optional[int] = None,
    permission_denied: bool = False,
) -> Acquisition:
    name = (tool_name or "?").strip()
    low = name.lower()
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    command: Optional[str] = None
    segment_classes: list[str] = []
    target_path: Optional[str] = None
    query: Optional[str] = None

    if low in READ_TOOLS:
        klass = STRUCTURED_READ
        target_path = tool_input.get("file_path") or tool_input.get("path")
    elif low in SEARCH_TOOLS:
        klass = STRUCTURED_SEARCH
        query = tool_input.get("pattern") or tool_input.get("query")
        target_path = tool_input.get("path")
    elif low in EDIT_TOOLS:
        klass = STRUCTURED_EDIT
        target_path = tool_input.get("file_path") or tool_input.get("path")
    elif low in BASH_TOOLS:
        command = tool_input.get("command") or tool_input.get("script") or ""
        klass, segment_classes = classify_bash_command(command)
        query = command
    elif low in KNOWN_NON_ACQUISITION_TOOLS:
        klass = NON_ACQUISITION
    else:
        klass = UNKNOWN_TOOL

    # A denied call performed nothing. Record what it would have been, then
    # reclassify: it is not a read, not a search, and not a test run.
    attempted = None
    if permission_denied:
        attempted = klass
        klass = TOOL_DENIED

    text = result_text or ""
    return Acquisition(
        tool_use_id=tool_use_id,
        agent_id=agent_id,
        session_key=session_key,
        tool_name=name,
        acquisition_class=klass,
        confidence=CONFIDENCE.get(klass, "none"),
        start_ts=start_ts,
        end_ts=end_ts,
        start_line=start_line,
        concurrency_group_line=(
            concurrency_group_line if concurrency_group_line is not None else start_line
        ),
        end_line=end_line,
        target_path=_norm_path(target_path),
        query=query,
        command=command,
        bash_segment_classes=segment_classes,
        result_chars=len(text),
        result_bytes=len(text.encode("utf-8", "replace")),
        result_sha=content_sha(result_text) if result_text is not None else None,
        result_shingles=shingles(result_text),
        result_paths=extract_result_paths(text)
        if klass in (STRUCTURED_SEARCH, BASH_SEARCH, BASH_DIRECTORY_LISTING, BASH_GIT_INSPECTION)
        else [],
        is_error=is_error,
        completed=end_line is not None,
        turn_id=turn_id,
        permission_denied=permission_denied,
        attempted_class=attempted,
    )


def _norm_path(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    return str(p).replace("\\", "/")


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


# The exact formula, versioned so a change is visible in every run's artifacts.
#
#   Each observed tool call is placed in exactly one bucket:
#
#     acquisition_classified  - it could deliver repository information AND its
#                               information source is mechanically identified
#                               (structured_read, structured_search, bash_read,
#                                bash_search, bash_directory_listing,
#                                bash_git_inspection)
#     acquisition_unknown     - it could deliver repository information but we
#                               cannot say what it obtained
#                               (bash_unknown, unknown_tool)
#     non_acquisition         - it could not deliver repository content at all
#                               (structured_edit, bash_verification, bash_build,
#                                bash_workspace_management, bash_non_acquisition)
#     denied                  - the CLI refused it; nothing ran, nothing was
#                               obtained (tool_denied)
#
#   acquisition_candidates = acquisition_classified + acquisition_unknown
#
#   acquisition_coverage   = acquisition_classified / acquisition_candidates
#
# Rationale for the denominator: it must represent tool operations that could
# plausibly have delivered repository/task information to the model. A `pytest`
# run, a `mkdir`, or a call the CLI refused cannot have done so, and counting
# them would let unrelated shell activity depress a number that is supposed to
# measure how much *acquisition* we can attribute. Conversely bash_unknown stays
# in the denominator precisely because it might have acquired something.
COVERAGE_FORMULA_VERSION = 2


@dataclass
class CoverageReport:
    # per-category counts, using the reported taxonomy
    structured_read: int = 0
    structured_search: int = 0
    structured_edit: int = 0
    bash_read: int = 0
    bash_search: int = 0
    bash_directory_listing: int = 0
    bash_git_inspection: int = 0
    bash_verification: int = 0
    bash_build: int = 0
    bash_workspace_management: int = 0
    bash_non_acquisition: int = 0
    bash_unknown: int = 0
    unknown_tool: int = 0
    non_acquisition: int = 0
    tool_denied: int = 0

    # bucket roll-ups
    acquisition_classified: int = 0
    acquisition_unknown: int = 0
    acquisition_candidates: int = 0
    non_acquisition_calls: int = 0
    denied_calls: int = 0

    acquisition_coverage: Optional[float] = None
    meets_minimum: Optional[bool] = None
    minimum_required: Optional[float] = None
    formula_version: int = COVERAGE_FORMULA_VERSION
    formula: str = (
        "acquisition_coverage = acquisition_classified / "
        "(acquisition_classified + acquisition_unknown); "
        "non-acquisition and permission-denied calls are excluded from both"
    )

    # kept for continuity with formula version 1 reports
    @property
    def acquisition_capable_calls(self) -> int:
        return self.acquisition_candidates

    def as_dict(self) -> dict:
        d = asdict(self)
        d["acquisition_capable_calls"] = self.acquisition_candidates
        return d


_COVERAGE_COUNT_ATTR = {
    STRUCTURED_READ: "structured_read",
    STRUCTURED_SEARCH: "structured_search",
    STRUCTURED_EDIT: "structured_edit",
    BASH_READ: "bash_read",
    BASH_SEARCH: "bash_search",
    BASH_DIRECTORY_LISTING: "bash_directory_listing",
    BASH_GIT_INSPECTION: "bash_git_inspection",
    BASH_VERIFICATION: "bash_verification",
    BASH_BUILD: "bash_build",
    BASH_WORKSPACE_MANAGEMENT: "bash_workspace_management",
    BASH_NON_ACQUISITION: "bash_non_acquisition",
    BASH_UNKNOWN: "bash_unknown",
    UNKNOWN_TOOL: "unknown_tool",
    NON_ACQUISITION: "non_acquisition",
    TOOL_DENIED: "tool_denied",
}


def coverage(acqs: Sequence[Acquisition], minimum: float = 0.90) -> CoverageReport:
    """Compute acquisition observability. See COVERAGE_FORMULA_VERSION above."""
    rep = CoverageReport(minimum_required=minimum)
    for a in acqs:
        attr = _COVERAGE_COUNT_ATTR.get(a.acquisition_class)
        if attr:
            setattr(rep, attr, getattr(rep, attr) + 1)

        if a.permission_denied:
            rep.denied_calls += 1
        elif a.acquisition_class in ACQUISITION_CLASSES:
            rep.acquisition_classified += 1
        elif a.acquisition_class in OPAQUE_ACQUISITION_CLASSES:
            rep.acquisition_unknown += 1
        else:
            rep.non_acquisition_calls += 1

    rep.acquisition_candidates = rep.acquisition_classified + rep.acquisition_unknown
    if rep.acquisition_candidates == 0:
        rep.acquisition_coverage = None
        rep.meets_minimum = None
    else:
        rep.acquisition_coverage = rep.acquisition_classified / rep.acquisition_candidates
        rep.meets_minimum = (
            rep.acquisition_coverage >= minimum and rep.unknown_tool == 0
        )
    return rep


# --------------------------------------------------------------------------
# Handoff sizing
# --------------------------------------------------------------------------


def handoff_size(text: str) -> dict:
    """Descriptive sizes for a natural-language handoff.

    `estimated_tokens` is a local heuristic, never a provider token count, and is
    deliberately named so it can never be confused with billed tokens.
    """
    text = text or ""
    words = len(text.split())
    chars = len(text)
    # ~4 chars/token is the usual English rule of thumb; combined with a word
    # floor it is adequate for RELATIVE comparison of handoff bodies only.
    est = max(int(round(chars / 4)), words)
    return {
        "chars": chars,
        "utf8_bytes": len(text.encode("utf-8")),
        "words": words,
        "lines": len(text.splitlines()),
        "estimated_tokens": est,
        "estimated_tokens_method": "chars/4 with word floor (local heuristic)",
        "estimated_tokens_availability": "estimated",
    }
