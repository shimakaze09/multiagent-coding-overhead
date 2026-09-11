"""Snapshots, for resuming a workflow after a crash (docs/scheduling.md).

A snapshot records every job by its display name. On restore, an interrupted
(running) job becomes ready again at the snapshot time and its interrupted
attempt is not counted. Snapshots record the engine capacity.
"""

import json
from pathlib import Path

from .clock import Clock
from .engine import Engine
from .names import normalize
from .state import READY, RUNNING

SNAPSHOT_VERSION = 1


def snapshot(engine):
    return {
        "version": SNAPSHOT_VERSION,
        "t": engine.clock.now,
        "capacity": dict(engine.capacity),
        "jobs": {run.spec.name: {"state": run.state, "attempts": run.attempts,
                                 "retry_at": run.retry_at}
                 for run in engine.runs.values()},
    }


def restore(workflow, snap, handlers=None, capacity=None):
    version = snap.get("version", 1)
    if version != 1:
        raise ValueError(f"unsupported snapshot version {version!r}")
    if capacity is None:
        capacity = snap.get("capacity") or {"cpu": snap.get("max_parallel", 2)}
    t = float(snap["t"])
    engine = Engine(workflow, handlers, capacity=capacity, clock=Clock(t))
    saved = {normalize(name): data for name, data in snap["jobs"].items()}
    for key, run in engine.runs.items():
        data = saved.get(key)
        if data is None:
            continue  # a job added to the workflow after the snapshot starts fresh
        run.state = data["state"]
        run.attempts = int(data["attempts"])
        run.retry_at = data.get("retry_at")
        if run.state == RUNNING:
            run.state = READY
            run.attempts -= 1
        if run.state == READY:
            run.ready_since = t
    return engine


def save(engine, path):
    Path(path).write_text(json.dumps(snapshot(engine), indent=2), encoding="utf-8")


def load(workflow, path, handlers=None, capacity=None):
    return restore(workflow, json.loads(Path(path).read_text(encoding="utf-8")), handlers, capacity)
