# Final research report: measuring the overhead of natural-language multi-agent coding

**Scope:** Stage 0 → Stage 0.5 → Stage 1, completed 2026-09-11.
**Status:** experimental phase complete. Decision: stop multi-agent optimization
for this experiment.

This report is self-contained. `PREREGISTRATION.md` and `SPEC.md` hold the
frozen definitions it summarizes. Every number below comes from preserved raw
telemetry through the committed analysis code, and every stored result record
reproduces byte for byte from the raw logs.

---

## 1. Original motivation

The starting hypothesis was that conventional multi-agent coding systems waste a
substantial amount of context in three ways:

* **natural-language communication**: agents describe to each other, in prose,
  what they have found, often pasting source excerpts;
* **repeated repository acquisition**: a downstream agent reads and searches the
  files an upstream agent already read;
* **isolated agent state**: every agent is a fresh session that rebuilds its own
  context from scratch.

Several optimization directions were considered:

1. structured (typed) handoffs instead of prose;
2. artifact references: pointing at a file, symbol or result instead of
   re-sending or re-reading it;
3. a compact semantic language or "Agent IR" for inter-agent messages;
4. shared state or context between agents;
5. eventually, latent or KV-cache-level communication between model instances.

The project deliberately **measured before implementing** any of these. Each
direction is substantial engineering. The question was whether the waste it
targets is large enough to justify building it.

## 2. Research questions

The question narrowed as the evidence came in:

| Stage | Question |
| --- | --- |
| Initial | Can communication between agents be compressed? |
| Stage 0 | How much multi-agent overhead is actually attributable to coordination, as opposed to simply running more agents? |
| Stage 0.5 | Where does the extra cost of a multi-agent arm come from: session fanout, handoffs, rereading, or useful extra work? |
| Stage 1 | Is the dominant component, session/context fanout, actually removable by preserving the Worker's context across the Investigator → Implementer transition? |

## 3. Experimental architecture

### Arms

* **Arm A (single agent):** one Claude Code session receives the task and has
  the full tool pool (Read, Grep, Glob, Edit, Write, Bash).
* **Arm B (conventional natural-language multi-agent)**, with a fixed topology:

  ```text
  Coordinator (fresh session, no repository tools)
    → Investigator (fresh session, read-only: Read/Grep/Glob/Bash; Edit/Write/NotebookEdit disallowed)
    → Coordinator (resumed)
    → Implementer (NEW fresh session: full tool pool)
    → Coordinator (resumed)
  ```

  The Investigator and Implementer run in separate physical Claude Code sessions.
  The Implementer receives the task statement, the Coordinator's instruction and
  the forwarded Investigator report. Every handoff is plain prose, stored
  verbatim.
* **C1 (`C1_shared_worker_context`, Stage 1):** the same three logical roles and
  the same five CLI invocations, but the Investigator's session is resumed as the
  Implementer's session:

  ```text
  Coordinator (fresh) → Worker/Investigator (fresh, read-only) → Coordinator (resumed)
    → SAME Worker session resumed as Implementer (full tool pool) → Coordinator (resumed)
  ```

  The resumed Worker receives only the Coordinator's implementation instruction.
  The task statement and its own Investigator report are already in its session
  history, so the report is **not** forwarded back and the task is not resent.
  The result is 2 fresh physical sessions instead of 3.

### Harness

* **Execution:** only the locally installed **Claude Code 2.1.260** CLI, pinned
  by path for every run, in `-p --output-format stream-json --verbose` mode.
* **Model:** alias `sonnet`, which resolved to `claude-sonnet-5` in every run.
  Claude Code also made small auxiliary `claude-haiku-4-5` calls, which are
  recorded separately.
* **Billing:** subscription authentication only (`authMethod = claude.ai`,
  `apiKeySource = none`, checked per session). No Anthropic API key, no API
  billing, no paid overage. Seven-day quota telemetry was read from the stream
  and never entered overage.
