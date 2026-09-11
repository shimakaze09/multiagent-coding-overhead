# Stage 0 — preregistration

Committed before real data collection. Written while the spawned Claude Code CLI
was unauthenticated, so **no Arm A or Arm B observation had been made** when
these choices were fixed. Amendments must be recorded in §10 with a date and a
reason, not by editing the text above them.

---

## 1. Question

> How much extra information acquisition in a conventional natural-language
> multi-agent coding system was information another agent had already acquired
> **before** the later agent went looking for it?

## 2. Design

Within-task comparison. Every task is attempted by both arms from the identical
pinned base commit, with the identical held-out verifier.

```text
12 tasks  x  2 arms (A, B)  x  3 repetitions (repeat_id 1..3)  =  72 runs
```

* **Arm A** — one Claude Code session.
* **Arm B** — three logical agents, fixed topology
  Coordinator → Investigator → Coordinator → Implementer → Coordinator.
* **Arm C is not part of Stage 0** and will not be run or reported here.

Repetitions are independent re-runs identified by `repeat_id`. There is no model
seed: the installed Claude Code exposes none, so runs are not expected to be
byte-identical and no such claim will be made.

Model, effort and limits are fixed across arms within a task and recorded in
`metadata.json`. The same model is used for every agent in both arms.

### Task mixture (fixed in advance)

| Count | Category | Purpose |
| --- | --- | --- |
| 4 | `cross_file_feature` | cross-file feature work |
| 4 | `cross_file_bug` | symptom and root cause in different files |
| 2 | `repo_wide_change` | repository-wide change/refactor |
| 2 | `single_file_control` | metric-behavior control |

Only `palindrome_punctuation` (`cross_file_bug`) exists so far; it was authored
to validate the harness. The remaining tasks will be authored before the pilot
and will not be revised after any pilot run begins.

## 3. Primary outcomes

Reported per run, and compared between arms within a task:

1. **Success** — the held-out deterministic verifier exits zero. No LLM judge.
2. **Information acquisition volume** — completed acquisition tool calls, reads,
   searches, returned chars/bytes, unique repository regions (3-line shingles).
3. **Coordination redundancy (Arm B)** — inter-agent duplicate acquisitions that
   were `globally_previously_available` under K(t), split into `primed` and
   `unprimed`.
4. **Resource use** — Claude sessions, agent turns, wall time.
5. **Handoff volume** — exact chars, UTF-8 bytes, words, plus `estimated_tokens`
   (labelled as an estimate, never as billed tokens).

Secondary and opportunistic, recorded with explicit `availability`: provider
reported input/output/cache tokens, `total_cost_usd` (as an API-equivalent
figure, not an amount paid), `duration_api_ms`.

## 4. The rules that will not be changed after seeing data

These are the choices most vulnerable to being tuned toward a desired result, so
they are fixed now.

**Temporal availability.**

```text
available(producer, consumer)  <=>  producer.end < consumer.start
```

Ordering key `(session_index, line_no)`. Producer and consumer must both be
completed acquisitions. An incomplete acquisition never becomes available.

**Overlap threshold.** A consumer acquisition overlaps a producer when the
producer's returned content is byte-identical, or when
`|producer_shingles ∩ consumer_shingles| / |consumer_shingles| >= 0.60`.
Shingles are 3-line windows over non-empty, prefix-normalized lines.

**Priming.** `PRIMED` iff a message delivered to that agent before the
acquisition started contained the acquisition's full path, its basename with
extension, the exact search query, or an identifier of >= 5 characters from the
query. A bare filename stem does not count.

**Attribution.** `potentially_avoidable_acquisition` requires
`globally_previously_available` **and** a different `agent_id`. Intra-agent
repetition is reported separately and is never counted as coordination
redundancy.

**Oracle upper bound.** Counts primed inter-agent repetition only, in physical
units. Unprimed independent discovery is excluded.

**Observability gate.** A run's duplication result is eligible for the headline
comparison only if `acquisition_coverage >= 0.90` and `unknown_tool == 0`.
Ineligible runs are still reported, flagged `low_observability`, and excluded
from headline aggregates. Their count and share will be stated.

**Arm validity.** A run whose `subagent_stats.spawned` is non-zero in any
session has an invalid arm label and is excluded from the arm comparison. Its
count will be stated.

**Billing validity.** A run in which any session's `apiKeySource` indicates
API-key billing is excluded and reported as `api_suspected`.

## 5. Exclusions, fixed in advance

A run is excluded from the arm comparison if any of:

* a session terminated as `turn_limit_exceeded`, `wall_limit_exceeded`, or
  `spawn_failed`;
* any session reported `authentication_failed` or a usage-limit condition;
* `arm_label_valid` is false;
* `api_charge` is `api_suspected`;
* the harness-input reconstruction gate fails for that run.

Excluded runs are reported with their exclusion reason. Exclusions will not be
introduced after the fact.

## 6. Statistical treatment

The pilot is small (12 tasks, 3 repetitions). Therefore:

* Effects are reported as **paired per-task differences and ratios** (Arm B minus
  Arm A, and Arm B / Arm A), with per-task medians across repetitions.
* Distributions are summarized with medians and full ranges, not means alone.
* No p-values or confidence intervals will be presented as inferential claims at
  n=12; any interval shown is descriptive.
