"""Job run states and transitions."""

from dataclasses import dataclass
from typing import Optional

PENDING = "pending"
READY = "ready"
RUNNING = "running"
RETRY_WAIT = "retry_wait"
SUCCEEDED = "succeeded"
FAILED = "failed"
SKIPPED = "skipped"

TERMINAL = frozenset({SUCCEEDED, FAILED, SKIPPED})
ALLOWED = {
    PENDING: {READY, SKIPPED},
    READY: {RUNNING, SKIPPED},
    RUNNING: {SUCCEEDED, RETRY_WAIT, FAILED, READY},  # READY: interrupted, resumed
    RETRY_WAIT: {READY, SKIPPED},
    SUCCEEDED: set(),
    FAILED: set(),
    SKIPPED: set(),
}


class InvalidTransition(RuntimeError):
    pass


@dataclass
class JobRun:
    spec: object
    state: str = PENDING
    attempts: int = 0
    ready_since: Optional[float] = None
    started_at: Optional[float] = None
    finish_at: Optional[float] = None
    retry_at: Optional[float] = None

    def move(self, new):
        if new not in ALLOWED[self.state]:
            raise InvalidTransition(f"{self.spec.name}: {self.state} -> {new}")
        self.state = new

    @property
    def terminal(self):
        return self.state in TERMINAL
