"""The SQLite index is DERIVED ONLY.

Required property: run -> build DB -> delete DB -> re-ingest raw logs -> same
analytical result.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from analysis import ingest, metrics, report as report_mod
from harness import telemetry, tools


TABLES = (
    "runs",
    "sessions",
    "events",
    "tool_calls",
    "file_reads",
    "searches",
    "agent_messages",
    "verifications",
    "unknown_events",
    "usage_reports",
    "content_shingles",
    "bash_classifications",
    "rate_limit_events",
    "permission_denials",
    "model_usage",
    "reacquisition_findings",
    "handoff_duplication",
    "run_integrity",
)


def _snapshot(db_path: Path) -> dict:
    """A stable, comparable projection of the whole database."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        out: dict = {}
        for table in TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            # drop autoincrement primary keys: they are storage detail, not data
            cleaned = []
            for r in rows:
                d = {k: r[k] for k in r.keys() if not k.endswith("_pk")}
                cleaned.append(d)
            cleaned.sort(key=lambda d: json.dumps(d, sort_keys=True, default=str))
            out[table] = cleaned
        return out
    finally:
        conn.close()


@pytest.fixture(scope="module")
def built_db(mock_runs, tmp_path_factory):
    runs = ingest.discover_runs(mock_runs["runs_dir"])
    assert len(runs) == 2
    db = tmp_path_factory.mktemp("db") / "stage0.sqlite3"
    ids = ingest.build(db, runs)
    assert len(ids) == 2
    return db, runs


# --------------------------------------------------------------------------
# Rebuildability
# --------------------------------------------------------------------------


