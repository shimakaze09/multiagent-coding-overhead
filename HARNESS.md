# Stage 0 — measuring repeated information acquisition in multi-agent coding

Measurement only. No protocol design, no structured state transfer, no Arm C.
See [SPEC.md](SPEC.md) for the full specification and
[PREREGISTRATION.md](PREREGISTRATION.md) for the analysis committed to in advance.

---

## 1. Research question

> In multi-agent coding, how much additional context/work comes from agents
> re-acquiring, re-reading, carrying, searching for, or rediscovering
> information that another agent already obtained?

Operationally:

> How much extra information acquisition in the natural-language multi-agent
> system was information another agent had already acquired **before** the later
> agent went looking for it?

Stage 0 measures existing behavior of two arms and then stops. It does not
propose or evaluate a remedy.

## 2. Subscription-only execution

Every Claude workload runs through the locally installed, subscription-
authenticated Claude Code CLI:

```text
Python experiment harness
        |
        v
local `claude` executable   (claude -p --output-format stream-json --verbose ...)
        |
        v
authenticated Claude Code subscription
```

Explicitly **not**:

```text
Python -> Anthropic Messages API -> API billing
```

## 3. Why no Anthropic API key is used

The Anthropic Messages API bills per token against an API account. This project
must run on the existing Claude Pro/Max subscription instead, so:

* the `anthropic` Python SDK is not a dependency and is never used for model
  execution — the only third-party package in `requirements.txt` is `pytest`;
* the harness never creates an API key, never enables usage credits or PAYG,
  and never unsets or replaces credentials;
* `--bare` is on the forbidden-flag list: on Claude Code 2.1.260 it forces
  `ANTHROPIC_API_KEY`/`apiKeyHelper` auth and never reads OAuth, i.e. it is an
  API-billing path. So are `--max-budget-usd` and `--betas`;
* if `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY_HELPER`,
  `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`)
  is present in the environment, the harness **stops before any Claude
  workload** and explains why. It tests **presence only** — no credential value
  is ever read, printed, or logged.

Removing such a variable is your decision, not the harness's. If you want to run
on the subscription and one is set, remove it from your own shell:

```bash
Remove-Item Env:ANTHROPIC_API_KEY
```

### Post-hoc verification, not just a pre-flight check

Every session also asserts against the CLI's own `system/init` event that
`apiKeySource` is not an API-key source. A run whose `apiKeySource` is
`ANTHROPIC_API_KEY`, `apiKeyHelper`, `bedrock`, or `vertex` is recorded as
`api_charge = "api_suspected"` and fails the run gate.

> **Note on your account.** `hasExtraUsageEnabled` is already `true` on this
> account. That is a pre-existing account setting the harness did not change and
> will not change. It means that if the subscription's limits are exhausted,
> usage can continue as paid overage. The harness caps sessions and turns (§9),
> and terminates cleanly if Claude Code reports a usage-limit condition, but it
> cannot make overage impossible. That is the one billing risk the code cannot
> remove for you.

## 4. How Claude Code authentication is checked

`python runner.py diagnose` runs `claude auth status --json` in a sanitized
environment and reports `loggedIn`, `authMethod`, and `apiProvider`. No model is
invoked and nothing is spent.

**Resolved.** The CLI is now signed in and a real Arm A run has been collected
(`runs/20260910T122445Z_palindrome_punctuation_A_r1`, `apiKeySource: "none"`,
i.e. subscription OAuth). The note below is kept because it is the failure mode
to recognise if authentication is ever lost.

The Claude *desktop application* keeps its OAuth token in its own store
(`%APPDATA%\Claude\config.json`), which the `claude` CLI does not read. With no
`~/.claude/.credentials.json` and no Windows Credential Manager entry, a spawned
CLI reports:

```json
{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}
```

and any `claude -p` run fails immediately with `authentication_failed`
("Not logged in · Please run /login"), `apiKeySource: "none"`, `total_cost_usd: 0`.

To collect real Arm A / Arm B data, sign the CLI in **yourself**, once, in an
interactive terminal:

```bash
"$env:APPDATA\Claude\claude-code\2.1.260\claude.exe" auth login
```

or `claude setup-token` for a long-lived subscription token. The harness will not
sign in for you: authentication involves entering credentials, which is yours to
do. Re-run `diagnose` afterwards; it should report `loggedIn: true` with an
`authMethod` that is not an API-key source.

## 5. The two arms

