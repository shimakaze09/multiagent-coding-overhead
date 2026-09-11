"""Discrete-event execution of a workflow on a simulated clock.

Handlers map a job name to ``handler(display_name, attempt) -> bool``; a
missing handler succeeds, and a handler that raises counts as a failed attempt.
"""

from . import report, scheduler
from .clock import Clock
from .events import EventLog
from .names import normalize
from .spec import SpecError
from .state import FAILED, PENDING, READY, RETRY_WAIT, RUNNING, SKIPPED, SUCCEEDED, JobRun

DEFAULT_CAPACITY = {"cpu": 2}
MAX_BACKOFF_S = 60.0


class Engine:
    def __init__(self, workflow, handlers=None, *, capacity=None, max_parallel=None, clock=None,
                 log=None):
        if capacity is not None and max_parallel is not None:
            raise ValueError("give capacity or max_parallel, not both")
        if max_parallel is not None:
            capacity = {"cpu": max_parallel}
        self.capacity = dict(capacity if capacity is not None else DEFAULT_CAPACITY)
        self.workflow = workflow
        self.handlers = {normalize(k): v for k, v in (handlers or {}).items()}
        self.clock = clock or Clock()
        self.log = log if log is not None else EventLog()
        self.runs = {job.key: JobRun(job) for job in workflow}
        self.peak = {r: 0 for r in self.capacity}
        self._check_resources()

    def _check_resources(self):
        for job in self.workflow:
            for r, n in job.resources:
                if r not in self.capacity:
                    raise SpecError(f"job {job.name!r} needs resource {r!r}, which has no capacity")
                if n > self.capacity[r]:
                    raise SpecError(f"job {job.name!r} needs {n} {r} but capacity is {self.capacity[r]}")

    def in_use(self):
        used = {r: 0 for r in self.capacity}
        for run in self.runs.values():
            if run.state == RUNNING:
                for r, n in run.spec.resources:
                    used[r] += n
        return used

    def _succeeds(self, run):
        handler = self.handlers.get(run.spec.key)
        if handler is None:
            return True
        try:
            return bool(handler(run.spec.name, run.attempts))
        except Exception:
            return False

    def _promote(self):
        now = self.clock.now
        changed = True
        while changed:
            changed = False
            for run in self.runs.values():
                if run.state == PENDING:
                    deps = [self.runs[k] for k in run.spec.dep_keys]
                    if any(d.state in (FAILED, SKIPPED) for d in deps):
                        run.move(SKIPPED)
                        self.log.append(now, "skipped", run.spec.name)
                        changed = True
                    elif all(d.state == SUCCEEDED for d in deps):
                        run.move(READY)
                        run.ready_since = now
                        self.log.append(now, "ready", run.spec.name)
                        changed = True
                elif run.state == RETRY_WAIT and run.retry_at <= now:
                    run.move(READY)
                    run.ready_since = now
                    self.log.append(now, "ready", run.spec.name, attempt=run.attempts + 1)
                    changed = True

    def _start(self):
        now = self.clock.now
        ready = [r for r in self.runs.values() if r.state == READY]
        for run in scheduler.pick(ready, self.capacity, self.in_use()):
            run.move(RUNNING)
            run.attempts += 1
            run.started_at = now
            run.finish_at = now + run.spec.duration_s
            self.log.append(now, "started", run.spec.name, attempt=run.attempts)
        for r, n in self.in_use().items():
            self.peak[r] = max(self.peak[r], n)

    def _finish(self):
        now = self.clock.now
        done = [r for r in self.runs.values() if r.state == RUNNING and r.finish_at <= now]
        for run in sorted(done, key=lambda r: (r.finish_at, r.spec.key)):
            if self._succeeds(run):
                run.move(SUCCEEDED)
                self.log.append(now, "succeeded", run.spec.name, attempt=run.attempts)
            elif run.attempts <= run.spec.retries:
                run.move(RETRY_WAIT)
                run.retry_at = now + min(self.workflow.backoff_s * 2 ** (run.attempts - 1),
                                         MAX_BACKOFF_S)
                self.log.append(now, "retry_scheduled", run.spec.name, attempt=run.attempts,
                                at=run.retry_at)
            else:
                run.move(FAILED)
                self.log.append(now, "failed", run.spec.name, attempt=run.attempts)

    def _next_time(self):
        times = [r.finish_at for r in self.runs.values() if r.state == RUNNING]
        times += [r.retry_at for r in self.runs.values() if r.state == RETRY_WAIT]
        return min(times) if times else None

    def run(self, until=None):
        """Run to completion, or stop before any event later than ``until``
        (simulating a crash at that time)."""
        while True:
            self._promote()
            self._start()
            t = self._next_time()
            if t is None:
                break
            if until is not None and t > until:
                self.clock.advance_to(max(self.clock.now, until))
                break
            self.clock.advance_to(t)
            self._finish()
        return report.summarize(self)

    @property
    def done(self):
        return all(r.terminal for r in self.runs.values())
