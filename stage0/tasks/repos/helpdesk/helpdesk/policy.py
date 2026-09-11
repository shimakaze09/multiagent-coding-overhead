"""Support policy, shared by the SLA monitor and the on-call rota.

BUSINESS_DAYS uses ISO weekday numbers (Monday = 1 ... Sunday = 7), the same
convention as the rota export that HR consumes.
"""

BUSINESS_DAYS = frozenset({1, 2, 3, 4, 5})
OPEN_HOUR = 9    # 09:00 local time
CLOSE_HOUR = 17  # 17:00 local time

# First-response targets, in business hours.
RESPONSE_TARGET_HOURS = {
    "urgent": 4,
    "high": 8,
    "normal": 16,
    "low": 40,
}
