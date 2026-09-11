# Workflow documents

```json
{"backoff_s": 5,
 "jobs": {
   "Build":      {"duration": 10, "priority": 1, "retries": 2},
   "Unit tests": {"deps": ["Build"], "duration": 20}}}
```

Per job: `deps` (default none), `duration` in seconds (default 1), `priority`
(default 0), `retries`: extra attempts after the first (default 0). Workflow
level: `backoff_s` (default 1). Unknown keys are errors.

After failed attempt *n* (1-based) a job with retries left waits
`min(backoff_s * 2 ** (n - 1), 60)` seconds before its next attempt. Every job
holds one `cpu` while running.

## Names

Job names are case-insensitive and whitespace-insensitive: `"Unit tests"`,
`"unit  TESTS"` and `" unit tests "` are the same job (dependencies may use any
spelling). The display name, exactly as first written, is what appears in
logs, summaries, snapshots and dumped documents.
