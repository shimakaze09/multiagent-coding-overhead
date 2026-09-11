# Stage 0 — Specification

Measurement-only stage. No protocol design, no structured-state transfer, no Arm C.

## 1. Research question

> In multi-agent coding, how much additional context/work comes from agents
> re-acquiring, re-reading, carrying, searching for, or rediscovering
> information that another agent already obtained?

Stage 0 exists **only to measure existing behavior** of two arms. It does not
propose, implement, or evaluate a remedy.

The operational form of the question:

> How much extra information acquisition in the natural-language multi-agent
> system was information another agent had already acquired **before** the later
> agent went looking for it?

## 2. Subscription-only constraint

This experiment runs on a Claude Pro/Max subscription through the locally
installed Claude Code CLI. It must not use the Anthropic Messages API.

Hard rules enforced in code (`config.py:preflight_billing_guard`, `runner.py`):

1. If `ANTHROPIC_API_KEY` is present in the environment, the harness terminates
   before any Claude workload. Presence only is tested; the value is never read,
   printed, or logged.
2. Same treatment for `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY_HELPER`,
   `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`.
3. The harness never creates an API key, never enables usage credits or PAYG,
   and never unsets or replaces credentials.
4. The `anthropic` Python SDK is not a dependency and is not used for model
   execution.
5. `--bare` is never passed. In Claude Code 2.1.260 `--bare` forces
   `ANTHROPIC_API_KEY`/`apiKeyHelper` auth and never reads OAuth — i.e. it is an
   API-billing path. It is on the forbidden-flag list.
6. Every run asserts, from the CLI's own `system/init` event, that
   `apiKeySource` is not an API-key source. A run whose `apiKeySource` is
   `ANTHROPIC_API_KEY`, `apiKeyHelper`, or similar is marked
   `billing_mode = "api_suspected"` and fails the run gate.

### Execution model

```text
Python experiment harness
        |
        v
local `claude` executable  (claude -p --output-format stream-json --verbose)
        |
        v
authenticated Claude Code subscription
```

Explicitly **not**:

```text
Python -> Anthropic Messages API -> API billing
```

### Environment sanitation

The harness may itself be launched from inside a Claude Code session. The parent
session exports `CLAUDE_CODE_*` variables (session id, messaging socket,
messaging token, host-auth-refresh flags, effort). Those are stripped from the
child environment (`harness/claude_cli.py:build_child_env`) so that:

* experiment sessions do not join or inherit the host session,
* the host session's effort/model settings do not silently alter an arm,
* the recorded invocation is reproducible from a clean shell.

The exact stripped and retained variable names are recorded per run in
`metadata.json` so the environment is part of the reconstruction gate.

## 3. Claude Code execution model as actually observed

Verified against the installed build, not assumed:

```text
claude_code_version = 2.1.260
```

Available and used:

| Flag | Status |
| --- | --- |
| `-p` / `--print` | available |
| `--output-format json` | available |
| `--output-format stream-json` | available |
| `--input-format stream-json` | available |
| `--verbose` | available |
| `--model` | available |
| `--allowedTools` / `--allowed-tools` | available |
| `--disallowedTools` / `--disallowed-tools` | available |
| `--resume` | available |
| `--tools` | available (restrict to a subset of the built-in set) |
| `--session-id <uuid>` | available (harness assigns session ids) |
| `--include-hook-events` | available (stream-json only) |
| `--permission-mode` | available |
| `--permission-prompts none` | available |
| `--no-session-persistence` | available |
| `--strict-mcp-config` | available |
| `--setting-sources` | available |
| `--add-dir` | available |
| `--append-system-prompt` | available |

**Not available — a false assumption in the original specification:**

| Flag | Status |
| --- | --- |
| `--max-turns` | **does not exist in 2.1.260** |

Turn limiting is therefore implemented harness-side: the streaming parser counts
assistant turns and the harness terminates the child process when
`max_turns_per_session` is exceeded. This is recorded as
`turn_limit_enforcement = "harness_side"` and the terminating event is logged as
`TURN_LIMIT_EXCEEDED`. A harness-side limit is not identical to a provider-side
limit: the child may already have an in-flight request when the limit trips.

`--max-budget-usd` exists but is documented as governing API-call spend and is
not used, because this is subscription execution and its semantics under a
subscription are unverified.

### Stream event types observed in a real authenticated run

The first real Arm A run (`20260910T122445Z_palindrome_punctuation_A_r1`) emitted
event types that the mock-derived parser had never seen. All are now parsed and
none appears as unknown:

| Event | Meaning | Treated as |
| --- | --- | --- |
| `rate_limit_event` | subscription-quota telemetry: `rate_limit_info` with `status`, `rateLimitType`, `utilization`, `resetsAt`, `isUsingOverage`, `surpassedThreshold`, `unifiedWindows.{five_hour,seven_day}` | **not** an error; only a status in `RateLimitSnapshot.BLOCKED_STATUSES` indicates execution failure |
| `system/permission_denied` | the CLI refused a tool call: `tool_name`, `tool_use_id`, `decision_reason_type`, `decision_reason`, `message` | the action was NOT performed |
| `system/thinking_tokens` | incremental `estimated_tokens` / `estimated_tokens_delta` | an estimate by the provider's own field name; never promoted to a reported token count |

An unrecognized `system` subtype is recorded in `unrecognized_system_subtypes`
and counted as `system/<subtype>` in the unknown-type report, rather than being
silently swallowed by the `system` branch.

### `--allowedTools` behaviour, established empirically

| Finding | Evidence |
| --- | --- |
| Two rule spellings exist | binary validator: `"Bash(npm run:*) - prefix matching (legacy)"` vs `"Bash(npm run *) - wildcard matching"` |
| `:*` must be last, and needs a non-empty prefix | binary validator error strings |
| Rules are NOT validated at startup | two malformed rules were accepted silently and the session ran normally; a bad rule fails at match time |
| Rules must be passed as separate argv elements | the flag is variadic (`<tools...>`) and rules contain spaces |
| Compound commands are decomposed per sub-command | a denial named the offending part: *"This Bash command contains multiple operations. The following part requires approval: python -c ..."* |
| Read-only Bash needs no rule | `cd "<ws>" && find . -type f \| sort` executed with no allowlist at all |

