from datetime import date, datetime

import pytest

from helpdesk import Ticket, breach_report
from helpdesk import rota, sla, workcal


def test_hours_within_one_business_day():
    assert workcal.business_hours_between(datetime(2024, 3, 5, 10), datetime(2024, 3, 5, 13)) == 3.0


def test_hours_before_opening_do_not_count():
    assert workcal.business_hours_between(datetime(2024, 3, 6, 7), datetime(2024, 3, 6, 10)) == 1.0


def test_hours_across_two_midweek_days():
    assert workcal.business_hours_between(datetime(2024, 3, 6, 15), datetime(2024, 3, 7, 11)) == 4.0


def test_high_priority_breach_midweek():
    t = Ticket("T-9", "high", opened_at=datetime(2024, 3, 5, 9),
               first_response_at=datetime(2024, 3, 6, 11))
    assert breach_report([t], now=datetime(2024, 3, 6, 12)) == ["T-9"]


def test_response_targets():
    assert sla.response_target_hours("urgent") == 4
    with pytest.raises(ValueError):
        sla.response_target_hours("critical")


def test_next_business_day_midweek():
    assert rota.next_business_day(date(2024, 3, 5)) == date(2024, 3, 6)
