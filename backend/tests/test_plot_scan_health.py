"""Plot Archive scanner health and bounded retry (storage lifecycle 11H; mockup M13).

The watcher wakes every minute but lists the results store only when it is due and healthy. A
failed listing never counts as a scan, a missing or refused store is probed slowly, a temporary
failure backs off with a cap, the state survives a restart, and a configuration change or an
administrator's Check again resumes indexing at once.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.services import storage_identity
from app.services.plot_archive_service import PlotArchiveService, PlotScanFailed
from app.services.storage_identity import StoreHealth, StoreReport


async def _config(session, **kv):
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


def _health(*answers: StoreHealth):
    seq = list(answers)

    async def inspect(session, store, bucket):
        return StoreReport(store, bucket, seq.pop(0) if len(seq) > 1 else seq[0])

    return patch.object(storage_identity, "inspect_bucket", side_effect=inspect)


class _Scans:
    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    async def __call__(self, session, bucket=None):
        self.calls += 1
        if self.fail:
            raise PlotScanFailed
        return 0


@pytest.fixture
def results(session):
    async def setup():
        await _config(session, results_bucket_name="bioaf-results-acme-abc123")

    return setup


@pytest.mark.asyncio
async def test_a_healthy_store_is_scanned_and_success_is_recorded(session, results):
    await results()
    scans = _Scans()
    with _health(StoreHealth.AVAILABLE), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        health = await PlotArchiveService.scheduled_scan(session)
    assert (health.status, scans.calls) == ("ok", 1)
    assert health.last_success_at is not None and health.next_attempt_at is None


@pytest.mark.asyncio
async def test_a_missing_store_is_not_listed_every_minute(session, results):
    await results()
    scans = _Scans()
    with _health(StoreHealth.NOT_FOUND), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        first = await PlotArchiveService.scheduled_scan(session)
        second = await PlotArchiveService.scheduled_scan(session)
    assert scans.calls == 0
    assert first.status == "missing"
    assert first.next_attempt_at is not None and first.last_attempt_at is not None
    assert first.next_attempt_at - first.last_attempt_at == timedelta(minutes=30)
    assert second.last_attempt_at == first.last_attempt_at  # not due: nothing was checked


@pytest.mark.asyncio
async def test_a_failed_listing_never_advances_the_last_success(session, results):
    await results()
    with _health(StoreHealth.AVAILABLE), patch.object(PlotArchiveService, "scan_and_index", new=_Scans()):
        ok = await PlotArchiveService.scheduled_scan(session)
    with (
        _health(StoreHealth.AVAILABLE),
        patch.object(PlotArchiveService, "scan_and_index", new=_Scans(fail=True)),
    ):
        failed = await PlotArchiveService.scheduled_scan(session, force=True)
    assert failed.status == "temporary"
    assert failed.last_success_at == ok.last_success_at
    assert failed.next_attempt_at is not None and failed.last_attempt_at is not None
    assert failed.next_attempt_at - failed.last_attempt_at == timedelta(minutes=1)


@pytest.mark.asyncio
async def test_temporary_failures_back_off_with_a_cap(session, results):
    await results()
    delays = []
    with _health(StoreHealth.NOT_RESPONDING), patch.object(PlotArchiveService, "scan_and_index", new=_Scans()):
        for _ in range(8):
            health = await PlotArchiveService.scheduled_scan(session, force=True)
            assert health.next_attempt_at is not None and health.last_attempt_at is not None
            delays.append((health.next_attempt_at - health.last_attempt_at).total_seconds() / 60)
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "health, status",
    [
        (StoreHealth.NO_ACCESS, "no_access"),
        (StoreHealth.WRONG_OWNER, "not_confirmed"),
    ],
)
async def test_a_refused_or_known_mismatched_store_is_never_listed(session, results, health, status):
    await results()
    scans = _Scans()
    with _health(health), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        result = await PlotArchiveService.scheduled_scan(session)
    assert (result.status, scans.calls) == (status, 0)


@pytest.mark.asyncio
async def test_check_again_resumes_indexing_once_the_store_is_back(session, results):
    await results()
    with _health(StoreHealth.NOT_FOUND):
        await PlotArchiveService.scheduled_scan(session)
    scans = _Scans()
    with _health(StoreHealth.AVAILABLE), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        health = await PlotArchiveService.scheduled_scan(session, force=True)
    assert (health.status, scans.calls) == ("ok", 1)


@pytest.mark.asyncio
async def test_a_configuration_change_makes_the_scan_due_at_once(session, results):
    await results()
    with _health(StoreHealth.NOT_FOUND):
        await PlotArchiveService.scheduled_scan(session)
    await _config(session, results_bucket_name="bioaf-results-acme-def456")
    scans = _Scans()
    with _health(StoreHealth.AVAILABLE), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        health = await PlotArchiveService.scheduled_scan(session)
    assert (health.status, scans.calls) == ("ok", 1)


@pytest.mark.asyncio
async def test_the_backoff_survives_a_restart(session, results):
    await results()
    with _health(StoreHealth.NOT_FOUND):
        await PlotArchiveService.scheduled_scan(session)
    session.expire_all()
    # A fresh read (as after a restart) still waits for the scheduled probe.
    scans = _Scans()
    with _health(StoreHealth.AVAILABLE), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        health = await PlotArchiveService.scheduled_scan(session)
    assert scans.calls == 0 and health.status == "missing"


@pytest.mark.asyncio
async def test_state_changes_are_logged_once(session, results, caplog):
    await results()
    with _health(StoreHealth.NOT_FOUND):
        for _ in range(3):
            await PlotArchiveService.scheduled_scan(session, force=True)
    warnings = [r for r in caplog.records if "Plot archive scanning paused" in r.getMessage()]
    assert len(warnings) == 1


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_the_plot_list_reports_scan_health(client, admin_token, session, results):
    await results()
    with _health(StoreHealth.NOT_FOUND):
        await PlotArchiveService.scheduled_scan(session)
    body = (await client.get("/api/plots", headers=_auth(admin_token))).json()
    assert body["scan_health"]["status"] == "missing"


@pytest.mark.asyncio
async def test_check_again_needs_infrastructure_actions(client, admin_token, comp_bio_token, session, results):
    await results()
    refused = await client.post("/api/plots/scan/check", headers=_auth(comp_bio_token))
    with _health(StoreHealth.AVAILABLE), patch.object(PlotArchiveService, "scan_and_index", new=_Scans()):
        allowed = await client.post("/api/plots/scan/check", headers=_auth(admin_token))
    assert refused.status_code == 403
    assert allowed.status_code == 200 and allowed.json()["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("health", [StoreHealth.NOT_CONFIRMED, StoreHealth.CANNOT_CHECK])
async def test_a_readable_store_only_missing_confirmation_keeps_supplying_plots(session, results, health):
    """Owner decision: upgrading must not stop new plots; only destruction needs confirmation."""
    await results()
    scans = _Scans()
    with _health(health), patch.object(PlotArchiveService, "scan_and_index", new=scans):
        result = await PlotArchiveService.scheduled_scan(session)
    assert (result.status, scans.calls) == ("ok", 1)
