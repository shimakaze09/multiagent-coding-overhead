-- Stage 0 derived analytical index.
--
-- This database is DERIVED ONLY. It is fully rebuildable from the raw run
-- artifacts (metadata.json, sessions/*/claude_stdout.jsonl,
-- sessions/*/invocation.json, handoffs/*.txt, verify/verification.json,
-- events.jsonl) and is never a source of truth. Delete it and re-ingest.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS run_integrity;
DROP TABLE IF EXISTS handoff_duplication;
DROP TABLE IF EXISTS reacquisition_findings;
DROP TABLE IF EXISTS model_usage;
DROP TABLE IF EXISTS permission_denials;
DROP TABLE IF EXISTS rate_limit_events;
DROP TABLE IF EXISTS content_shingles;
DROP TABLE IF EXISTS bash_classifications;
DROP TABLE IF EXISTS usage_reports;
DROP TABLE IF EXISTS unknown_events;
DROP TABLE IF EXISTS verifications;
DROP TABLE IF EXISTS agent_messages;
DROP TABLE IF EXISTS searches;
DROP TABLE IF EXISTS file_reads;
DROP TABLE IF EXISTS tool_calls;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS runs;

CREATE TABLE runs (
    run_id                  TEXT PRIMARY KEY,
    task_id                 TEXT NOT NULL,
    task_category           TEXT,
    arm                     TEXT NOT NULL,
    repeat_id               INTEGER NOT NULL,
    created_at              TEXT,
    ended_at                TEXT,
    model                   TEXT,
    cli_version             TEXT,
    parser_version          INTEGER,
    event_schema_version    INTEGER,
    base_commit             TEXT,
    final_tree_hash         TEXT,
    solved                  INTEGER,             -- 0/1, held-out verifier
    session_count           INTEGER,
    wall_seconds            REAL,
    diff_bytes              INTEGER,
    handoff_total_chars     INTEGER,
    handoff_total_estimated_tokens INTEGER,
    acquisition_coverage    REAL,
    coverage_meets_minimum  INTEGER,
    coverage_formula_version INTEGER,
    acquisition_classified  INTEGER,
    acquisition_unknown     INTEGER,
    acquisition_candidates  INTEGER,
    non_acquisition_calls   INTEGER,
    denied_calls            INTEGER,
    reanalysis_parser_version INTEGER,
    subscription_execution  INTEGER,             -- 1 = ran on subscription
    api_charge              TEXT,                -- 'false_expected' | 'unknown' | 'api_suspected'
    all_sessions_subscription_ok INTEGER,
    arm_label_valid         INTEGER,
    context_residency_level INTEGER,
    -- token/cache telemetry: value may be NULL, availability says why
    reported_input_tokens        INTEGER,
    reported_output_tokens       INTEGER,
    reported_cache_read_tokens   INTEGER,
    reported_cache_write_tokens  INTEGER,
    reported_total_input_tokens  INTEGER,   -- input + cache_read + cache_write
    token_availability           TEXT,
    cache_availability           TEXT,
    cli_reported_cost_usd        REAL,
    cost_availability            TEXT,
    metadata_json           TEXT
);

CREATE TABLE sessions (
    session_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT NOT NULL,
    session_index           INTEGER NOT NULL,
    agent_id                TEXT NOT NULL,
    role                    TEXT,
    cli_session_id          TEXT,
    resumed_session_id      TEXT,
    model                   TEXT,
    api_key_source          TEXT,
    exit_code               INTEGER,
    termination_reason      TEXT,
    wall_seconds            REAL,
    -- turn accounting: three DIFFERENT quantities, never to be conflated
    assistant_stream_events INTEGER,        -- Claude Code stream events
    thinking_only_stream_events INTEGER,    -- standalone thinking blocks
    assistant_events_excluding_thinking_only INTEGER,
    cli_reported_num_turns  INTEGER,        -- Claude Code's own num_turns
    turns_observed          INTEGER,        -- legacy alias of assistant_stream_events
    num_turns_reported      INTEGER,        -- legacy alias of cli_reported_num_turns
    permission_denied_count INTEGER,
    rate_limit_status       TEXT,
    rate_limit_type         TEXT,
    rate_limit_utilization  REAL,
    rate_limit_is_overage   INTEGER,
    model_count             INTEGER,        -- distinct models in result.modelUsage
    tool_call_count         INTEGER,
    subagent_spawned        INTEGER,
    billing_ok              INTEGER,
    billing_note            TEXT,
    prompt_chars            INTEGER,
    prompt_estimated_tokens INTEGER,
    unknown_event_count     INTEGER,
    unparsable_lines        INTEGER,
    raw_stdout_lines        INTEGER,
    summary_json            TEXT,
    UNIQUE (run_id, session_key)
);

CREATE TABLE events (
    event_pk                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    event_id                TEXT,
    seq                     INTEGER,
    ts                      TEXT,
    task_id                 TEXT,
    arm                     TEXT,
    agent_id                TEXT,
    turn_id                 INTEGER,
    type                    TEXT,
    parent_event_id         TEXT,
    source                  TEXT,
    order_confidence        TEXT,
    raw_session_key         TEXT,
    raw_line_no             INTEGER,
    payload_json            TEXT
);
CREATE INDEX idx_events_run_seq ON events(run_id, seq);
CREATE INDEX idx_events_type ON events(run_id, type);

CREATE TABLE tool_calls (
    tool_call_pk            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT NOT NULL,
    session_index           INTEGER NOT NULL,
    agent_id                TEXT NOT NULL,
    tool_use_id             TEXT NOT NULL,
    tool_name               TEXT,
    acquisition_class       TEXT,
    confidence              TEXT,
    target_path             TEXT,
    query                   TEXT,
    command                 TEXT,
    start_line              INTEGER,
    end_line                INTEGER,
    start_ts                TEXT,
    end_ts                  TEXT,
    turn_id                 INTEGER,
    completed               INTEGER,
    is_error                INTEGER,
    permission_denied       INTEGER,        -- the CLI refused it; nothing ran
    attempted_class         TEXT,           -- what it would have been, had it run
    result_chars            INTEGER,
    result_bytes            INTEGER,
    result_sha              TEXT,
    shingle_count           INTEGER
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX idx_tool_calls_class ON tool_calls(run_id, acquisition_class);

-- file_reads and searches are projections of tool_calls, kept as their own tables
-- because the duplication analysis queries them separately.
CREATE TABLE file_reads (
    file_read_pk            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    session_index           INTEGER,
    agent_id                TEXT,
    tool_use_id             TEXT,
    path                    TEXT,
    path_version_hint       TEXT,       -- git blob sha at base commit, when resolvable
    result_sha              TEXT,       -- identity of the information actually returned
    result_chars            INTEGER,
    start_line              INTEGER,
    end_line                INTEGER,
    start_ts                TEXT,
    end_ts                  TEXT,
    acquisition_class       TEXT,
    range_available          INTEGER DEFAULT 0,   -- byte/line ranges are NOT exposed
    range_note              TEXT
);
CREATE INDEX idx_file_reads_run ON file_reads(run_id);

CREATE TABLE searches (
    search_pk               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    session_index           INTEGER,
    agent_id                TEXT,
    tool_use_id             TEXT,
    tool_name               TEXT,
    pattern                 TEXT,
    scope                   TEXT,
    result_sha              TEXT,
    result_chars            INTEGER,
    result_path_count       INTEGER,
    result_paths_json       TEXT,
    start_line              INTEGER,
    end_line                INTEGER,
    acquisition_class       TEXT
);
CREATE INDEX idx_searches_run ON searches(run_id);

CREATE TABLE agent_messages (
    message_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    kind                    TEXT,          -- 'handoff' | 'assistant_text' | 'received_handoff'
    idx                     INTEGER,
    sender                  TEXT,
    recipient               TEXT,
    label                   TEXT,
    delivered_before_session_index INTEGER,
    chars                   INTEGER,
    utf8_bytes              INTEGER,
    words                   INTEGER,
    lines                   INTEGER,
    estimated_tokens        INTEGER,       -- local heuristic, never provider tokens
    estimated_tokens_availability TEXT,
    sha                     TEXT,
    body                    TEXT
);
CREATE INDEX idx_agent_messages_run ON agent_messages(run_id);

CREATE TABLE verifications (
    verification_pk         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    solved                  INTEGER,
    exit_code               INTEGER,
    argv_json               TEXT,
    wall_seconds            REAL,
    judge                   TEXT,
    llm_judge_used          INTEGER,
    stdout_tail             TEXT
);

CREATE TABLE unknown_events (
    unknown_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    line_no                 INTEGER,
    cli_type                TEXT,
    reason                  TEXT,
    raw_keys_json           TEXT
);

CREATE TABLE usage_reports (
    usage_pk                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    scope                   TEXT,          -- 'assistant_message' | 'session_result'
    line_no                 INTEGER,
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    cache_read_tokens       INTEGER,
    cache_write_tokens      INTEGER,
    thinking_tokens         INTEGER,
    total_tokens            INTEGER,
    service_tier            TEXT,
    availability            TEXT,
    source                  TEXT
);

-- Normalized Bash categories are DERIVED data. The raw command, stdout/stderr
-- and exit status live unchanged in sessions/*/claude_stdout.jsonl.
CREATE TABLE bash_classifications (
    bash_pk                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    agent_id                TEXT,
    tool_use_id             TEXT,
    command                 TEXT,           -- raw command, verbatim
    overall_class           TEXT,           -- bash_read | bash_search | ...
    segment_classes_json    TEXT,           -- per-segment decisions, for audit
    permission_denied       INTEGER,
    attempted_class         TEXT,
    is_error                INTEGER,
    result_chars            INTEGER,
    result_text             TEXT            -- raw tool_result body, verbatim
);

CREATE TABLE rate_limit_events (
    rate_limit_pk           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    line_no                 INTEGER,
    status                  TEXT,           -- e.g. 'allowed_warning'
    rate_limit_type         TEXT,           -- e.g. 'seven_day'
    utilization             REAL,
    resets_at_epoch         INTEGER,
    is_using_overage        INTEGER,
    surpassed_threshold     REAL,
    indicates_execution_failure INTEGER,
    windows_json            TEXT,
    raw_json                TEXT
);

CREATE TABLE permission_denials (
    denial_pk               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    line_no                 INTEGER,
    tool_name               TEXT,
    tool_use_id             TEXT,
    decision_reason_type    TEXT,
    decision_reason         TEXT,
    tool_input_json         TEXT
);

CREATE TABLE model_usage (
    model_usage_pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    model                   TEXT,
    canonical_model         TEXT,
    provider                TEXT,
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    cache_read_input_tokens INTEGER,
    cache_creation_input_tokens INTEGER,
    thinking_tokens         INTEGER,
    cost_usd                REAL
);

CREATE TABLE content_shingles (
    shingle_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    session_key             TEXT,
    agent_id                TEXT,
    tool_use_id             TEXT,
    shingle                 TEXT NOT NULL,
    ordinal                 INTEGER
);
CREATE INDEX idx_shingles_run ON content_shingles(run_id, shingle);

-- One row per overlapping acquisition found by metrics.analyze. For PRIMED
-- inter-agent rows, reacquisition_subcategory partitions the gross
-- primed_reacquisition count (PREREGISTRATION amendment 5).
CREATE TABLE reacquisition_findings (
    finding_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    consumer_tool_use_id    TEXT,
    consumer_agent          TEXT,
    consumer_session_key    TEXT,
    consumer_class          TEXT,
    consumer_target         TEXT,
    producer_tool_use_id    TEXT,
    producer_agent          TEXT,
    producer_target         TEXT,
    relation                TEXT,           -- inter_agent | intra_agent
    overlap_kind            TEXT,
    overlap_ratio           REAL,
    overlap_chunks          INTEGER,
    priming                 TEXT,           -- primed | unprimed | priming_undetermined
    reacquisition_subcategory TEXT,         -- edit_precondition_associated | ... | not_applicable
    subcategory_rule        TEXT,
    subcategory_evidence_json TEXT,
    duplicate_chars         INTEGER,
    duplicate_bytes         INTEGER
);

-- Communication duplication: repository / tool / relayed content inside each
-- natural-language handoff (3-line indentation-insensitive content windows).
CREATE TABLE handoff_duplication (
    handoff_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    idx                     INTEGER,
    sender                  TEXT,
    recipient               TEXT,
    label                   TEXT,
    handoff_chars           INTEGER,
    handoff_utf8_bytes      INTEGER,
    handoff_estimated_tokens INTEGER,       -- local heuristic, never provider tokens
    informative_chunks      INTEGER,
    repository_chunks       INTEGER,
    repository_quote_chars_estimate INTEGER,
    repository_quote_fraction REAL,
    tool_output_chunks      INTEGER,
    tool_output_quote_chars_estimate INTEGER,
    relayed_chunks          INTEGER,
    relayed_chars_estimate  INTEGER,
    repository_files_quoted_json TEXT
);

-- Reported separately from SOLVED: visible tests, protected-path changes,
-- scope of the change, and the handoff -> repository reacquisition overlap.
CREATE TABLE run_integrity (
    run_id                  TEXT PRIMARY KEY REFERENCES runs(run_id),
    visible_tests_ran       INTEGER,
    visible_tests_passed    INTEGER,
    visible_tests_exit_code INTEGER,
    read_only_files_json    TEXT,
    protected_paths_changed_json TEXT,
    changed_paths_json      TEXT,
    changed_outside_expected_scope_json TEXT,
    handoff_reacquisition_overlap_json TEXT,
    isolation_check_json    TEXT            -- held-out isolation check (detection only)
);