## 4. Raw telemetry model

Raw artifacts are the source of truth and are never rewritten.

```text
runs/<run_id>/
    metadata.json                 harness inputs, capability report, env manifest
    events.jsonl                  normalized experiment event log (append-only)
    sessions/<session_key>/
        invocation.json           argv, cwd, env manifest, stdin bytes, limits
        claude_stdout.jsonl       raw CLI stdout, byte-for-byte, unparsed
        claude_stderr.txt         raw CLI stderr
        result.json               final `result` event, verbatim
        exit.json                 exit code, timing, termination reason
    handoffs/<n>_<from>_to_<to>.txt   exact natural-language handoff bodies
    verify/                       verifier stdout/stderr/exit per attempt
    workspace_diff.patch          final diff vs base commit
```

Two independent raw sources exist and neither may overwrite the other:

1. raw Claude Code CLI output (`claude_stdout.jsonl`, `claude_stderr.txt`)
2. the normalized experiment event log (`events.jsonl`)

Every normalized event derived from a CLI event carries
`raw_ref = {session_key, line_no}` pointing back into the raw stream.

SQLite (`analysis/`) is a **derived analytical index only**. It is rebuildable
from raw artifacts and is never a source of truth.

## 5. Normalized events

Every event:

```json
{
  "event_id": "...", "seq": 1487, "ts": "...",
  "run_id": "...", "task_id": "...", "arm": "A",
  "agent_id": "solo", "turn_id": 12,
  "type": "...", "parent_event_id": "...",
  "source": "claude_code", "payload": {},
  "raw_ref": {"session_key": "...", "line_no": 42}
}
```

* `seq` is strictly monotonic per run, allocated under a lock, append-only.
* `seq` is authoritative for ordering **only where our harness knows the
  order**. It does not establish causal order between events that were already
  concurrent inside Claude Code. Concurrency-derived orderings are marked
  `order_confidence: "cli_reported"` rather than `"harness_observed"`.
* Timestamps: `ts` is the harness's UTC clock. Where the CLI supplied its own
  timestamp it is preserved in `payload.cli_timestamp`.

Event types: `TASK_START`, `TASK_END`, `CLAUDE_SESSION_START`,
`CLAUDE_SESSION_END`, `AGENT_TURN_START`, `AGENT_TURN_END`, `TOOL_CALL_START`,
`TOOL_CALL_END`, `FILE_READ_START`, `FILE_READ_END`, `SEARCH_START`,
`SEARCH_END`, `EDIT`, `TEST_RUN`, `AGENT_MESSAGE`, `ERROR`, `USAGE_REPORT`,
`RAW_CLAUDE_EVENT`, `UNKNOWN_CLAUDE_EVENT`, `TURN_LIMIT_EXCEEDED`,
`HANDOFF_SENT`, `WORKSPACE_PREPARED`, `VERIFICATION`, `USAGE_LIMIT_REPORTED`,
`PERMISSION_DENIED`, `RATE_LIMIT_REPORTED`.

### Turn accounting — three distinct quantities

Claude Code emits a **separate assistant stream event for a standalone
`thinking` block**, followed by another assistant event for the `tool_use` or
`text` that completes the same API-level message. Both carry the *same*
`message.usage`. So a naive count of assistant events is not Claude's own turn
count, and per-message usage must not be summed.

More precisely: Claude Code emits **one stream event per content block**. A
single API assistant message carrying a `thinking` block plus three `tool_use`
blocks appears as four stream events on consecutive lines, all sharing the same
`message.id` and the same `message.usage`. Two consequences:

1. Per-message usage must never be summed - it would multiply-count one message.
   `aggregate_usage` therefore prefers the final `result.usage`.
2. Tool calls sharing a `message.id` were **issued together** and are genuinely
   concurrent. See "Concurrency grouping" below.

Four distinct quantities are recorded, named apart everywhere (parser, report,
SQLite) and accompanied by a `turn_accounting_note`:

