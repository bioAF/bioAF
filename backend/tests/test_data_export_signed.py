"""Signed-link delivery: unique objects, truthful lifetime, request-owned failure cleanup (plan 24 §5)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.adapters import registry
from app.adapters.models import SignedDownload
from app.exceptions import StorageUnavailableError
from app.services.data_export import resources

UTC = timezone.utc
CONFIG_BUCKET = "bioaf-config_backups-demo"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.adapters.storage import gcs
    from app.config import settings

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path / "scratch"))
    adapter = registry.get_storage_adapter()
    signed: list[str] = []
    state = SimpleNamespace(adapter=adapter, signed=signed, lifetime=24 * 3600, fail_sign=None)

    async def sign(uri, *, max_ttl_seconds, min_ttl_seconds):
        if state.fail_sign:
            raise state.fail_sign
        signed.append(uri)
        lifetime = min(max_ttl_seconds, state.lifetime)
        return SignedDownload(
            url=f"https://signed.test/{uri}", expires_at=datetime.now(UTC) + timedelta(seconds=lifetime)
        )

    monkeypatch.setattr(adapter, "sign_download", sign)
    state.scratch = tmp_path / "scratch"

    async def exports() -> list[str]:
        return [o.storage_uri for o in await adapter.list_objects(adapter.build_uri(CONFIG_BUCKET, "exports/"))]

    state.exports = exports
    return state


@pytest.fixture(autouse=True)
def _slot_is_free_after_each_test():
    yield
    assert not resources.SLOT.busy


@pytest.fixture
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _seed(session, org: int) -> None:
    await session.execute(
        text("INSERT INTO projects (id, organization_id, name, status) VALUES (1, :o, 'P', 'active')"), {"o": org}
    )
    await session.execute(
        text("INSERT INTO experiments (id, organization_id, project_id, name, status) VALUES (1, :o, 1, 'E', 'x')"),
        {"o": org},
    )
    await session.commit()


async def _signed(client, headers):
    return await client.post(
        "/api/experiments/1/export/data",
        json={"delivery_method": "gcs", "include_fastq": False, "include_provenance": False},
        headers=headers,
    )


async def _audits(session) -> list[dict]:
    rows = await session.execute(text("SELECT details_json FROM audit_log WHERE action = 'data_exported'"))
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_same_second_signed_exports_get_distinct_objects_with_their_own_payload(
    client, session, admin_user, headers, store
):
    await _seed(session, admin_user.organization_id)

    first = await _signed(client, headers)
    second = await _signed(client, headers)

    assert first.status_code == second.status_code == 200
    assert len(set(store.signed)) == 2
    for uri in store.signed:
        assert uri.startswith(f"gs://{CONFIG_BUCKET}/exports/{admin_user.organization_id}/")
        zf = zipfile.ZipFile(io.BytesIO(await store.adapter.read_bytes(uri)))
        assert any(n.endswith("/README.txt") for n in zf.namelist())
    assert not any(store.scratch.iterdir())


@pytest.mark.asyncio
async def test_the_advertised_lifetime_is_the_links_real_lifetime(client, session, admin_user, headers, store):
    store.lifetime = 3 * 3600
    await _seed(session, admin_user.organization_id)

    resp = await _signed(client, headers)

    assert resp.status_code == 200
    assert 2.95 <= resp.json()["expires_in_hours"] <= 3.0
    [audit] = await _audits(session)
    assert audit["stage"] == "link_issued"
    assert datetime.fromisoformat(audit["link_expires_at"]) > datetime.now(UTC) + timedelta(hours=2.9)


@pytest.mark.asyncio
async def test_a_full_day_link_is_advertised_by_its_remaining_lifetime(client, session, admin_user, headers, store):
    """Measured when the response is built, rounded down: never more than what is left."""
    await _seed(session, admin_user.organization_id)

    resp = await _signed(client, headers)

    assert resp.json()["expires_in_hours"] == 23.99


@pytest.mark.asyncio
async def test_a_link_shorter_than_the_minimum_is_refused_and_its_object_removed(
    client, session, admin_user, headers, store
):
    store.lifetime = 30 * 60
    await _seed(session, admin_user.organization_id)

    resp = await _signed(client, headers)

    assert resp.status_code == 502
    assert resp.json()["code"] == "storage_unavailable"
    assert await store.exports() == []
    assert await _audits(session) == []


@pytest.mark.asyncio
async def test_a_signing_failure_removes_only_that_requests_object(client, session, admin_user, headers, store):
    await _seed(session, admin_user.organization_id)
    assert (await _signed(client, headers)).status_code == 200
    kept = await store.exports()
    store.fail_sign = StorageUnavailableError("credentials rotating")

    resp = await _signed(client, headers)

    assert resp.status_code == 502
    assert await store.exports() == kept
    assert len(await _audits(session)) == 1


@pytest.mark.asyncio
async def test_a_failed_audit_commit_withdraws_the_issued_object(
    client, session, admin_user, headers, store, monkeypatch
):
    from app.api import data_export

    async def broken(**kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(data_export, "log_action", broken)
    await _seed(session, admin_user.organization_id)

    with pytest.raises(RuntimeError):
        await _signed(client, headers)

    assert await store.exports() == []
    assert not any(store.scratch.iterdir())


@pytest.mark.asyncio
async def test_a_cleanup_failure_does_not_mask_the_original_error(
    client, session, admin_user, headers, store, monkeypatch, caplog
):
    async def undeletable(uri, **kwargs):
        raise OSError("bucket unreachable")

    store.fail_sign = StorageUnavailableError("credentials rotating")
    monkeypatch.setattr(store.adapter, "delete", undeletable)
    await _seed(session, admin_user.organization_id)

    resp = await _signed(client, headers)

    assert resp.status_code == 502
    assert resp.json()["detail"] == "credentials rotating"
    assert "Export object cleanup failed (OSError)" in caplog.text
    assert "unreachable" not in caplog.text


@pytest.mark.asyncio
async def test_an_ineligible_signer_refuses_before_any_archive_work(
    client, session, admin_user, headers, store, monkeypatch
):
    async def ineligible(*, min_ttl_seconds):
        raise StorageUnavailableError("credentials rotating")

    monkeypatch.setattr(store.adapter, "check_signed_download", ineligible)
    await _seed(session, admin_user.organization_id)

    resp = await _signed(client, headers)

    assert resp.status_code == 502
    assert not store.scratch.exists()
    assert store.signed == []
    assert await _audits(session) == []
