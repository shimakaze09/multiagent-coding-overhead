# Result: multi-agent coding overhead (Stage 0 → Stage 1)

The full report is in `FINAL_RESEARCH_REPORT.md`. It uses Claude Code 2.1.260
with one model (Sonnet) and subscription billing. The data is 18 real runs: six
valid single-agent vs multi-agent task pairs, plus four shared-session runs.
Each arm ran once per task, so the results are descriptive, not statistical.

## What did we think?

That conventional natural-language multi-agent coding wastes a great deal of
context, in three ways:

- agents re-describing their findings to each other in prose;
- downstream agents re-reading what upstream agents already read;
- every agent starting as a fresh, isolated session.

Several optimizations were on the table: structured handoffs, artifact
references, a compact "Agent IR", shared context, and eventually KV-level
communication. We chose to measure the waste before building any of them.

## What did we measure?

On six small deterministic coding tasks (cross-file bugs, a feature, a
refactor, a misleading-symptom bug), we compared:

- a single agent (Arm A);
- a Coordinator → Investigator → Coordinator → Implementer → Coordinator team
  (Arm B).

Correctness came from held-out tests only. Cost came from exact provider usage:
uncached input, cache read, cache write, output. Every tool call was classified,
and every handoff stored verbatim.

## What did we discover?

- **Both arms solved all six tasks.** The team never did better.
- **The team always cost more.** Median B/A: **1.8× input, 3.4× output,
  3.4× wall time, 2.8× API-equivalent cost** (not the amount paid).
- **Rereading was not the problem.** 12 of 15 rereads were required by the
  Edit tool (it will not edit a file unread in that session). Only 3 were
  discretionary, on 2 of 6 tasks.
- **Handoffs were a secondary cost**, about 11–27% of the extra input.
- **Session fanout was the largest component**, a median of about 82% of the
  extra input (reconstructed, not exact accounting). Every extra session
  re-sends its own large starting context on every call.

## What did we try to optimize?

The largest component. In **C1**, the Investigator's session is resumed as the
Implementer, so there's one Worker session instead of two, and its report is not
sent back to it. We tested this on four tasks.

## Did it work?

**Partly, and not where it counted.**

| What C1 changed | Effect |
| --- | --- |
| Correctness | preserved, 4/4 |
| Tool-policy change on resume | worked in real Claude Code |
| Fresh-session first-call context | **−43% to −46%** |
| Handoff text | −25% (median) |
| Repository rereads | 10 → 3 |
| **Total input** | **+7.1%** pooled (median ×1.10) |
| **Cache write** | **+11.1%** pooled (higher on all 4 tasks) |
| **API-equivalent cost** | **+4.7%** pooled (median ×1.00) |
| Wall time | −5.0% pooled |

Why: the resumed session reused only the common base prefix from cache. After
the tool and role change it wrote its whole retained history to cache again,
then carried that longer history into every later call. The cost moved; it did
not disappear.

## What is the final decision?

**STOP MULTI-AGENT OPTIMIZATION FOR THIS EXPERIMENT.** No C2, no Agent IR, no
artifact-reference protocol, no KV-cache system, unless new evidence or a new
workload shows that multi-agent decomposition improves success or quality.
Without that benefit, cutting its overhead only brings it closer to a
single-agent baseline it never beat.

This is a useful negative result. Measuring first ruled out several plausible
optimization directions before substantial effort went into building them.
Possible future work belongs to new experiments: harder tasks, specialist
models, local inference with real KV control, quality objectives, long-horizon
work. The current data does not justify it as a continuation.

## Addendum (2026-09-11): the scope of this conclusion

Stage 0–1 characterized overhead in tasks where Single Strong already achieved
ceiling performance. Stage 2 investigates whether the cost–quality tradeoff
changes when that ceiling is removed (PREREGISTRATION section 19). The
conclusions above are unchanged.
