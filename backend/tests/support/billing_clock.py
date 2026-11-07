"""Control the billing clock and the host timezone in cost tests.

Billing dates come from one UTC reference instant (app/services/billing_period.py).
A test fixes that instant and runs under a chosen host timezone through the
``billing_clock`` fixture in conftest.py, so its dates do not depend on the wall
clock or on where the suite runs:

    def test_x(billing_clock):
        billing_clock(datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc), "Pacific/Honolulu")

Pass a list of instants to model a clock that advances on every read; the last
instant repeats once the list is exhausted. The call returns the instants read
so far.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

HOST_TIMEZONES = ["UTC", "Asia/Tokyo", "Pacific/Honolulu"]


class BillingClock:
    def __init__(self) -> None:
        import app.services.billing_period as billing_period_module

        self._module = billing_period_module
        self._original_now = billing_period_module._utc_now
        self._original_tz = os.environ.get("TZ")

    def __call__(self, reference: datetime | list[datetime], host_tz: str = "UTC") -> list[datetime]:
        instants = list(reference) if isinstance(reference, list) else [reference]
        reads: list[datetime] = []

        def _now() -> datetime:
            value = instants[min(len(reads), len(instants) - 1)]
            reads.append(value)
            return value

        self._module._utc_now = _now
        os.environ["TZ"] = host_tz
        time.tzset()
        return reads

    def restore(self) -> None:
        self._module._utc_now = self._original_now
        if self._original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._original_tz
        time.tzset()
