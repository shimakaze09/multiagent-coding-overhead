"""Snapshots, for resuming a workflow after a crash (docs/scheduling.md).

A snapshot records every job by its display name. On restore, an interrupted
(running) job becomes ready again at the snapshot time and its interrupted
attempt is not counted. Version 2 records each ready job's ready time and the
engine's parallelism; version-1 snapshots are still accepted.
"""

import json
from pathlib import Path

from .clock import Clock
from .engine import Engine
from .names import normalize
from .state import READY, RUNNING

SNAPSHOT_VERSION = 2


def snapshot(engine):
    return {
        "version": SNAPSHOT_VERSION,
        "t": engine.clock.now,
        "max_parallel": engine.max_parallel,
        "jobs": {run.spec.name: {"state": run.state, "attempts": run.attempts,
                                 "ready_since": run.ready_since, "retry_at": run.retry_at}
                 for run in engine.runs.values()},
    }


def restore(workflow, snap, handlers=None, max_parallel=None):
    version = snap.get("version", 1)
    if version not in (1, 2):
        raise ValueError(f"unsupported snapshot version {version!r}")
    if max_parallel is None:
        max_parallel = snap.get("max_parallel", 2)
    t = float(snap["t"])
    engine = Engine(workflow, handlers, max_parallel=max_parallel, clock=Clock(t))
    saved = {normalize(name): data for name, data in snap["jobs"].items()}
    for key, run in engine.runs.items():
        data = saved.get(key)
        if data is None:
            continue  # a job added to the workflow after the snapshot starts fresh
        run.state = data["state"]
        run.attempts = int(data["attempts"])
        run.retry_at = data.get("retry_at")
        run.ready_since = data.get("ready_since")
        if run.state == RUNNING:
            run.state = READY
            run.attempts -= 1
            run.ready_since = t
        elif run.state == READY and run.ready_since is None:
            run.ready_since = t
    return engine


def save(engine, path):
    Path(path).write_text(json.dumps(snapshot(engine), indent=2), encoding="utf-8")


def load(workflow, path, handlers=None, max_parallel=None):
    return restore(workflow, json.loads(Path(path).read_text(encoding="utf-8")), handlers,
                   max_parallel)
