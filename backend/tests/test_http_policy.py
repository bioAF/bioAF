"""The shared outbound request policy: bounded attempts, retry delays, budgets.

Driven through a real httpx client with a MockTransport and a deterministic
clock; no wall-clock sleeps decide any outcome.
"""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest

from app.services import http_policy
from app.services.http_policy import MODEL_LIST_READ, SINGLE_ATTEMPT, SOURCE_READ, parse_retry_after, send
from tests.support.request_clock import AutoClock, ManualClock


class Upstream:
    """A scripted upstream: each request consumes the next step."""

    def __init__(self, clock, steps):
        self.clock = clock
        self.steps = list(steps)
        self.dispatched: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.dispatched.append(self.clock.monotonic())
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, BaseException):
            raise step
        return step

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _ok():
    return httpx.Response(200, json={"ok": True})


# --- Retry-After --------------------------------------------------------------


def test_retry_after_seconds_and_dates():
    clock = ManualClock()
    now = clock.wall()
    assert parse_retry_after("7", now=now) == 7.0
    assert parse_retry_after(" 0 ", now=now) == 0.0
    assert parse_retry_after(format_datetime(now + timedelta(seconds=30), usegmt=True), now=now) == 30.0
    # An HTTP date already past means "now", not a negative wait.
    assert parse_retry_after(format_datetime(now - timedelta(seconds=30), usegmt=True), now=now) == 0.0


@pytest.mark.parametrize("value", [None, "", "-3", "soon", "1e400", "nan", "inf", "3.5.1", "Mon, 99 Foo 2026"])
def test_unusable_retry_after_values_are_ignored(value):
    assert parse_retry_after(value, now=ManualClock().wall()) is None


# --- Retries --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transient_status_is_retried_then_recovers():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(503), httpx.Response(502), _ok()])
    async with upstream.client() as client:
        resp = await send(client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t")
    assert resp.status_code == 200
    assert len(upstream.dispatched) == 3
    # Bounded exponential backoff with jitter: each wait is within [base/2, base] of its step.
    gaps = [b - a for a, b in zip(upstream.dispatched, upstream.dispatched[1:])]
    assert SOURCE_READ.base_delay / 2 <= gaps[0] <= SOURCE_READ.base_delay
    assert SOURCE_READ.base_delay <= gaps[1] <= 2 * SOURCE_READ.base_delay


@pytest.mark.asyncio
async def test_retries_are_bounded_and_the_last_response_is_returned():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(503)])
    async with upstream.client() as client:
        resp = await send(client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t")
    assert resp.status_code == 503
    assert len(upstream.dispatched) == SOURCE_READ.max_attempts == 3


@pytest.mark.asyncio
async def test_a_transient_exception_is_retried_and_the_last_one_raised():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.ConnectError("refused")])
    async with upstream.client() as client:
        with pytest.raises(httpx.ConnectError):
            await send(client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t")
    assert len(upstream.dispatched) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
@pytest.mark.asyncio
async def test_non_transient_statuses_are_not_retried(status):
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(status)])
    async with upstream.client() as client:
        resp = await send(client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t")
    assert resp.status_code == status
    assert len(upstream.dispatched) == 1


@pytest.mark.asyncio
async def test_a_post_never_acquires_retries_from_a_read_policy():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(503)])
    async with upstream.client() as client:
        resp = await send(client, "POST", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t", json={})
    assert resp.status_code == 503
    assert len(upstream.dispatched) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.asyncio
async def test_the_single_attempt_policy_sends_once(method):
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.ConnectError("refused")])
    async with upstream.client() as client:
        with pytest.raises(httpx.ConnectError):
            await send(client, method, "https://x.test/a", policy=SINGLE_ATTEMPT, clock=clock, label="t")
    assert len(upstream.dispatched) == 1


@pytest.mark.asyncio
async def test_model_list_reads_do_not_retry_a_read_timeout():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.ReadTimeout("slow")])
    async with upstream.client() as client:
        with pytest.raises(httpx.ReadTimeout):
            await send(client, "GET", "https://x.test/a", policy=MODEL_LIST_READ, clock=clock, label="t")
    assert len(upstream.dispatched) == 1