**Arm A — single agent.** One Claude Code session. Gets the task statement
verbatim, the same repository at the same pinned base commit, the same success
criterion, and the same tool capability pool.

**Arm B — natural-language multi-agent.** Exactly three logical agents, fixed
topology, separate sessions:

```text
User task -> Coordinator -> Investigator -> Coordinator -> Implementer -> Coordinator -> Final
```

* **Coordinator** — delegation and handoff only; no repository tools in the
  baseline. One session, resumed across its three turns, so it is one logical
  agent with memory.
* **Investigator** — `Read`, `Grep`, `Glob`, read-only `Bash`; cannot modify
  anything. Outputs an ordinary natural-language report, stored verbatim.
* **Implementer** — inspect, edit, run tests. Receives the task statement
  verbatim, the Coordinator's instruction, and the Investigator's report. It is
  **never** told "do not reread anything": repeated acquisition is the dependent
  variable.

Both arms draw from the same capability pool (`Read`, `Grep`, `Glob`, `Edit`,
`Write`, `Bash`), differing only by role policy. Neither arm gets custom
high-precision instrumentation tools the other lacks. The `Agent`/`Task` tool is
disallowed in both arms, so Arm A is a genuine single agent; `subagent_stats.spawned`
is recorded per session and a non-zero value invalidates that run's arm label.

Arm C (structured state / artifact references) is **out of scope and not
implemented**.

## 6. Running a smoke task

```bash
python runner.py diagnose
```

Checks Python, git, the CLI and its flags, CLI authentication, the task registry,
and the billing guard; writes `runs/diagnostics.json`. It exits non-zero if
anything Stage 0 depends on is unavailable.

Then, once the CLI is authenticated:

```bash
python runner.py smoke
```

One task, Arm A then Arm B, followed by both reports and the comparison. This is
capped by `SMOKE_LIMITS` and can never launch the pilot.

To see the whole pipeline work **without** an authenticated CLI and without
spending anything:

```bash
python runner.py mock-smoke
```

This drives `tools/mock_claude.py`, which speaks the same argv surface and emits
the same stream-json shapes as the real CLI and really performs its reads,
searches and edits. It is not a model, so its output is instrumentation
validation only and never an Arm A / Arm B result.

Other commands:

```bash
python runner.py ingest                  # rebuild the derived SQLite index
python runner.py report --run <run_id>   # one run's report
python runner.py report --task <task_id> # every run, plus the arm comparison
python runner.py trace <run_id>          # human-readable trace
python -m pytest tests -q                # the test suite
```

## 7. Where raw telemetry lives

Raw artifacts are the source of truth and are never rewritten.

```text
runs/<run_id>/
    metadata.json                 harness inputs, capability report, env manifest
    events.jsonl                  normalized experiment event log (append-only)
    run_summary.json
    sessions/<session_key>/
        invocation.json           argv, cwd, env manifest, stdin bytes, limits
                                  (written BEFORE the process is spawned)
        claude_stdout.jsonl       raw CLI stdout, byte-for-byte, unparsed
        claude_stderr.txt         raw CLI stderr
        result.json               the final `result` event, verbatim
        exit.json                 exit code, timing, termination reason
    handoffs/NN_<from>_to_<to>.txt   exact natural-language handoff bodies
    verify/                       verifier stdout/stderr/exit
    workspace/                    the isolated git workspace for this run
    workspace_diff.patch          final diff vs the base commit
```

There are **two** independent raw sources and neither may overwrite the other:

1. raw Claude Code CLI output (`claude_stdout.jsonl`, `claude_stderr.txt`)
2. the normalized experiment event log (`events.jsonl`)

Every normalized event derived from a CLI event carries
`raw_ref = {session_key, line_no}` pointing back into the raw stream. Parsing
never discards the original.

## 8. How SQLite is rebuilt

`analysis/stage0.sqlite3` is a **derived analytical index only**. It is
disposable:

```bash
python runner.py ingest      # deletes any existing DB and rebuilds from raw logs
```

Acquisitions, including content shingles, are recomputed from
`claude_stdout.jsonl` rather than read back from `events.jsonl`, so the raw
stream remains authoritative. `tests/test_ingest.py` asserts the required
property: build → delete → re-ingest → identical database, and identical
analytical results with no database present at all.

## 9. How duplication metrics work

**Information identity.** Claude Code does not expose byte or line ranges for
reads, so none are fabricated. Identity is at returned-content granularity:

* `result_sha` — BLAKE2b of the exact `tool_result` text the agent received;
* `shingles` — BLAKE2b over **3-line** sliding windows (3-line windows rather
  than per-line hashes, so trivial lines like `}` or `import os` cannot create
  false overlap);
* `path_version_hint` — the git blob SHA at the base commit, as a secondary hint.

A read before an edit and a read after it have different `result_sha` and are
therefore **not** the same information.

**Concurrency grouping.** Claude Code emits one stream event per content block,
so tool calls issued together in a single API assistant message land on
consecutive lines (real runs show 5 and 3 parallel `Read`s). Ordering by raw line
would make simultaneous calls look sequential, so each acquisition carries
`api_message_id` and `concurrency_group_line`, and the ordering key uses the
group line.

**Temporal availability, K(t).** An acquisition's information was available for
reuse only if the producing acquisition *completed before the consuming
acquisition started*:

```text
available(producer, consumer)  <=>  producer.end < consumer.start
```

Not `producer.end < consumer.end`. Given

```text
A READ START / B READ START / A READ END / B READ END
```

B could not have reused A's result when B began, so B's read is **not**
attributable to A. Ordering uses the key `(session_index, line_no)`: the agent
chain is strictly sequential, and parallel tool calls issued in one assistant
message share a start line, which is exactly the concurrency case. This rule is
asserted directly in `tests/test_metrics.py`.

**Classification.** For each overlapping acquisition:

* `globally_previously_available` — equivalent information had finished being
  acquired before this one started;
* `potentially_avoidable_acquisition` — that, **and** by a different agent;
* split into **inter-agent duplication** and **intra-agent repetition**.

Neutral terminology only. Nothing is labelled "wasted".

**Primed vs unprimed.** A later acquisition is `PRIMED` if a message delivered to
that agent *before it began* named the target specifically (full path, basename
with extension, or the exact search query / an identifier from it). A bare
filename stem does not count — matching `report` inside "report headings" would
be a false positive. `UNPRIMED` overlapping discovery is reported **separately**
from `primed reacquisition`, because independent verification may be useful
rather than redundant. Every classification stores its evidence (which message,
which token, which excerpt) so it can be audited by hand.

**Acquisition coverage (formula v2).** Bash can acquire repository content
opaquely, so coverage is measured rather than assumed. Every tool call falls in
exactly one bucket:

```text
acquisition_classified   could deliver repository information AND its source is
                         mechanically identified (structured_read,
                         structured_search, bash_read, bash_search,
                         bash_directory_listing, bash_git_inspection)
acquisition_unknown      could deliver repository information but we cannot say
                         what it obtained (bash_unknown, unknown_tool)
non_acquisition          could not deliver repository content at all
                         (structured_edit, bash_verification, bash_build,
                          bash_workspace_management, bash_non_acquisition)
denied                   the CLI refused it; nothing ran (tool_denied)

acquisition_candidates = acquisition_classified + acquisition_unknown
acquisition_coverage   = acquisition_classified / acquisition_candidates
```

The denominator represents *operations that could plausibly have delivered
repository/task information to the model*. A `pytest` run, a `mkdir`, or a
refused call could not, so they are excluded — otherwise unrelated shell activity
would depress a number meant to measure attributable acquisition.
`bash_unknown` stays in the denominator precisely because it might have acquired
something; that is the term that keeps the metric honest.

Bash categories are **derived data only**: the raw command, stdout, stderr and
exit status are always preserved unchanged. Two rules do most of the work —
neutral stream filters (`sort`, `wc`, bare `head`) never decide a category, and
segments are split on `&&`/`|`/`;`/newline **only outside quotes**, so a
`python -c "..."` script body is not shredded into bogus unknowns.

Minimum before a run's duplication result is eligible for headline comparison:
**0.90, with zero unknown tools**. Below that the run is reported but flagged
`low_observability`. The v1 -> v2 revision is recorded as an amendment in
PREREGISTRATION.md §10.

**Reacquisition subcategories.** Every primed reacquisition is further
classified as `edit_precondition_associated`, `verification_associated`,
`discretionary_information_reacquisition` or `unknown`:

* **Edit precondition:** the first Read of a file the same agent then
  successfully edits in the same session, which Claude Code 2.1.260 requires
  before Edit.
* **Verification:** a re-read after the agent's own edit, or an explicit
  verification request naming the target. A request that is only about test
  outcomes ("confirm the tests still pass") makes the read `unknown` instead.