* **Permissions:** `--permission-mode acceptEdits`,
  `--permission-prompts none`, and a narrow allowlist (`narrow_pytest_v1`) that
  permits only `python -m pytest` in Bash. Everything else that needs approval is
  denied and instrumented. The policy is identical in every arm.
* **Tasks:** deterministic Git fixture repositories, each copied into an
  isolated workspace at a pinned base commit. The visible tests are read-only.
* **Correctness:** a held-out verifier, injected only after the agent phase and
  run on its own (`--noconftest`). SOLVED is its exit code and nothing else. No
  LLM judge.
* **Telemetry:** the raw CLI stream (JSONL) for every session, a normalized event
  log, invocation records written before each process starts, and per-message
  provider usage (uncached input, cache read, cache write/creation, output). A
  derived SQLite index is always rebuilt from the raw logs.
* **Reconstruction gate:** every harness input (argv, stdin prompt, tools,
  session id) can be rebuilt from the logs and diffed against the stored record.
  Claude Code's hidden system prompt is not observable, and nothing claims it is.
* **Analysis:**
  - acquisitions are classified per tool call: structured Read/Grep/Glob, plus
    Bash commands parsed into read, search, listing, git inspection, test runs
    and so on;
  - temporal availability K(t) is strict: a producer must end before the
    consumer starts, and parallel calls are grouped;
  - content overlap uses 3-line shingles (threshold 0.60);
  - a reread counts as *primed* when a delivered message named the target;
  - held-out leakage detection checks both paths/markers and content.

## 4. Instrumentation amendments (chronological, all disclosed)

| # | Defect or change | Effect | Raw data changed? | Rerun? | Valid conclusions changed? |
| --- | --- | --- | --- | --- | --- |
| 1 | Coverage formula v1 counted non-acquisition and denied Bash calls, and a trailing `\| sort` made a listing unclassifiable | First Arm A coverage 0.750 → 1.000 on re-analysis | No | No | None: no comparison existed yet |
| 2 | Permission-denied tool calls were counted as completed work (tests, acquisitions) | New `tool_denied` category, excluded from counts | No | No | None |
| 3 | The first Arm A run could not execute tests under `--permission-prompts none` | Narrow pytest allowlist added to every arm (a config change, recorded in `config_hash`) | No | Yes: a new Arm A run; the first run is superseded, not deleted | None: made before any Arm B data |
| 4 | K(t) ordered parallel tool calls by raw stream line | Concurrency grouping by API message | No | No | None: both Arm A runs had zero overlap findings |
| 5 | After Pair 1: both "duplicate reads" were reads the Edit tool requires | Reacquisition split into subcategories under the preserved gross count; communication-duplication metrics added. Disclosed as post-hoc; the verification rule was narrowed to exclude test-outcome sentences | No | No | Pair 1's gross count unchanged; its 2 primed rereads both classify as edit-precondition-associated |
| 6 | Held-out files are reachable outside the workspace (no filesystem isolation) | Detection-only isolation check, plus an exclusion rule; made before Pair 2 | No | No | None: 0 flags on all runs |
| 15 (Stage 0.5, prospective) | Pair 2 showed the v1 handoff-overlap metric misses excerpts with omitted lines; the content leakage check was also missing | `handoff_repository_overlap_v2`, overhead decomposition, held-out content check, all labelled post-hoc on older runs | No | No | Pair 2's preregistered H3 is still evaluated with v1 |
| 7 | **Turn-limit bug:** the harness counted every assistant *stream line* (one per content block) as a "turn" | **Task 4 Arm A, first attempt (`20260911T014415Z_settings_list_fields_A_r1`), was killed after 11 real model turns and is INVALID.** Fix counts distinct API messages; the limit (25) is unchanged; `config_hash` changed | No | Yes: Task 4 rerun as a fresh pair (`--repeat-id 2`), user-authorized. The invalid attempt is preserved and listed | None: every earlier valid session also completes under the fix (asserted). Under the old rule, 3 of the 5 Stage-0.5 Arm A attempts exceed the limit: the killed first Task-4 attempt (26 stream lines), plus the valid Task-4 rerun (37) and Task-5 run (40) |
| 8 | **Shell parser bug:** `2>&1` was split at `&`, and `python --version` had no category | Task 4 Arm B rerun (`…_B_r2`) scored coverage 0.700 (invalid) under v1, 1.000 under v2 (re-analysis of unchanged raw data, user-authorized, disclosed) | No | No | Only that run changed; no other run contains either command form (asserted) |
| Stage 1 | Resume chains in the decomposition were keyed by agent id | Keyed by physical session (the `--resume` target), which C1 requires | No | No | None: all six Stage-0.5 decompositions reproduce exactly (asserted) |

