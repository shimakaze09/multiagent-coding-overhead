"""Workflow specifications (docs/workflows.md)::

    {"backoff_s": 5,
     "jobs": {"Build": {"deps": [], "duration": 10, "priority": 0, "retries": 2}}}

``retries`` is the number of extra attempts after the first; ``backoff_s`` is
the base wait before a retry.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from . import graph
from .names import normalize


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class JobSpec:
    name: str
    deps: tuple = ()
    duration_s: float = 1.0
    priority: int = 0
    retries: int = 0
    resources: tuple = (("cpu", 1),)

    @property
    def key(self):
        return normalize(self.name)

    @property
    def dep_keys(self):
        return tuple(normalize(d) for d in self.deps)

    def resource_map(self):
        return dict(self.resources)


@dataclass
class Workflow:
    jobs: dict  # key -> JobSpec, in definition order
    backoff_s: float = 1.0

    def job(self, name):
        try:
            return self.jobs[normalize(name)]
        except KeyError:
            raise KeyError(f"no job named {name!r}") from None

    def __iter__(self):
        return iter(self.jobs.values())

    def __len__(self):
        return len(self.jobs)

    def plan(self):
        """Display names in a valid execution order (graph.toposort)."""
        return graph.toposort(self)

    def dump(self):
        return {"backoff_s": self.backoff_s,
                "jobs": {j.name: {"deps": list(j.deps), "duration": j.duration_s,
                                  "priority": j.priority, "retries": j.retries} for j in self}}


_JOB_KEYS = {"deps", "duration", "priority", "retries"}


def _parse_jobs(data):
    jobs = []
    for name, j in data.get("jobs", {}).items():
        unknown = set(j) - _JOB_KEYS
        if unknown:
            raise SpecError(f"job {name!r}: unknown keys {sorted(unknown)}")
        retries = int(j.get("retries", 0))
        if retries < 0:
            raise SpecError(f"job {name!r}: retries cannot be negative")
        jobs.append(JobSpec(name, tuple(j.get("deps", ())), float(j.get("duration", 1)),
                            int(j.get("priority", 0)), retries))
    return jobs


def build(jobs, backoff_s=1.0):
    by_key = {}
    for job in jobs:
        if job.key in by_key:
            raise SpecError(f"duplicate job name {job.name!r}")
        by_key[job.key] = job
    for job in jobs:
        for dep, key in zip(job.deps, job.dep_keys):
            if key not in by_key:
                raise SpecError(f"job {job.name!r} depends on unknown job {dep!r}")
    wf = Workflow(by_key, backoff_s)
    cycle = graph.find_cycle(wf)
    if cycle:
        raise SpecError(f"dependency cycle: {' -> '.join(cycle)}")
    return wf


def parse(data):
    version = data.get("version", 1)
    if version != 1:
        raise SpecError(f"unsupported workflow version {version!r}")
    backoff_s = float(data.get("backoff_s", 1))
    if backoff_s < 0:
        raise SpecError("backoff_s cannot be negative")
    return build(_parse_jobs(data), backoff_s)


def load(path):
    return parse(json.loads(Path(path).read_text(encoding="utf-8")))
