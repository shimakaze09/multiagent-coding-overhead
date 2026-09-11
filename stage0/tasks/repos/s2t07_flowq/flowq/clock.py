"""A simulated clock: the engine never reads wall time."""


class Clock:
    def __init__(self, now=0.0):
        self.now = float(now)

    def advance_to(self, t):
        if t < self.now:
            raise ValueError("time cannot go backwards")
        self.now = float(t)