# --- Retry hints and budgets ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_429_retry_hint_is_honored_exactly():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(429, headers={"Retry-After": "20"}), _ok()])
    throttled: list[float] = []
    async with upstream.client() as client:
        resp = await send(
            client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t", on_throttle=throttled.append
        )
    assert resp.status_code == 200
    assert upstream.dispatched[1] - upstream.dispatched[0] == 20.0
    assert throttled == [20.0]


@pytest.mark.asyncio
async def test_an_http_date_retry_hint_is_honored():
    clock = AutoClock()
    when = format_datetime(clock.wall() + timedelta(seconds=12), usegmt=True)
    upstream = Upstream(clock, [httpx.Response(429, headers={"Retry-After": when}), _ok()])
    async with upstream.client() as client:
        await send(client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t")
    assert upstream.dispatched[1] - upstream.dispatched[0] == 12.0


@pytest.mark.parametrize("hint", ["-1", "garbage", "nan"])
@pytest.mark.asyncio
async def test_a_malformed_retry_hint_falls_back_to_backoff(hint):
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(429, headers={"Retry-After": hint}), _ok()])
    throttled: list[float] = []
    async with upstream.client() as client:
        resp = await send(
            client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t", on_throttle=throttled.append
        )
    assert resp.status_code == 200
    gap = upstream.dispatched[1] - upstream.dispatched[0]
    assert SOURCE_READ.base_delay / 2 <= gap <= SOURCE_READ.base_delay
    assert throttled == [pytest.approx(gap)]


@pytest.mark.asyncio
async def test_a_hint_beyond_the_remaining_budget_ends_the_operation_without_another_request():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(429, headers={"Retry-After": "90"}), _ok()])
    async with upstream.client() as client:
        resp = await send(
            client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t", deadline=clock.now + 60
        )
    # Never capped downward into an early retry: the 429 is the outcome.
    assert resp.status_code == 429
    assert len(upstream.dispatched) == 1
    assert clock.slept == []


@pytest.mark.asyncio
async def test_backoff_beyond_the_budget_ends_the_operation():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(503), _ok()])
    async with upstream.client() as client:
        resp = await send(
            client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t", deadline=clock.now + 0.1
        )
    assert resp.status_code == 503
    assert len(upstream.dispatched) == 1


@pytest.mark.asyncio
async def test_an_expired_budget_sends_nothing():
    clock = AutoClock()
    upstream = Upstream(clock, [_ok()])
    async with upstream.client() as client:
        with pytest.raises(http_policy.BudgetExhausted):
            await send(
                client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t", deadline=clock.now - 1
            )
    assert upstream.dispatched == []


@pytest.mark.asyncio
async def test_quota_is_acquired_before_every_attempt():
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(503), httpx.Response(503), _ok()])
    acquired: list[float] = []

    async def before_dispatch(deadline):
        acquired.append(clock.monotonic())

    async with upstream.client() as client:
        await send(
            client,
            "GET",
            "https://x.test/a",
            policy=SOURCE_READ,
            clock=clock,
            label="t",
            before_dispatch=before_dispatch,
        )
    assert acquired == upstream.dispatched


@pytest.mark.asyncio
async def test_cancellation_during_backoff_sends_no_later_request():
    clock = ManualClock()
    upstream = Upstream(clock, [httpx.Response(503), _ok()])
    async with upstream.client() as client:
        task = asyncio.create_task(send(client, "GET", "https://x.test/a", policy=SOURCE_READ, clock=clock, label="t"))
        await clock.settle()
        assert clock.sleeping == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await clock.advance(60)
    assert len(upstream.dispatched) == 1


@pytest.mark.asyncio
async def test_retry_diagnostics_are_safe(caplog):
    caplog.set_level("DEBUG", logger="bioaf")
    clock = AutoClock()
    upstream = Upstream(clock, [httpx.Response(429, headers={"Retry-After": "2"}), httpx.ConnectError("SYNTH-RAW")])
    async with upstream.client() as client:
        with pytest.raises(httpx.ConnectError):
            await send(
                client,
                "GET",
                "https://x.test/a?api_key=SYNTH-KEY",
                policy=SOURCE_READ,
                clock=clock,
                label="pubmed.esearch",
            )
    assert "SYNTH" not in caplog.text and "x.test" not in caplog.text
    assert "pubmed.esearch" in caplog.text
    assert "status=429" in caplog.text and "ConnectError" in caplog.text
    assert "attempts=3" in caplog.text