Nothing was silently changed. Every amendment has a dated entry in
`PREREGISTRATION.md`, tests pin each frozen definition, and each amendment that
changed code has its own commit.

## 5. Dataset

18 real runs in total: 58 Claude Code sessions, all subscription-billed. Every
raw artifact is SHA-256-checksummed in `run_manifests/` (four manifests, 520
files), and all checksums verify.

**Six valid A/B pairs:**

| Task | Shape | Arm A run | Arm B run | Notes |
| --- | --- | --- | --- | --- |
| palindrome_punctuation | cross-file bug | `20260910T125941Z_…_A_r1` | `20260910T230448Z_…_B_r1` | Pair 1; `…122445Z_…_A_r1` superseded by amendment 3 |
| cart_invoice_rounding | cross-file bug | `20260911T004656Z_…_A_r1` | `20260911T004825Z_…_B_r1` | Pair 2 (hypotheses H1–H5 preregistered) |
| shipping_inch_dimensions | cross-file bug | `20260911T014034Z_…_A_r1` | `20260911T014126Z_…_B_r1` | Stage 0.5 |
| settings_list_fields | cross-file feature | `20260911T015555Z_…_A_r2` | `20260911T015737Z_…_B_r2` | Stage 0.5; repeat 1 Arm A is invalid (amendment 7) |
| rename_max_connections | repository-wide refactor | `20260911T020645Z_…_A_r1` | `20260911T020834Z_…_B_r1` | Stage 0.5 |
| sla_weekend_hours | cross-file bug, misleading symptom | `20260911T021224Z_…_A_r1` | `20260911T021334Z_…_B_r1` | Stage 0.5 |

**Four B-vs-C1 comparisons**, one for each Stage-0.5 task above, with C1 runs
`20260911T030211Z`, `…030417Z`, `…030731Z` and `…031118Z`.

**n = 1 run per arm per task.** All results are descriptive. Means and medians
summarize six (or four) single observations. No statistical claims are made,
and run-to-run variance is unknown.

## 6. Stage 0 result: apparent duplication was mostly tool mechanics

In Pair 1 the Implementer reread both files the Investigator had read and named.
At first sight that was exactly the waste the project set out to remove. But both
rereads were of files the Implementer then edited, and Claude Code's Edit tool
refuses to modify a file that has not been Read in the current session. A read
the tool demands is not evidence of communication failure. So the gross count was
kept and partitioned mechanically:

* `gross_primed_reacquisition`: inter-agent, previously available, overlapping,
  primed;
* `edit_precondition_associated`: the first Read of a file later edited
  successfully in the same session;
* `verification_associated`: a reread after editing, or on an explicit
  verification request;
* `discretionary_information_reacquisition`: none of the above, the true
  candidate for avoidable rediscovery;
* `unknown`: ambiguous cases, preferred over speculation.

Pair 2 was designed with supporting files to give discretionary rereads a
chance to appear. They did: 2 discretionary rereads (`discounts.py`, `money.py`,
2,996 chars), alongside 1 edit-precondition reread. All five preregistered
hypotheses were supported, but every effect was small.

