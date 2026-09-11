"""Workflow specifications (docs/workflows.md).

Version 2 (current)::

    {"version": 2,
     "jobs": {"Build": {"deps": [], "duration_s": 10, "priority": 0,
                        "retry": {"max_attempts": 3,
                                  "backoff": {"initial_s": 5, "factor": 2, "max_s": 60}},
                        "resources": {"cpu": 1}}}}

Version 1 (flowq 1.x, deprecated) has per-job "duration" and "retries" (extra
attempts after the first) and one workflow-level "backoff_s".
"""

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from . import graph
from .names import normalize


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    initial_s: float = 1.0
    factor: float = 2.0
    max_s: float = 60.0

    def __post_init__(self):
        if self.max_attempts < 1:
            raise SpecError("max_attempts must be at least 1")
        if self.initial_s < 0 or self.max_s < 0:
            raise SpecError("backoff times cannot be negative")
        if self.factor < 1:
            raise SpecError("the backoff factor must be at least 1")

    def delay(self, failed_attempt):
        """Seconds to wait after the 1-based attempt ``failed_attempt`` failed."""
        return min(self.initial_s * self.factor ** (failed_attempt - 1), self.max_s)


@dataclass(frozen=True)
class JobSpec:
    name: str
    deps: tuple = ()
    duration_s: float = 1.0
    priority: int = 0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    resources: tuple = (("cpu", 1),)

    @property
    def key(self):
        return normalize(self.name)

    @property
    def dep_keys(self):
        return tuple(normalize(d) for d in self.deps)

    def resource_map(self):
        return dict(self.resources)

    @property
    def retries(self):
        """flowq 1.x name: extra attempts after the first. Deprecated."""
        warnings.warn("JobSpec.retries is deprecated; use retry.max_attempts",
                      DeprecationWarning, stacklevel=2)
        return self.retry.max_attempts - 1


@dataclass
class Workflow:
    jobs: dict  # key -> JobSpec, in definition order

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
        """The workflow as a version-2 document."""
        jobs = {}
        for j in self:
            jobs[j.name] = {
                "deps": list(j.deps), "duration_s": j.duration_s, "priority": j.priority,
                "retry": {"max_attempts": j.retry.max_attempts,
                          "backoff": {"initial_s": j.retry.initial_s, "factor": j.retry.factor,
                                      "max_s": j.retry.max_s}},
                "resources": dict(j.resources),
            }
        return {"version": 2, "jobs": jobs}


_V2_JOB_KEYS = {"deps", "duration_s", "priority", "retry", "resources"}
_V1_JOB_KEYS = {"deps", "duration", "priority", "retries"}


def _resources(name, raw):
    if not isinstance(raw, dict) or not raw:
        raise SpecError(f"job {name!r}: resources must be a non-empty mapping")
    out = []
    for r, n in raw.items():
        if not isinstance(n, int) or n < 0:
            raise SpecError(f"job {name!r}: resource {r!r} must be a non-negative integer")
        out.append((str(r), n))
    return tuple(sorted(out))


def _parse_v2(data):
    jobs = []
    for name, j in data.get("jobs", {}).items():
        unknown = set(j) - _V2_JOB_KEYS
        if unknown:
            raise SpecError(f"job {name!r}: unknown keys {sorted(unknown)}")
        retry = j.get("retry", {})
        backoff = retry.get("backoff", {})
        policy = RetryPolicy(int(retry.get("max_attempts", 1)), float(backoff.get("initial_s", 1)),
                             float(backoff.get("factor", 2)), float(backoff.get("max_s", 60)))
        jobs.append(JobSpec(name, tuple(j.get("deps", ())), float(j.get("duration_s", 1)),
                            int(j.get("priority", 0)), policy,
                            _resources(name, j.get("resources", {"cpu": 1}))))
    return jobs


def _parse_v1(data):
    backoff_s = float(data.get("backoff_s", 1))
    jobs = []
    for name, j in data.get("jobs", {}).items():
        unknown = set(j) - _V1_JOB_KEYS
        if unknown:
            raise SpecError(f"job {name!r}: unknown keys {sorted(unknown)}")
        retries = int(j.get("retries", 0))
        if retries < 0:
            raise SpecError(f"job {name!r}: retries cannot be negative")
        policy = RetryPolicy(max_attempts=max(retries, 1), initial_s=backoff_s, factor=2.0, max_s=60.0)
        jobs.append(JobSpec(name, tuple(j.get("deps", ())), float(j.get("duration", 1)),
                            int(j.get("priority", 0)), policy))
    return jobs


def build(jobs):
    by_key = {}
    for job in jobs:
        if job.key in by_key:
            raise SpecError(f"duplicate job name {job.name!r}")
        by_key[job.key] = job
    for job in jobs:
        for dep, key in zip(job.deps, job.dep_keys):
            if key not in by_key:
                raise SpecError(f"job {job.name!r} depends on unknown job {dep!r}")
    wf = Workflow(by_key)
    cycle = graph.find_cycle(wf)
    if cycle:
        raise SpecError(f"dependency cycle: {' -> '.join(cycle)}")
    return wf


def parse(data):
    version = data.get("version", 1)
    if version == 1:
        warnings.warn("version-1 workflow documents are deprecated; see docs/workflows.md",
                      DeprecationWarning, stacklevel=2)
        return build(_parse_v1(data))
    if version == 2:
        return build(_parse_v2(data))
    raise SpecError(f"unsupported workflow version {version!r}")


def load(path):
    return parse(json.loads(Path(path).read_text(encoding="utf-8")))