| Quantity | Two real Arm A runs |
| --- | --- |
| `assistant_stream_events` (one per content block) | 14 / 11 |
| `thinking_only_stream_events` | 3 / 2 |
| `api_assistant_messages` (distinct `message.id`) | 7 / 6 |
| `tool_use_blocks` | 10 / 7 |
| `cli_reported_num_turns` (Claude Code's own) | 11 / 8 |

An earlier version of this document claimed
`assistant_stream_events - thinking_only_stream_events == cli_reported_num_turns`.
**That was wrong** - it fitted the first run (14-3=11) by coincidence and fails
on the second (11-2=9 vs 8). The relationship that holds on every real run
observed so far is:

```text
cli_reported_num_turns == tool_use_blocks + 1
```

11 = 10+1, 8 = 7+1, and 1 = 0+1 on the auth-failure probe. That is an empirical
fit on three runs, **not** a specification, and nothing in the analysis depends
on it. `num_turns` is reported as Claude Code's own counter and used only as
such.

### Concurrency grouping

Because parallel tool calls occupy consecutive stream lines, ordering
acquisitions by raw line would make simultaneous calls look sequential and could
attribute one to another under K(t). Each tool call therefore carries
`api_message_id` and `concurrency_group_line` (the first stream line of its
containing message), and `metrics.Acq.start_key` uses the group line, not the raw
line. Observed in real runs: one assistant message issued 5 parallel `Read`s in
the first run and 3 in the second. `start_line` is still recorded for raw
fidelity.

Unknown CLI event types are never dropped: they are emitted as
`UNKNOWN_CLAUDE_EVENT` and counted in the run's observability report. The parser
carries its own version (`harness/telemetry.py:PARSER_VERSION`) independent of
the Claude Code version.

## 6. Token, cache and cost telemetry — as actually observed

The original specification assumed these fields might be unavailable in
subscription mode. **They are available.** Observed on the installed build in
the `assistant` message `usage` object and in the final `result` event:

```text
usage.input_tokens
usage.output_tokens
usage.cache_creation_input_tokens
usage.cache_read_input_tokens
usage.cache_creation.ephemeral_1h_input_tokens
usage.cache_creation.ephemeral_5m_input_tokens
usage.output_tokens_details.thinking_tokens
usage.service_tier
modelUsage            (per-model breakdown)
total_cost_usd
num_turns
duration_ms, duration_api_ms
permission_denials
subagent_stats        (internal subagent fan-out counts)
```

### Token field semantics — confirmed against a real run

The first real Arm A run reported:

```text
input_tokens                =      14
cache_read_input_tokens     = 126,874
cache_creation_input_tokens =  10,369
output_tokens               =   2,223
```

Therefore, and stated explicitly because it is easy to misread:

* **`input_tokens` is UNCACHED, newly-submitted input only. It is NOT the
  prompt/context length.** Here it was 14 while the submitted context was about
  137k tokens.
* `cache_read_input_tokens` is input served from the prompt cache.
* `cache_creation_input_tokens` is input written into the prompt cache.
* **`total_input_tokens` = `input_tokens` + `cache_read_input_tokens` +
  `cache_creation_input_tokens`** (137,257 here) is the only field that may be
  described as submitted context volume. It is computed by the harness, labelled,
  and reported alongside the provider-reported components.
* `total_tokens` mixes input and output and is not a context measure.

Provider-reported values are never altered. The machine-readable definitions live
in `telemetry.TOKEN_SEMANTICS` and are embedded in every run's usage block.

### Scope: `result.usage` covers only the main model

The same run's `modelUsage` showed **two** models — `claude-sonnet-5` (the
session model) and `claude-haiku-4-5` (1,104 input / 18 output tokens, invoked by
Claude Code itself). `result.usage` reported only the sonnet figures, while
`total_cost_usd` (0.0903028) covered both, exactly matching the sum of the
per-model `costUSD`. Token accounting that uses `result.usage` alone therefore
undercounts. `model_usage_totals` records per-model figures and an all-models
total, and the report prints a note whenever more than one model was used.

Every normalized numeric field is stored as a three-part measurement:

```json
{"value": 1234, "source": "claude_code_stream", "availability": "reported"}
```

or

```json
{"value": null, "source": null, "availability": "not_exposed"}
```

`availability` is one of `reported`, `not_exposed`, `estimated`,
`unreliable`.

### Cost

`total_cost_usd` is preserved exactly as CLI telemetry. It is **not** treated as
what the user paid. Runs record:

```json
{"subscription_execution": true, "api_charge": "false_expected",
 "cli_reported_cost_usd": {"value": 0.0, "source": "claude_code_stream",
                           "availability": "reported"},
 "cli_cost_interpretation": "api_equivalent_not_amount_paid"}
```

Primary resource metrics are sessions, turns, tool calls, wall time, acquisition
volume, duplication, and success — not dollars.

### Estimated tokens

Local approximate tokenization is used **only** for relative comparison of
natural-language handoff bodies, and only under the field name
`estimated_tokens`, never `tokens`. It is a whitespace/character heuristic
(no `tiktoken`, which is the wrong tokenizer for Claude and would misrepresent
provider counts). Handoffs additionally record exact `chars`, `utf8_bytes`,
`words`.

## 7. Tool and acquisition observation

Primary source: `stream-json --verbose`, which emits `tool_use` blocks in
`assistant` messages and `tool_result` blocks in the following `user` message.
This yields, per tool call: `tool_use_id`, tool name, full input, full result
content, a start boundary (assistant event) and an end boundary (result event).

Stream telemetry alone is sufficient, so the hook mechanism is **not used**:

* `--include-hook-events` exists on this build and is exposed as
  `RunConfig.include_hook_events`, defaulting to `False`.
* Hook events are only emitted for hooks that are actually configured. Stage 0
  configures none, passes `--setting-sources project`, and each run's isolated
  workspace contains no `.claude` directory, so no hooks fire and the flag
  currently yields nothing. This is deliberate: it keeps the experiment from
  touching the user's Claude configuration at all.
* No global, project or local user Claude settings are read or modified. Nothing
  interferes with the user's normal Claude sessions.
* If hook enrichment is ever needed, it must be configured project-locally
  inside the per-run workspace, torn down with the workspace, and its raw
  payloads preserved. That is not implemented, because it is not needed:
  `tool_use` and `tool_result` are fully exposed in the stream.

### Acquisition classes

| Class | Source | Acquisition? | Confidence |
| --- | --- | --- | --- |
| `structured_read` | `Read` | yes, classified | high |
| `structured_search` | `Grep`, `Glob` | yes, classified | high |
| `structured_edit` | `Edit`, `Write`, `NotebookEdit` | no | high |
| `bash_read` | `cat`, `head`, `tail`, `sed -n <script> <file>`, `type`, `Get-Content` **with a file operand** | yes, classified | medium |
| `bash_search` | `grep`, `rg`, `awk`, `findstr` | yes, classified | medium |
| `bash_directory_listing` | `find`, `tree`, `fd`, `ls -R` | yes, classified | medium |
| `bash_git_inspection` | `git show/diff/blame/grep/log/ls-files/status` | yes, classified | medium |
| `bash_verification` | test/lint/typecheck runs, and `python -c` / `node -e` inline scripts | no | medium |
| `bash_build` | `make`, `gcc`, `cargo build`, `tsc` | no | medium |
| `bash_workspace_management` | `mkdir`, `cp`, `mv`, `rm`, `sed -i`, `git commit/checkout` | no | medium |
| `bash_non_acquisition` | `pwd`, `cd`, `echo`, `stat`, plain `ls` | no | medium |
| `bash_unknown` | any `Bash` command not confidently recognized | yes, UNCLASSIFIED | none |
| `unknown_tool` | tool name not in the known set | yes, UNCLASSIFIED | none |
| `tool_denied` | the CLI refused the call | no - nothing ran | high |

