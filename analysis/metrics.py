"""Duplication metrics: temporal availability, priming, oracle upper bound.

Ordering
--------
Every acquisition carries an order key `(session_index, line_no)`:

* `session_index` comes from the session_key prefix and reflects the fixed,
  strictly sequential agent chain, so cross-session ordering is
  harness-observed and authoritative.
* the line component is the CONCURRENCY GROUP line, not the raw stream line.
  Claude Code emits one stream event per content block, so several tool_use
  blocks belonging to ONE API assistant message appear on consecutive lines even
  though they were issued together. Grouping them by `message.id` and using the
  message's first line makes those calls share a start boundary, which is
  exactly the concurrency case temporal availability must handle correctly.
  Verified on real runs: one assistant message issued 5 parallel Reads.

This avoids depending on wall-clock timestamps, which may be absent. Timestamps,
where present, are recorded as secondary corroboration.

Temporal availability, K(t)
---------------------------
    available(producer, consumer)  <=>  producer.end < consumer.start

Not `producer.end < consumer.end`. Given

    A READ START / B READ START / A READ END / B READ END

B could not have reused A's result when B began, so B's read is NOT attributable
to A.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional, Sequence

from harness import tools

# A consumer acquisition counts as substantially overlapping a producer when this
# fraction of the consumer's own content shingles were already available.
SHINGLE_OVERLAP_THRESHOLD = 0.60

IDENTICAL = "identical_content"
SHINGLE = "shingle_overlap"
SAME_QUERY = "same_query_same_result"
SAME_PATH = "same_path_same_content"

INTER_AGENT = "inter_agent"
INTRA_AGENT = "intra_agent"

PRIMED = "primed"
UNPRIMED = "unprimed"
PRIMING_UNKNOWN = "priming_undetermined"


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class Acq:
    """An acquisition with a global order key."""

    session_index: int
    session_key: str
    agent_id: str
    tool_use_id: str
    tool_name: str
    acquisition_class: str
    target_path: Optional[str]
    query: Optional[str]
    command: Optional[str]
    result_sha: Optional[str]
    result_shingles: tuple[str, ...]
    result_chars: int
    result_bytes: int
    start_line: int
    concurrency_group_line: int
    end_line: Optional[int]
    start_ts: Optional[str]
    end_ts: Optional[str]
    turn_id: Optional[int]
    completed: bool
    permission_denied: bool = False
    is_error: Optional[bool] = None

    @property
    def start_key(self) -> tuple[int, int]:
        """Start boundary for temporal availability.

        Uses the CONCURRENCY GROUP, not the raw stream line: Claude Code emits
        one stream event per content block, so several tool_use blocks in one API
        assistant message land on consecutive lines even though they were issued
        together. Ordering them by raw line would make genuinely concurrent calls
        look sequential and could attribute one to another.
        """
        return (self.session_index, self.concurrency_group_line)

    @property
    def end_key(self) -> tuple[int, int]:
        # An unfinished acquisition never becomes available to anyone.
        return (self.session_index, self.end_line if self.end_line is not None else 10**9)

    @property
    def is_acquisition(self) -> bool:
        return self.acquisition_class in tools.ACQUISITION_CLASSES

    @property
    def is_read(self) -> bool:
        return self.acquisition_class in (
            tools.STRUCTURED_READ,
            tools.CLASSIFIED_BASH_READ,
        )

    @property
    def is_search(self) -> bool:
        return self.acquisition_class in (
            tools.STRUCTURED_SEARCH,
            tools.CLASSIFIED_BASH_SEARCH,
        )

    @property
    def is_edit(self) -> bool:
        return self.acquisition_class == tools.STRUCTURED_EDIT

    @property
    def executed(self) -> bool:
        """The call actually ran and succeeded: completed, not refused, no error."""
        return self.completed and not self.permission_denied and not self.is_error


@dataclass
class Message:
    """A message delivered to an agent, with its position in the global order."""

    recipient: str
    sender: str
    label: str
    text: str
    delivered_before_session_index: int

    @property
    def delivery_key(self) -> tuple[int, int]:
        # Delivered at the start of the recipient's session, before any line of it.
        return (self.delivered_before_session_index, -1)


@dataclass
class Finding:
    consumer_tool_use_id: str
    consumer_agent: str
    consumer_session_key: str
    consumer_class: str
    consumer_target: Optional[str]
    producer_tool_use_id: str
    producer_agent: str
    producer_session_key: str
    producer_target: Optional[str]
    overlap_kind: str
    overlap_ratio: float
    overlap_shingles: int
    relation: str
    globally_previously_available: bool
    potentially_avoidable_acquisition: bool
    priming: str
    priming_evidence: dict
    duplicate_chars: int
    duplicate_bytes: int
    timing_basis: str
    # Set only for PRIMED inter-agent reacquisitions; see classify_reacquisition.
    reacquisition_subcategory: str = "not_applicable"
    subcategory_evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def acq_from_dict(d: dict, session_index: int) -> Acq:
    return Acq(
        session_index=session_index,
        session_key=d.get("session_key") or "",
        agent_id=d.get("agent_id") or "",
        tool_use_id=d.get("tool_use_id") or "",
        tool_name=d.get("tool_name") or "",
        acquisition_class=d.get("acquisition_class") or "",
        target_path=d.get("target_path"),
        query=d.get("query"),
        command=d.get("command"),
        result_sha=d.get("result_sha"),
        result_shingles=tuple(d.get("result_shingles") or ()),
        result_chars=int(d.get("result_chars") or 0),
        result_bytes=int(d.get("result_bytes") or 0),
        start_line=int(d.get("start_line") or 0),
        concurrency_group_line=int(
            d.get("concurrency_group_line") or d.get("start_line") or 0
        ),
        end_line=d.get("end_line"),
        start_ts=d.get("start_ts"),
        end_ts=d.get("end_ts"),
        turn_id=d.get("turn_id"),
        completed=bool(d.get("completed")),
        permission_denied=bool(d.get("permission_denied")),
        is_error=d.get("is_error"),
    )


def session_index_of(session_key: str) -> int:
    m = re.match(r"^(\d+)_", session_key or "")
    return int(m.group(1)) if m else 0


# --------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------


def _norm_query(q: Optional[str]) -> Optional[str]:
    if not q:
        return None
    return re.sub(r"\s+", " ", q.strip()).lower()


def overlap(producer: Acq, consumer: Acq) -> tuple[Optional[str], float, int]:
    """Return (kind, ratio, shared_shingles) for how much of `consumer`'s content
    the `producer` had already obtained. ratio is a fraction of the CONSUMER."""
    if producer.result_sha and producer.result_sha == consumer.result_sha:
        return IDENTICAL, 1.0, len(set(consumer.result_shingles))

    cs = set(consumer.result_shingles)
    ps = set(producer.result_shingles)
    if cs and ps:
        shared = cs & ps
        if shared:
            ratio = len(shared) / len(cs)
            if ratio >= SHINGLE_OVERLAP_THRESHOLD:
                kind = SHINGLE
                if (
                    consumer.is_search
                    and producer.is_search
                    and _norm_query(producer.query) == _norm_query(consumer.query)
                ):
                    kind = SAME_QUERY
                elif (
                    consumer.is_read
                    and producer.is_read
                    and producer.target_path
                    and producer.target_path == consumer.target_path
                ):
                    kind = SAME_PATH
                return kind, ratio, len(shared)
            return None, ratio, len(shared)
    return None, 0.0, 0


# --------------------------------------------------------------------------
# Priming
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _priming_targets(acq: Acq) -> list[tuple[str, str]]:
    """(tier, string) pairs that, if present in a delivered message, mean the
    agent was pointed at this file/location/finding before it went looking.

    Deliberately conservative. A bare filename stem is NOT a candidate: matching
    `report` inside the English phrase "report headings" would be a false
    positive. Only a full path, a basename WITH its extension, the exact search
    query, or an identifier-length token from the query counts. This may
    under-count priming, which is the safer direction for the conclusion.
    """
    out: list[tuple[str, str]] = []
    if acq.target_path:
        p = acq.target_path
        if "/" in p:
            out.append(("full_path", p))
        base = p.rsplit("/", 1)[-1]
        if base and "." in base:
            out.append(("basename", base))
    if acq.query:
        q = acq.query.strip()
        if 3 <= len(q) <= 200:
            out.append(("query_exact", q))
        for tok in _TOKEN_RE.findall(q)[:6]:
            if len(tok) >= 5:
                out.append(("query_token", tok))
    seen: list[tuple[str, str]] = []
    used: set[str] = set()
    for tier, t in out:
        t = t.strip()
        if len(t) >= 4 and t not in used:
            used.add(t)
            seen.append((tier, t))
    return seen


def classify_priming(
    consumer: Acq, messages: Sequence[Message]
) -> tuple[str, dict]:
    """PRIMED iff a message delivered to this agent BEFORE this acquisition began
    contained a sufficiently specific pointer to its target."""
    candidates = [
        m
        for m in messages
        if m.recipient == consumer.agent_id and m.delivery_key < consumer.start_key
    ]
    if not candidates:
        return UNPRIMED, {"reason": "no message delivered to this agent beforehand"}

    targets = _priming_targets(consumer)
    if not targets:
        return PRIMING_UNKNOWN, {
            "reason": "acquisition has no extractable target (e.g. opaque bash)",
            "messages_considered": len(candidates),
        }

    for m in candidates:
        low = m.text.lower()
        for tier, t in targets:
            if t.lower() in low:
                idx = low.find(t.lower())
                return PRIMED, {
                    "matched_token": t,
                    "match_tier": tier,
                    "message_label": m.label,
                    "message_sender": m.sender,
                    "match_offset": idx,
                    "excerpt": m.text[max(0, idx - 90) : idx + 90],
                }
    return UNPRIMED, {
        "reason": "no delivered message named this target",
        "targets_checked": [t for _tier, t in targets],
        "messages_considered": len(candidates),
    }


# --------------------------------------------------------------------------
# Reacquisition subcategories (PREREGISTRATION amendment 5)
# --------------------------------------------------------------------------
#
# The gross `primed_reacquisition` count is preserved unchanged. Underneath it,
# every PRIMED inter-agent reacquisition is placed in exactly one subcategory,
# decided mechanically from the consumer's own session, in this precedence:
#
#   verification_associated (post-edit)  - a Read of a file the same agent had
#       ALREADY successfully edited in this session: it is checking its change.
#   edit_precondition_associated         - the FIRST Read of a file in this
#       session, followed later in the same session by a successful Edit/Write of
#       that same file with no intervening edit, on a Claude Code build whose Edit
#       tool is verified to require a current-session Read. Not "wasted": the
#       tool demands it. On an unverified build it is `unknown` instead.
#   verification_associated (request)    - the acquisition's target is named in a
#       sentence of a delivered message that explicitly asks for verification,
#       and that sentence is NOT about test outcomes. If it is ("confirm every
#       test still passes"), it asks for a test run, and the read is `unknown`.
#   unknown                              - e.g. the only same-file edit failed or
#       was refused; the acquisition follows the agent's own test run (it may be
#       diagnosis, not rediscovery); no attributable target.
#   discretionary_information_reacquisition - primed, not required by the Edit
#       precondition, not attributable to verification. The strongest candidate
#       for avoidable rediscovery.
#
# `unknown` is always preferred to a speculative attribution.

REACQ_EDIT_PRECONDITION = "edit_precondition_associated"
REACQ_VERIFICATION = "verification_associated"
REACQ_DISCRETIONARY = "discretionary_information_reacquisition"
REACQ_UNKNOWN = "unknown"
REACQ_NOT_APPLICABLE = "not_applicable"

# Frozen before Pair 2 (PREREGISTRATION "Frozen definitions v1"). Any change to
# classify_reacquisition, its constants, or its precedence must bump this and be
# recorded as a dated amendment. tests/test_frozen_definitions.py enforces it.
REACQUISITION_CLASSIFIER_VERSION = 1
REACQ_SUBCATEGORIES = (
    REACQ_EDIT_PRECONDITION,
    REACQ_VERIFICATION,
    REACQ_DISCRETIONARY,
    REACQ_UNKNOWN,
)

# Claude Code builds on which the Edit/Write read-before-write rule has been
# verified, with the evidence. Any other build yields `unknown`, never
# `edit_precondition_associated`.
EDIT_REQUIRES_PRIOR_READ = {
    "2.1.260": (
        "the installed binary defines, beside the Edit tool: \"File has not been "
        "read yet. Read it first before writing to it.\" (telemetry key "
        "tengu_edit_tool_not_read_*), and a stale-read rule \"File has been "
        "modified since read, either by the user or by a linter. Read it again "
        "before attempting to write it.\""
    ),
}

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_VERIFICATION_RE = re.compile(
    r"\b(verify|verifies|verified|re-verify|reverify|confirm|confirms|"
    r"double-check|re-check|recheck|validate|make sure|check that|check whether|"
    r"check if)\b",
    re.I,
)
# A sentence ends at a newline, or at . ! ? followed by whitespace. A dot inside
# a file name ("palindrome.py") is therefore not a boundary.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?](?=\s)|\n")


def cli_version_key(version: Optional[str]) -> Optional[str]:
    m = _VERSION_RE.search(version or "")
    return m.group(1) if m else None


def _same_file(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    a = a.replace("\\", "/").casefold()
    b = b.replace("\\", "/").casefold()
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _sentences_with(text: str, token: str) -> list[str]:
    out = []
    low, tok = text.lower(), token.lower()
    start = 0
    while True:
        i = low.find(tok, start)
        if i < 0:
            break
        left = max((m.end() for m in _SENTENCE_BOUNDARY_RE.finditer(text, 0, i)), default=0)
        m = _SENTENCE_BOUNDARY_RE.search(text, i + len(tok))
        right = m.start() + 1 if m else len(text)
        out.append(text[left:right].strip())
        start = i + len(tok)
    return out


# A verification request whose sentence is about TEST OUTCOMES ("confirm every
# test still passes", "re-verify it still passes") asks the agent to run tests.
# It does not explain a Read of the named file, so such a match only yields
# `unknown`. Found by inspecting the first real Arm B run: both of its
# verification-verb matches were of this kind.
_TEST_OUTCOME_RE = re.compile(
    r"\b(pass|passes|passing|passed|fail|fails|failing|failed|pytest|test suite)\b",
    re.I,
)


def _explicit_verification_request(
    consumer: Acq, messages: Sequence[Message]
) -> Optional[dict]:
    """First sentence (in a message delivered before the acquisition) that names
    the target and contains a verification verb. A sentence that is not about
    test outcomes is preferred over one that is."""
    about_tests: Optional[dict] = None
    for m in messages:
        if m.recipient != consumer.agent_id or not (m.delivery_key < consumer.start_key):
            continue
        for _tier, tok in _priming_targets(consumer):
            for sentence in _sentences_with(m.text, tok):
                v = _VERIFICATION_RE.search(sentence)
                if not v:
                    continue
                found = {
                    "message_label": m.label,
                    "matched_token": tok,
                    "verb": v.group(0),
                    "sentence": sentence[:300],
                    "about_test_outcome": bool(_TEST_OUTCOME_RE.search(sentence)),
                }
                if not found["about_test_outcome"]:
                    return found
                about_tests = about_tests or found
    return about_tests


def classify_reacquisition(
    consumer: Acq,
    all_acquisitions: Sequence[Acq],
    messages: Sequence[Message],
    *,
    cli_version: Optional[str],
) -> tuple[str, dict]:
    """Subcategory for ONE primed inter-agent reacquisition. Rules above."""
    ver = cli_version_key(cli_version)
    same_session = [
        a
        for a in all_acquisitions
        if a.session_key == consumer.session_key and a.tool_use_id != consumer.tool_use_id
    ]

    if consumer.acquisition_class == tools.STRUCTURED_READ and consumer.target_path:
        same_file_edits = [
            a for a in same_session if a.is_edit and _same_file(a.target_path, consumer.target_path)
        ]
        edited_before = [a for a in same_file_edits if a.executed and a.end_key < consumer.start_key]
        edited_after = sorted(
            (a for a in same_file_edits if a.executed and a.start_key > consumer.end_key),
            key=lambda a: a.start_key,
        )
        failed_after = [
            a for a in same_file_edits if not a.executed and a.start_key > consumer.end_key
        ]
        earlier_reads = [
            a
            for a in same_session
            if a.acquisition_class == tools.STRUCTURED_READ
            and a.executed
            and _same_file(a.target_path, consumer.target_path)
            and a.end_key < consumer.start_key
        ]

        if edited_before:
            return REACQ_VERIFICATION, {
                "rule": "post_edit_reread",
                "edit_tool_use_id": edited_before[-1].tool_use_id,
                "note": "re-read of a file this agent had already edited in this session",
            }
        if edited_after and not earlier_reads:
            first = edited_after[0]
            base = {
                "edit_tool_use_id": first.tool_use_id,
                "edit_tool": first.tool_name,
                "read_end_line": consumer.end_line,
                "edit_start_line": first.start_line,
                "cli_version": ver,
            }
            if ver not in EDIT_REQUIRES_PRIOR_READ:
                return REACQ_UNKNOWN, {
                    "rule": "edit_precondition_unverified_for_this_cli_version",
                    **base,
                }
            return REACQ_EDIT_PRECONDITION, {
                "rule": "first_read_before_first_successful_edit_of_same_file",
                "tool_semantics": EDIT_REQUIRES_PRIOR_READ[ver],
                **base,
            }
        if failed_after and not edited_after:
            return REACQ_UNKNOWN, {
                "rule": "same_file_edit_attempted_but_did_not_execute",
                "edit_tool_use_ids": [a.tool_use_id for a in failed_after],
            }
        # An earlier Read already satisfied the precondition: this one was not
        # required by the tool. Fall through.

    request = _explicit_verification_request(consumer, messages)
    if request and not request["about_test_outcome"]:
        return REACQ_VERIFICATION, {"rule": "explicit_verification_request", **request}
    if request:
        return REACQ_UNKNOWN, {
            "rule": "verification_request_about_test_outcome",
            "note": (
                "the target is named in a sentence asking to confirm test outcomes; "
                "that asks for a test run, and does not explain a Read of the file"
            ),
            **request,
        }

    prior_tests = [
        a
        for a in same_session
        if a.acquisition_class == tools.BASH_VERIFICATION
        and a.executed
        and a.end_key < consumer.start_key
    ]
    if prior_tests:
        return REACQ_UNKNOWN, {
            "rule": "follows_own_verification_result",
            "verification_tool_use_id": prior_tests[-1].tool_use_id,
            "note": "may be diagnosis of a test result rather than rediscovery",
        }

    if not (consumer.target_path or consumer.query):
        return REACQ_UNKNOWN, {"rule": "no_attributable_target"}

    return REACQ_DISCRETIONARY, {
        "rule": "primed_not_edit_required_not_verification",
        "consumer_class": consumer.acquisition_class,
    }


def _physical(findings: Sequence["Finding"]) -> dict:
    return {
        "tool_calls": len(findings),
        "chars": sum(f.duplicate_chars for f in findings),
        "bytes": sum(f.duplicate_bytes for f in findings),
        "content_chunks": sum(f.overlap_shingles for f in findings),
        "targets": sorted({f.consumer_target or "" for f in findings}),
    }


# --------------------------------------------------------------------------
# Main analysis
# --------------------------------------------------------------------------


@dataclass
class DuplicationReport:
    total_tool_calls: int = 0
    total_acquisitions: int = 0
    completed_acquisitions: int = 0
    reads: int = 0
    searches: int = 0
    unique_result_shas: int = 0
    unique_paths_read: int = 0
    unique_repository_regions: int = 0
    total_acquired_chars: int = 0
    total_acquired_bytes: int = 0

    findings: list[dict] = field(default_factory=list)
    inter_agent_duplicate_acquisitions: int = 0
    intra_agent_repeat_acquisitions: int = 0
    globally_previously_available: int = 0
    potentially_avoidable_acquisitions: int = 0
    primed_reacquisitions: int = 0
    unprimed_overlapping_discoveries: int = 0
    priming_undetermined: int = 0

    repeated_searches_same_query_inter_agent: int = 0
    repeated_search_results_inter_agent: int = 0

    duplicate_chars_inter_agent: int = 0
    duplicate_bytes_inter_agent: int = 0
    duplicate_shingles_inter_agent: int = 0

    oracle_upper_bound: dict = field(default_factory=dict)
    concurrency_excluded_pairs: int = 0
    timing_basis: str = "order_key=(session_index,line_no)"

    # Subcategories of the gross primed_reacquisitions count (amendment 5).
    edit_precondition_associated: int = 0
    verification_associated: int = 0
    discretionary_information_reacquisition: int = 0
    reacquisition_unknown: int = 0
    subcategory_physical: dict = field(default_factory=dict)
    discretionary_upper_bound: dict = field(default_factory=dict)
    edit_precondition_cli_version: Optional[str] = None
    edit_precondition_verified: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def analyze(
    acquisitions: Sequence[Acq],
    messages: Sequence[Message],
    *,
    threshold: float = SHINGLE_OVERLAP_THRESHOLD,
    cli_version: Optional[str] = None,
) -> DuplicationReport:
    """`acquisitions` should be ALL tool calls of the run (edits and test runs
    included): the reacquisition subcategories need them."""
    rep = DuplicationReport()
    rep.total_tool_calls = len(acquisitions)

    acqs = [a for a in acquisitions if a.is_acquisition]
    rep.total_acquisitions = len(acqs)
    done = [a for a in acqs if a.completed]
    rep.completed_acquisitions = len(done)
    rep.reads = sum(1 for a in acqs if a.is_read)
    rep.searches = sum(1 for a in acqs if a.is_search)
    rep.total_acquired_chars = sum(a.result_chars for a in done)
    rep.total_acquired_bytes = sum(a.result_bytes for a in done)
    rep.unique_result_shas = len({a.result_sha for a in done if a.result_sha})
    rep.unique_paths_read = len({a.target_path for a in done if a.is_read and a.target_path})
    all_shingles = {s for a in done for s in a.result_shingles}
    rep.unique_repository_regions = len(all_shingles)

    ordered = sorted(done, key=lambda a: (a.start_key, a.end_key, a.tool_use_id))

    concurrency_excluded = 0
    findings: list[Finding] = []

    for i, consumer in enumerate(ordered):
        best: Optional[tuple[float, str, int, Acq]] = None
        for producer in ordered[:i] + ordered[i + 1 :]:
            if producer.tool_use_id == consumer.tool_use_id:
                continue
            kind, ratio, shared = overlap(producer, consumer)
            if kind is None:
                continue
            # --- K(t): the producing acquisition must have COMPLETED before the
            # consuming acquisition STARTED.
            if not (producer.end_key < consumer.start_key):
                # Count only the genuine concurrency case - the candidate had
                # begun no later than this one but had not finished. A candidate
                # that simply started later is not "excluded by concurrency", it
                # is just downstream.
                if producer.start_key <= consumer.start_key:
                    concurrency_excluded += 1
                continue
            if best is None or ratio > best[0]:
                best = (ratio, kind, shared, producer)

        if best is None:
            continue

        ratio, kind, shared, producer = best

        def _label(a: Acq) -> Optional[str]:
            # For a search the pattern is the informative target, not the scope.
            if a.is_search:
                return a.query or a.target_path
            return a.target_path or a.query

        relation = INTER_AGENT if producer.agent_id != consumer.agent_id else INTRA_AGENT
        priming, evidence = classify_priming(consumer, messages)

        subcategory, sub_evidence = REACQ_NOT_APPLICABLE, {}
        if relation == INTER_AGENT and priming == PRIMED:
            subcategory, sub_evidence = classify_reacquisition(
                consumer, acquisitions, messages, cli_version=cli_version
            )

        dup_chars = int(round(consumer.result_chars * ratio))
        dup_bytes = int(round(consumer.result_bytes * ratio))

        findings.append(
            Finding(
                consumer_tool_use_id=consumer.tool_use_id,
                consumer_agent=consumer.agent_id,
                consumer_session_key=consumer.session_key,
                consumer_class=consumer.acquisition_class,
                consumer_target=_label(consumer),
                producer_tool_use_id=producer.tool_use_id,
                producer_agent=producer.agent_id,
                producer_session_key=producer.session_key,
                producer_target=_label(producer),
                overlap_kind=kind,
                overlap_ratio=round(ratio, 4),
                overlap_shingles=shared,
                relation=relation,
                globally_previously_available=True,
                potentially_avoidable_acquisition=(relation == INTER_AGENT),
                priming=priming,
                priming_evidence=evidence,
                duplicate_chars=dup_chars,
                duplicate_bytes=dup_bytes,
                timing_basis=rep.timing_basis,
                reacquisition_subcategory=subcategory,
                subcategory_evidence=sub_evidence,
            )
        )

    rep.concurrency_excluded_pairs = concurrency_excluded
    rep.findings = [f.as_dict() for f in findings]
    rep.globally_previously_available = len(findings)
    inter = [f for f in findings if f.relation == INTER_AGENT]
    intra = [f for f in findings if f.relation == INTRA_AGENT]
    rep.inter_agent_duplicate_acquisitions = len(inter)
    rep.intra_agent_repeat_acquisitions = len(intra)
    rep.potentially_avoidable_acquisitions = len(inter)
    rep.primed_reacquisitions = sum(1 for f in inter if f.priming == PRIMED)
    rep.unprimed_overlapping_discoveries = sum(1 for f in inter if f.priming == UNPRIMED)
    rep.priming_undetermined = sum(1 for f in inter if f.priming == PRIMING_UNKNOWN)

    rep.duplicate_chars_inter_agent = sum(f.duplicate_chars for f in inter)
    rep.duplicate_bytes_inter_agent = sum(f.duplicate_bytes for f in inter)
    rep.duplicate_shingles_inter_agent = sum(f.overlap_shingles for f in inter)

    rep.repeated_searches_same_query_inter_agent = sum(
        1 for f in inter if f.overlap_kind == SAME_QUERY
    )
    rep.repeated_search_results_inter_agent = sum(
        1
        for f in inter
        if f.overlap_kind in (IDENTICAL, SAME_QUERY)
        and f.consumer_class in (tools.STRUCTURED_SEARCH, tools.CLASSIFIED_BASH_SEARCH)
    )

    rep.oracle_upper_bound = oracle_upper_bound(findings)

    primed_inter = [f for f in inter if f.priming == PRIMED]
    by_sub = {s: [f for f in primed_inter if f.reacquisition_subcategory == s]
              for s in REACQ_SUBCATEGORIES}
    rep.edit_precondition_associated = len(by_sub[REACQ_EDIT_PRECONDITION])
    rep.verification_associated = len(by_sub[REACQ_VERIFICATION])
    rep.discretionary_information_reacquisition = len(by_sub[REACQ_DISCRETIONARY])
    rep.reacquisition_unknown = len(by_sub[REACQ_UNKNOWN])
    assert sum(len(v) for v in by_sub.values()) == rep.primed_reacquisitions, (
        "every primed reacquisition must land in exactly one subcategory"
    )
    rep.subcategory_physical = {s: _physical(v) for s, v in by_sub.items()}
    rep.discretionary_upper_bound = {
        "unit": "physical (acquisitions / content chunks / bytes), not dollars",
        **_physical(by_sub[REACQ_DISCRETIONARY]),
        "note": (
            "Discretionary primed reacquisition only: not demanded by the Edit "
            "precondition and not attributable to verification. The strongest "
            "candidate for avoidable rediscovery; still an upper bound."
        ),
    }
    rep.edit_precondition_cli_version = cli_version_key(cli_version)
    rep.edit_precondition_verified = rep.edit_precondition_cli_version in EDIT_REQUIRES_PRIOR_READ
    return rep


def oracle_upper_bound(findings: Sequence[Finding]) -> dict:
    """Maximum mechanically-removable coordination redundancy, in physical units.

    Counts only PRIMED inter-agent repetition: the consumer had already been told
    something specific about the target before it went looking, and the
    information had genuinely completed acquisition beforehand. Unprimed
    independent discovery is excluded, because independent verification may be
    useful rather than redundant.

    Deliberately NOT expressed in dollars: subscription telemetry cannot support
    that, and Stage 0 does not require it.
    """
    primed_inter = [
        f for f in findings if f.relation == INTER_AGENT and f.priming == PRIMED
    ]
    reads = [
        f
        for f in primed_inter
        if f.consumer_class in (tools.STRUCTURED_READ, tools.CLASSIFIED_BASH_READ)
    ]
    searches = [
        f
        for f in primed_inter
        if f.consumer_class in (tools.STRUCTURED_SEARCH, tools.CLASSIFIED_BASH_SEARCH)
    ]
    return {
        "unit": "physical (acquisitions / content chunks / bytes), not dollars",
        "primed_repeated_file_acquisitions": len(reads),
        "primed_repeated_search_acquisitions": len(searches),
        "primed_repeated_acquisitions_total": len(primed_inter),
        "duplicate_tool_calls": len(primed_inter),
        "duplicate_content_chunks_shingles": sum(f.overlap_shingles for f in primed_inter),
        "duplicate_chars": sum(f.duplicate_chars for f in primed_inter),
        "duplicate_bytes": sum(f.duplicate_bytes for f in primed_inter),
        "duplicate_turns_touched": len({(f.consumer_session_key, f.consumer_tool_use_id) for f in primed_inter}),
        "note": (
            "Upper bound: assumes every primed inter-agent repetition could have "
            "been replaced by a reference. It is not a claim that all of it should be."
        ),
    }


# --------------------------------------------------------------------------
# Arm comparison
# --------------------------------------------------------------------------


def compare_arms(arm_a: dict, arm_b: dict) -> dict:
    """Arm B overhead over Arm A in physical units. `None` where undefined."""

    def ratio(b, a):
        if a in (None, 0) or b is None:
            return None
        return round(b / a, 4)

    def delta(b, a):
        if a is None or b is None:
            return None
        return b - a

    keys = (
        "total_tool_calls",
        "total_acquisitions",
        "reads",
        "searches",
        "total_acquired_chars",
        "total_acquired_bytes",
        "unique_repository_regions",
    )
    out = {
        k: {
            "arm_a": arm_a.get(k),
            "arm_b": arm_b.get(k),
            "delta": delta(arm_b.get(k), arm_a.get(k)),
            "ratio_b_over_a": ratio(arm_b.get(k), arm_a.get(k)),
        }
        for k in keys
    }
    out["duplication_only_meaningful_in_multi_agent"] = {
        "arm_a_intra_agent_repeats": arm_a.get("intra_agent_repeat_acquisitions"),
        "arm_b_inter_agent_duplicates": arm_b.get("inter_agent_duplicate_acquisitions"),
        "arm_b_primed_reacquisitions": arm_b.get("primed_reacquisitions"),
        "arm_b_unprimed_overlapping_discoveries": arm_b.get(
            "unprimed_overlapping_discoveries"
        ),
    }
    return out
