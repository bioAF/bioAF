"""Installation metrics consent: the installation-wide choice, its identity, and its concurrency.

The Installation ID exists only after a first opt-in, survives every later preference change, and a
stale choice never overwrites one already committed (ADR-075).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

import app.database as database
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics import state as metrics_state
from app.services.install_metrics.state import (
    NOTICE_VERSION,
    InstallMetricsService,
    InvalidChoice,
    NoticeVersionMismatch,
    RevisionConflict,
)

NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)


async def _events(session) -> int:
    return (await session.execute(select(func.count()).select_from(InstallMetricEvent))).scalar_one()


async def _choose(session, choice, revision, *, notice=NOTICE_VERSION, now=NOW):
    state = await InstallMetricsService.choose(
        session, choice=choice, notice_version=notice, revision=revision, now=now
    )
    await session.commit()
    return state


@pytest.mark.asyncio
async def test_a_fresh_installation_is_pending_with_no_installation_id(session):
    state = await InstallMetricsService.read(session)

    assert state.preference == "pending"
    assert state.installation_id is None
    assert state.revision == 0
    assert await _events(session) == 0


@pytest.mark.asyncio
async def test_first_opt_in_creates_one_random_id_and_keeps_local_consent_evidence(session):
    state = await _choose(session, "enabled", 0)

    assert state.preference == "enabled"
    assert uuid.UUID(state.installation_id).version == 4
    assert state.notice_version == NOTICE_VERSION
    assert state.decided_at == NOW
    assert state.revision == 1
    # The choice survives a new session (an ordinary restart).
    async with database.async_session_factory() as later:
        assert await InstallMetricsService.read(later) == state


@pytest.mark.asyncio
async def test_declining_records_the_choice_without_an_id_or_a_report(session):
    state = await _choose(session, "disabled", 0)

    assert state.preference == "disabled"
    assert state.installation_id is None
    assert state.decided_at == NOW
    assert await _events(session) == 0


@pytest.mark.parametrize("choice", [None, "", "pending", "yes", True])
@pytest.mark.asyncio
async def test_a_missing_or_invalid_choice_is_refused_and_leaves_the_decision_pending(session, choice):
    with pytest.raises(InvalidChoice):
        await InstallMetricsService.choose(session, choice=choice, notice_version=NOTICE_VERSION, revision=0)
    await session.rollback()

    state = await InstallMetricsService.read(session)
    assert state.preference == "pending"
    assert state.installation_id is None


@pytest.mark.asyncio
async def test_a_choice_against_another_notice_version_is_refused(session):
    with pytest.raises(NoticeVersionMismatch):
        await InstallMetricsService.choose(session, choice="enabled", notice_version="1999-01-01", revision=0)
    await session.rollback()

    assert (await InstallMetricsService.read(session)).preference == "pending"


@pytest.mark.asyncio
async def test_a_stale_prompt_cannot_overwrite_the_first_committed_choice(session):
    await _choose(session, "disabled", 0)

    with pytest.raises(RevisionConflict):
        await InstallMetricsService.choose(session, choice="enabled", notice_version=NOTICE_VERSION, revision=0)
    await session.rollback()

    state = await InstallMetricsService.read(session)
    assert state.preference == "disabled"
    assert state.installation_id is None


@pytest.mark.asyncio
async def test_concurrent_first_choices_commit_exactly_one(session):
    async def attempt(choice):
        async with database.async_session_factory() as own:
            try:
                state = await InstallMetricsService.choose(
                    own, choice=choice, notice_version=NOTICE_VERSION, revision=0, now=NOW
                )
                await own.commit()
                return state
            except RevisionConflict:
                await own.rollback()
                return None

    results = await asyncio.gather(attempt("enabled"), attempt("enabled"), attempt("disabled"))

    committed = [r for r in results if r is not None]
    assert len(committed) == 1
    final = await InstallMetricsService.read(session)
    assert final == committed[0]
    assert final.revision == 1


@pytest.mark.asyncio
async def test_withdrawal_and_re_enable_keep_the_same_installation_id(session):
    first = await _choose(session, "enabled", 0)
    withdrawn = await _choose(session, "disabled", first.revision)
    again = await _choose(session, "enabled", withdrawn.revision)

    assert withdrawn.installation_id == first.installation_id
    assert again.installation_id == first.installation_id
    assert withdrawn.enabled_since is None
    assert again.revision == 3


@pytest.mark.asyncio
async def test_two_reporting_installations_get_different_ids(session):
    first = await _choose(session, "enabled", 0)
    # A second installation: its own database, so its own state from nothing.
    await session.execute(text("DELETE FROM platform_config WHERE key = :k"), {"k": metrics_state.STATE_KEY})
    await session.execute(text("DELETE FROM install_metric_events"))
    await session.commit()

    second = await _choose(session, "enabled", 0)

    assert second.installation_id != first.installation_id


@pytest.mark.asyncio
async def test_unreadable_stored_state_reads_as_pending_and_is_never_replaced_silently(session):
    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, 'not json')"), {"k": metrics_state.STATE_KEY}
    )
    await session.commit()

    state = await InstallMetricsService.read(session)

    assert state.preference == "pending"
    assert state.installation_id is None
    with pytest.raises(metrics_state.StateUnavailable):
        await InstallMetricsService.choose(session, choice="enabled", notice_version=NOTICE_VERSION, revision=0)