The normalized category is **derived data only**. The raw command, raw stdout,
raw stderr and exit status are always preserved unchanged in
`claude_stdout.jsonl`, and the DB additionally stores the verbatim command and
tool-result body next to the derived category.

Two rules matter more than the tables:

* **Neutral stream filters.** `sort`, `wc`, `uniq`, `cut`, `tr`, `jq`, `xargs`
  and bare `head`/`cat`/`tail`/`sed` with no file operand consume stdin and
  acquire nothing. They are recorded in the per-segment audit trail as
  `_neutral_filter` but never decide the overall category, so
  `cd <ws> && find . | sort` is a directory listing, not an unknown command.
  A real run was mis-binned for exactly this reason.
* **Quote-aware segmentation.** Segments are split on `&&`, `||`, `|`, `;`, `&`
  and newlines **only outside quotes**, so `python -c "import x; print(x)"` is
  one command rather than a script body shredded into bogus unknown segments.
  Not a shell parser; when the family is unrecognized the answer is
  `bash_unknown`.

`bash_unknown` is reported as an explicit share. Acquisition coverage is **not**
claimed to be complete when substantial repository reading happens through
arbitrary shell commands. `bash_unknown` is always preferred to false precision.

### Information identity

Claude Code does not expose byte or line ranges for reads in a form the harness
can rely on, so ranges are **not** fabricated. The finest reliable granularity
is used instead:

* `content_sha` = BLAKE2b of the exact `tool_result` text returned to the agent.
  This is the information the agent actually received.
* `shingles` = BLAKE2b over **3-line** sliding windows of the returned text.
  3-line windows are used rather than per-line hashes to avoid false overlap
  from trivial lines (`}`, blank lines, `import os`).
* `path` = the path from the tool input where present.
* `path_version_hint` = git blob SHA of that path at the run's base commit, when
  the path resolves inside the workspace and is unmodified. Once a file has been
  edited, reads before and after the edit have different `content_sha` and are
  therefore **not** treated as the same information. This satisfies the
  `Renderer.cpp@version_A != Renderer.cpp@version_B` requirement at
  content-identity granularity.

Recorded limitation: identity is at returned-content granularity, not byte-range
granularity.

### Tool capability parity

Arm A and Arm B workers draw from the same built-in tool set. The experiment
constrains the set identically across arms via `--tools`, differing only by role
policy (Investigator has no write tools; Coordinator has no repository tools).
No arm receives custom high-precision instrumentation tools that the other lacks.

The `Agent`/`Task` tool is disallowed in **both** arms, so that Arm A is a
genuine single agent and Arm B's decomposition is exactly the three logical
agents under study. `subagent_stats.spawned` is recorded per session and a
non-zero value in either arm invalidates that run's arm label.

## 8. Arms

### Arm A — single agent

One Claude Code session. Receives the original task statement verbatim, the same
repository at the same base commit, the same success criterion, the same tool
capability. No instruction to behave unusually.

### Arm B — natural-language multi-agent

Exactly three logical agents, fixed topology, separate Claude Code sessions:

```text
User task -> Coordinator -> Investigator -> Coordinator -> Implementer -> Coordinator -> Final
```

* **Coordinator** — delegation and handoff only; no repository read or write
  tools in the baseline. Inputs: original task, worker reports.
* **Investigator** — inspect only (`Read`, `Grep`, `Glob`, read-only `Bash`).
  Inputs: original task verbatim + coordinator instruction. Output: a normal
  natural-language investigation report, stored verbatim as `AGENT_MESSAGE`.
* **Implementer** — inspect, edit, run tests. Inputs: original task verbatim +
  coordinator instruction + investigator report. It is **permitted and expected**
  to inspect the repository again; it is never told "do not reread anything",
  because repeated acquisition is the dependent variable.

Handoff bodies are stored exactly.

## 9. Duplicate-acquisition measurement

### Temporal availability, K(t)

An acquisition's information was globally available for reuse only if the
producing acquisition **completed before the consuming acquisition started**:

```text
available(producer, consumer)  <=>  producer.end < consumer.start
```

Not `producer.end < consumer.end`. Given

```text
A READ START
B READ START
A READ END
B READ END
```

B's read is **not** attributable to A, because A had not finished when B began.
This is asserted by dedicated tests (`tests/test_metrics.py`).

### Classification of an overlapping acquisition

For each acquisition, overlap is computed against all acquisitions that were
already complete when it started:

* `globally_previously_available` — equivalent information had completed
  acquisition before this one started.
* `potentially_avoidable_acquisition` — `globally_previously_available` **and**
  the earlier acquisition was by a *different* agent.

Neutral terminology only. Nothing is labelled `wasted` by default.

Duplication is split into:

* **inter-agent duplication** — different `agent_id`.
* **intra-agent repetition** — same `agent_id`.

### Primed vs unprimed

For a later acquisition by agent B, `PRIMED` means B had, before it began that
acquisition, received a message (coordinator instruction or upstream report)
containing sufficiently specific information pointing at that file, symbol,
location or finding. Otherwise `UNPRIMED`.

Priming is decided by matching acquisition targets (path basename, full path,
and result shingles) against the text of messages already delivered to B. The
evidence for each decision — which message, which matching token — is stored so
every classification can be manually audited.

`unprimed independent discovery` is reported **separately** from
`primed reacquisition`, because independent verification may be useful rather
than redundant.

### Acquisition coverage

Formula version **2**, recorded in every run's artifacts. Each observed tool
call falls in exactly one bucket:

```text
acquisition_classified  could deliver repository information AND its information
                        source is mechanically identified
                        (structured_read, structured_search, bash_read,
                         bash_search, bash_directory_listing,
                         bash_git_inspection)

acquisition_unknown     could deliver repository information but we cannot say
                        what it obtained
                        (bash_unknown, unknown_tool)

non_acquisition         could not deliver repository content at all
                        (structured_edit, bash_verification, bash_build,
                         bash_workspace_management, bash_non_acquisition)

denied                  the CLI refused it; nothing ran, nothing was obtained
                        (tool_denied)

acquisition_candidates = acquisition_classified + acquisition_unknown

acquisition_coverage   = acquisition_classified / acquisition_candidates
```