* No arbitrary percentage will be treated as a scientific threshold.
* Success rate is reported as a count out of 36 per arm, not as a significance
  test.

## 7. Predictions

Stated so that being wrong is visible.

1. Arm B performs strictly more information acquisition than Arm A on cross-file
   tasks (more reads, more returned bytes). **Confidence: high.**
2. A substantial share of Arm B's *extra* acquisition is
   `globally_previously_available` and `primed`. **Confidence: moderate** — this
   is the actual open question.
3. Arm B's success rate is not materially higher than Arm A's on these tasks.
   **Confidence: low-moderate.**
4. Single-file control tasks show *less* cross-file exploration, search breadth
   and repository rediscovery than deliberately cross-file tasks — **not** zero
   duplicated reads. Two agents may naturally read the same file; the control
   validates metric behavior, not a zero-overlap assumption. **Confidence: high.**
5. Repeated *searches* are rarer than repeated *reads*, because search results
   are more often summarized into the handoff. **Confidence: low.**

## 8. Decision rule

At the end of Stage 0, in this order:

1. Does Arm B materially outperform Arm A in success?
2. How much extra repository acquisition does Arm B perform?
3. Of that extra acquisition, how much was already globally available?
4. How much was primed versus genuinely independent?
5. Is the repeated information concentrated enough to reference cheaply?
6. Is it expressible as file/range/artifact handles?
7. Is telemetry coverage high enough to trust the result?

Outcomes:

* **CONTINUE** — multi-agent is worth using at all, primed inter-agent repetition
  is a non-trivial and concentrated share of Arm B's extra acquisition, and it is
  expressible as references. Explicit shared state / artifact references have
  enough room to matter.
* **NARROW** — only precise artifact references look worthwhile; larger protocol
  work is not justified.
* **STOP** — observed coordination redundancy is too small to matter, **or**
  multi-agent provides no benefit over the single agent, **or** telemetry
  coverage is too poor to support a conclusion.

The decision will be recorded with the numbers that produced it. "Too small" and
"concentrated" will be reported as the measured quantities, not as pass/fail
against a number invented afterwards.

## 9. Known limitations accepted in advance

1. Information identity is at returned-content granularity; no read ranges exist.
2. Arbitrary `Bash` can acquire content opaquely; measured as
   `unclassified_bash_acquisition` and gated, not assumed away.
3. Context residency is Level 2 at best; the hidden system prompt is unobservable.
4. Turn limiting is harness-side and approximate at the boundary.
5. `total_cost_usd` under a subscription is an API-equivalent figure.
6. `seq` does not establish causal order for events concurrent inside the CLI.
7. Arm B's topology is fixed and hand-built. Results describe *this* topology,
   not multi-agent coding in general.
8. One model, one machine, small repositories, 12 tasks. Stage 0 is a pilot; it
   cannot support a general claim.
9. The mock CLI (`tools/mock_claude.py`) exists solely to validate
   instrumentation. Its output is never a result.

## 10. Amendments

### Amendment 1 — 2026-09-11 — acquisition_coverage formula v1 -> v2

**Prompted by:** the first real authenticated Arm A run,
`20260910T122445Z_palindrome_punctuation_A_r1`. No Arm B data existed, and no
comparison had been made, when this amendment was written.

**What changed.** The coverage denominator was redefined from "every
acquisition-capable Bash call" to "tool operations that could plausibly have
delivered repository/task information to the model":

```text
v1: acquisition_coverage = attributable / (attributable + unclassified_bash + unknown_tool)
    where non-acquisition Bash was ALSO capable of landing in the denominator

v2: acquisition_candidates = acquisition_classified + acquisition_unknown
    acquisition_coverage   = acquisition_classified / acquisition_candidates
    non-acquisition and permission-denied calls excluded from both
```

**Why, in terms independent of the run.** v1 conflated two different things:
"we could not classify this operation" and "this operation was not acquisition".
A `pytest` invocation, a `mkdir`, or a call the CLI refused cannot deliver
repository content, so counting them let unrelated shell activity depress a
number that is supposed to measure how much *acquisition* is attributable.
`bash_unknown` remains in the denominator, so the metric still fails loudly when
acquisition really is opaque.

**Effect on the run that prompted it.** Coverage went 0.750 -> 1.000. Two
contributions, both independent of the formula:

1. A trailing `| sort` made `cd <ws> && find . -type f | sort` unclassifiable.
   `sort` is a stdin filter that acquires nothing; it should never have decided
   the category. Fixed in the classifier, not the metric.
2. Two `pytest` calls were permission-denied and never ran. One had been counted
   as a completed test run and one as an opaque acquisition.

**Guard against motivated reasoning.** `tests/test_real_arm_a.py::
test_the_coverage_change_is_not_merely_a_denial_loophole` recomputes this run's
coverage *as if the denied calls had executed*: it is still 1.000, because both
were verification commands either way. So the pass does not depend on the denial
exclusion. A companion test
(`test_an_undenied_opaque_bash_still_lowers_coverage`) asserts that a genuinely
opaque command still drives coverage down.

**Not changed by this amendment:** the 0.90 minimum, the `unknown_tool == 0`
requirement, the temporal-availability rule, the overlap threshold, the priming
definition, the oracle-bound definition, the exclusion list, or any prediction in
section 7.

