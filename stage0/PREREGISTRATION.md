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

### Amendment 8 - 2026-09-11 - bash command splitter mis-parsed `2>&1` (DURING the Stage-0.5 pilot)

**Written during the Stage-0.5 pilot**, after observing the Task-4 rerun pair
and before any further run. The user authorized this amendment, including the
re-analysis of the observed run, explicitly.

**POST-RUN INSTRUMENTATION DEFECT DISCOVERED.** Under the frozen bash classifier
(v1), `20260911T015737Z_settings_list_fields_B_r2` (Task 4 rerun, Arm B) had
acquisition coverage **0.700** (14 classified, 6 unknown). That flagged it
`low_observability` and made the pair invalid. All six unknown commands were
the Implementer's own non-acquisition work:

- five test runs of the form `python -m pytest -q [file] 2>&1`: `tools.split_bash_segments`
  treated the `&` in the `2>&1` redirection as a background separator, which
  left a bogus segment `1`;
- one `python --version`: v1 had no category for version queries.

None of these commands returned repository content. The raw stream is complete
and unmodified (41/41 checksums).

**Fix (bash classifier v2, `tools.BASH_CLASSIFIER_VERSION = 2`).**
- `&` inside a redirection (`N>&M`, `>&`, `<&`, `&>`) is not a separator. A
  bare `&`, `&&`, `||`, `|` and `;` still separate commands.
- Interpreter version queries (`--version`, `-V`, `version`, exact tokens) are
  non-acquisition metadata.

The coverage formula (v2), the gate (≥ 0.90, no unknown tools) and every other
category are unchanged. The classifier is part of the derived analysis, not of
`config_hash`, and nothing about execution changed.

**Re-analysis (post-hoc, disclosed; no rerun).** Under v2 the same raw data
gives coverage **1.000** (14 classified, 0 unknown), so the run is valid and the
Task-4 rerun pair is valid. The v1 result (0.700, invalid) is recorded here and
in the pilot report. No other real run contains either command form, and every
other run's classification is unchanged (asserted in `tests/test_amendment8.py`).

## 16. Stage 1: C1_shared_worker_context (preregistered 2026-09-11, before any C1 run)

Stage 0.5 was accepted with six valid A/B pairs. Branch C (shared prefix/context
reuse) is selected. This section freezes its first experiment. No custom
KV-cache sharing, no custom inference server, no Agent IR, no artifact
protocol, no compact language, no LoRA/MoE.

### 16.1 Question

How much of conventional multi-agent coding overhead can be removed by
preserving repository/context state across the Investigator → Implementer role
transition, without changing the model, task, Coordinator or logical workflow?
The experiment tests whether the large `session_fanout_context` observed in
Stage 0.5 is actually addressable. It does NOT ask whether shared sessions are
universally better.

### 16.2 Arms

* **A** and **B**: unchanged and not rerun. `arms/single.py` and
  `arms/multi_nl.py` are byte-identical to their frozen versions (hash-pinned
  in tests), and the A/B `config_hash` stays `9edbfb5d0d082d49a61969068fafd4ac`.
  B keeps five invocations and three fresh sessions (Coordinator, Investigator,
  a NEW Implementer session).
* **C1 = `C1_shared_worker_context`** (`arms/c1_shared_worker.py`, topology
  version 1). It is not the old speculative "structured-state Arm C".

