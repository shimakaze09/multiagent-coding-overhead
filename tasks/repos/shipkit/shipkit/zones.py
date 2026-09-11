"""Destination zones, as a percentage of the zone-1 price."""

ZONE_PERCENT = {1: 100, 2: 125, 3: 160, 4: 220}


def apply_zone(cents, zone):
    """Zone-adjusted price in cents, rounded half up to the cent."""
    try:
        pct = ZONE_PERCENT[zone]
    except KeyError:
        raise ValueError(f"unknown zone: {zone!r}") from None
    return (cents * pct + 50) // 100