## 7. Stage 0.5 result: where the multi-agent overhead comes from

**Outcomes.** Both arms solved all six tasks. Arm B used more resources on
every task. There was no observed success advantage for Arm B.

| B/A ratio (six valid pairs) | Mean | Median | Range |
| --- | --- | --- | --- |
| Total input (main model) | **1.84** | **1.77** | 1.08 – 2.94 |
| Output | **3.39** | 3.39 | 2.16 – 4.85 |
| Wall time | **3.27** | 3.42 | 2.06 – 4.31 |
| API-equivalent cost (not the amount paid) | **2.84** | 2.83 | 1.96 – 3.68 |

**Decomposition of the B − A input delta.** Figures are context-weighted token
attribution. They are reconstructed from exact per-call usage and estimated text
sizes, and are *not* exact, mutually exclusive token accounting. The categories
are not forced to sum to the delta.

* **Discretionary reacquisition: small and task-specific.** Across six tasks
  there were 15 primed rereads: 12 edit-associated and 3 discretionary.
  Discretionary rereads appeared on only 2 of 6 tasks, at most about 3% of the
  delta. The Implementer never acquired informational content the Investigator
  lacked (0 chars on all six).
* **Natural-language handoff: moderate and secondary.** Handoff text was about
  11–27% of the delta on five tasks; on the refactor, the reconstructed rows
  overshoot its small delta. The Investigator report is forwarded verbatim, so it
  is sent twice and also carried in the Coordinator's resumed history. It often
  includes source excerpts: the repository-quote fraction was 0.02–0.24.
* **Session/context fanout: the largest measured component.** Every fresh
  session re-sends an estimated ~14.7k-token hidden base (system prompt plus tool
  schemas). Each additional session re-submits its own starting context on every
  API call. The median reconstructed share was about **0.82** of the B − A input
  delta (range 0.65–1.11). It was larger than handoff plus reacquisition on 5 of
  6 tasks. Most of this volume is cache reads, which are billed at a lower rate
  than fresh input.

## 8. Stage 1 result: C1 shared Worker context

What C1 changed, as intended:

* **Correctness:** C1 solved 4/4.
* **Tool policy on resume:** the change works in real Claude Code on all four
  runs. The Investigator phase ran read-only; the resumed Implementer phase had
  Edit and Write; the session id was preserved.
* **Physical sessions:** 3 → 2. Fresh-session first-call context fell by
  **42.6–45.6%** (exact).
* **Forwarded report:** it disappeared, avoiding 3.6k–8.7k chars per task.
  Total handoff text fell by a median of 25.6%.
* **Rereads:** repository rereads fell sharply, with gross primed rereads 10 → 3.
  Edit accepted the Investigator-phase Read after resume: on two tasks the
  Implementer edited files without rereading them, and no Edit was refused.

But overall cost did not fall:

| C1 vs B, four tasks | Pooled | Median C1/B ratio |
| --- | --- | --- |
| Total input | **+7.1%** | **1.099** |
| Cache write/creation | **+11.1%** | **1.084** |
| API-equivalent cost (not the amount paid) | **+4.7%** | **0.997** |
| Wall time | **−5.0%** | **0.949** |

Per task, C1/B input was 1.13, 0.70, 1.07 and 2.17. Cache write was higher on
all four tasks.

**Mechanism** (observed in exact per-call usage):
- the resumed Worker's first Implementer-phase call read the same 11,393 cached
  tokens as a fresh B Implementer, which is the common base prefix;
- it then *wrote* 12,930–16,470 new cache tokens, against 8,338–11,077 for B's
  fresh Implementer;
- changing the tool set and system appendix at the role transition means the
  retained Investigator history had to be written into the cache again;
- the resumed session then carried that longer history into every later call.

Removing a fresh session therefore moved cost into a larger, re-cached resumed
context instead of eliminating it.