### Amendment 2 — 2026-09-11 — permission-denied tool calls

Denied calls are now their own category (`tool_denied`) and are excluded from
acquisition candidates, from read/search counts, and from test-run counts, with
`attempted_class` recording what they would have been. Claude Code's own denial
message states "The action was NOT performed", so counting a denied call as work
performed was simply wrong. This also means a run's `test_runs` may be 0 while
the agent tried to run tests — see SPEC.md section 16.1, which is an open
validity issue blocking Arm B.

### Amendment 3 - 2026-09-11 - narrow Bash permission allowlist

**Prompted by:** the first real Arm A run being unable to execute the task's
tests. Still no Arm B data, and no comparison made.

**Change.** Every session in both arms now receives
`--allowedTools "Bash(python -m pytest *)" "Bash(python -m pytest)"
"Bash(python -m pytest:*)"` - three spellings of one permission, policy id
`narrow_pytest_v1`. `--permission-mode acceptEdits` and
`--permission-prompts none` are unchanged, so everything outside the allowlist is
still denied and still instrumented. `bypassPermissions` is not used.

**Why this is not a widening of the experiment.** The task statement asks the
agent to run the tests. Without this the agent could not, and both arms would
have been measured while unable to execute code. The allowlist restores exactly
the capability the task assumes and nothing else: no bare interpreter prefix, no
`python -c`, no `pytest` console script, no arbitrary Bash.

**Recorded per run:** `metadata.json.permission_policy`,
`metadata.json.effective_config`, and `config_hash` (a BLAKE2b digest over the
full effective configuration, including the allowlist). Two runs sharing a
`config_hash` were configured identically, which is how Arm A/Arm B parity will
be checked.

**Validated by** run `20260910T125941Z_palindrome_punctuation_A_r1`: pytest
executed, Claude received `6 passed`, the excluded `python -c` was still denied,
coverage 1.000, held-out verifier 12 passed.

**Not changed:** the coverage minimum, the temporal-availability rule, the
overlap threshold, the priming definition, the oracle bound, the exclusion list,
the arms, the topology, or any prediction in section 7.

### Amendment 4 - 2026-09-11 - concurrency grouping for temporal availability

**Correction, not a design change.** Claude Code emits one stream event per
content block, so tool calls issued together in one API assistant message occupy
consecutive stream lines. The ordering key used the raw line, which would make
simultaneous calls look sequential and could attribute one to another under
K(t). Acquisitions now carry `api_message_id` and `concurrency_group_line`, and
`start_key` uses the group line.

Real runs show 5 and 3 parallel `Read`s respectively, so this case is not
hypothetical. Neither stored Arm A run's reported numbers change (both had zero
overlapping findings), but the fix matters for Arm B, where inter-agent
attribution is the measurement of interest.

Also corrected: this document previously claimed
`assistant_stream_events - thinking_only_stream_events == cli_reported_num_turns`.
That fitted the first run by coincidence and fails on the second. The
relationship that holds on all three real runs observed is
`num_turns == tool_use_blocks + 1`, recorded as an empirical fit only; no
analysis depends on it.

### Amendment 5 - 2026-09-11 - reacquisition subcategories, communication duplication, fixture 2

**Written AFTER seeing the first real A/B pair.** It is prompted by a confound
that pair exposed, so it is disclosed as post-hoc. The preregistered measures in
section 4 are unchanged, and every number they produce is still reported.