def test_schema_creates_every_expected_table(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    try:
        names = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    for t in TABLES:
        assert t in names, t


def test_delete_and_reingest_reproduces_the_same_database(built_db, tmp_path):
    db, runs = built_db
    before = _snapshot(db)

    rebuilt = tmp_path / "rebuilt.sqlite3"
    ingest.build(rebuilt, runs)
    after = _snapshot(rebuilt)

    assert set(before) == set(after)
    for table in TABLES:
        assert before[table] == after[table], f"table {table} differs after re-ingest"


def test_reingest_is_deterministic_over_three_builds(built_db, tmp_path):
    _db, runs = built_db
    snaps = []
    for i in range(3):
        p = tmp_path / f"db{i}.sqlite3"
        ingest.build(p, runs)
        snaps.append(_snapshot(p))
    assert snaps[0] == snaps[1] == snaps[2]


def test_analytical_result_survives_db_deletion(built_db, tmp_path):
    """The headline numbers come from raw artifacts, not from the database."""
    db, runs = built_db
    b_dir = next(d for d in runs if "_B_" in d.name)

    first = report_mod.run_report(b_dir)
    db.unlink()
    assert not db.exists()
    second = report_mod.run_report(b_dir)

    for key in (
        "solved",
        "tool_calls_total",
        "read_calls",
        "inter_agent_overlapping_acquisitions",
        "primed_reacquisitions",
        "unprimed_overlapping_discoveries",
        "concurrency_excluded_pairs",
        "total_acquired_chars",
        "unique_repository_regions_read",
        "handoff_total_chars",
    ):
        assert first[key] == second[key], key
    assert first["oracle_upper_bound"] == second["oracle_upper_bound"]

    # and it can be rebuilt afterwards
    ingest.build(db, runs)
    assert db.exists()


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def test_runs_table_records_billing_and_coverage(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM runs ORDER BY arm").fetchall()
    finally:
        conn.close()
    assert [r["arm"] for r in rows] == ["A", "B"]
    for r in rows:
        assert r["solved"] == 1
        assert r["subscription_execution"] == 1
        assert r["api_charge"] == "false_expected"
        assert r["all_sessions_subscription_ok"] == 1
        assert r["arm_label_valid"] == 1
        assert r["context_residency_level"] == 2
        assert r["acquisition_coverage"] == 1.0
        assert r["coverage_meets_minimum"] == 1
        assert r["base_commit"] and r["final_tree_hash"]
        assert r["parser_version"] == telemetry.PARSER_VERSION


def test_base_commit_is_identical_across_arms(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    try:
        commits = {r[0] for r in conn.execute("SELECT base_commit FROM runs")}
    finally:
        conn.close()
    assert len(commits) == 1, "both arms must start from the same pinned base commit"


def test_sessions_table_captures_the_arm_b_topology(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.* FROM sessions s JOIN runs r ON r.run_id = s.run_id "
            "WHERE r.arm = 'B' ORDER BY s.session_index"
        ).fetchall()
    finally:
        conn.close()
    assert [r["role"] for r in rows] == [
        "coordinator", "investigator", "coordinator", "implementer", "coordinator"
    ]
    assert [r["session_index"] for r in rows] == [1, 2, 3, 4, 5]
    assert all(r["api_key_source"] == "none" for r in rows)
    assert all(r["billing_ok"] == 1 for r in rows)
    assert all(r["subagent_spawned"] == 0 for r in rows)


def test_tool_calls_reads_and_searches_are_consistent_projections(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    try:
        reads_tc = conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE acquisition_class IN "
            "('structured_read','bash_read')"
        ).fetchone()[0]
        reads = conn.execute("SELECT COUNT(*) FROM file_reads").fetchone()[0]
        searches_tc = conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE acquisition_class IN "
            "('structured_search','bash_search')"
        ).fetchone()[0]
        searches = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    finally:
        conn.close()
    assert reads_tc == reads > 0
    assert searches_tc == searches, "searches table must mirror tool_calls"



def test_file_reads_state_that_ranges_are_unavailable(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM file_reads LIMIT 5").fetchall()
    finally:
        conn.close()
    assert rows
    for r in rows:
        assert r["range_available"] == 0
        assert "does not expose byte/line ranges" in r["range_note"]
        assert r["result_sha"], "content identity must be recorded instead"


def test_shingles_are_recorded_for_overlap_analysis(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM content_shingles").fetchone()[0]
        shared = conn.execute(
            "SELECT COUNT(*) FROM (SELECT shingle FROM content_shingles "
            "GROUP BY run_id, shingle HAVING COUNT(DISTINCT agent_id) > 1)"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n > 0
    assert shared > 0, "Arm B must show content seen by more than one agent"


def test_bash_classifications_are_recorded(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM bash_classifications").fetchall()
    finally:
        conn.close()
    assert rows
    classes = {r["overall_class"] for r in rows}
    assert classes & {tools.CLASSIFIED_TEST_RUN, tools.CLASSIFIED_BASH_READ}
    assert tools.UNCLASSIFIED_BASH not in classes


def test_agent_messages_record_delivery_position_for_priming(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM agent_messages WHERE recipient = 'implementer'"
        ).fetchall()
    finally:
        conn.close()
    assert rows
    for r in rows:
        assert r["delivered_before_session_index"] == 4
        assert r["estimated_tokens_availability"] == telemetry.ESTIMATED
        assert r["body"]
        assert tools.content_sha(r["body"]) == r["sha"]


def test_usage_reports_preserve_availability(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM usage_reports").fetchall()
    finally:
        conn.close()
    assert rows
    for r in rows:
        assert r["availability"] in (
            telemetry.REPORTED, telemetry.NOT_EXPOSED, telemetry.UNRELIABLE, telemetry.ESTIMATED
        )


def test_no_unknown_events_for_a_clean_stream(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM unknown_events").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_events_table_preserves_raw_pointers(built_db):
    db, _ = built_db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE raw_session_key IS NOT NULL LIMIT 20"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert total > 0 and rows
    for r in rows:
        assert r["raw_line_no"] >= 1
        assert r["order_confidence"] == "cli_reported"


def test_ingest_ignores_a_directory_that_is_not_a_run(tmp_path):
    (tmp_path / "not_a_run").mkdir()
    assert ingest.discover_runs(tmp_path) == []
    db = tmp_path / "x.sqlite3"
    assert ingest.build(db, [tmp_path / "not_a_run"]) == []