## 9. Hypotheses the data did not support

* **"Duplicate repository search/reading is the dominant problem."** Not
  supported. Most rereads were required by the Edit tool. Discretionary rereads
  were rare (3 of 15), small, and on 2 of 6 tasks only.
* **"Artifact references are the highest-priority optimization."** Not supported
  for this workload. The rereads they would remove are a small share of the
  overhead.
* **"Preserving Worker session/context will materially reduce overall cost."**
  Not supported by C1. Total input, cache writes and cost did not fall overall.
* **"Compressing natural-language handoffs alone would remove most of the
  overhead."** Not supported. Handoffs are a secondary component, and removing
  the forwarded report in C1 did not reduce total cost.
* **"Multi-agent decomposition improves task success on these workloads."** Not
  observed. The single agent solved all six tasks.

## 10. What did survive

* Independent sessions carry measurable overhead: every fresh session brings a
  large base context.
* Verbose handoffs duplicate some information: forwarded reports and quoted
  source excerpts.
* A shared session does reduce rereads and fresh-session context.
* Architectural changes can move costs rather than eliminate them.
* Actual provider cache behaviour has to be measured before assuming that
  context reuse saves tokens. Changing tools or system text mid-session changes
  what can be served from cache.

## 11. Final conclusion

For the tested small-to-medium deterministic coding tasks, conventional
natural-language multi-agent decomposition introduced substantial resource
overhead (median 1.8× input, 3.4× output, 3.4× wall time, 2.8× API-equivalent
cost) without improving task success.

The coordination waste initially suspected, repeated repository acquisition and
verbose handoffs, was measurable but not large enough to explain most of the
cost. Session/context fanout was the largest measured component. A
shared-worker-session experiment reduced fresh-session duplication, but did not
reduce total input or cache creation.

The optimization opportunity therefore appears substantially smaller than the
original architecture hypothesis suggested. This conclusion is limited to the
six tested tasks, one run per arm, Claude Code 2.1.260 and a single model.

The main contribution is negative in the useful sense. Measuring first
eliminated several plausible optimization directions before substantial
implementation effort was spent on them: artifact references, Agent IR, compact
handoffs, and shared-context or KV-level reuse built on top of this workflow.

## 12. Decision

**STOP MULTI-AGENT OPTIMIZATION FOR THIS EXPERIMENT.**

This means:

* no C2 (deeper shared-prefix or context reuse);
* no Agent IR or compact semantic language;
* no artifact-reference protocol;
* no KV-cache or latent-communication system.

These should not be started without new external evidence, or a new workload,
showing a success or quality benefit from multi-agent decomposition. Without
such a benefit, reducing multi-agent overhead only brings multi-agent closer to
the single-agent baseline it already failed to beat.

## 13. Possible future research (separate from the conclusions)

The following would be **new experiments**, not continuations justified by the
current data:

* harder tasks where single agents sometimes fail, so that decomposition has
  something to gain;
* heterogeneous specialist models instead of same-model role decomposition;
* local inference stacks that expose real KV-prefix control, rather than
  inferring cache behaviour through a CLI;
* quality and reliability objectives (independent review, error detection)
  rather than pure token or cost efficiency;
* long-horizon tasks where parallelism may matter.

---

*Reproducibility:* `git log` holds every freeze and amendment commit. From the
repository root, `python runner.py ingest && python runner.py summary && python
runner.py stage1-summary` rebuilds the index and summaries from raw telemetry.
`sha256sum -c` on the four `run_manifests/` files verifies the raw data. The
local test suite (645 tests) runs without launching Claude.

## Addendum (2026-09-11): the scope of this conclusion

Stage 0–1 characterized overhead in tasks where Single Strong already achieved
ceiling performance. Stage 2 investigates whether the cost–quality tradeoff
changes when that ceiling is removed (PREREGISTRATION section 19). The
conclusions above are unchanged.
