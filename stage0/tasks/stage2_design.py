"""Stage-2 task design metadata (PREREGISTRATION section 19.4). ANALYSIS ONLY.

Each candidate task has ``tasks/holdout/<task_id>/design.json`` next to its
held-out verifier, reference solution and naive variants: under the held-out
directory, so the frozen isolation check (marker ``tasks/holdout``) flags any
agent access. Prompts are built from ``Task.statement`` alone and never read
this module or those files.

The design features describe the task as built. They are not a difficulty
score: empirical difficulty comes only from Phase-C calibration.
"""

from __future__ import annotations

import json
from typing import Optional

from tasks.registry import HOLDOUT_DIR

FAMILIES = ("deep_diagnosis", "cross_subsystem_change", "long_horizon_migration",
            "hidden_regression")
DESIGN_FEATURES = (
    "task_family", "estimated_reasoning_depth", "subsystems_touched",
    "expected_minimum_edit_scope", "hidden_constraints_count", "plausible_root_causes",
    "cross_file_dependency_count", "requires_regression_preservation",
    "requires_backward_compatibility",
)
DIFFICULTY_DIMENSIONS = (
    "depth", "breadth", "long_horizon", "hidden_interaction", "misleading_evidence",
    "constraint_density", "independent_verification_value", "search_space_width",
)
DIMENSION_LEVELS = ("none", "low", "medium", "high")


def design_path(task_id: str):
    return HOLDOUT_DIR / task_id / "design.json"


def load_design(task_id: str) -> Optional[dict]:
    p = design_path(task_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def reference_dir(task_id: str):
    return HOLDOUT_DIR / task_id / "reference"


def naive_dir(task_id: str):
    return HOLDOUT_DIR / task_id / "naive"
