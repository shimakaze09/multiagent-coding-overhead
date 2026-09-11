# Workflow documents

## Version 2 (current)

```json
{"version": 2,
 "jobs": {
   "Build":      {"duration_s": 10, "priority": 1,
                  "retry": {"max_attempts": 3,
                            "backoff": {"initial_s": 5, "factor": 2, "max_s": 60}},
                  "resources": {"cpu": 2}},
   "Unit tests": {"deps": ["Build"], "duration_s": 20}}}
```

Defaults: `deps` none, `duration_s` 1, `priority` 0, `retry.max_attempts` 1
(no retries), backoff `initial_s` 1, `factor` 2, `max_s` 60, `resources`
`{"cpu": 1}`. Unknown keys are errors.

After failed attempt *n* (1-based) the job waits
`min(initial_s * factor ** (n - 1), max_s)` seconds before its next attempt.

## Version 1 (flowq 1.x, deprecated)

Documents without `"version"` are version 1. Per job: `deps`, `duration`,
`priority`, `retries` (extra attempts after the first). Workflow level:
`backoff_s` (default 1). Loading one emits a `DeprecationWarning`. It means
exactly `max_attempts = retries + 1`, `initial_s = backoff_s`, `factor = 2`,
`max_s = 60`, `resources = {"cpu": 1}`, and runs exactly as it did in 1.x.

## Names

Job names are case-insensitive and whitespace-insensitive: `"Unit tests"`,
`"unit  TESTS"` and `" unit tests "` are the same job (dependencies may use any
spelling). The display name, exactly as first written, is what appears in
logs, summaries, snapshots and dumped documents.