* **Discretionary:** only what is left over, and the strongest candidate for
  avoidable rediscovery.

The gross count is kept, and the subcategories partition it exactly.

**Communication duplication.** Each handoff is compared with repository content,
the sender's own tool output, and content it had received. The comparison uses
indentation-insensitive 3-line windows that ignore syntax-only runs. Separately,
`handoff_reacquisition_overlap` counts content that was acquired by the
Investigator, copied into a handoff, and re-acquired by the Implementer.

**Oracle upper bound.** The maximum mechanically-removable coordination
redundancy, counting only *primed inter-agent* repetition, reported in physical
units (duplicate acquisitions, content chunks, chars, bytes, tool calls) — not
forced into dollars.

## 10. Which metrics are unavailable, and which turned out to be available

### Read `input_tokens` correctly

A real run reported `input_tokens = 14` next to `cache_read_input_tokens =
126874` and `cache_creation_input_tokens = 10369`.

**`input_tokens` is uncached, newly-submitted input only. It is not the
prompt/context length.** The submitted context is the sum of the three, which the
harness computes and labels as `total_input_tokens` (137,257 here) — the only
field that may be described as context volume. `total_tokens` mixes input and
output and is not a context measure. Provider values are never altered; the
machine-readable definitions are in `telemetry.TOKEN_SEMANTICS` and are embedded
in every run's usage block.

`result.usage` also covers **only the session's main model**. The same run's
`modelUsage` showed Claude Code additionally invoking `claude-haiku-4-5`
(1,104 input / 18 output), and `total_cost_usd` covered both. `model_usage_totals`
records per-model figures so token accounting does not silently undercount.

### What turned out to be available

The original specification assumed subscription-mode Claude Code might not expose
token, cache or cost telemetry. **On 2.1.260 it does.** Observed in the
`assistant` message `usage` object and the final `result` event:

```text
input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens,
cache_creation.ephemeral_1h_input_tokens / ephemeral_5m_input_tokens,
output_tokens_details.thinking_tokens, service_tier,
modelUsage, total_cost_usd, num_turns,
duration_ms, duration_api_ms, permission_denials, subagent_stats
```

Every numeric field is stored as a `{value, source, availability}` triple, where
`availability` is `reported`, `not_exposed`, `estimated`, or `unreliable`. An
absent field is `not_exposed` with a `null` value — never silently zero.

Genuinely unavailable, and reported as such:

| Not available | Consequence |
| --- | --- |
| Byte/line read ranges | Identity is at returned-content granularity, not ranges. |
| The hidden system prompt and provider HTTP request | Context residency is **Level 2** (reconstructed from session event history), never reported as Level 1. Reconstruction is claimed only for what *our harness* supplied. |
| `--max-turns` (absent in 2.1.260) | Turn limiting is harness-side: the parser counts assistant turns and terminates the child. Approximate at the boundary — an in-flight request may already be running. |
| What you actually paid | `total_cost_usd` is preserved verbatim as CLI telemetry but is an API-equivalent figure, recorded as `cli_cost_interpretation: "api_equivalent_not_amount_paid"`. It is **not** the primary metric. |
| Causal order for events concurrent inside the CLI | CLI-derived events are marked `order_confidence: "cli_reported"`, not `"harness_observed"`. |
| Detail of internal subagent fan-out | Only `subagent_stats` is summarized; fan-out is disallowed rather than measured. |

Local approximate tokenization is used **only** to compare natural-language
handoff bodies, and only under the name `estimated_tokens` (never `tokens`).
`tiktoken` is deliberately not used: it is the wrong tokenizer for Claude and
would misrepresent provider counts. Handoffs also record exact `chars`,
`utf8_bytes`, `words`, `lines`.

Primary conclusions rest on tool activity, acquisition volume, duplication under
temporal availability, sessions, turns, latency and success — not on dollars.

## 11. How to avoid accidentally launching the full pilot

Nothing launches the pilot implicitly.

* `config.SMOKE_LIMITS` caps a development run at 1 task and 6 sessions.
  `smoke` and `mock-smoke` use it and cannot exceed it.
* Every session is checked against `max_sessions_per_invocation` **before**
  spawning; exhausting the budget raises rather than continuing.
* `max_turns_per_session` and `max_wall_seconds_per_session` bound each session;
  the child process is terminated when either is exceeded.
* `pilot` refuses to start without **both** `--confirm-pilot` and
  `--i-really-mean-it`, and prints the planned run count first
  (12 tasks × 2 arms × 3 repeats = 72 runs).
