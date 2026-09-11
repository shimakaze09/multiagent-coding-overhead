"""Monthly accounting periods. A closed period accepts no postings."""


class PeriodClosed(ValueError):
    pass


def period_of(on):
    return (on.year, on.month)


class PeriodCalendar:
    def __init__(self, closed=()):
        self._closed = {tuple(p) for p in closed}

    def close(self, year, month):
        self._closed.add((year, month))

    def reopen(self, year, month):
        self._closed.discard((year, month))

    def is_open(self, on):
        return period_of(on) not in self._closed

    @property
    def closed(self):
        return sorted(self._closed)
