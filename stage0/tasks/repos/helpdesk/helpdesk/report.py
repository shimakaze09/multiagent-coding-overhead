"""The daily SLA breach report sent to team leads."""

from . import sla


def breach_report(tickets, now):
    """IDs of tickets whose first-response SLA is breached, oldest first."""
    hits = [t for t in tickets if sla.breached(t, now)]
    return [t.ticket_id for t in sorted(hits, key=lambda t: t.opened_at)]
