"""An upgrade is recorded as completed only on evidence that it completed (plan 9, section 3).

Before, startup marked a requested upgrade completed when the running version matched its target, and
deleted the host's status file. The image version alone does not show the database upgrade succeeded,
and deleting the file threw away the diagnostics of a failure. Now completion needs this code's
schema upgrade to be recorded complete, a still-running update stays pending, a failure keeps its
reason, and the status file stays.
"""

import json

import pytest

from app.config import settings
from app.models.upgrade_history import UpgradeHistory
from app.schema_upgrade import code_head
from app.services.upgrade_service import UpgradeService


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "update_status_dir", str(tmp_path))
    monkeypatch.setattr(settings, "app_version", "2026.12.0")
    return tmp_path


def _write(path, payload):
    path.write_text(json.dumps(payload))


async def _requested(session, admin_user, to_version="2026.12.0"):
    upgrade = UpgradeHistory(
        organization_id=admin_user.organization_id,
        from_version="2026.11.7",
        to_version=to_version,
        status="started",
        started_by_user_id=admin_user.id,
    )
    session.add(upgrade)
    await session.commit()
    return upgrade


@pytest.mark.asyncio
async def test_completed_only_when_the_schema_upgrade_completed(session, admin_user, status_dir):
    upgrade = await _requested(session, admin_user)
    _write(status_dir / "current.json", {"status": "completed", "to_version": "2026.12.0"})
    _write(status_dir / "schema-upgrade.json", {"status": "completed", "target_revision": code_head()})

    await UpgradeService.resolve_pending_upgrades(session)

    assert upgrade.status == "completed" and upgrade.completed_at is not None
    assert (status_dir / "current.json").exists(), "the host's record is kept"


@pytest.mark.asyncio
async def test_matching_version_without_schema_evidence_stays_pending(session, admin_user, status_dir):
    upgrade = await _requested(session, admin_user)
    _write(status_dir / "schema-upgrade.json", {"status": "failed", "target_revision": code_head()})

    await UpgradeService.resolve_pending_upgrades(session)

    assert upgrade.status == "started"


@pytest.mark.asyncio
async def test_a_failed_update_keeps_its_reason(session, admin_user, status_dir):
    upgrade = await _requested(session, admin_user, to_version="2027.1.0")
    _write(
        status_dir / "current.json",
        {"status": "failed", "to_version": "2027.1.0", "step": "migrate", "error": "Update process exited with code 1"},
    )

    await UpgradeService.resolve_pending_upgrades(session)

    assert upgrade.status == "failed"
    assert "migrate" in upgrade.notes and "exited with code 1" in upgrade.notes
    assert json.loads((status_dir / "current.json").read_text())["status"] == "failed"


@pytest.mark.asyncio
async def test_an_update_still_running_stays_pending(session, admin_user, status_dir):
    upgrade = await _requested(session, admin_user, to_version="2027.1.0")
    _write(status_dir / "current.json", {"status": "in_progress", "to_version": "2027.1.0", "step": "pull"})

    await UpgradeService.resolve_pending_upgrades(session)

    assert upgrade.status == "started"
