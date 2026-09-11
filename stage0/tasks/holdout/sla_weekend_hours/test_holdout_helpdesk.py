"""Held-out deterministic verifier for the `sla_weekend_hours` task.

Injected into the workspace only AFTER the agent phase ends, identically for
every arm, and run on its own (`--noconftest`, not together with the visible
tests), so SOLVED is decided by these tests alone.
"""

from datetime import date, datetime

import pytest

from helpdesk import Ticket, breach_report
from helpdesk import policy, rota, sla, workcal

FRI, SAT, SUN, MON = date(2024, 3, 8), date(2024, 3, 9), date(2024, 3, 10), date(2024, 3, 11)


# --- the reported defect -------------------------------------------------


def test_task_example_is_not_breached():
    t = Ticket("T-1", "urgent", opened_at=datetime(2024, 3, 8, 16, 0),
               first_response_at=datetime(2024, 3, 11, 11, 0))
    assert sla.elapsed_business_hours(t, datetime(2024, 3, 11, 12)) == 3.0
    assert breach_report([t], now=datetime(2024, 3, 11, 12, 0)) == []


def test_monday_hours_count():
    assert workcal.business_hours_between(datetime(2024, 3, 11, 9), datetime(2024, 3, 11, 14)) == 5.0
    t = Ticket("T-2", "urgent", opened_at=datetime(2024, 3, 11, 9),
               first_response_at=datetime(2024, 3, 11, 14))
    assert breach_report([t], now=datetime(2024, 3, 11, 15)) == ["T-2"]


def test_friday_counts_in_full():
    assert workcal.business_hours_between(datetime(2024, 3, 8, 9), datetime(2024, 3, 8, 17)) == 8.0


def test_weekend_hours_do_not_count():
    assert workcal.business_hours_between(datetime(2024, 3, 9, 10), datetime(2024, 3, 11, 10)) == 1.0


@pytest.mark.parametrize("offset,expected", list(enumerate([True, True, True, True, True, False, False])))
def test_business_days_of_a_week(offset, expected):
    assert workcal.is_business_day(date(2024, 3, 4 + offset)) is expected


def test_two_week_span():
    assert workcal.business_hours_between(datetime(2024, 3, 4, 9), datetime(2024, 3, 18, 9)) == 80.0


def test_unanswered_ticket_is_measured_to_now():
    t = Ticket("T-3", "urgent", opened_at=datetime(2024, 3, 8, 16))
    assert breach_report([t], now=datetime(2024, 3, 11, 12)) == []
    assert breach_report([t], now=datetime(2024, 3, 11, 13)) == ["T-3"]


def test_breach_report_lists_oldest_first():
    older = Ticket("A", "urgent", opened_at=datetime(2024, 3, 5, 9))
    newer = Ticket("B", "urgent", opened_at=datetime(2024, 3, 6, 9))
    assert breach_report([newer, older], now=datetime(2024, 3, 7, 17)) == ["A", "B"]


def test_end_before_start_is_zero():
    assert workcal.business_hours_between(datetime(2024, 3, 6, 12), datetime(2024, 3, 6, 11)) == 0.0


# --- shared behaviour that must NOT change --------------------------------


def test_policy_unchanged():
    assert policy.BUSINESS_DAYS == frozenset({1, 2, 3, 4, 5})
    assert (policy.OPEN_HOUR, policy.CLOSE_HOUR) == (9, 17)
    assert policy.RESPONSE_TARGET_HOURS == {"urgent": 4, "high": 8, "normal": 16, "low": 40}


def test_rota_unchanged():
    assert rota.next_business_day(FRI) == MON
    assert rota.next_business_day(SAT) == MON
    assert rota.weekend_cover_needed(SUN) is True
    assert rota.weekend_cover_needed(MON) is False
    assert rota.weekend_cover_needed(FRI) is False


def test_response_targets_unchanged():
    assert [sla.response_target_hours(p) for p in ("urgent", "high", "normal", "low")] == [4, 8, 16, 40]