**The confound.** Both primed reacquisitions in
`20260910T230448Z_palindrome_punctuation_B_r1` were Reads of files the
Implementer then edited. Claude Code 2.1.260's Edit tool refuses a file that has
not been Read in the current session ("File has not been read yet. Read it first
before writing to it."). A read the tool demands is not evidence of
communication or reasoning redundancy.

**1. Subcategories under the gross count.** `primed_reacquisition` is kept as the
gross measurement. Every primed inter-agent reacquisition is placed in exactly
one of the following, decided mechanically from the consumer's own session
(`metrics.classify_reacquisition`):

| Subcategory | Rule |
| --- | --- |
| `verification_associated` | re-read of a file this agent had already successfully edited in this session, OR the target is named in a sentence of a delivered message that contains an explicit verification verb and is not about test outcomes (if it is, e.g. "confirm every test still passes", the read is `unknown`) |
| `edit_precondition_associated` | the FIRST Read of the file in this session, later followed in the same session by a successful Edit/Write of the same file, on a CLI version for which the rule is verified (`EDIT_REQUIRES_PRIOR_READ`) |
| `unknown` | e.g. the same-file edit failed or was refused; the CLI version is unverified; the read follows the agent's own test run; no attributable target |
| `discretionary_information_reacquisition` | primed, not edit-required, not attributable to verification; the strongest candidate for avoidable rediscovery |

Disclosure: the test-outcome exclusion was added while writing this amendment,
after checking what the verification-request rule did on this run once the
edits were removed. It matched both files, via "…just re-verify it still passes"
and "…confirm every test still passes". Those are requests to run tests, not
reasons to Read the file. The exclusion does not change the actual result,
because edit precedence decides both reads, and it moves those ambiguous
matches to `unknown`, not to a more favourable bucket.

`unknown` is always preferred to speculation. Subcategories partition the gross
count exactly (asserted in code). The new `discretionary_upper_bound` is
reported beside, not instead of, the preregistered `oracle_upper_bound`.

**2. Communication duplication.** For every handoff: repository content chunks,
the sender's own tool-output chunks, and relayed chunks from what it had
received. Measured with indentation-insensitive 3-line windows that must carry
at least 16 alphanumeric characters over at least 2 content lines, so syntax-only
runs cannot count as duplication. The preregistered `shingles` are untouched.
Chunk overlap is primary; characters are an estimate (stripped length of the
covered lines).

**3. `handoff_reacquisition_overlap`.** Chunks in the Investigator's
acquisitions, those also copied into handoffs to the Implementer, those also
re-acquired by the Implementer, and the intersection of all three. Reported
separately from ordinary inter-agent reacquisition.

**Re-analysis of the existing run (no new Claude session, raw artifacts
byte-identical):**

```text
gross primed reacquisitions        2   (unchanged)
  edit_precondition_associated     2   (1,694 chars, 36 chunks)
  verification_associated          0
  discretionary                    0
  unknown                          0
investigator acquired              3,618 chars, 80 chunks
investigator report                5,633 chars; 36 repository chunks, all from its
                                   own tool output; quote fraction 0.314
all handoffs                       17,601 chars; repository quote fraction 0.233
handoff -> reacquisition overlap   80 investigator chunks, 36 copied into handoffs,
                                   36 re-acquired, 21 in all three (~1,269 chars)
```

**4. Fixture 2, `cart_invoice_rounding` (prepared, not run).** The symptom and the
only correct production change are in `shopcart/cart.py`. Understanding it needs
`discounts.py` (per-unit rounding contract), `invoice.py`, `money.py` and
`currency.py`, which are pinned by held-out regression tests and must not be
edited. A naive per-line rounding fix fails. The statement names no supporting
file, so any priming toward them can only come from the handoff. Rereads of
`cart.py` are candidates for the edit precondition; rereads of the supporting
files are candidates for discretionary reacquisition.

**5. Benchmark tests and SOLVED.** A task may declare `read_only_paths`. The
harness makes those files read-only after the base commit, in shared code,
identically for both arms, and reports any change under them. For fixture 2 the
held-out verifier runs alone with `--noconftest`, and SOLVED is its exit code
only. Visible tests are run by the harness separately and reported separately.
Fixture 1 is left unchanged for comparability: its verifier still runs the
held-out file together with the visible `tests/`.

**Not changed:** temporal availability, the overlap threshold, the priming
definition, the gross oracle bound, the coverage gate, the exclusion list, the
arms, the topology, the permission policy, `config_hash`, or any prediction in
section 7.

### Amendment 6 - 2026-09-11 - held-out isolation check, reporting only (BEFORE Pair 2)

**Written after the first A/B pair and BEFORE the second pair has been run.**
No classifier rule or metric definition changes.

**The gap.** The harness does not enforce filesystem isolation. A run workspace
lives at `stage0/runs/<run_id>/workspace`, so the held-out verifiers
(`tasks/holdout/`), `tests/test_fixture_*.py` (which contains a reference fix),
the task definitions and this document are three directory levels away. Whether
Claude Code 2.1.260 would read outside its working directory under
`--permission-prompts none` has not been checked, because checking it needs a
Claude session.

**The check** (`analysis/isolation.py`, version 1) is detection only. It
examines every tool input (Read/Edit/Write `file_path`, Grep/Glob `path` and
`pattern`, and path-like tokens in Bash commands, with `..` resolved against
the workspace and git-bash `/d/...` paths mapped to drives) and every tool
result for:

* any path that resolves outside the run workspace (`outside_workspace_accesses`);
* the markers `tasks/holdout`, `test_holdout_`, `test_fixture_`, `_reference_fix`,
  `preregistration`, matched case-insensitively (`held_out_or_reference_exposure`).

**New exclusion, fixed now:** a run with `breach_suspected = true` is excluded
from the arm comparison and reported with its evidence. POSIX-absolute paths
without a drive letter cannot be resolved on this host. They are listed as
`unresolved_posix_absolute_paths` and are not treated as breaches.

**Applied to the three existing real runs** (raw artifacts byte-identical to
`run_manifests/raw_run_checksums.sha256`): 0 outside-workspace accesses and 0
exposures in each.

Also added in this amendment, all descriptive and none a new headline metric:
per-agent acquisition in Arm B, a per-file breakdown of reacquisition for the
task's declared `supporting_paths` and for the file being fixed, the pair
parity checks, and the frozen pair summary. `cart_invoice_rounding` gains
`supporting_paths` (discounts.py, invoice.py, money.py, currency.py). That field
is used only by the breakdown. The fixture's code, tests, statement and verifier
are unchanged.

## 11. Frozen definitions v1 — 2026-09-11, before the second controlled pair

This section freezes every definition the second controlled pair will be
evaluated under. `tests/test_frozen_definitions.py` asserts each value in code.
A later change needs a version bump and a dated amendment. **Changes made after
the first pair and before the second pair:** amendments 4, 5 and 6 (all dated
2026-09-11 and disclosed above). Nothing will be changed after seeing Pair 2's
result, except as a disclosed, labelled exploratory analysis.

**Versions.** parser 4 · coverage formula 2 · reacquisition classifier 1 ·
handoff metrics 1 · isolation check 1 · event schema 1 · CLI wrapper 2.

**Configuration identity.** `config_hash = 22ce9b7e5249dd497ee7c4c0318216b4`.
Model alias `sonnet`. `--permission-mode acceptEdits`,
`--permission-prompts none`, permission policy `narrow_pytest_v1`
(`Bash(python -m pytest *)`, `Bash(python -m pytest)`, `Bash(python -m pytest:*)`).
Limits: 6 sessions per invocation, 25 turns per session (enforced by the
harness), 900 s per session. `config_hash` deliberately excludes the analysis
versions above, which are recorded separately in each report.

**Fixture identity.** `cart_invoice_rounding`, base commit
`a74bac3cf4e9df479992e572be68169d53aea697`, workspace tree
`47220ec9f6e894bc4410dd1ce11fd7175f029364`, read-only `tests/test_basic.py`.
Held-out verifier:
`python -m pytest --noconftest -p no:cacheprovider -q test_holdout_cart.py`.

**Temporal availability K(t).** `producer.end < consumer.start` (strict).
Ordering key `(session_index, concurrency_group_line)`, where
`concurrency_group_line` is the first stream line of the API message that issued
the tool call, so parallel calls in one message are never available to each
other. Both acquisitions must be completed.

**Overlap.** Byte-identical content, or
`|producer ∩ consumer| / |consumer| >= 0.60` over 3-line shingles.

**Priming.** Unchanged from section 4: a delivered message contains the full
path, the basename with extension, the exact query, or a query token of at least
5 characters.

**`gross_primed_reacquisition`** = inter-agent, `globally_previously_available`,
overlapping, primed acquisitions. It is partitioned exactly into:

1. `verification_associated`: a reread of a file this agent had already
   successfully edited in this session.
2. `edit_precondition_associated`: the first Read of the file in this session,
   followed later in the same session by a successful Edit/Write of the same
   file. Only applies on a CLI version listed in `EDIT_REQUIRES_PRIOR_READ`
   (currently `2.1.260` only).
3. `verification_associated`: the target is named in a sentence of a delivered
   message containing one of: verify, verifies, verified, re-verify, reverify,
   confirm, confirms, double-check, re-check, recheck, validate, make sure,
   check that, check whether, check if. If that sentence also contains pass,
   passes, passing, passed, fail, fails, failing, failed, pytest or test suite,
   it is a test-outcome request and the read is `unknown` instead.
4. `unknown`: the same-file edit failed or was refused; the CLI version is
   unverified; the read follows the agent's own test run; or there is no
   attributable target.
5. `discretionary_information_reacquisition`: everything else.

Precedence is the order above. `unknown` beats speculation.

**`handoff_repository_content_duplication`.** Per handoff: repository chunks,
the sender's own tool-output chunks, and relayed chunks. Windows are 3 lines,
indentation-insensitive, with at least 16 alphanumeric characters over at least
2 content lines. Chunk overlap is primary; characters are an estimate.

**`handoff_reacquisition_overlap`.** Chunks in the Investigator's acquisitions
(I), those also in handoffs to the Implementer (I∩H), those also re-acquired by
the Implementer (I∩R), and the intersection of all three (I∩H∩R).

**Supporting-file discretionary rereads.** The per-file breakdown above,
restricted to inter-agent file reads (Read tool or bash read) of the task's
`supporting_paths`.

**Pair parity.** The two runs of a pair are comparable only if they share the
task, `config_hash`, base commit, protected files, a single Claude Code
version, and resolved main model. A failed parity check is reported, and the
pair is not treated as a controlled comparison.

## 12. Second Controlled A/B Pair — Pre-run hypotheses

Dated 2026-09-11. Written before either run exists. The pair is Arm A and Arm B
on `cart_invoice_rounding`, one repetition each, under the frozen definitions in
section 11. With n = 1 per arm, every statement below is descriptive about this
pair, and no outcome generalises to other tasks. Every hypothesis is stated so
that either answer is a result, and a null result is reported with the same
prominence.

* **H1 — supporting-file rereads.** In Arm B, the Implementer reads at least one
  supporting file (`discounts.py`, `invoice.py`, `money.py`, `currency.py`) that
  the Investigator had already read and that a handoff named, and the frozen
  classifier puts at least one such read in
  `discretionary_information_reacquisition`. *Not supported* if the count is 0,
  including when such reads exist but classify as `unknown` or unprimed.
* **H2 — the file being fixed.** Implementer rereads of `shopcart/cart.py` that
  meet the gross definition classify as `edit_precondition_associated`, not
  `discretionary_information_reacquisition`. *Not supported* if any `cart.py`
  reread classifies as discretionary.
* **H3 — handoff duplication.** The Investigator's report contains repository
  content copied from its own tool output
  (`handoff_repository_content_duplication`: report repository chunks > 0). The
  size is reported as measured; no size is predicted.
* **H4 — handoff then reacquisition.** `handoff_reacquisition_overlap` has at
  least one chunk in I∩H∩R. That would be content the Implementer re-acquired
  even though it had been handed the same content.
* **H5 — cost of the topology.** Arm B uses more Claude sessions, more total
  input tokens (main model), and more wall time than Arm A. SOLVED is the same in
  both arms. Each part is reported separately. None of it is tested
  statistically.

What would change the programme: if H1 is not supported **and** H4 has no
chunk in all three sets, this fixture gives no evidence of avoidable
information reacquisition in NL handoffs, and that is reported as such. If
parity fails or either run is excluded, the pair answers none of H1–H5.

## 13. Pair-2 primary outputs (frozen)

The primary output is `runner.py report --task cart_invoice_rounding`, section
"Stage 0 controlled pair" (`analysis.report.render_pair_summary`). It shows,
for the two runs:

1. Parity: task, `config_hash`, base commit, Claude Code version, resolved
   model, protected files.
2. SOLVED from the held-out verifier; Claude sessions; CLI turns; model calls;
   tool calls.
3. Total input tokens (main model), uncached input, cache read, cache write,
   output tokens, uncached input tokens summed across all models including
   auxiliary ones (`modelUsage.inputTokens`, which excludes cache), and
   API-equivalent cost (labelled not paid).
4. Wall seconds; repository acquisition chars and bytes; unique repository
   regions; unique files read.
5. Arm B per-agent acquisition (Investigator, Implementer) and handoff volume.
6. `gross_primed_reacquisition` and its four subcategories, with chars and
   chunks; unprimed overlapping discoveries; concurrency-excluded pairs; the
   edit-precondition CLI verification.
7. Supporting-file breakdown (H1) and file-being-fixed breakdown (H2).
8. `handoff_repository_content_duplication` (H3).
9. `handoff_reacquisition_overlap` including per-target (H4).
10. Observability and integrity per run: acquisition coverage,
    `low_observability`, unknown CLI event types, unknown tools, unclassified
    bash, unparsable lines, isolation check, visible tests (reported separately
    from SOLVED), protected-file changes, changes outside the expected scope,
    permission denials, subscription billing, and arm-label validity.

Exclusions for the pair are sections 5 and 10 (amendment 6 isolation). Any
number not in this list that is reported after seeing Pair 2 is labelled
**exploratory**. No new headline metric will be introduced after seeing the
result.

## 14. Pair 2 outcome (recorded 2026-09-11, after the run)

The pair was run once, as authorized, at frozen commit `b506137`: Arm A
`20260911T004656Z_cart_invoice_rounding_A_r1`, Arm B
`20260911T004825Z_cart_invoice_rounding_B_r1`. Claude Code was pinned to
2.1.260. All six parity checks passed, both runs had acquisition coverage 1.000,
and no exclusion applied. The run was accepted as a valid preregistered
observation. Raw checksums are in `run_manifests/pair2_raw_run_checksums.sha256`,
and the frozen pair summary is in `results/pair2_cart_invoice_rounding_report.txt`.

Under the frozen section 11 definitions only:

| | Result |
| --- | --- |
| SOLVED | A yes, B yes (160/160 held-out tests each) |
| H1 | supported: 2 supporting-file discretionary rereads (`discounts.py`, `money.py`; 2,996 chars) |
| H2 | supported: the single `cart.py` reread is `edit_precondition_associated` |
| H3 | supported: 2 repository chunks in the report (quote fraction 0.0469) |
| H4 | supported: 2 chunks in I∩H∩R (~330 chars, `cart.py` only) |
| H5 | supported: 5 vs 1 sessions, 381,293 vs 129,667 total input tokens, 121.0 vs 28.1 s; SOLVED equal |

Gross primed reacquisitions were 3: 1 edit-precondition, 0 verification, 2
discretionary and 0 unknown.

Disclosed limitation of the frozen H3 metric, not a defect: the Investigator
quoted 12 source lines, 11 of them verbatim, but omitted the adjacent docstring
and validation lines. The 3-line windows therefore matched only 2 chunks. This
record does not change, and must never be changed to account for it. Section 15
adds a separate, prospective metric.

## 15. Stage 0.5: multi-task decomposition pilot (preregistered 2026-09-11, before any Stage-0.5 run)

### 15.1 Status of earlier data

Pair 1 and Pair 2 are immutable historical data. Their raw telemetry is
checksummed in `run_manifests/`, and their preregistered primary results
(sections 11–14) stand as recorded. Every metric below is PROSPECTIVE: it
applies to the Stage-0.5 pilot. On a run listed in a manifest, reports label it
`post_hoc_exploratory` (`stage05_metrics_status`), and it never replaces or
reinterprets a Pair-1 or Pair-2 result. H3 of Pair 2 stays evaluated with v1.

### 15.2 Question

When conventional natural-language multi-agent coding costs more than a
single-agent baseline, what fraction of the additional resource usage is
associated with:

1. per-agent/session context fanout,
2. natural-language handoff,
3. discretionary information reacquisition,
4. tool-required reacquisition,
5. genuinely unique useful work?

The purpose is decomposition, not proving that multi-agent coding is bad.

### 15.3 Definitions (`analysis/decomposition.py` v1, `overlap_v2.py` v1, `leakage.py` v1)

The unit is main-model input tokens (uncached + cache read + cache write), per
API call. Each figure is labelled as one of:
- **exact**: observable telemetry;
- **reconstructed**: derived from exact figures with a stated rule;
- **estimated**: chars/4 with a word floor, the `tools.handoff_size` heuristic, never a provider count;
- **unavailable**.

Summed per distinct `message.id`, per-call usage reproduces `result.usage`
exactly on all five historical runs. That check is reported as
`reconciles_with_result_usage`.

* `session_fanout_context`:
  - *Exact*: each session's first-call input with its cache read/write split
    (the first call precedes any tool use), plus session and resumed-session
    counts.
  - *Reconstructed*: API calls × (first-call input − estimated handoff text in
    that context).
  - *Estimated*: task-statement repeats (a subset of the fanout figure), and the
    hidden system prompt plus tool schemas by subtraction, for fresh sessions
    only.
  - *Unavailable*: the split between the hidden system prompt and the tool
    schemas.
  - A resumed session's history counts as its context.
* `handoff_context`:
  - *Exact*: chars and UTF-8 bytes of every inter-agent message (instructions,
    Investigator report, forwarded report, Implementer report).
  - *Estimated*: tokens.
  - *Reconstructed*: context-weighted tokens (estimated size × the API calls
    whose input contains the text).
  - Provider usage is not separately attributable, and no figure claims it is.
* `discretionary_information_reacquisition` and `edit_precondition_associated`:
  the frozen classifier (section 11), unchanged and never merged. Counts and
  chars are exact; context weight = estimated tokens × later calls in the same
  session. `verification_associated` and `unknown` stay separate rows.
* `unique_downstream_acquisition`: shingles of the downstream agent's tool
  results found in neither the upstream agent's tool results nor any handoff
  delivered to it. Each acquisition is typed `file_upstream_never_read`,
  `search_upstream_never_ran`, `other_new_content` or
  `self_generated_after_own_edit` (diffs and rereads of its own changes).
  Materiality is mechanical only, with no LLM judge:
  - `added_new_constraint = candidate` if, before its first edit, the agent
    read a file the upstream agent never read;
  - `changed_implementation = yes` if a production file changed at the end of
    the run is named in no handoff to it. A scratch file created and deleted
    does not count;
  - `merely_verified = yes` if all its unique content is self-generated;
  - `changed_diagnosis` and `corrected_earlier_finding` are
    `undetermined_without_judgement`, with the mechanical evidence shown.
* The unattributed/hidden remainder is the observable delta minus the rows.
  The rows are disjoint by context position, are not forced to sum to the
  total, and the remainder may be negative. Content-level overlap
  (acquire → handoff → reacquire, v1 and v2) is reported separately and never
  subtracted.
* `handoff_repository_overlap_v1` is the frozen 3-line-window metric
  (`analysis/handoff.py`), unchanged.
* `handoff_repository_overlap_v2` is a new, separate metric. It exists because
  Pair 2 showed that v1 undercounts excerpts where an agent quotes selected
  source lines but omits the lines around them. Definition:
  - lines are normalized (read prefix, whitespace, one leading `-`/`+`/`>`
    marker);
  - a line is *substantive* if it has at least 12 alphanumeric characters and
    occurs in exactly one repository file at the base commit (lines in two or
    more files are common and excluded);
  - a text quotes a file only with at least 2 distinct substantive lines of it;
  - tool results count every substantive line.
  - No embeddings, no LLM. Characters are exact sums of matched lines.
  - Also reported as v2 acquire → handoff → reacquire.
  - Post-hoc on Pair 2 (exploratory): report 11 lines / 500 chars, against 2 v1
    chunks.
* `held_out_content_check`, a new exclusion added to sections 5 and 10:
  - a tool result containing at least 3 distinct substantive lines of the
    task's held-out verifier that appear neither in the repository nor in the
    statement means held-out content exposure is suspected, and the run is
    excluded;
  - it complements the frozen path/marker isolation check (v1, unchanged),
    which cannot see held-out content that arrives without a path or marker;
  - it is 0 on all five historical runs.
* Run validity, applied mechanically by `report.run_validity`. A run is
  invalid if any of the following holds:
  - no verifier result;
  - `low_observability`;
  - unknown tools;
  - an invalid arm label;
  - subscription billing not confirmed;
  - any session termination other than `completed`;
  - an isolation breach or unavailable isolation check;
  - held-out content exposure.

  A pair is invalid if either run is invalid or any of the six parity checks
  fails. Editing files other than the documented expected set is not an
  exclusion: SOLVED is the held-out verifier's exit code only.

### 15.4 Four new tasks (fixtures frozen here; analysis-only file roles are never shown to agents)

| # | task_id | shape | expected edits | base commit |
| --- | --- | --- | --- | --- |
| 3 | `shipping_inch_dimensions` | cross_file_bug | `shipkit/units.py` | `4bdd314c02a47a7d31c20c0ea04a5b3d13a1b61a` |
| 4 | `settings_list_fields` | cross_file_feature | `confkit/schema.py`, `confkit/coerce.py` | `4b5d6496d774d604f43fcbf1b50052ee7ef92bcc` |
| 5 | `rename_max_connections` | repo_wide_refactor | 5 `dbclient/*.py` files | `58be8a085a3f289ab4292401935474c39f7de747` |
| 6 | `sla_weekend_hours` | misleading_symptom_bug | `helpdesk/workcal.py` | `934585d4a7b1eeb652f499e147fc6a07ab795dc9` |

`symptom_paths`, `supporting_paths` and `expected_edit_paths` are used for
analysis only. Prompts are built from the statement alone, and a test asserts
this. `tests/test_fixture_stage05.py` verifies each of the following for every
task:
- the unfixed state fails the held-out verifier, and the reference fix passes;
- a different correct fix passes where one exists (tasks 3 and 6);
- two plausible naive fixes fail;
- the visible tests pass unfixed and do not expose held-out values;
- the statement names no file role;
- both leakage detectors fire on this task's material;
- the base commit is deterministic, and Arm A and Arm B start from identical
  trees;
- SOLVED comes from the held-out verifier only.

Disclosed fixture correction made before any run: task 5's held-out tests at
first required deprecation messages to contain `max_connections` with an
underscore. The statement asks only that they *mention* `max_connections`, and
a natural CLI message says `--max-connections`, so the check now accepts
`max[_-]connections`. No other held-out requirement was changed.

### 15.5 Pilot size and repetitions

Execute later: **4 tasks × (Arm A + Arm B) × 1 repeat**, 8 runs. Together with
Pair 1 and Pair 2 that gives six task shapes. `repeat_id` is preserved, and the
cross-task summary reports per-task run-to-run spread whenever a task has more
than one valid repeat. No task gets more repetitions until between-task and
run-to-run variation have been inspected. The 72-run pilot is not scheduled.

### 15.6 Decision questions (no expected answers are preregistered)

1. Is Arm B overhead consistent across different task shapes?
2. Is discretionary reacquisition common or task-specific?
3. Does Investigator work produce unique useful information?
4. Is natural-language handoff duplication consistently material?
5. Is session/context fanout larger than handoff + reacquisition?
6. Which category appears to offer the largest realistic optimization opportunity?
7. Are there tasks where Arm B improves success despite higher cost?

### 15.7 Stage-1 selection rule (no single percentage threshold)

After the mini-pilot, the next experiment is chosen by which mechanism is
CONSISTENTLY substantial across the valid pairs, judged from the decomposition
rows, their spread across task shapes, and the success outcomes together:

* **Branch A**, artifact/shared-state experiment: if discretionary
  reacquisition is repeatedly substantial and could be addressed with
  file/symbol/artifact references.
* **Branch B**, compact structured handoff experiment: if handoff duplication
  is repeatedly substantial while discretionary rereads are low.
* **Branch C**, shared-prefix/context reuse experiment: if session/context
  fanout dominates the observable overhead.
* **Branch D**, stop multi-agent optimization: if Arm B gives little or no
  success benefit and the overhead is mostly intrinsic to running additional
  agents rather than removable coordination waste.

These are experiment branches, not implementation tasks. Cross-task means and
medians are descriptive, and invalid pairs never enter them.

### Amendment 7 - 2026-09-11 - harness turn limit counted stream lines, not turns (DURING the Stage-0.5 pilot)

**Written during the Stage-0.5 pilot**, after observing the Task-3 pair and
Task-4 Arm A, and before any further run. The user authorized this amendment and
the Task-4 rerun explicitly.

**POST-RUN INSTRUMENTATION DEFECT DISCOVERED.** The frozen limit is "25 turns
per session, enforced by the harness" (section 11; README: "the parser counts
assistant turns"). Wrapper v2 (`claude_cli.run_invocation`) incremented its
counter on every raw stdout line containing `"type":"assistant"`. Claude Code
emits one such line per content block (thinking, text, each tool_use; SPEC
section 8), so the limit fired on content blocks, not turns.
`20260911T014415Z_settings_list_fields_A_r1` (Task 4, Arm A) was terminated
`turn_limit_exceeded` at its 26th assistant stream line, after only 11 API
messages and 17 tool calls. The held-out verifier still passed. The session has
no `result` event, so its token totals are truncated. The limit was also biased
against Arm A: a single session carries a whole task's content blocks, while
Arm B splits them across sessions.

**Treatment of that run.** Under section 5 it stays INVALID. Its raw data is
preserved unmodified, and it is not re-analysed as valid.

**Fix (wrapper v3).** `claude_cli.TurnCounter` counts distinct API messages
(`message.id`), which is what a turn is, and ignores lines that merely contain
the assistant marker. The limit value (25) is unchanged. `turn_limit_enforcement`
becomes `harness_side_api_messages`, which changes `config_hash` from
`22ce9b7e5249dd497ee7c4c0318216b4` to `9edbfb5d0d082d49a61969068fafd4ac`.

**Unaffected data.** Every session that completed under v2 would also complete
under v3 (asserted in `tests/test_turn_counter.py`), so no valid run changes:

- Pair 1 and Pair 2 (all sessions at 22 or fewer stream lines);
- the Stage-0.5 Task-3 pair (16 and 15 stream lines in its largest sessions).

That pair keeps `config_hash` `22ce9b7e…`. Each pair is internally consistent,
and the cross-task summary spans two wrapper versions; this is disclosed here
and in the pilot report.

**Rerun.** Task 4 is run again as a fresh pair (Arm A and Arm B) under v3 with
`--repeat-id 2`, so the invalid attempt stays visible as repeat 1 (Arm A only,
invalid). Tasks 5 and 6 run under v3. Nothing else changes: no metric,
threshold, fixture, arm, topology, permission policy or limit value.
