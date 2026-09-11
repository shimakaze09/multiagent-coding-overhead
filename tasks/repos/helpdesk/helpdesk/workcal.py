"""Business-hours arithmetic."""

from datetime import datetime, time, timedelta

from . import policy


def is_business_day(day):
    return day.weekday() in policy.BUSINESS_DAYS


def business_hours_between(start, end):
    """Business hours elapsed between two naive local datetimes."""
    if end <= start:
        return 0.0
    total = timedelta()
    day = start.date()
    while day <= end.date():
        if is_business_day(day):
            opens = datetime.combine(day, time(policy.OPEN_HOUR))
            closes = datetime.combine(day, time(policy.CLOSE_HOUR))
            lo, hi = max(start, opens), min(end, closes)
            if hi > lo:
                total += hi - lo
        day += timedelta(days=1)
    return total.total_seconds() / 3600