The denominator must represent *tool operations that could plausibly have
delivered repository/task information to the model*. Rationale for each
exclusion:

* A **non-acquisition** command (`pytest`, `mkdir`, `git commit`, `pwd`) cannot
  deliver repository content, so counting it would let unrelated shell activity
  depress a number that is meant to measure how much *acquisition* is
  attributable. `bash_verification` and `bash_build` are excluded on the same
  grounds even though their output is informative: that output is **generated by
  running code**, not **acquired from the repository**, and it is captured
  verbatim in the raw stream regardless.
* A **denied** call performed nothing. Claude Code's own denial message says
  "The action was NOT performed". It cannot be a read, a search, or a test run.
* **`bash_unknown` stays in the denominator** precisely because it might have
  acquired something. This is the term that makes the metric honest, and no
  change may remove it.

Formula version 1 divided attributable acquisition by *every* acquisition-capable
Bash call, which conflated "we could not classify this" with "this was not
acquisition". Version 2 separates them. The revision was prompted by a real run
and is recorded as an amendment in PREREGISTRATION.md section 10.

Proposed minimum before the real pilot is trusted: **coverage >= 0.90**, with
`unknown_tool == 0`. Below that, duplication conclusions from the run are
reported but flagged `low_observability` and excluded from headline comparison.

### Context residency

Three levels, and the achieved level is always reported:

* **Level 1** — exact; complete submitted context observable. Not achievable
  here: Claude Code's hidden system prompt and provider request are not exposed.
* **Level 2** — reconstructed from session event history (what our harness sent
  plus the observed message/tool-result stream).
* **Level 3** — unavailable.

Stage 0 targets Level 2 and never reports it as Level 1.

### Cache behavior

Cache fields are reported by this build and are recorded. No attempt is made to
reverse-engineer provider cache or billing behavior from latency. Prompt-cache
optimization is not a Stage 0 requirement.

## 10. Oracle upper bound

Maximum mechanically-removable coordination redundancy, computed from observed
acquisition only:

```text
U_information =
    primed repeated file acquisition
  + primed repeated search/result acquisition
  + other mechanically addressable repeated information
```

Reported in physical units, not forced into dollars: duplicate acquisitions,
duplicate content chunks (shingles), duplicate returned bytes, duplicate tool
calls, duplicate turns. Provider-token-based estimates are reported separately
where token telemetry is available, and are attributed to tool-result volume
rather than to exact billed tokens.

## 11. Success criterion

A task is `SOLVED` only when the configured held-out deterministic verifier
exits zero. No LLM judge is used for primary success. The verifier is executed
identically for Arm A and Arm B, from a copy of the held-out tests injected
after the agent phase ends, so agents cannot edit the verifier.

## 12. Workspace isolation

Every run starts from the same pinned base commit in its own workspace (a clone
of the pinned task repo). Runs record `repo`, `base_commit`, `final_tree_hash`,
and `workspace_diff.patch`. Runs cannot contaminate one another; this is asserted
by `tests/test_workspace.py`.

## 13. Repetitions

The word `seed` is not used: the installed Claude Code exposes no supported
deterministic model seed. Independent repetitions are identified by `repeat_id`.

Planned full pilot: 12 tasks x 2 arms x 3 repeats = 72 runs. Not run during
implementation. Development runs 1 task x Arm A x1 x Arm B x1.

## 14. Subscription usage protection

`config.py` limits, all enforced before spawning:

```text
max_tasks_per_invocation
max_sessions_per_invocation
max_turns_per_session
max_wall_seconds_per_session
require_explicit_pilot_flag
```

The full pilot requires `--confirm-pilot`. A smoke command can never launch more
than `smoke` limits allow. If Claude Code reports a usage-limit condition, the
harness logs it and terminates cleanly; it never works around a usage limit.

## 15. Reconstruction gate

Claude Code's hidden system prompt and provider HTTP request are **not** under
our control and reconstruction of them is not claimed.

The gate is: everything **our harness supplied** to each invocation is
reconstructible from the logs alone —

```text
argv, cwd, stdin/prompt bytes, role/task prompt, handoff contents,
tool allow/deny configuration, model selection, turn/time limits,
project-local configuration we control, child environment manifest
```

`invocation.json` is written **before** process execution. The gate test
re-derives the invocation from logs and compares. A failing gate blocks the run.

## 16. Known observability limitations

1. No byte/line read ranges. Identity is at returned-content granularity.
2. Arbitrary `Bash` can acquire repository content opaquely; measured and
   reported as `unclassified_bash_acquisition`, not assumed away.
3. Hidden system prompt and provider request are not observable; context
   residency is Level 2 at best.
4. `--max-turns` does not exist; turn limiting is harness-side and approximate
   at the boundary.
5. `total_cost_usd` under a subscription is an API-equivalent figure, not an
   amount paid.
6. `seq` does not establish causal order for events concurrent inside the CLI.
7. Internal subagent fan-out is only summarized (`subagent_stats`); it is
   disallowed rather than measured in detail.
8. **The spawned CLI must be independently authenticated.** The Claude desktop
   application holds its OAuth token in its own store, which the CLI does not
   read. Until `claude auth login` (or `claude setup-token`) has been run for the
   CLI, every spawned session fails with `authentication_failed` and no
   experiment data can be collected. See README.
9. **Path extraction from a listing is heuristic.** `result_paths` matches
   dotted, extension-bearing paths, so a `find` that returns a bare directory
   (`<ws>/tinylib`) yields an empty path list. The acquisition is still fully
   recorded by content hash and shingles; only the convenience path list is
   empty.
