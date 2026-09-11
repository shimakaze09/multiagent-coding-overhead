"""Task definitions and lookup.

A task is: a pinned source tree, a verbatim task statement, a held-out
deterministic verifier, and a category. Nothing about arms lives here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

TASKS_DIR = Path(__file__).resolve().parent
DEFS_DIR = TASKS_DIR / "defs"
REPOS_DIR = TASKS_DIR / "repos"
HOLDOUT_DIR = TASKS_DIR / "holdout"

# Task categories for the planned pilot mixture (SPEC.md section 13):
#   4 cross-file feature, 4 cross-file bug, 2 repo-wide refactor, 2 single-file
#   control. Only enough fixture tasks to validate the harness exist now.
CATEGORIES = (
    "cross_file_feature",
    "cross_file_bug",
    "repo_wide_change",
    "single_file_control",
)


class TaskError(RuntimeError):
    pass


@dataclass(frozen=True)
class Task:
    task_id: str
    category: str
    repo: str
    statement: str
    verifier_source: str
    verifier_dest_name: str
    verifier_command: tuple[str, ...]
    visible_test_command: tuple[str, ...] = ()
    notes: str = ""
    # Glob patterns (relative to the repo root) made read-only in the workspace by
    # the harness, identically for every arm, e.g. the benchmark's visible tests.
    read_only_paths: tuple[str, ...] = ()
    # Paths a correct production fix is expected to touch. Reported only; SOLVED
    # always comes from the held-out verifier.
    expected_modified_paths: tuple[str, ...] = ()
    # Files that carry constraints/evidence but must NOT need editing. Used only
    # for the per-file reacquisition breakdown (Pair-2 hypothesis H1).
    supporting_paths: tuple[str, ...] = ()

    @property
    def source_tree(self) -> Path:
        p = REPOS_DIR / self.repo
        if not p.is_dir():
            raise TaskError(f"repo tree missing for task {self.task_id}: {p}")
        return p

    @property
    def verifier_path(self) -> Path:
        p = HOLDOUT_DIR / self.verifier_source
        if not p.exists():
            raise TaskError(f"verifier missing for task {self.task_id}: {p}")
        return p

    def as_dict(self) -> dict:
        d = asdict(self)
        d["verifier_command"] = list(self.verifier_command)
        d["visible_test_command"] = list(self.visible_test_command)
        d["read_only_paths"] = list(self.read_only_paths)
        d["expected_modified_paths"] = list(self.expected_modified_paths)
        d["supporting_paths"] = list(self.supporting_paths)
        return d


def _from_json(path: Path) -> Task:
    raw = json.loads(path.read_text(encoding="utf-8"))
    missing = [
        k
        for k in ("task_id", "category", "repo", "statement", "verifier_source",
                  "verifier_dest_name", "verifier_command")
        if k not in raw
    ]
    if missing:
        raise TaskError(f"task def {path.name} missing fields: {missing}")
    if raw["category"] not in CATEGORIES:
        raise TaskError(f"task def {path.name} has unknown category {raw['category']!r}")
    return Task(
        task_id=raw["task_id"],
        category=raw["category"],
        repo=raw["repo"],
        statement=raw["statement"],
        verifier_source=raw["verifier_source"],
        verifier_dest_name=raw["verifier_dest_name"],
        verifier_command=tuple(raw["verifier_command"]),
        visible_test_command=tuple(raw.get("visible_test_command", ())),
        notes=raw.get("notes", ""),
        read_only_paths=tuple(raw.get("read_only_paths", ())),
        expected_modified_paths=tuple(raw.get("expected_modified_paths", ())),
        supporting_paths=tuple(raw.get("supporting_paths", ())),
    )


def all_tasks() -> dict[str, Task]:
    out: dict[str, Task] = {}
    for path in sorted(DEFS_DIR.glob("*.json")):
        task = _from_json(path)
        if task.task_id in out:
            raise TaskError(f"duplicate task_id: {task.task_id}")
        out[task.task_id] = task
    return out


def get_task(task_id: str) -> Task:
    tasks = all_tasks()
    if task_id not in tasks:
        raise TaskError(
            f"unknown task_id {task_id!r}; available: {sorted(tasks) or 'none'}"
        )
    return tasks[task_id]