| # | Invocation | Logical role | Physical session | Fresh/resumed |
| --- | --- | --- | --- | --- |
| 1 | Coordinator kickoff | coordinator | Coordinator | fresh |
| 2 | Worker, Investigator phase | investigator | Worker | fresh |
| 3 | Coordinator plan | coordinator | Coordinator | resumed |
| 4 | Worker, Implementer phase | implementer | Worker (resume of #2) | resumed |
| 5 | Coordinator wrap-up | coordinator | Coordinator | resumed |

So C1 has 5 CLI invocations (as B), **2 fresh physical sessions** (B: 3), 3
resumed invocations (B: 2) and 3 logical roles. Every report shows
`cli_invocations`, `fresh_sessions`, `resumed_invocations`, `physical_sessions`,
`logical_roles`, and each invocation's `logical_role` and `physical_session_id`.

**The Coordinator is unchanged.** It uses Arm B's prompt functions verbatim:
original task → Investigator instruction → Investigator report → implementation
instruction → Implementer report → final result. It is not merged into the
Worker.

**Worker Implementer phase input.** It receives the Coordinator's
implementation instruction plus a fixed working agreement, nothing else. It
already holds, in Claude Code session history, the task statement, its own
investigation and its own report. The report is **not** forwarded back
(`investigator_report_forwarded_to_worker = false`), and the task statement is
**not** resent (`task_statement_resent_on_worker_resume = false`). No
instruction discourages rereading.

**Tool-policy transition.** The Investigator phase uses B's Investigator policy:
`--tools Read,Grep,Glob,Bash`, `--disallowedTools Edit,Write,NotebookEdit`. The
Implementer phase uses B's Implementer policy: `--tools
Read,Grep,Glob,Edit,Write,Bash` and the same `narrow_pytest_v1` allowlist. Each
phase also carries its role's `--append-system-prompt`.

Evidence that this works without inference:

* `--tools`, `--disallowedTools` and `--append-system-prompt` are per-process
  flags. Each `-p` invocation is a new process whose `init` event reports its
  own tool set: in the stored runs, Investigator `init.tools = Bash,Glob,Grep,Read`
  and Implementer `init.tools` adds `Edit,Write`.
* The CLI help ties no tool set to `--resume`.
* Stored Arm-B Coordinator resumes keep the original session id (`--fork-session`
  would create a new one; it is not used).

A *changed* tool set on resume has not yet been exercised against real Claude
Code. Therefore **each C1 run is valid only if `worker_transition.verified`**:
from the invocations' own `init` events, the Investigator phase has no
Edit/Write/NotebookEdit, the Implementer phase has Edit and Write, both phases
share one physical session id, the Implementer phase resumes it, and the
Coordinator is separate. If not verified, the run is invalid and reported as a
limitation. Investigator permissions are never broadened.

### 16.3 Reconstruction

The harness-input reconstruction gate covers all five C1 invocations
(`tests/test_reconstruction.py`, parametrized over A, B and C1). The Worker
transition is recorded as a `WORKER_ROLE_TRANSITION` event and in
`topology.json`:

* physical session id;
* previous and new logical role;
* resume target;
* the exact new prompt;
* the previous and new tool policy;
* whether the session id was preserved.

The event type is additive, and event `SCHEMA_VERSION` stays 1.

### 16.4 Measurement (frozen definitions reused; nothing redefined for C1)

* **Topology/fanout, exact:**
  - fresh session count and resumed invocation count;
  - first-call input for the Coordinator and the Worker (fresh);
  - the Worker's Implementer-resume first-call context;
  - uncached input, cache read and cache write per invocation and in total.

  Provider usage is reported exactly, and cache reads are billed input, never
  described as eliminated cost.
* **Reacquisition:** the frozen classifier (section 11) is unchanged, reporting
  `edit_precondition_associated`, `verification_associated`,
  `discretionary_information_reacquisition` and `unknown`. Whether Claude Code's
  Edit accepts an Investigator-phase Read, in the same physical session, as
  satisfying read-before-edit after resume is **not assumed**; the runs measure
  it.
  - *Disclosed comparability confound:* the frozen priming rule counts delivered
    messages only. In C1 the Investigator report lives in the Worker's own history
    and is not re-delivered, so a reread primed only by that report is
    classified *unprimed*.
  - E6 therefore reports gross primed reacquisition and unprimed overlapping
    discoveries side by side, plus all inter-agent overlapping acquisitions.
* **Handoffs:**
  - *B:* Investigator → Coordinator report, Coordinator → Implementer instruction,
    forwarded report.
  - *C1:* Investigator → Coordinator report, Coordinator → resumed Worker
    instruction, and no forwarded report.
  - *Measures:* inter-agent chars, bytes, estimated tokens, and handoff overlap
    v1 and v2.
  - *Worker history:* it is not an inter-agent handoff. In the decomposition's
    context-weighted handoff row, the frozen v1 rule counts a resumed session's
    own delivered reply as handoff text in its context. That happens for B's
    Coordinator, and for C1's Worker carrying its own report. This applies
    unchanged and is stated here.
* **Decomposition implementation (not a definition change):** resume chains are
  keyed by physical session (the `--resume` target) instead of agent id, so the
  Worker's Implementer phase carries its Investigator-phase history. For every
  A/B run the two keyings coincide. All six Stage-0.5 pair decompositions
  reproduce exactly (asserted against `results/stage05_pilot/pilot_tables.jsonl`),
  and `DECOMPOSITION_VERSION` stays 1.
* **Unique downstream acquisition:** the Stage-0.5 definition is unchanged. It
  reports new files, new constraints (mechanical), final changed files not named
  in handoffs, and merely-verified work. Changed diagnosis and corrected finding
  stay undetermined. No LLM judge.
* **Outcome:** the held-out verifier only, with the same fixtures and verifiers
  as A/B. Analysis-only file roles never reach C1 prompts (tested).

### 16.5 Endpoints (no thresholds)

* **E1 correctness:** does C1 preserve B's task success?
* **E2 input reduction:** does C1 use less total main-model input than B?
* **E3 fresh-context reduction:** how much observable first-session/context
  fanout is removed (fresh sessions; summed fresh first-call input, exact)?
* **E4 cache-write reduction:** does sharing Worker context reduce cache
  creation/write input? This matters more economically than already-cheap cache
  reads.
* **E5 handoff reduction:** how much context is removed by not forwarding the
  Investigator report back to its own Worker?
* **E6 reread reduction:** does Worker continuity reduce repository
  reacquisition (gross primed, edit-precondition, discretionary, unprimed
  overlapping)?
* **E7 wall time/cost:** does C1 reduce wall time and API-equivalent cost while
  preserving correctness?

### 16.6 Task set, comparison, execution plan

Tasks: the four prospective Stage-0.5 tasks, `shipping_inch_dimensions`,
`settings_list_fields`, `rename_max_connections` and `sla_weekend_hours`.
Existing A and B runs are reused. Future execution is **4 C1 runs**, with no new
A/B runs and no repeats.

The primary comparison is **B vs C1** (`runner.py report --task <t>
--compare-stage1`; `runner.py stage1-summary`); A is shown for reference.

Pair parity is on the base configuration:

* same task, base commit, Claude Code version, resolved model and protected
  files;
* C1 `base_config_hash` equal to B's `config_hash`, with one preregistered
  exception: Task 3's B run was recorded under wrapper v2 (`22ce9b7e…`). The
  only difference is amendment 7's turn counting, and that B run never reached
  the limit, so it counts as parity-compatible with an explicit note.

Exclusions are those of sections 5, 10 and 15.3, plus C1's
`worker_transition.verified`. Invalid pairs never enter aggregates. n = 1 per
task: descriptive only.

### 16.7 Confounds (accepted, stated in advance)

* **Role contamination:** the Implementer retains the Investigator's reasoning
  and history. This is intended: C1 tests persistent role context, not isolated
  expert independence.
* **Reduced independence:** C1 is less independent than B, so any independent
  verification value may fall. Recorded as a trade-off.
* **Same underlying model:** same-model role specialization, not
  specialist-model routing.
* **Claude Code specifics:** results depend partly on Claude Code 2.1.260
  `--resume` semantics and its prompt caching. Changing the tool set and system
  appendix at the transition may change the cached prompt prefix, and E4
  measures the net effect. No generalization to arbitrary agent systems.

### 16.8 Failure interpretation

C1 failure is not a harness failure unless telemetry shows an actual defect.
All of the following are legitimate results:

* cheaper and solves;
* cheaper and fails;
* same cost;
* more expensive;
* rereads anyway;
* Edit refuses the previous-phase Read.

### 16.9 Identity

| Item | Value |
| --- | --- |
| `arm` | `C1` |
| `arm_topology` | `C1_shared_worker_context` |
| `topology_version` | 1 |
| `experiment_schema_version` | 2, C1 metadata only; A/B metadata is left as written |
| C1 `config_hash` (sonnet, smoke limits) | `5ca22e4846c07ca973ee4e891df059c1` = `topology_config_hash(base effective config, C1_TOPOLOGY)` |
| `base_config_hash` | `9edbfb5d0d082d49a61969068fafd4ac` |

No historical metadata or B `config_hash` is rewritten.

## 17. Stage 1 outcome (recorded 2026-09-11, after the runs)

The four preregistered C1 runs ran once each at frozen commit `56f067b`, with
Claude Code 2.1.260 pinned. All four are valid, and `worker_transition.verified`
held on every run. Real Claude Code honoured the changed tool policy on
`--resume`: Investigator phase `Bash,Glob,Grep,Read`; Implementer phase adds
`Edit,Write`; the session id was preserved. No amendment, rerun or post-run
instrumentation defect. Raw checksums are in
`run_manifests/stage1_c1/raw_run_checksums.sha256`, and the derived records in
`results/stage1_c1/`.

| Task | B/C1 solved | C1/B input | C1/B cache write | C1/B cost | C1/B wall | Handoff chars B→C1 | Gross primed rereads B→C1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shipping_inch_dimensions | ✅/✅ | 1.132 | 1.097 | 1.018 | 0.789 | 11,388→5,830 | 1→0 |
| settings_list_fields | ✅/✅ | 0.703 | 1.071 | 0.919 | 0.858 | 25,781→18,653 | 3→3 |
| rename_max_connections | ✅/✅ | 1.065 | 1.004 | 0.976 | 1.040 | 22,912→17,516 | 5→0 |
| sla_weekend_hours | ✅/✅ | 2.172 | 1.370 | 1.460 | 1.142 | 11,921→11,953 | 1→0 |

Median C1/B ratios: input 1.099, cache write 1.084, cost 0.997, wall 0.949. Pooled
over the four tasks: total input +7.1%, cache write +11.1%, API-equivalent cost
+4.7%, wall time −5.0%. Fresh-session first-call input fell 42.6–45.6% (exact).

Endpoints:

* E1: preserved, 4/4.
* E2: not reduced overall.
* E3: fresh-session context reduced.
* E4: cache write not reduced; higher on all 4 tasks.
* E5: the forwarded report disappeared, and total handoff chars fell by a median
  of 25.6%.
* E6: rereads fell (gross primed 10→3).
* E7: wall time slightly lower, cost unchanged overall.

Edit accepted an Investigator-phase Read after resume (no refusal on any run).
Voluntary rereads that remained are counted as `edit_precondition_associated` by
the frozen classifier, which is an upper bound for C1.

Best-supported conclusion: **C1-C**, shared Worker context does not meaningfully
reduce overhead. Decision: **D, stop multi-agent optimization for this
experiment** (see `FINAL_RESEARCH_REPORT.md`).

## 18. Stage 2A: heterogeneous model routing (preregistered 2026-09-11, before any Stage-2 run)

A **new research branch**, not a continuation of C1. The Stage 0–1 conclusion
stands unchanged: same-model multi-agent decomposition did not justify its
additional cost on the tested workloads. That does not answer a different
question: can role decomposition become economically useful if expensive model
capability is concentrated only in the role that needs it? No Stage 0, 0.5 or 1
result, raw telemetry, metric, manifest or conclusion is altered.

### 18.1 Question and objective

> Can a heterogeneous multi-agent coding workflow match the success of a single
> strong model while reducing total API-equivalent cost by assigning cheaper
> models and/or lower reasoning effort to mechanically simpler roles?

The objective is **task success at API-equivalent cost**, not minimum tokens. A
configuration may use more tokens and still be preferable if most of them are
processed by a substantially cheaper model.

### 18.2 Local capability evidence (collected without any model call)

Sources: the pinned 2.1.260 executable's `--help` output (a local parse, no
model call); strings in the installed binary's bundled source (the model
registry, the alias resolver, the effort parser); stored `init`/`result`
telemetry from all 18 real runs; the local settings files; and the environment,
checked by presence only.

| # | Question | Finding | Evidence |
| --- | --- | --- | --- |
| 1 | Selectable model identifiers | an alias or a full model name via `--model` | `--help`: "Provide an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a model's full name". The binary's alias list is `sonnet, opus, haiku, fable, best, sonnet[1m], opus[1m], fable[1m], opusplan`. Its known-model list includes `claude-haiku-4-5` and `claude-sonnet-5`, among others. |
| 2 | Explicit Haiku | **yes**, by full id | The registry entry for `claude-haiku-4-5` has first-party id `claude-haiku-4-5-20251001`. This subscription already served that exact id: it is Claude Code's auxiliary model in 33 stored sessions. It has never yet served as a *main* model in this harness, so the first Stage-2 run verifies it (18.10). |
| 3 | Explicit Sonnet | **yes** | `--model sonnet` resolved to `claude-sonnet-5` in 58 of 58 stored `init` events. |
| 4 | Model per fresh/resumed invocation | per invocation | `--model` is a per-process flag passed on every invocation, and each invocation's `init` reports its own model. Stage 2A never changes a model on `--resume`: the Coordinator's resumes keep CHEAP, and the Investigator and Implementer are fresh sessions. |
| 5–7 | Effort control | flag, env var and setting exist | Flag: `--effort <level>`, "Effort level for the current session (low, medium, high, xhigh, max)"; an unknown value is ignored with a warning. Env var: `CLAUDE_CODE_EFFORT_LEVEL`. Setting: `effortLevel`. No model alias encodes effort. |
| — | Effort comparability | **not comparable across the two models, and not observable** | Registry capabilities: `claude-sonnet-5` has `effort, max_effort, xhigh_effort`, with `default_effort: "high"`; `claude-haiku-4-5` has only `context_management`. No `init`/`result` field reports the effort used. |
| 8 | Model/effort change on `--resume` | accepted as per-process flags; **not exercised, not claimed, not used** | Stage 2A needs no model or effort change on resume. |
| — | Settings / environment | nothing selects a model or effort | Sessions use `--setting-sources project`, and the task fixtures contain no `.claude` settings. The user settings contain no model or effort key and are not loaded. None of the model/effort variables is present. |
| — | Role vs auxiliary usage | separable exactly | `result.usage` equals `modelUsage[<main model>]` in 57 of 57 stored sessions, so auxiliary = `modelUsage − result.usage`. |
| — | Cost | reproducible | Recomputing every stored per-model `costUSD` from the binary's own pricing tables (`claude-sonnet-5` → `tier_2_10`: 2 / 10 / 2.5 (5m) / 4 (1h) / 0.2 USD per MTok for input / output / cache write / cache read; `claude-haiku-4-5` → `haiku_45`: 1 / 5 / 1.25 / 2 / 0.1) matches 90 of 90 entries exactly. |

Explicit Haiku selection is supported, so Stage 2 proceeds. Routing is done by
`--model` only, never by prompt wording.

### 18.3 Resolved identifiers

| Class | Requested (`--model`) | Expected resolved (`init.model`) | Canonical |
| --- | --- | --- | --- |
| CHEAP_MODEL | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` | `claude-haiku-4-5` |
| STRONG_MODEL | `sonnet` | `claude-sonnet-5` | `claude-sonnet-5` |

**CHEAP uses the full id.** The `haiku` alias resolves indirectly: first
`ANTHROPIC_DEFAULT_HAIKU_MODEL`, then a remotely configurable lookup, then a
built-in. The full id depends on none of that.

**STRONG keeps the historical request `sonnet` byte for byte.** That keeps every
strong invocation and the Single-Strong baseline on the historical argv.

A resolved model is compared on its canonical form, which strips the date suffix.

### 18.4 Effort policy for Stage 2A

**`cli_default_not_passed`: no effort control is passed to any role.** The
flag, the env var and the setting all exist. But they are not comparable across
the two models (Haiku 4.5 has no effort capability in the registry) and the
effort used is not observable in telemetry. So the suggested "cheap: low,
strong: historical" policy cannot be applied cleanly, and no effort value is
invented.

The strong model's effort is therefore identical to every historical run: the
CLI default, registry `default_effort` `high`. The cheap model runs at its own
default.

Each invocation records its effort as:

* requested `not_passed`;
* resolved `not_reported_by_cli_telemetry`.

A harness guard refuses to start a Stage-2 run if any of these variables would
reach a child process, testing presence only:

* `ANTHROPIC_MODEL`;
* `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL`;
* `ANTHROPIC_SMALL_FAST_MODEL`;
* `CLAUDE_CODE_SUBAGENT_MODEL`;
* `CLAUDE_CODE_EFFORT_LEVEL`;
* `MAX_THINKING_TOKENS`.

There is no model × effort × role factorial. Effort becomes Stage 2B only if
Stage 2A supports heterogeneous routing, and it needs its own preregistration.

### 18.5 Motivation (from Stage 0.5)

Across the prospective multi-agent tasks, the Implementer:

* acquired no informational repository content the Investigator lacked (unique
  downstream informational content was nil or marginal);
* discovered no new constraint;
* generally implemented the diagnosis it was given;
* mostly edited and verified. Its rereads were Edit preconditions, not new
  information.

So the roles may have different intelligence requirements. The hypothesis to
test, not an assumption:

* Coordinator: the cheap model may be sufficient.
* Investigator: the strong model likely provides most of the diagnostic value.
* Implementer: the cheap model may be sufficient once the diagnosis and
  constraints are explicit.

### 18.6 Configurations and topology

| Id | Name | Coordinator | Investigator | Implementer | Sessions |
| --- | --- | --- | --- | --- | --- |
| `S2_R1` | Strong-Investigator Hybrid (**primary**) | CHEAP | STRONG | CHEAP | Arm-B topology |
| `S2_R2` | Strong-Implementer Hybrid (contrast) | CHEAP | CHEAP | STRONG | Arm-B topology |
| `S2_R3` | All-Cheap Multi | CHEAP | CHEAP | CHEAP | Arm-B topology |
| `S2_S` | Single Cheap | one CHEAP agent | | | Arm-A topology |
| `S2_SS` | Single Strong, fresh baseline | one STRONG agent | | | Arm-A topology; only where 18.7 requires it |

**Multi-agent configurations use Arm B's separated topology.** The sequence is
Coordinator → Investigator → Coordinator → Implementer → Coordinator. The
Coordinator is one session, resumed twice. The Investigator and Implementer
each get a separate fresh session. No C1 shared Worker: that would add a second
independent variable. Also identical to Arm B:

* Arm B's prompt functions, verbatim;
* Arm B's handoffs, including the forwarded Investigator report;
* Arm B's per-role `--tools`, `--disallowedTools` and `--append-system-prompt`;
* the `narrow_pytest_v1` allowlist, limits and tasks.

Single-agent configurations are Arm A exactly (prompt, tools, prompt inputs).

**No model-specific prompt tuning is permitted, before or after results.** The
prompt sources (`arms/single.py`, `arms/multi_nl.py`) and the role appendix are
hash-checked before every Stage-2 run, and a run refuses to start on drift. If a
model-specific prompt change ever becomes necessary, Stage 2A stops, and the
change is a separate future experiment.

The cheap model gets no broader tools, and no test is relaxed. Held-out
verifiers stay withheld, with isolation and content checks unchanged.

The report labels are:

| Label | Source |
| --- | --- |
| Single Strong | historical Arm A, or `S2_SS` |
| Single Cheap | `S2_S` |
| All-Cheap Multi | `S2_R3` |
| Strong-Investigator Hybrid | `S2_R1` |
| Strong-Implementer Hybrid | `S2_R2` |

Arm B, the all-strong multi-agent workflow, is shown for reference only and
enters no comparison.

### 18.7 Baseline reuse decision (made now, before any Stage-2 result)

A historical Arm A run is reused as Single Strong only with **exact parity**,
checked mechanically by `analysis/stage2.baseline_parity`:

* single agent, one session;
* base `config_hash` equal to the current `9edbfb5d0d082d49a61969068fafd4ac`.
  That hash covers the model request, tools, permission policy, limits and turn
  counting.
* same task base commit;
* Claude Code 2.1.260;
* requested `sonnet`, resolved `claude-sonnet-5`, routing verified;
* prompt byte-identical to Arm A's;
* same statement, held-out verifier and protected paths;
* a valid run.

The task fixtures and verifiers are unchanged since the Stage-0.5 freeze
(`git diff 5a756bf HEAD -- stage0/tasks` is empty), and the workspace base
commit is deterministic.

| Task | Historical Arm A | Reusable | Reason |
| --- | --- | --- | --- |
| shipping_inch_dimensions | `20260911T014034Z_…_A_r1` | **no** | `config_hash 22ce9b7e…`: recorded under wrapper v2, before amendment 7's turn counting. It never reached the limit, but exact parity is required here: the Stage-1 "amendment 7 note" exception is **not** used. |
| settings_list_fields | `20260911T015555Z_…_A_r2` | yes | all checks pass. The earlier `…_A_r1` is invalid (amendment 7) and is not used. |
| rename_max_connections | `20260911T020645Z_…_A_r1` | yes | all checks pass |
| sla_weekend_hours | `20260911T021224Z_…_A_r1` | yes | all checks pass |

**Decision:**

* reuse the three exact-parity Arm A runs;
* run **one fresh Single-Strong baseline (`S2_SS`) for
  `shipping_inch_dimensions`**, before seeing any Stage-2 result.

Selection is mechanical: an exact-parity historical Arm A run first, else the
first valid `S2_SS` run.

The same analysis for Arm B: `settings_list_fields`, `rename_max_connections`
and `sla_weekend_hours` have exact base-config parity; `shipping_inch_dimensions`
does not. Arm B is reference only: no comparison depends on it, and no Arm B run
is added.

### 18.8 Endpoints (no thresholds unless stated)

* **E1, success:** the held-out verifier result for every configuration. This is
  the primary quality criterion.
* **E2, API-equivalent cost (primary efficiency endpoint):** reported two ways.
  - **By role:** Coordinator, Investigator, Implementer and total. Each is split
    into role-assigned plus auxiliary, which gives all-model cost.
  - **By model:** CHEAP, STRONG, other and total, each split into role-assigned
    and auxiliary.

  Cost is computed from exact provider usage using the CLI's own pricing tables.
  The all-model total equals the CLI's `total_cost_usd`, including Claude Code's
  auxiliary usage. It is not the amount paid: execution is on the subscription.
  Token count is never substituted for cost.
* **E3, strong-model usage:** STRONG invocations, input, output, cache read,
  cache write, and the share of total cost attributable to STRONG.
* **E4, total tokens (explanatory only):** uncached input, cache read, cache
  write, output, and thinking where exposed; auxiliary tokens are shown
  separately.
* **E5, latency:** wall time and API time, per role and total.
* **E6, failure location (mechanical, v1, no LLM judge):** for a valid unsolved
  run, the first matching rule applies. The analysis-only `expected_edit_paths`
  are used here and never reach a prompt.
  - **R1:** an invocation did not complete (limit or error). The location is
    that role's: Coordinator routing, Investigator diagnosis, or Implementer
    coding.
  - **R2:** the investigation report names no file the fix must change →
    Investigator diagnosis.
  - **R3:** the implementation instruction names none, and no such file changed
    → Coordinator routing.
  - **R4:** no file the fix must change was changed → Implementer coding.
  - **R5:** the visible tests fail on the final workspace → Implementer coding.
  - **R6:** the visible tests pass but the held-out verifier fails →
    verification.
  - **R7:** otherwise → unknown.

  For a single agent, R2 and R3 do not apply and the role is `solo`.
* **E7, escalation opportunity (evidence only, nothing implemented):** for a
  failure located in a CHEAP role, look at the configuration that assigns STRONG
  to that role:
  - Investigator → `S2_R1`;
  - Implementer → `S2_R2`;
  - single agent → Single Strong;
  - Coordinator → Arm B reference.

  The verdict is:
  - `potentially_recoverable` if that configuration solved the same task;
  - `not_indicated` if it failed;
  - `undetermined` if there is no valid counterpart.

  Runtime-observable signals are also recorded (a non-completed invocation; the
  outcome of the agents' last `pytest` call).

Per task and configuration the report shows:

* SOLVED;
* API-equivalent cost;
* wall time;
* strong-model invocations and cost;
* total input and output.

There is no scalar quality-cost score. **Cost is compared only between two runs
that are both valid, both solved, and both with exact cost.** Failed cheap runs
remain reported data.

### 18.9 Comparisons and interpretation cases

The comparisons (ratio = second / first):

* **A:** Single Strong vs Strong-Investigator Hybrid.
* **B:** Single Cheap vs Strong-Investigator Hybrid.
* **C:** Strong-Investigator vs Strong-Implementer Hybrid.
* **D:** Single Cheap vs All-Cheap Multi.

Each reports, over tasks with both runs valid:

* success counts;
* jointly solved tasks;
* the pooled cost ratio (sum over jointly solved tasks);
* the median per-task ratio.

The cases are evaluated only when every configuration has a valid run on all
four tasks, and they are not mutually exclusive. `S(x)` is the set of tasks
solved by `x`, and cost ratios are pooled over jointly solved tasks.

* **Case A** holds if the Strong-Investigator Hybrid has the same success as
  Single Strong and is substantially cheaper: `S(R1) = S(SS)`, and the pooled
  cost ratio R1/SS ≤ **0.75**. The 0.75 is a preregistered descriptive cut; the
  exact ratio is always shown. This supports heterogeneous routing.
* **Case B** holds if Single Cheap has the same success and is cheaper than the
  hybrid: `S(S) ⊇ S(R1)`, and the pooled cost ratio S/R1 < 1. Multi-agent
  decomposition is then unnecessary: use the cheap single model. Whether
  `S(S) ⊇ S(SS)` is also reported.
* **Case C** holds if the hybrid fails a task Single Strong solves:
  `S(SS) \ S(R1)` is non-empty. The removed strong capability mattered. This is
  reported per task and never hidden behind aggregate cost.
* **Case D** holds if Strong-Implementer beats Strong-Investigator:
  `|S(R2)| > |S(R1)|`. A cost-only variant (equal success, and R2 cheaper) is
  flagged separately. Either way the Stage-0.5 intelligence-concentration
  reading would be incomplete.
* **Case E** holds if All-Cheap Multi beats Single Cheap: `|S(R3)| > |S(S)|`.
  That would be evidence that decomposition compensates for a weaker model.

### 18.10 Validity and invalidation

The shared exclusions still apply (sections 5, 10 and 15.3):

* no verifier result;
* low observability;
* unknown tools;
* internal subagent fan-out;
* subscription billing not confirmed;
* a suspected isolation or content breach.

Stage 2 adds the following **invalidation rules**:

* **Model routing.** Every invocation must pass all of these checks, from its
  own telemetry, not from argv:
  - the requested model equals the assignment;
  - the canonical `init.model` equals the assigned model;
  - every assistant `message.model` equals the assigned model;
  - the assigned model is present in `modelUsage`;
  - `result.usage` is within `modelUsage[<assigned model>]`;
  - `modelUsage` contains no model other than the assigned one and the
    auxiliary `claude-haiku-4-5`. For example, a strong model inside a
    cheap-assigned invocation is not allowed.

  When a check fails, the orchestrator stops before the next session and the
  run is **invalid**.
* **Accounting.** Negative auxiliary usage or cost (role usage exceeding the
  model's reported usage) makes the run invalid.
* **Infrastructure.** Each of these makes the run invalid:
  - a spawn failure;
  - a provider API error status;
  - a chain stopped by quota protection or by a routing stop.
* **Model outcomes, not exclusions.** A limit termination (turn or wall) or a
  model-caused error of an assigned model is an outcome: SOLVED is still the
  held-out verifier's exit code. The frozen A/B rule that excludes non-completed
  sessions is deliberately not applied to Stage-2 configurations. The chain
  stops at such an invocation, as in Arm B. If the terminated invocation has no
  result event, its output tokens are a lower bound: the run's cost is labelled
  a lower bound and does not enter cost comparisons.

Invalid runs are listed with their reasons and never enter success counts or
cost comparisons. The first valid run of a (task, configuration) is the one
used.

**Stop rules:**

* If the first Stage-2 run shows that the CLI does not serve the assigned model
  (a routing mismatch), **Stage 2A execution stops** and the limitation is
  reported.
* Paid overage stops execution; high utilization alone does not.
* An infrastructure-invalid run may be rerun once with the next `--repeat-id`,
  disclosed. A valid run is never rerun.

### 18.11 Pilot size and execution plan

The pilot is **4 tasks × 4 new configurations × 1 run = 16 runs**, plus **1**
fresh Single-Strong baseline (18.7): **17 runs** in total. There are no repeats,
no effort variants and no new tasks. The fixed order:

1. `shipping_inch_dimensions`: `S2_R1`, `S2_R2`, `S2_R3`, `S2_S`, `S2_SS`. The
   first run, `S2_R1`, doubles as the routing validation: it has both models and
   a CHEAP Coordinator resume.
2. `settings_list_fields`: `S2_R1`, `S2_R2`, `S2_R3`, `S2_S`.
3. `rename_max_connections`: the same four.
4. `sla_weekend_hours`: the same four.

Claude Code 2.1.260 stays pinned (`STAGE0_CLAUDE_CLI`) and limits are the smoke
limits. Raw checksums go to a subdirectory, `run_manifests/stage2a/`, so
`historical_run_ids()` is unchanged.

Outputs:

* `runner.py stage2-report --task <t>` for each task;
* `runner.py stage2-summary`.

**Stage 2B**, cheap-first with a confidence or failure trigger and strong-model
escalation, is **not implemented**. It needs its own preregistration: defining
the trigger after seeing failures would bias it. Stage 2A's E7 only collects
evidence for whether it would be justified.

### 18.12 Identity

| Item | Value |
| --- | --- |
| `stage` / `experiment_schema_version` / `topology_version` | 2 / 3 / 1; A/B and C1 metadata are left as written |
| `base_config_hash` (all Stage-2 runs) | `9edbfb5d0d082d49a61969068fafd4ac`: the unchanged base configuration |
| `S2_R1` `config_hash` | `f1a4b7f32952ee7618e3062c0f0a5ebd` |
| `S2_R2` `config_hash` | `073ebc97390854d8331531a681a8374d` |
| `S2_R3` `config_hash` | `4b75b94f619fe83c93aa3582a3da0aa3` |
| `S2_S` `config_hash` | `d9e92534b466f8793b390b222c18fe76` |
| `S2_SS` `config_hash` | `d2cd1cf2024412d57d685340261f5fe5` |

Each `config_hash` is `topology_config_hash(base effective config,
stage2_topology(arm))`. The topology contains:

* the role → requested-model map;
* the role → expected-resolved-model map;
* the role → effort map;
* the topology version;
* the prompt version (source and appendix hashes, no model-specific prompts);
* the permission policy id;
* the pricing table.

Each run's metadata adds a `stage2_identity` over:

* the `config_hash`;
* the task base commit;
* the held-out verifier hash;
* the classifier versions (parser, wrapper, coverage, bash classifier,
  reacquisition, handoff, isolation, overlap v2, decomposition, content check,
  model-routing check, cost accounting).

No historical hash is rewritten.

### 18.13 Accepted confounds (stated in advance)

* **Different model defaults.** The model defaults differ, beyond "capability":

  | | Haiku 4.5 | Sonnet 5 |
  | --- | --- | --- |
  | Context window | 200k | 1M |
  | Default max output | 32k | 64k |
  | Thinking / effort | no effort capability | adaptive thinking; default effort `high` |

  Claude Code may compact a Haiku session sooner. These are properties of the
  models as the CLI serves them, and are not equalized.
* **Prompts are not tuned for Haiku.** They were written for, and frozen on,
  Sonnet runs. This is deliberate: the variable is model assignment.
* **Auxiliary usage.** Claude Code's own auxiliary calls continue in every
  configuration. They are separated from role usage but included in cost.
* **Cost is a price, not an observation.** API-equivalent cost uses the CLI's
  list prices; relative prices drive the result.
* **n = 1** per task and configuration: descriptive only.
* **Server-side unobservables.** Effort and any server-side routing behind a
  model id are not observable.

## 19. Stage 2 amendment: difficulty-calibrated quality × cost experiment (preregistered 2026-09-11, before any Stage-2 inference)

### 19.0 Integrity record

* `ee0f020` was a valid pre-inference Stage-2 routing freeze (section 18).
* When this amendment was written, **no Stage-2 model call had occurred**:
  - no Stage-2 run directory exists;
  - `runs/` holds only the 18 manifested Stage 0–1 runs.
* The research question was broadened **before observing any Stage-2 outcome**.
* The 17-run execution plan of 18.11 is **SUPERSEDED** before execution. None
  of it ran and no result was discarded.
* Motivation: the documented Stage 0–1 ceiling effect. Single Strong solved
  every tested task (all six A/B pairs; the four C1 tasks are the same tasks),
  so a quality advantage for multi-agent decomposition could not appear.
* Kept unchanged:
  - section 18's routing infrastructure: `S2_R1`, `S2_R2`, `S2_R3`, `S2_S`,
    `S2_SS`, model-resolution telemetry, role/model cost accounting, auxiliary
    separation and mismatch invalidation;
  - its five configuration hashes;
  - every historical A/B/C1 record.

  This amendment changes the task design and the interpretation, and adds one
  configuration.

### 19.1 Questions

Primary:

> At what task difficulty does multi-agent decomposition begin to improve
> solution quality or reliability over a single strong agent, and what token,
> latency, and monetary cost is required for that improvement?

Secondary:

> If such a quality advantage exists, can heterogeneous routing preserve most
> of it while using less strong-model compute?

The experiment is about **quality × cost × difficulty**, not token overhead
alone. Multi-agent decomposition *may* become advantageous as task depth,
breadth and horizon increase; that is a hypothesis, not an assumption. Valid
conclusions include:

* no advantage;
* an advantage only in a narrow difficulty range;
* a substantial advantage;
* an advantage too expensive to justify.

### 19.2 The four Stage-0.5 tasks: EASY controls

`shipping_inch_dimensions`, `settings_list_fields`, `rename_max_connections`
and `sla_weekend_hours` are retained as the **EASY control stratum**
(`easy_control`). They are not calibrated and are not used to infer hard-task
behaviour. They get no extra repetitions: n = 1 per architecture, reported
separately and never pooled with calibrated strata. No E2 runs happen there.

The frozen section-18.7 decision stands:

| Control data | Source |
| --- | --- |
| Single Strong, 3 tasks | exact-parity historical Arm A |
| Single Strong, `shipping_inch_dimensions` | one fresh `S2_SS` run |
| All-Strong Multi, 3 tasks | exact-parity historical Arm B (`settings_list_fields`, `rename_max_connections`, `sla_weekend_hours`), checked mechanically by `stage2.multi_strong_parity` |
| All-Strong Multi, `shipping_inch_dimensions` | one fresh `S2_M` run |

The exact-parity check for historical Arm B covers every item of 18.7, plus
byte-identical Arm-B prompts for all five invocations. Control runs use the
historical smoke limits.

### 19.3 Candidate pool

The pool has 12 tasks in 4 families on 3 codebases. Task ids are neutral;
family, design and difficulty are analysis-only.

| Family | ledgerly | flowq | docpipe |
| --- | --- | --- | --- |
| A deep diagnosis (symptom far from cause; several credible hypotheses) | `s2t01_ledgerly` | `s2t05_flowq` | `s2t09_docpipe` |
| B cross-subsystem change (several components and contracts) | `s2t02_ledgerly` | `s2t06_flowq` | `s2t10_docpipe` |
| C long-horizon migration (coordinated edits, compatibility preserved) | `s2t03_ledgerly` | `s2t07_flowq` | `s2t11_docpipe` |
| D hidden regression / adversarial correctness | `s2t04_ledgerly` | `s2t08_flowq` | `s2t12_docpipe` |

The codebases, each with its own conventions documents that a developer can
read:

* **`ledgerly`**: double-entry bookkeeping, 11 modules. Money and minor units,
  dated exchange rates, journal, ledger with periods, reports, bank and
  rate-file import, JSON persistence, revenue splits.
* **`flowq`**: a workflow engine on a simulated clock, 10 modules. Spec
  versions, dependency graph, scheduler, retries with backoff, resource
  capacity, crash/resume snapshots, reports.
* **`docpipe`**: a Markdown subset to HTML and text, 12 modules. Lexer, inline
  parser, parser, heading ids, TOC, footnotes, two renderers with an extension
  API, render cache, link safety.

Every fixture has the following, all checked by `tests/test_fixture_stage2.py`
without Claude:

* a deterministic base commit;
* visible tests that pass on the unfixed code;
* a held-out verifier of 6–8 independent tests, each named after its
  constraint; the unfixed code passes 0–3 of them;
* a reference solution (`tasks/holdout/<task>/reference/`);
* failure-before-fix and success-after-reference proofs;
* 1–2 plausible naive variants that pass the visible tests but fail the
  verifier;
* the content and path/marker leakage detectors;
* analysis-only metadata isolated in `tasks/holdout/<task>/design.json`. It is
  never copied into a workspace and is flagged by the frozen isolation marker
  `tasks/holdout`.

Per codebase, every task's fixed tree is the same correct codebase. Difficulty
comes from software-engineering reasoning only. There are no obfuscated names,
filler files, trivia, hidden information or flaky tests: everything a
developer needs is in the statement, code, docs or tests.

### 19.4 Design metadata (descriptive, never a score)

`design.json` records:

* the design features:
  - `task_family`;
  - `estimated_reasoning_depth`;
  - `subsystems_touched`;
  - `expected_minimum_edit_scope`;
  - `hidden_constraints_count`;
  - `plausible_root_causes`;
  - `cross_file_dependency_count`;
  - `requires_regression_preservation`;
  - `requires_backward_compatibility`;
* the eight dimensions, each rated none/low/medium/high: depth, breadth,
  long_horizon, hidden_interaction, misleading_evidence, constraint_density,
  independent_verification_value, search_space_width;
* the held-out constraints.

There is **no scalar difficulty score**. Empirical difficulty comes only from
Phase C.

| Task | Depth | Subsystems | Min. files | Hidden constraints | Back-compat |
| --- | --- | --- | --- | --- | --- |
| s2t01_ledgerly | 5 | 5 | 1 | 5 | no |
| s2t02_ledgerly | 2 | 7 | 5 | 6 | yes |
| s2t03_ledgerly | 2 | 7 | 6 | 5 | yes |
| s2t04_ledgerly | 3 | 3 | 1 | 6 | no |
| s2t05_flowq | 4 | 4 | 1 | 5 | yes |
| s2t06_flowq | 3 | 6 | 7 | 6 | yes |
| s2t07_flowq | 2 | 4 | 4 | 6 | yes |
| s2t08_flowq | 3 | 3 | 2 | 4 | yes |
| s2t09_docpipe | 4 | 5 | 1 | 5 | no |
| s2t10_docpipe | 2 | 9 | 10 | 6 | yes |
| s2t11_docpipe | 3 | 6 | 6 | 6 | yes |
| s2t12_docpipe | 2 | 3 | 1 | 6 | yes |

### 19.5 Phase C: calibration (Single Strong only)

* **Runs.** Only `S2_SS` runs on the 12 candidates, tagged
  `stage2_phase = "calibration"` (`runner.py calibrate`).
* **Sequential rule** (`analysis/difficulty.py`, `DIFFICULTY_PROTOCOL_VERSION`
  1):
  - 3 valid runs;
  - 3/3 successes completes the task (all-success early stop);
  - otherwise 2 more, 5 in total.

  An invalid run is not counted and is replaced by the next repeat id. After
  more than 2 replacements the task is *unresolved* and reported.
* **Strata**, from the pooled rate p = k/n:

  | Stratum | Rate |
  | --- | --- |
  | easy | p ≥ 0.9 |
  | medium | 0.7 ≤ p < 0.9 |
  | hard | 0.4 ≤ p < 0.7 |
  | very_hard | 0 < p < 0.4 |
  | beyond | p = 0 |

  With n = 5: 5 → easy, 4 → medium, 3 or 2 → hard, 1 → very_hard,
  0 → beyond. With n = 3, 3/3 → easy.
* **Targets, not thresholds.** The calibration targets (Easy ≈ 90–100%,
  Medium ≈ 70–90%, Hard ≈ 40–70%, Very Hard ≈ 10–40%) are not enforced by
  deleting tasks. No candidate is edited or dropped after calibration except
  by the fixed caps. If calibration produces no hard strata, that is reported;
  a new pool would need a new preregistration.
* **Freeze.** `runner.py difficulty-freeze` writes
  `results/stage2/difficulty_labels.json`: strata, counted run ids and a
  calibration-run hash. It refuses if any candidate is incomplete or if the
  labels are already frozen. After the freeze, `calibrate` refuses to run.
* **Benchmark selection.**
  - Caps per stratum: easy 2, medium 3, hard 3, very_hard 3, beyond 2.
  - Over a cap: round-robin over families in fixed order, then
    sha256(task_id).
  - Nothing but the stratum and the design family is used.

### 19.6 Separating calibration from evaluation: a split-sample design

Calibration runs choose strata only. **Phase E re-measures every architecture,
Single Strong included, with fresh independent runs.**

Selecting tasks because Single Strong failed in calibration biases its
calibration success rate downward (regression to the mean). Reusing those same
runs in the comparison would inflate any multi-agent gain.

Enforcement:

* `difficulty.calibration_entries` accepts only calibration-phase `S2_SS` runs
  on candidates.
* `quality_cost.evaluation_entries` accepts only evaluation-phase runs.
* Evaluation results cannot change a label (tests 4–5).
* Every evaluation run records the labels hash and its stratum as metadata,
  never in a prompt (test 13).

Alternatives considered:

* A random task split or a hierarchical model: neither fits 12 tasks within
  this budget better than repeated split-sample measurement.
* Reusing calibration runs: rejected for the bias above.

### 19.7 Architectures, limits and identity

| Arch. | Configuration | Roles | Notes |
| --- | --- | --- | --- |
| A Single Strong | `S2_SS` | one STRONG agent | Arm A prompt and tools |
| M All-Strong Multi | `S2_M` (**new**) | Coordinator, Investigator and Implementer all STRONG | Arm-B topology and prompts under Stage-2 identity and routing verification. Not historical Arm B unless exact parity (19.2). |
| H1 Strong-Investigator Hybrid | `S2_R1` | CHEAP / STRONG / CHEAP | section 18 |
| H2 Strong-Implementer Hybrid | `S2_R2` | CHEAP / CHEAP / STRONG | section 18 |
| CM All-Cheap Multi | `S2_R3` | all CHEAP | section 18 |
| CS Single Cheap | `S2_S` | one CHEAP agent | section 18 |

C1 and every Layer-3 optimization are excluded. The effort policy stays
`cli_default_not_passed` (18.4). No prompt changes.

**Limits.** In the calibrated strata every architecture gets
`config.STAGE2_LIMITS`: 60 turns and 1,800 s per session.

* The smoke limits (25 turns, 900 s) were sized for tasks where Single Strong
  used at most 18 turns. On harder tasks they would bind first for the single
  agent's one session and could manufacture a multi-agent advantage.
* Limit terminations stay outcomes (18.10), and limit hits are reported per
  architecture.
* Disclosed asymmetry: a multi-agent run gets the per-session cap for each of
  its sessions, exactly as historical Arm B did.
* The EASY controls keep `SMOKE_LIMITS`, for historical parity.

| Identity item | Value |
| --- | --- |
| amendment base config (`RunConfig(limits=STAGE2_LIMITS)`) | `38c37c95c6160f3a17fc71d910e4f6fb` |
| `S2_SS` / `S2_M` | `7b8ebe0d1074c2ae2cc984bc63c59e30` / `ecbff88e07b7f4255cd7ab0e22ab9503` |
| `S2_R1` / `S2_R2` | `423975fef4e6c99390df411b65b0ab73` / `67b4604c80555090d15190521e94c09a` |
| `S2_R3` / `S2_S` | `2fa77f9c107074e39c5d389f8647d5f8` / `cdd156bf965213748ee194a71ec2b2b2` |
| `S2_M` under the smoke limits (EASY controls) | `d5459739f84ac4c5202bb144b45507da` |

The section-18 hashes are unchanged. Every run's `stage2_identity` adds the
task base commit, the verifier hash and the classifier versions.

### 19.8 Execution plan (evidence-efficient; fixed now)

1. **Phase C1**: calibration (19.5), then the freeze.
2. **Phase E1**: `S2_SS` vs `S2_M` on every frozen benchmark task.
   * Each gets 3 runs.
   * If both are 3/3 or both are 0/3, stop. Otherwise both go to 5.
   * The rule looks at both arms symmetrically and **never at which one is
     ahead**.
   * The EASY controls get n = 1 (19.2).
3. **Phase E2**, per stratum. It runs only when E1 is complete for that stratum
   **and** the pooled Δ = rate(M) − rate(A) ≥ **0.15**. Then `S2_R1`, `S2_R2`,
   `S2_R3` and `S2_S` get 3 runs each on every task of the stratum.
   * Otherwise the stratum gets no routing runs.
   * There is no extension in E2.
   * `runner.py evaluate` refuses E2 architectures while the gate is closed.

`runner.py evaluation-plan` prints the next preregistered runs. Invalid runs are
replaced by the next repeat id and disclosed. A routing mismatch stops Stage 2
(18.10). Paid overage stops execution; high utilization alone does not. Raw
checksums go to `run_manifests/stage2/`, so `historical_run_ids()` stays
unchanged.

### 19.9 Quality metrics (mechanical; `analysis/quality.py`)

No LLM judge:

* **Q1 held-out success**: the verifier's exit code (SOLVED).
* **Q2 held-out test fraction**: tests passed / tests defined. From
  `pytest -rA`; an unreported test counts as not passed.
* **Q3 regression preservation**: visible tests and held-out `test_r_*` tests.
* **Q4 patch scope**: files changed, lines added and removed, unexpected files.
  Reported only; smaller is not assumed to be better.
* **Q5 constraint satisfaction**: constraints whose `test_c<k>_*` tests all
  pass, over the total.
* **Q6 reliability**: success frequency over repetitions.

### 19.10 Cost metrics and derived economics

Per run:

* total input, uncached input, cache read, cache write, output, and thinking
  where exposed;
* API-equivalent cost, split by model for heterogeneous configurations (18.8);
* wall time;
* tool calls, model calls, strong-model calls, cheap-model calls.

Per architecture, stratum and task:

* **cost per successful solution** = total cost over all attempts / successes.
  It is *undefined* (infinite) at zero successes; no finite number is invented.
* **expected cost to success** = mean cost per attempt / estimated success
  probability. Labelled an estimate; undefined at p̂ = 0.
* wall time per success;
* tokens per success.

### 19.11 Statistics

Per cell:

* n and successes;
* success rate with a **Wilson** 95% interval (z = 1.959963984540054);
* mean, median and range of cost and wall time.

Rates are pooled within a stratum, with per-task tables alongside. Effect sizes
and uncertainty only: **no significance is claimed from 3–5 runs**.

### 19.12 Primary comparison, crossover, token ratios

The primary comparison is **Single Strong vs All-Strong Multi per stratum**:

* both success rates, the absolute and relative difference;
* cost per run and per success;
* wall time per run and per success;
* tokens per run and per success.

Ratios are reported per stratum, M/A and each hybrid/A: total input, output,
cache write, cost and wall time.

**Crossover**: the first stratum, in difficulty order, with Δ ≥ 0.15. *No
crossover observed* is a valid result.

Token question: does the Multi/Single token ratio grow, shrink or stay stable
as difficulty rises? The Stage 0–1 easy tasks gave a median of about 1.8×
input. The competing hypotheses, all recorded as plausible:

* **H-overhead-constant**: roughly fixed session and handoff overhead, so the
  ratio shrinks as intrinsic work grows.
* **H-fanout-growth**: larger handoffs and duplicated contexts, so the ratio
  stays high or grows.
* **H-quality-efficiency**: even with more tokens per run, tokens *per success*
  may fall if success rises enough.

They are distinguished by the trend of the per-run ratios across strata and by
the tokens-per-success and cost-per-success ratios.

### 19.13 Decision tree and frozen thresholds (`quality_cost.decide`)

Per stratum:

* **Ceiling.** If the Single Strong evaluation rate is ≥ 0.9 in a non-easy
  stratum, the outcome is *ceiling, not interpretable as a hard-task
  comparison*. The labels are not changed, and it is never reported as
  "multi-agent ineffective".
* **Floor.** If every architecture is ≤ 0.1: *beyond current capability*, with
  no cost conclusions.
* **Otherwise,** by Δ = rate(M) − rate(A):
  - |Δ| < 0.15 → *multi-agent unnecessary at this difficulty*;
  - Δ ≤ −0.15 → *multi-agent worse*;
  - Δ ≥ 0.15 → test H1, then H2, as below.
* **H1** preserves most of the gain iff rate(H1) − rate(A) ≥ **0.8** × Δ
  **and** H1's mean cost per run is below M's. If it does → *heterogeneous
  routing useful*.
* If not, the same test for **H2** → *implementation capability more important
  than expected*.
* If neither → *All-Strong capability may be necessary*.

Cost is then assessed separately (cost per success, the frontier). Raw rates
are always reported alongside every threshold decision.

### 19.14 Quality–cost frontier (`quality_cost.frontier`)

X dominates Y iff X is at least as successful and no more expensive, with at
least one strict improvement. Per stratum, the nondominated sets are computed
for:

* success rate vs mean API-equivalent cost;
* success rate vs mean strong-model cost (for routing).

`runner.py stage2-frontier --json OUT` exports the plot-ready points. No graph
is needed now.

### 19.15 Roadmap

1. **Layer 1**: does multi-agent decomposition improve difficult-task quality?
2. **Layer 2**: can heterogeneous routing preserve that gain at lower cost?
3. **Layer 3**, only if multi-agent decomposition is quality-justified: profile
   the remaining overhead and reconsider shared context, artifact references,
   compact structured handoffs, semantic IR and KV/prefix mechanisms.

Layer 3 is **not** implemented now.

Agent IR, artifact protocols and other communication mechanisms are not
abandoned permanently. They are deferred until both hold:

* multi-agent decomposition demonstrates a measurable quality or reliability
  advantage;
* communication or context remains a material part of cost.

If those conditions never occur, the mechanisms remain unjustified.

### 19.16 Budget estimate

| Phase | Runs | Sessions |
| --- | --- | --- |
| C: calibration | 36–60 (12 × 3–5) | 1 per run |
| E1: A vs M | 72–120 (≤ 12 tasks × 2 × 3–5), plus 2 EASY-control runs | A: 1, M: 5 |
| E2: routing, gated | 0–144 (12 per task, gated strata only) | H1/H2/CM: 5, CS: 1 |
| Total | about 110–326 | |

The rough API-equivalent cost scales historical per-run costs by 1.5–3× for
harder tasks. Historically, Single Strong cost 0.09–0.26 USD and Arm B 0.30–0.60
USD per run:

* calibration ≈ 10–45 USD;
* E1 ≈ 50–250 USD;
* E2 ≈ 0–200 USD.

These are not amounts paid: execution is on the subscription.

### 19.17 Accepted confounds

* **Designer bias.** The fixtures were designed by people who know the
  solutions; calibration measures difficulty empirically rather than trusting
  the design.
* **Scale and repetition.** The codebases are small and n is small.
* **Frozen setup.** The prompts are frozen from Stage 0 and not tuned per
  model, and the results depend on Claude Code 2.1.260 specifics and model
  defaults.
* **Turn-cap asymmetry.** Multi-agent runs get a per-session turn cap for each
  of their sessions.
* **Timing.** Calibration and evaluation run at different times; model drift
  between them is not observable.
* **Easy controls.** They are n = 1 and use the smoke limits.

### 19.18 First commands (from `stage0/`, CLI pinned)

    $env:STAGE0_CLAUDE_CLI = "$env:APPDATA\Claude\claude-code\2.1.260\claude.exe"
    python runner.py diagnose
    python runner.py calibrate --task s2t01_ledgerly --repeat-id 1
    ... repeat ids 1-3 for each of the 12 candidates, then as difficulty-summary lists
    python runner.py difficulty-summary
    python runner.py difficulty-freeze
    python runner.py evaluation-plan