10. **A multi-line inline shell script is not decomposed.** Quote-aware
    segmentation keeps such a script in one segment, and it is classified by its
    interpreter invocation (`bash_verification` for `python -c`). A command whose
    family is unrecognized is `bash_unknown` and counts against coverage.

### 16.1 RESOLVED: tool calls denied for want of an approval surface

**Resolved by a narrow Bash allowlist, validated by one real Arm A run
(`20260910T125941Z_palindrome_punctuation_A_r1`).**

The policy (`config.BASH_TEST_ALLOWLIST`, id `narrow_pytest_v1`), applied
identically to every session in both arms and recorded in `metadata.json` and
`config_hash`:

```text
Bash(python -m pytest *)     wildcard matching (current form, per --help)
Bash(python -m pytest)       exact, no arguments
Bash(python -m pytest:*)     prefix matching (legacy form) - same command family
```

Three spellings of exactly ONE permission. `--allowedTools` rules turned out not
to be validated at startup (two deliberately malformed rules were accepted
silently and the session ran normally), so a mis-spelled rule fails at match time
rather than raising; supplying all three supported spellings removes the
dependency on which one this build's matcher honours.

Syntax was verified against the installed binary, not assumed. `claude --help`
documents `--allowedTools <tools...>` with the example `"Bash(git *)"`, and the
binary's own rule validator distinguishes
`"Bash(npm run:*) - prefix matching (legacy)"` from
`"Bash(npm run *) - wildcard matching"`, rejecting `:*` anywhere but at the end.

`bypassPermissions` is **not** used, and `--permission-prompts none` is retained,
so anything outside the allowlist is still denied automatically and still
instrumented as `tool_denied`.

What the validation run showed:

* `cd "<ws>" && python -m pytest -q` **executed**, `is_error=false`, and Claude
  received the real output `6 passed in 0.02s`. The single pytest rule covered
  the compound form, so `cd` needs no rule of its own.
* `python -c "..."` in a later compound command was **still denied**, and the
  CLI named the offending part: *"This Bash command contains multiple
  operations. The following part requires approval: python -c ..."*. That is
  direct evidence of per-sub-command decomposition, and that the allowlist is
  narrow rather than permissive.
* Claude did not claim the denied action had succeeded. Its final message said
  the suite had already run successfully and summarised the change.
* `acquisition_coverage` 1.000, unknown CLI event types `{}`, held-out verifier
  12 passed, `apiKeySource: "none"`.

Caveat kept on the record: the 6 visible tests pass both before and after the
fix, so Claude observed a passing suite **on its own edited code** but the
visible suite alone does not establish correctness. Only the 12-test held-out
verifier does.

Consequence accepted deliberately: because the policy is identical for every
role, the Arm B Investigator may also run the test suite. That does not weaken
its read-only constraint (`Edit`/`Write`/`NotebookEdit` remain denied) and it
keeps the permission configuration byte-identical across arms, which is the
property the comparison depends on.

### 16.2 Historical record of the issue (superseded by 16.1)

**This was an experimental-validity problem, not an observability gap.**

In the first real Arm A run, two of the agent's four `Bash` calls were refused:

```text
system/permission_denied
  tool_name            = "Bash"
  decision_reason_type = "asyncAgent"
  decision_reason      = "no approval surface in this session;
                          permission request denied automatically"
```

Both were `python -m pytest` invocations. Read-only `Bash` (`find`, `cd`) was
allowed; executing code was not. The cause is the harness's own invocation:
`--permission-mode acceptEdits` together with `--permission-prompts none` means
anything that would prompt is denied automatically, and the CLI's message adds
that everything else requiring approval will be denied for the rest of the
session.

Consequences:

* The task statement asks the agent to run the tests. It could not. The run is
  recorded `SOLVED: YES` **only because the harness runs the held-out verifier
  itself**, after the agent phase.
* Arm parity is preserved — Arm B's Implementer would be denied identically — so
  this does not bias one arm against the other. But **both** arms would be
  measured while unable to execute tests, which is not the behavior Stage 0 set
  out to observe, and it plausibly changes acquisition patterns (an agent that
  cannot run tests may read more, or stop earlier).

Options, none of them yet applied, because changing the invocation without a
validating run would replace a known problem with an unmeasured one:

| Option | Change | Trade-off |
| --- | --- | --- |
| A | `--permission-mode bypassPermissions` | allows everything in the isolated per-run workspace; the CLI's own docs recommend it only for sandboxes |
| B | `--allowedTools` naming the test command family, e.g. `"Bash(python -m pytest*)"` | narrow and auditable; must be identical across arms, and a command outside the pattern is still denied |
| C | keep the current setting | measures agents that cannot execute code; must then be stated as a property of the experiment, not an accident |

Option B was chosen, applied identically to Arm A and Arm B, recorded in
`metadata.json` and `config_hash`, and validated by one real Arm A run. See
section 16.1.

## 17. Stage 0 stop point

Stage 0 stops after: Arm A smoke run, Arm B smoke run, instrumentation
validation, duplication metrics, human-readable trace. Then it reports what was
actually observable.

Out of scope and not implemented: Arm C, structured state transfer, artifact
references, Agent IR, semantic bytecode, compact AI language, binary protocol,
LoRA/adapters/MoE, specialist training, expert offloading, latent communication,
KV-cache research, custom inference engine, production distributed architecture,
UI/dashboard, Kafka/Redis/NATS/Kubernetes, custom MVCC, SWE-bench, API-based
Claude client.

## 18. Statement on exact accounting

> Stage 0 does not require exact API token or dollar accounting because this
> experiment uses subscription-authenticated Claude Code rather than the
> Anthropic Messages API.

Where this build does expose token, cache and cost telemetry, it is recorded
with explicit `availability` and `source`, and is treated as secondary and
opportunistic. Primary conclusions rest on tool activity, acquisition volume,
duplication under temporal availability, sessions, turns, latency and success.

## 19. Refined measurement after the first A/B pair (amendment 5)

### 19.1 Reacquisition subcategories

