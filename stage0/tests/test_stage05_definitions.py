"""Freeze guard for Stage 0.5 (PREREGISTRATION section 15, 2026-09-11).

Everything here was fixed BEFORE any Stage-0.5 Claude run. A later change needs
a version bump and a dated amendment. The frozen Stage-0 definitions remain
pinned by tests/test_frozen_definitions.py, which is unchanged.
"""

from __future__ import annotations

import pytest

import config
from analysis import decomposition, handoff, isolation, leakage, metrics, overlap_v2
from analysis import report as report_mod
from harness import workspace as ws_mod
from tasks import registry

NEW_TASK_BASE_COMMITS = {
    "shipping_inch_dimensions": ("4bdd314c02a47a7d31c20c0ea04a5b3d13a1b61a",
                                 "23d2e4ec07a42417bfb65f043b29b874ab21abcc"),
    "settings_list_fields": ("4b5d6496d774d604f43fcbf1b50052ee7ef92bcc",
                             "2f8d0acf36931609f57782296c1ee4c4487b68bd"),
    "rename_max_connections": ("58be8a085a3f289ab4292401935474c39f7de747",
                               "fcc1c3c9c59f14ee8975b29f1a9b1c3aed51b876"),
    "sla_weekend_hours": ("934585d4a7b1eeb652f499e147fc6a07ab795dc9",
                          "780e05cc41fa33a992b196648688fd907a207806"),
}


def test_stage05_versions_and_constants():
    assert overlap_v2.OVERLAP_V2_VERSION == 1
    assert overlap_v2.V2_MIN_ALNUM == 12
    assert overlap_v2.V2_MIN_LINES_PER_FILE == 2
    assert decomposition.DECOMPOSITION_VERSION == 1
    assert leakage.LEAKAGE_CONTENT_VERSION == 1
    assert leakage.MIN_PROTECTED_LINES == 3


def test_stage0_frozen_definitions_are_untouched():
    assert handoff.HANDOFF_SCHEMA_VERSION == 1
    assert isolation.ISOLATION_CHECK_VERSION == 1
    assert metrics.REACQUISITION_CLASSIFIER_VERSION == 1
    # amendment 7 (turn-limit counting) is the only change since the Stage-0.5 freeze
    assert config.RunConfig().config_hash() == "9edbfb5d0d082d49a61969068fafd4ac"


@pytest.mark.parametrize("task_id", sorted(NEW_TASK_BASE_COMMITS))
def test_new_task_base_commits_are_pinned(task_id, tmp_path):
    task = registry.get_task(task_id)
    ws = ws_mod.prepare_workspace(task.source_tree, tmp_path / "w",
                                  exclude=(task.verifier_dest_name,),
                                  read_only_paths=task.read_only_paths)
    assert (ws.base_commit, ws.tree_hash()) == NEW_TASK_BASE_COMMITS[task_id]


def test_six_task_shapes_are_registered():
    # The Stage-2 difficulty candidates (PREREGISTRATION 19.3) are the only additions.
    assert set(registry.all_tasks()) == {"palindrome_punctuation", "cart_invoice_rounding",
                                         *NEW_TASK_BASE_COMMITS, *config.STAGE2_CANDIDATES}


def test_historical_runs_are_exactly_the_manifested_real_runs():
    assert report_mod.historical_run_ids() == {
        "20260910T122445Z_palindrome_punctuation_A_r1",
        "20260910T125941Z_palindrome_punctuation_A_r1",
        "20260910T230448Z_palindrome_punctuation_B_r1",
        "20260911T004656Z_cart_invoice_rounding_A_r1",
        "20260911T004825Z_cart_invoice_rounding_B_r1",
    }


def test_preregistration_records_stage05_before_any_run(request):
    text = (request.config.rootpath / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "## 15. Stage 0.5" in text
    for name in ("session_fanout_context", "handoff_context",
                 "discretionary_information_reacquisition", "edit_precondition_associated",
                 "unique_downstream_acquisition", "handoff_repository_overlap_v1",
                 "handoff_repository_overlap_v2", "held_out_content_check"):
        assert name in text, name
    for q in ("Is Arm B overhead consistent across different task shapes?",
              "Is discretionary reacquisition common or task-specific?",
              "Does Investigator work produce unique useful information?",
              "Is natural-language handoff duplication consistently material?",
              "Is session/context fanout larger than handoff + reacquisition?",
              "Which category appears to offer the largest realistic optimization opportunity?",
              "Are there tasks where Arm B improves success despite higher cost?"):
        assert q in text, q
    for branch in ("Branch A", "Branch B", "Branch C", "Branch D"):
        assert branch in text
    assert "4 tasks × (Arm A + Arm B) × 1 repeat" in text
    for base, _tree in NEW_TASK_BASE_COMMITS.values():
        assert base in text
