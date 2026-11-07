"""The UTC billing period cost operations read their dates from.

Cost reporting is by UTC usage date: ``record_date`` is a usage day, and a
usage month is ``[month_start, next_month_start)`` of the UTC calendar month
containing the reference instant, independent of the host timezone. An
operation captures one period and passes it along, so crossing midnight partway
through cannot give its steps different dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BillingPeriod:
    reference: datetime
    today: date
    month_start: date
    next_month_start: date

    @property
    def days_elapsed(self) -> int:
        return (self.today - self.month_start).days + 1

    @property
    def days_in_month(self) -> int:
        return (self.next_month_start - self.month_start).days

    @property
    def previous_month_start(self) -> date:
        return (self.month_start - timedelta(days=1)).replace(day=1)


def billing_period(reference: datetime | None = None) -> BillingPeriod:
    """The UTC billing period containing ``reference`` (default: now)."""
    if reference is None:
        reference = _utc_now()
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("billing reference must be timezone-aware")
    reference = reference.astimezone(timezone.utc)
    today = reference.date()
    month_start = today.replace(day=1)
    if month_start.month == 12:
        next_month_start = date(month_start.year + 1, 1, 1)
    else:
        next_month_start = date(month_start.year, month_start.month + 1, 1)
    return BillingPeriod(
        reference=reference,
        today=today,
        month_start=month_start,
        next_month_start=next_month_start,
    )