`primed_reacquisition` stays the gross measurement. Underneath it, every primed
inter-agent reacquisition is placed in exactly one subcategory by
`metrics.classify_reacquisition`, using only the consumer's own session:

1. `verification_associated` — re-read of a file the agent already successfully
   edited in this session (checking its change).
2. `edit_precondition_associated` — the first Read of a file in this session,
   later followed in the same session by a successful Edit/Write of that file,
   with no earlier Read of it. Applies only on a CLI version listed in
   `metrics.EDIT_REQUIRES_PRIOR_READ` (currently 2.1.260, with the binary
   evidence recorded). Otherwise the read is `unknown`.
3. `verification_associated` — the target is named in a sentence of a delivered
   message containing an explicit verification verb (verify, confirm,
   double-check, validate, make sure, check that, ...). Sentences end at a
   newline or at `.`/`!`/`?` followed by whitespace, so the dot in a file name is
   not a boundary. If the matching sentence is about test outcomes (pass/fail,
   `pytest`, "test suite"), it asks for a test run rather than explaining a
   Read, so the read is `unknown` instead.
4. `unknown` — a same-file edit that failed or was refused; a read following the
   agent's own test run (it may be diagnosis rather than rediscovery); no
   attributable target.
5. `discretionary_information_reacquisition` — everything else that is primed.

A read after the precondition is already satisfied (a second read before the
edit) is not edit-required. An edit in a different session does not count,
because Claude Code's rule is per session. Only Read-tool reads can satisfy the
precondition, so a `cat` never can. Subcategories partition the gross count
exactly, which is asserted in code.

### 19.2 Communication duplication

`analysis/handoff.py`, measured with `tools.content_windows`: 3-line windows,
indentation stripped, Read-tool line prefixes removed, and uninformative windows
dropped (fewer than 16 alphanumeric characters, or fewer than 2 lines containing
any). For each handoff:

* `repository_content_chunks_in_handoff` — windows present in any file at the
  base commit, read from the run's own workspace repository;
* `tool_output_chunks_in_handoff` — windows present in tool output the sender
  had observed before sending;
* `relayed_chunks_in_handoff` — windows present in handoffs the sender had
  received;
* `handoff_repository_quote_fraction` — the quoted-characters estimate divided by
  the handoff's characters.

Window overlap is primary; characters are an estimate.

`handoff_reacquisition_overlap` reports:

* **I** — chunks in the Investigator's acquisitions;
* **I∩H** — those also copied into handoffs to the Implementer;
* **I∩R** — those also re-acquired by the Implementer;
* **I∩H∩R** — the intersection of all three.

This is content that was carried downstream twice: once as prose, once as a tool
result.

### 19.3 Benchmark-test protection and SOLVED

A task may declare `read_only_paths` and `expected_modified_paths`.

* The harness makes the protected files read-only after the base commit, so the
  base commit SHA is unaffected, in `arms.start_run` for both arms.
* `finish_run` records `workspace_integrity` (changed paths, changes under
  protected paths, changes outside the expected scope) and runs the visible tests
  separately, all before the held-out verifier is injected.
* SOLVED remains the held-out verifier's exit code only.
* On POSIX a rename over a read-only file, or a newly added file, cannot be
  prevented this way, which is why changes are also detected and reported.

### 19.4 Fixture 2 — `cart_invoice_rounding`

Designed to separate the edit-required read of the file being fixed from
informational reacquisition of supporting files:

* **A** `shopcart/cart.py` — the symptom and the only correct change.
* **B** `money.py`, **C** `discounts.py`, **D** `currency.py`, **E** `invoice.py` —
  constraints and evidence, pinned by held-out regression tests.

A naive per-line half-up fix returns 268 for the task's own example instead of
267, so the per-unit rounding contract in C/E has to be discovered. The statement
names no supporting file. `tests/test_fixture_cart.py` validates all of this
without a Claude session.

## 20. Freeze before the second controlled pair (2026-09-11)

The definitions are frozen in PREREGISTRATION sections 11–13 and pinned in code by
`tests/test_frozen_definitions.py`. Additions made in the freeze, all of them
reporting and integrity only (amendment 6):

* `analysis/isolation.py`: a detection-only held-out isolation check over raw
  telemetry (section 12 does not provide filesystem isolation). Stored in
  `run_metrics.isolation_check_json`, and shown in the run report and the pair
  summary.
* `Task.supporting_paths` and `report.file_breakdown`: per-file reacquisition for
  supporting files (H1) and for the file being fixed (H2).
* `report.per_agent_acquisition`: tool activity and acquisition per logical agent.
* `report.render_pair_summary`: the frozen Pair-2 primary output with parity
  checks, printed by `runner.py report --task`.
* `metrics.REACQUISITION_CLASSIFIER_VERSION = 1`, now reported alongside the
  parser, coverage-formula, handoff and isolation versions.
* `run_manifests/raw_run_checksums.sha256`: SHA-256 of every raw artifact of the
  three pre-freeze real runs. Run directories stay untracked. `tests/test_run_manifest.py`
  checks the local copies.
* `tests/conftest.py` refuses to launch any `claude`/`claude.exe` subprocess, so
  the local suite cannot consume subscription quota.

## 21. Stage 0.5: decomposition instrumentation (2026-09-11)

Preregistered in PREREGISTRATION section 15. Everything is prospective, and
reports mark results on historical runs as `post_hoc_exploratory`.

* `analysis/decomposition.py`:
  - `api_calls` gives exact per-call usage from the raw stream;
  - `decompose` breaks down session fanout, handoff context, the frozen
    reacquisition subcategories, unique downstream acquisition, other tool
    results and the unattributed remainder, each labelled exact /
    reconstructed / estimated / unavailable;
  - `pair_decomposition` gives the B − A rows;
  - `unique_downstream` gives unique downstream acquisition with mechanical
    materiality indicators.
* `analysis/overlap_v2.py`: `handoff_repository_overlap_v2` (line level; see
  PREREGISTRATION 15.3). v1 in `handoff.py` is untouched.
