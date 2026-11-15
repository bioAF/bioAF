"""The deliberate clone-identity reset (ADR-075, docs/installation-metrics.md).

A restored backup restores the Installation ID and choice as of the backup. An installation cloned on
purpose (a copy that will keep running beside the original) needs its own identity: the reset clears the
ID, the choice and every reserved report, so the next admin is asked again and an opt-in gets a new ID.
No fingerprinting and no automatic clone detection.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

import app.database as database
from app.config import settings
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics.state import NOTICE_VERSION, InstallMetricsService

NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)


async def _count(session) -> int:
    return (await session.execute(select(func.count()).select_from(InstallMetricEvent))).scalar_one()


@pytest.mark.asyncio
async def test_a_reset_clears_the_identity_choice_and_queued_reports(session, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "install_metrics_disabled", False)
    before = await InstallMetricsService.choose(
        session, choice="enabled", notice_version=NOTICE_VERSION, revision=0, now=NOW
    )
    await session.commit()
    assert await _count(session) == 1

    after = await InstallMetricsService.reset_identity(session)
    await session.commit()

    assert after.installation_id is None
    assert after.preference == "pending"
    assert after.decided_at is None and after.enabled_since is None and after.baseline is None
    assert after.revision == before.revision + 1
    assert after.classification == before.classification
    assert after.setup_completed is True
    assert await _count(session) == 0

    again = await InstallMetricsService.choose(
        session, choice="enabled", notice_version=NOTICE_VERSION, revision=after.revision, now=NOW
    )
    await session.commit()
    assert again.installation_id not in (None, before.installation_id)
    assert await _count(session) == 1


@pytest.mark.asyncio
async def test_the_reset_command_requires_explicit_confirmation(session, admin_user, capsys):
    from app.cli import reset_install_metrics_identity as cli

    await InstallMetricsService.choose(session, choice="disabled", notice_version=NOTICE_VERSION, revision=0)
    await session.commit()

    assert await cli.run([], database.async_session_factory) != 0
    assert (await InstallMetricsService.read(session)).preference == "disabled"

    assert await cli.run(["--confirm"], database.async_session_factory) == 0
    session.expire_all()
    assert (await InstallMetricsService.read(session)).preference == "pending"
    assert "reset" in capsys.readouterr().out
