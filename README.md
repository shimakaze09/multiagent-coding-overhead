# Multi-Agent Coding Overhead

An empirical study of whether conventional multi-agent coding workflows justify
their additional context and token cost. The study compares a single coding agent
with a natural-language multi-agent team, and with a shared-context variant of
that team, on deterministic coding tasks. It runs on real Claude Code sessions
with exact provider usage telemetry.

**Read the results:**
- [**`RESULT.md`**](RESULT.md): 1–2 page executive summary
- [**`FINAL_RESEARCH_REPORT.md`**](FINAL_RESEARCH_REPORT.md): the full, standalone research report
- [`PREREGISTRATION.md`](PREREGISTRATION.md): frozen definitions, hypotheses and every disclosed amendment

## Research question

The project began by asking whether the overhead of multi-agent coding could be
reduced through:

- structured communication between agents;
- artifact references instead of re-sending or re-reading content;
- shared state or context;
- a compact "Agent IR" for inter-agent messages;
- session reuse.

Before building any of that architecture, it **measured where the overhead
actually comes from**.

## Experimental arms

| Arm | Workflow |
| --- | --- |
| **A: Single Agent** | One coding agent solves the task directly. |
| **B: Conventional Multi-Agent** | Coordinator → Investigator → Coordinator → Implementer → Coordinator. The Investigator and Implementer run in separate physical Claude Code sessions and communicate through natural-language handoffs. |
| **C1: Shared Worker Context** | The same logical multi-agent workflow, but the Investigator and Implementer reuse the same physical Worker session (the Investigator session is resumed as the Implementer). |

All arms use the same model, the same tool permissions, the same task fixtures
and the same held-out verifiers.

## Main findings

Across **six valid A/B coding-task pairs** (n = 1 run per arm per task, so
the results are descriptive):

- both Arm A and Arm B solved all six tasks;
- Arm B used about **1.84×** the input tokens on average;
- Arm B produced about **3.39×** the output;
- Arm B took about **3.27×** the wall time;
- Arm B had about **2.84×** the API-equivalent cost;
- no task showed a success advantage from the multi-agent workflow.

Where the extra cost came from:

- **Discretionary repository rereading was small.** Most rereads were required
  by the Edit tool, which will not edit a file that hasn't been read in the same
  session.
- **Natural-language handoff duplication existed but was not the main cost.**
- **Session/context fanout was the largest measured overhead.** Every extra
  session re-sends its own large starting context on every model call.

**C1 (shared Worker context)**, tested on four tasks:

- reduced fresh-session context;
- reduced handoff duplication;
- sharply reduced repository rereads;
- preserved correctness on all four tasks.

But pooled across the four tasks:

- total input **increased** about 7.1%;
- cache writes **increased** about 11.1%;
- API-equivalent cost **increased** about 4.7%;
- wall time **decreased** only about 5%.

> Context reuse changed where the context cost was paid more than how much
> context cost was paid.

## Conclusion

For the tested small-to-medium deterministic coding tasks, the tested multi-agent
decomposition introduced substantial resource overhead without improving task
success. The measured optimization opportunities were not large enough to justify
further engineering of Agent IR, artifact protocols, or deeper shared-context
mechanisms.

This is **not** a claim that multi-agent systems are universally ineffective.
Other workloads and setups may produce different results:

- harder tasks where a single agent sometimes fails;
- heterogeneous specialist models;
- genuine parallelism;
- independent review;
- local inference with explicit KV-cache control.

## Reproducibility

- **Frozen Git commits:** each stage's definitions were committed before its
  runs, and every amendment has its own commit.
- **Held-out verifiers:** correctness is the exit code of hidden tests injected
  after the agent finishes. No LLM judge.
- **JSONL source telemetry:** the raw Claude Code stream for every session, plus
  a normalized event log.
- **Rebuildable SQLite index:** derived data, always regenerated from the raw
  logs.
- **Checksum manifests:** SHA-256 for every raw artifact of all 18 real runs, in
  [`run_manifests/`](run_manifests/). The raw run telemetry itself
  is intentionally git-ignored (see [`.gitignore`](.gitignore)).
  The manifests let a copy of it be verified byte for byte.
- **Disclosed instrumentation amendments:** each one records the defect, its
  effect, and whether raw data changed or a rerun occurred. This includes one
  invalid run that is preserved and reported.
- **Negative and null findings preserved:** hypotheses the data did not support
  are reported as such.

The local test suite runs without launching Claude, from the repository root:

```bash
python -m pytest tests -q
```

The harness, arms, analysis code and task fixtures are at the repository root; [`HARNESS.md`](HARNESS.md) documents how to run them.