* `analysis/leakage.py`: the held-out content check, complementing
  `isolation.py` v1.
* `report.run_report` adds `stage05_metrics_status`, `task_shape`,
  `handoff_repository_overlap_v2`, `unique_downstream_acquisition`,
  `overhead_decomposition`, `held_out_content_check`, `session_terminations`
  and `validity`. `render_decomposition` prints the decomposition section after
  the frozen pair summary; the frozen pair summary itself is unchanged.
* `python runner.py summary` prints the cross-task table: one row per
  (task, repeat) using the latest Arm A and Arm B run, validity and parity per
  pair, means and medians over valid pairs only, and per-task repeat spread.
* `Task` gains the analysis-only fields `symptom_paths` and `shape`;
  `expected_edit_paths` is a synonym for `expected_modified_paths`. None of
  them reaches a prompt.
* New fixtures: `shipkit`, `confkit`, `dbclient`, `helpdesk` (tasks 3–6).

## 22. Stage 1: C1_shared_worker_context (2026-09-11)

Preregistered in PREREGISTRATION section 16.

* `arms/c1_shared_worker.py`: five invocations (Coordinator; Worker
  Investigator phase; Coordinator resume; Worker Implementer phase as `--resume`
  of the Investigator session; Coordinator resume), giving 2 fresh physical
  sessions. The Coordinator and Investigator prompts are Arm B's functions. The
  Implementer phase gets `implementer_resume_prompt(instruction)` only: no
  forwarded report and no task statement. It writes `topology.json` and a
  `WORKER_ROLE_TRANSITION` event.
* `config.C1_TOPOLOGY`, `topology_config_hash`: the C1 identity. `ARMS`,
  `effective_config()` and the A/B `config_hash` are untouched. `start_run(...,
  topology=)` is used only by C1.
* `report.topology_summary` (invocations, fresh/resumed, physical sessions,
  exact first-call usage, requested vs `init` tools) and
  `report.worker_transition_check` (a C1 validity condition),
  `render_stage1_comparison`, `stage1_rows`, `render_stage1_summary`.
* `runner.py smoke --task <t> --arm C1`, `report --task <t> --compare-stage1`,
  `stage1-summary`. `--arm both` is still exactly A and B.
* `decomposition.session_contexts` keys resume chains by physical session
  (identical results on every A/B run).
* The mock CLI recognizes the C1 Implementer-resume prompt, for local tests only.

## 23. Stage 2A: heterogeneous model routing (2026-09-11)

Preregistered in PREREGISTRATION section 18. It is a new experiment: A, B and C1
are unchanged.

* `config.py`: `CHEAP_MODEL = "claude-haiku-4-5-20251001"`,
  `STRONG_MODEL = "sonnet"`, and alongside them:
  - `MODEL_CLASSES`;
  - `AUXILIARY_MODELS_CANONICAL`;
  - `MODEL_PRICING_USD_PER_MTOK`, from the CLI registry and verified against
    stored `costUSD`;
  - `STAGE2_EFFORT_POLICY` (`cli_default_not_passed`);
  - `STAGE2_CONFIGS`: `S2_R1`, `S2_R2`, `S2_R3`, `S2_S`, `S2_SS`;
  - `stage2_topology(arm)`, whose identity goes through `topology_config_hash`;
  - `preflight_model_routing_env`, a presence-only guard for model/effort
    variables that would reach a child.

  `ARMS`, `effective_config()` and the A/B `config_hash` are untouched.
* `harness/agent.run_agent(..., model=None)`: a per-invocation model override.
  A, B and C1 never pass it, so every one of their invocations still gets
  `ctx.model`.
* `arms/__init__.start_run`: a topology may name its `stage` and
  `experiment_schema_version`. C1's names neither, so its metadata is unchanged.
* `harness/model_routing.py`:
  - `check_invocation`: the requested vs `init.model` vs every assistant
    `message.model` vs `modelUsage`.
  - `split_usage`: role-assigned usage (`result.usage`) vs Claude Code auxiliary
    usage (`modelUsage − result.usage`, per model), with API-equivalent cost.
    Also a lower-bound fallback when there is no result event.
* `arms/s2_routing.py`:
  - Arm B's orchestration and prompt functions verbatim, with each role's
    `--model` from the topology.
  - Single-agent configurations use Arm A's prompt.
  - Every invocation is verified immediately. On a mismatch the chain stops
    before the next session.
  - It writes `routing.json` and `MODEL_ROUTING` events (an additive event type;
    `SCHEMA_VERSION` stays 1).
  - Before any invocation it checks the frozen prompt hashes, and it adds
    `stage2_identity` (config hash, task base commit, verifier hash, classifier
    versions) to the metadata.
* `analysis/routing.py`: the run-level routing and accounting check, recomputed
  from raw logs. It also works on historical A/B runs, where `sonnet` = STRONG.
  It supplies the Stage-2 validity rules (`report.run_validity` applies them to
  `S2_*` arms only).
* `analysis/stage2.py`:
  - per-run metrics E2–E5 (cost by role and by model, strong usage, tokens,
    wall);
  - `baseline_parity` and `select_single_strong`: the exact-parity reuse rule;
  - E6 `failure_location` (rules R1–R7) and E7 `escalation_evidence`;
  - `compare` (A–D; cost only between valid runs that both solved);
  - `interpretation_cases` (A–E);
  - `render_task_report` and `render_summary`.
* `runner.py`:
  - `smoke --arm S2_R1|S2_R2|S2_R3|S2_S|S2_SS`. Stage-2 arms require the default
    `--model sonnet`, checked before anything is probed.
  - `stage2-report --task <t>` and `stage2-summary`.
  - `--arm both` is still exactly A and B.
* `tools/mock_claude.py`: for the Stage-2 model names only, it resolves them
  like 2.1.260, reports per-model `modelUsage` with an auxiliary Haiku entry,
  and has a forced-mismatch test hook. Its A/B/C1 output (`mock-sonnet`) is
  unchanged.
