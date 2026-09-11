"""Support tickets as exported from the ticketing system."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Ticket:
    ticket_id: str
    priority: str
    opened_at: datetime
    first_response_at: Optional[datetime] = None