* If Claude Code reports a usage-limit condition, it is logged as
  `USAGE_LIMIT_REPORTED` and the run ends cleanly. The harness never works
  around a usage limit.

Repetitions are identified by `repeat_id`, not "seed": the installed Claude Code
exposes no supported deterministic model seed.

## 12. Current status

Instrumentation is complete, validated end to end against the mock CLI, and
re-validated against **one real authenticated Arm A run** (284 tests pass).

* Real Arm A runs (two, both solved, both `acquisition_coverage = 1.000`, both
  with zero unknown CLI event types):
  `20260910T122445Z_..._A_r1` (before the permission fix — tests were denied) and
  `20260910T125941Z_..._A_r1` (after — tests executed).
  Neither is a research result.
* One real Arm B run: `20260910T230448Z_palindrome_punctuation_B_r1`. It solved,
  shares its `config_hash` and base commit with the validated Arm A run, has
  coverage 1.000 and zero unknown CLI event types.
  * **Gross:** 2 primed inter-agent reacquisitions (1,694 chars).
  * **Refined (amendment 5):** both are `edit_precondition_associated`, i.e. the
    Implementer read each file immediately before editing it, which Claude Code's
    Edit tool requires. That leaves 0 discretionary.
  * **Communication duplication:** the Investigator's report quoted 36 repository
    chunks (~1,770 chars, 31% of the report); 21 chunks (~1,269 chars) were both
    carried in the handoff and re-acquired by tool.
  * **This is one A/B pair, n=1.** It is an observation, not a result, and
    supports no general claim.
* Second controlled fixture `cart_invoice_rounding` is prepared and validated
  without Claude, but has **not been run**. When authorised, run ONE Arm A and
  ONE Arm B with:

  ```bash
  python runner.py smoke --task cart_invoice_rounding --arm both
  ```

### Bash permission policy (resolves the earlier blocker)

The first real Arm A run could not execute the task's tests: two `python -m
pytest` calls were refused (`system/permission_denied`,
`decision_reason_type: "asyncAgent"`). Read-only Bash in the same run was fine,
so the only missing capability was running the test suite.

Fixed with a **narrow allowlist**, not `bypassPermissions`
(`config.BASH_TEST_ALLOWLIST`, policy id `narrow_pytest_v1`):

```text
Bash(python -m pytest *)     wildcard matching (current form, per --help)
Bash(python -m pytest)       exact, no arguments
Bash(python -m pytest:*)     prefix matching (legacy form) - same family
```

Three spellings of exactly one permission. `--allowedTools` rules are not
validated at startup — malformed rules are accepted silently and simply fail to
match — so all three supported spellings are supplied.

Deliberately still denied: bare `Bash`, any bare interpreter prefix
(`Bash(python *)`), `python -c`, the `pytest` console script, and everything
else. `--permission-prompts none` is retained, so anything outside the allowlist
is denied automatically and still instrumented as `tool_denied`.

The policy is applied identically to every session in both arms and is recorded
in `metadata.json.permission_policy`, `metadata.json.effective_config`, and
`config_hash`. Runs sharing a `config_hash` were configured identically.

Validated by `runs/20260910T125941Z_palindrome_punctuation_A_r1`:
`cd "<ws>" && python -m pytest -q` executed, Claude received `6 passed in
0.02s`, a later `python -c` was still denied with the CLI naming the offending
sub-command, coverage 1.000, held-out verifier 12 passed.

Note: the 6 visible tests pass before and after the fix, so Claude observed a
passing suite on its own edited code but the visible suite alone does not
establish correctness — only the 12-test held-out verifier does.

### Subscription quota

The real run's `rate_limit_event` reported **`seven_day` utilization 0.85** with
`isUsingOverage: false`. The planned pilot is 72 runs. At 85% of the weekly
window, a pilot launched now would likely exhaust it and — because
`hasExtraUsageEnabled` is true on this account — continue as paid overage. Check
the latest `rate_limit_telemetry` in a fresh run's report before scaling up.

## 13. Stack

Python 3.11+ standard library only — `subprocess`, `sqlite3`, `json`,
`dataclasses`, `hashlib`, `threading`, filesystem — plus `git` via subprocess,
the Claude Code CLI, and `pytest` for development. No `anthropic` SDK, no
LangGraph/CrewAI/AutoGen, no HTTP client, no framework.
