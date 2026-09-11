"""First-response SLA checks."""

from . import policy, workcal


def response_target_hours(priority):
    try:
        return policy.RESPONSE_TARGET_HOURS[priority]
    except KeyError:
        raise ValueError(f"unknown priority: {priority!r}") from None


def elapsed_business_hours(ticket, now):
    """Business hours from opening to first response (or to `now`, if unanswered)."""
    responded = ticket.first_response_at or now
    return workcal.business_hours_between(ticket.opened_at, responded)


def breached(ticket, now):
    return elapsed_business_hours(ticket, now) > response_target_hours(ticket.priority)