def test_named_policies_are_bounded():
    for policy in (SOURCE_READ, MODEL_LIST_READ, SINGLE_ATTEMPT):
        assert 1 <= policy.max_attempts <= 3
        assert math.isfinite(policy.max_delay)
    assert SINGLE_ATTEMPT.max_attempts == 1


# --- Review of 5dde62e4: deadlines bound the request in flight -------------------------


def _slow_client(seconds: float) -> httpx.AsyncClient:
    async def slow(request):
        await asyncio.sleep(seconds)
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(slow), timeout=5.0)


@pytest.mark.asyncio
async def test_a_request_in_flight_is_cut_off_at_the_operation_deadline():
    import time

    policy = http_policy.RequestPolicy(name="short", budget_seconds=0.01)
    started = time.monotonic()
    async with _slow_client(0.5) as client:
        with pytest.raises(http_policy.BudgetExhausted):
            await send(client, "GET", "https://x.test/a", policy=policy, label="t")
    assert time.monotonic() - started < 0.3


@pytest.mark.asyncio
async def test_single_attempt_requests_have_no_operation_budget():
    """Paid inference keeps its own long client timeouts; the policy adds no deadline."""
    assert SINGLE_ATTEMPT.budget_seconds is None
    async with _slow_client(0.05) as client:
        resp = await send(client, "POST", "https://x.test/a", policy=SINGLE_ATTEMPT, label="t", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_an_explicit_deadline_still_bounds_a_single_attempt():
    import time

    async with _slow_client(0.5) as client:
        with pytest.raises(http_policy.BudgetExhausted):
            await send(
                client,
                "POST",
                "https://x.test/a",
                policy=SINGLE_ATTEMPT,
                label="t",
                deadline=time.monotonic() + 0.01,
                json={},
            )


@pytest.mark.parametrize("value", ["9" * 400, "86401", "999999999"])
def test_unbounded_or_absurd_numeric_hints_are_ignored(value):
    assert parse_retry_after(value, now=ManualClock().wall()) is None


def test_a_far_future_date_hint_is_ignored():
    clock = ManualClock()
    far = format_datetime(clock.wall() + timedelta(days=30), usegmt=True)
    assert parse_retry_after(far, now=clock.wall()) is None


@pytest.mark.asyncio
async def test_nothing_is_sent_when_the_quota_wait_ends_at_the_deadline():
    clock = ManualClock()
    upstream = Upstream(clock, [_ok()])

    async def wait_until_deadline(deadline):
        await clock.sleep(deadline - clock.monotonic())

    async with upstream.client() as client:
        task = asyncio.create_task(
            send(
                client,
                "GET",
                "https://x.test/a",
                policy=SOURCE_READ,
                clock=clock,
                label="t",
                deadline=clock.now + 1.0,
                before_dispatch=wait_until_deadline,
            )
        )
        await clock.settle()
        await clock.advance(1.0)
        with pytest.raises(http_policy.BudgetExhausted):
            await task
    assert upstream.dispatched == []


@pytest.mark.asyncio
async def test_a_source_hint_of_an_hour_holds_other_callers_for_the_full_hour():
    from app.services import request_pacing

    clock = ManualClock()
    registry = request_pacing.reset(clock)
    upstream = Upstream(clock, [httpx.Response(429, headers={"Retry-After": "3600"}), _ok()])
    pacer = registry.pacer("bucket")

    async def acquire(deadline):
        await pacer.acquire(0.0, deadline)

    async with upstream.client() as client:
        first = await send(
            client,
            "GET",
            "https://x.test/a",
            policy=SOURCE_READ,
            clock=clock,
            label="t",
            before_dispatch=acquire,
            on_throttle=pacer.cool_down,
        )
        assert first.status_code == 429  # 3600 s exceeds this operation's budget
        task = asyncio.create_task(
            send(
                client,
                "GET",
                "https://x.test/a",
                policy=SOURCE_READ,
                clock=clock,
                label="t",
                deadline=clock.now + 7200,
                before_dispatch=acquire,
                on_throttle=pacer.cool_down,
            )
        )
        await clock.settle()
        await clock.advance(3599.0)
        assert len(upstream.dispatched) == 1
        await clock.advance(1.0)
        assert (await task).status_code == 200
    assert upstream.dispatched[1] - upstream.dispatched[0] == pytest.approx(3600.0)
