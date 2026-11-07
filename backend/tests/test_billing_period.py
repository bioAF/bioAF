"""The UTC billing period every cost operation reads its dates from.

Cost reporting is by UTC usage date (plan 7a): a usage month is
[month_start, next_month_start) of the UTC calendar month containing the
reference instant, whatever timezone the host runs in.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.billing_period import billing_period

HOST_TIMEZONES = ["UTC", "Asia/Tokyo", "Pacific/Honolulu"]


@pytest.fixture(params=HOST_TIMEZONES)
def host_tz(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_mid_month_period(host_tz):
    p = billing_period(_utc(2026, 11, 6, 12, 0))
    assert p.today == date(2026, 11, 6)
    assert p.month_start == date(2026, 11, 1)
    assert p.next_month_start == date(2026, 12, 1)
    assert p.days_elapsed == 6
    assert p.days_in_month == 30


@pytest.mark.parametrize(
    "reference, today, month_start, next_month_start, days_in_month",
    [
        # Last instant of a UTC day and the first of the next.
        (_utc(2026, 10, 31, 23, 59, 59), date(2026, 10, 31), date(2026, 10, 1), date(2026, 11, 1), 31),
        (_utc(2026, 11, 1, 0, 0, 0), date(2026, 11, 1), date(2026, 11, 1), date(2026, 12, 1), 30),
        # December and the year rollover out of it.
        (_utc(2026, 12, 31, 23, 59, 59), date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1), 31),
        (_utc(2027, 1, 1, 0, 0, 0), date(2027, 1, 1), date(2027, 1, 1), date(2027, 2, 1), 31),
        # Leap and non-leap February.
        (_utc(2028, 2, 29, 12, 0), date(2028, 2, 29), date(2028, 2, 1), date(2028, 3, 1), 29),
        (_utc(2027, 2, 28, 12, 0), date(2027, 2, 28), date(2027, 2, 1), date(2027, 3, 1), 28),
        (_utc(2100, 2, 15, 12, 0), date(2100, 2, 15), date(2100, 2, 1), date(2100, 3, 1), 28),
        (_utc(2000, 2, 15, 12, 0), date(2000, 2, 15), date(2000, 2, 1), date(2000, 3, 1), 29),
    ],
)
def test_rollover_boundaries(host_tz, reference, today, month_start, next_month_start, days_in_month):
    p = billing_period(reference)
    assert p.today == today
    assert p.month_start == month_start
    assert p.next_month_start == next_month_start
    assert p.days_in_month == days_in_month
    assert p.days_elapsed == today.day


@pytest.mark.parametrize(
    "reference, expected_today",
    [
        # Tokyo (UTC+9) is already in November; the UTC period is still October.
        (datetime(2026, 11, 1, 8, 0, tzinfo=timezone(timedelta(hours=9))), date(2026, 10, 31)),
        # Honolulu (UTC-10) is still on December 31; the UTC period is next year.
        (datetime(2026, 12, 31, 15, 0, tzinfo=timezone(timedelta(hours=-10))), date(2027, 1, 1)),
    ],
)
def test_offset_reference_is_read_in_utc(host_tz, reference, expected_today):
    assert billing_period(reference).today == expected_today


def test_naive_reference_is_refused():
    with pytest.raises(ValueError):
        billing_period(datetime(2026, 11, 6, 12, 0))


def test_default_reference_is_the_current_utc_instant(monkeypatch):
    import app.services.billing_period as bp

    monkeypatch.setattr(bp, "_utc_now", lambda: _utc(2026, 10, 31, 23, 0))
    p = billing_period()
    assert p.today == date(2026, 10, 31)
    assert p.reference == _utc(2026, 10, 31, 23, 0)


@pytest.mark.parametrize(
    "reference, previous_month_start",
    [
        (_utc(2026, 11, 6, 12, 0), date(2026, 10, 1)),
        (_utc(2027, 1, 1, 0, 0), date(2026, 12, 1)),
        (_utc(2028, 3, 1, 0, 0), date(2028, 2, 1)),
    ],
)
def test_previous_month_start(reference, previous_month_start):
    assert billing_period(reference).previous_month_start == previous_month_start
