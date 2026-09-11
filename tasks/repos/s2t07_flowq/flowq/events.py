"""An append-only log of what the engine did."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    seq: int
    t: float
    kind: str
    job: str
    data: dict = field(default_factory=dict)


class EventLog:
    def __init__(self):
        self.events = []

    def append(self, t, kind, job, **data):
        event = Event(len(self.events) + 1, float(t), kind, job, data)
        self.events.append(event)
        return event

    def __iter__(self):
        return iter(self.events)

    def __len__(self):
        return len(self.events)

    def of_kind(self, kind):
        return [e for e in self.events if e.kind == kind]

    def started(self):
        return [e.job for e in self.of_kind("started")]
