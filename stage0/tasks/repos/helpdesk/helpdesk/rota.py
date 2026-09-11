"""On-call rota helpers."""

from datetime import timedelta

from . import policy


def next_business_day(day):
    """The first business day strictly after `day`."""
    nxt = day + timedelta(days=1)
    while nxt.isoweekday() not in policy.BUSINESS_DAYS:
        nxt += timedelta(days=1)
    return nxt


def weekend_cover_needed(day):
    """True if `day` needs the out-of-hours on-call engineer."""
    return day.isoweekday() not in policy.BUSINESS_DAYS
