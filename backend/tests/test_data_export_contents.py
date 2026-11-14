"""Exports carry the real selected bytes at safe, unique paths (plan 24 contracts 1, 2, 4 and 6)."""

from __future__ import annotations

import io
import os
import zipfile
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from sqlalchemy import text

from app.adapters import registry
from app.adapters.capabilities import ProviderCapabilities
from app.services.data_export import resources

EXP_UUID = "aaaaaaaa-0000-0000-0000-00000000000a"
PROJ_UUID = "bbbbbbbb-0000-0000-0000-00000000000b"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Objects in a private local store; scratch in a private directory the test can inspect."""
    from app.adapters.storage import gcs
    from app.config import settings

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path / "scratch"))
    adapter = registry.get_storage_adapter()

    async def put(key: str, data: bytes) -> str:
        uri = adapter.build_uri("bioaf-raw-demo", key)
        await adapter.write_bytes(uri, data)
        return uri

    return SimpleNamespace(put=put, scratch=tmp_path / "scratch", adapter=adapter)


@pytest.fixture(autouse=True)
def _slot_is_free_after_each_test():
    yield
    assert not resources.SLOT.busy, "an export left the global slot held"


@pytest.fixture
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _project(session, org: int, *, pid: int = 1, name: str = "Proj", uuid: str = PROJ_UUID) -> None:
    await session.execute(
        text("INSERT INTO projects (id, uuid, organization_id, name, status) VALUES (:id, :u, :org, :n, 'active')"),
        {"id": pid, "u": uuid, "org": org, "n": name},
    )


async def _experiment(session, org: int, *, eid: int = 1, name: str = "Exp", uuid: str = EXP_UUID, pid: int = 1):
    await session.execute(
        text(
            "INSERT INTO experiments (id, uuid, organization_id, project_id, name, status) "
            "VALUES (:id, :u, :org, :pid, :n, 'registered')"
        ),
        {"id": eid, "u": uuid, "org": org, "pid": pid, "n": name},
    )


async def _file(
    session,
    org: int,
    fid: int,
    filename: str,
    uri: str | None,
    *,
    experiment_id: int | None = 1,
    project_id: int | None = None,
    size: int | None = None,
    file_type: str = "txt",
    storage_deleted: bool = False,
) -> str:
    row = await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, storage_uri, file_type, size_bytes, "
            "experiment_id, project_id, source_type, storage_deleted) VALUES (:id, :org, :fn, :uri, :uri, :ft, "
            ":size, :eid, :pid, 'upload', :sd) RETURNING uuid"
        ),
        {
            "id": fid,
            "org": org,
            "fn": filename,
            "uri": uri or "",
            "ft": file_type,
            "size": size,
            "eid": experiment_id,
            "pid": project_id,
            "sd": storage_deleted,
        },
    )
    return str(row.scalar_one())


async def _export(client, headers, path: str, **body):
    payload = {"delivery_method": "direct", "include_fastq": False, "include_provenance": False, **body}
    return await client.post(path, json=payload, headers=headers)


def _zip(resp) -> zipfile.ZipFile:
    assert resp.status_code == 200, resp.text
    return zipfile.ZipFile(io.BytesIO(resp.content))


async def _audits(session, entity_type: str, entity_id: int) -> list[dict]:
    rows = await session.execute(
        text(
            "SELECT details_json FROM audit_log WHERE entity_type = :t AND entity_id = :i AND action = 'data_exported'"
        ),
        {"t": entity_type, "i": entity_id},
    )
    return [r[0] for r in rows]


# -- Contents -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_experiment_export_carries_exact_text_binary_and_empty_payloads(
    client, session, admin_user, headers, store
):
    org = admin_user.organization_id
    blob = os.urandom(300_000)
    payloads = {"greeting.txt": b"hello world", "image.bin": blob, "empty.txt": b""}
    await _project(session, org)
    await _experiment(session, org)
    uuids = {}
    for i, (name, data) in enumerate(payloads.items(), start=1):
        uuids[name] = await _file(session, org, i, name, await store.put(f"k/{name}", data), size=len(data))
    await session.commit()

    zf = _zip(await _export(client, headers, "/api/experiments/1/export/data"))

    root = f"experiment_{EXP_UUID}_Exp"
    for name, data in payloads.items():
        assert zf.read(f"{root}/results/{uuids[name]}/{name}") == data
    assert f"{root}/README.txt" in zf.namelist()
    assert f"{root}/sample_manifest.csv" in zf.namelist()
    assert zf.testzip() is None


@pytest.mark.asyncio
async def test_the_same_basename_twice_yields_two_distinct_entries(client, session, admin_user, headers, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    a = await _file(session, org, 1, "counts.csv", await store.put("a/counts.csv", b"A"), size=1)
    b = await _file(session, org, 2, "counts.csv", await store.put("b/counts.csv", b"B"), size=1)
    await session.commit()

    zf = _zip(await _export(client, headers, "/api/experiments/1/export/data"))

    root = f"experiment_{EXP_UUID}_Exp"
    assert zf.read(f"{root}/results/{a}/counts.csv") == b"A"
    assert zf.read(f"{root}/results/{b}/counts.csv") == b"B"


@pytest.mark.asyncio
async def test_a_project_export_nests_experiments_whose_names_sanitize_alike(
    client, session, admin_user, headers, store
):
    org = admin_user.organization_id
    u1, u2 = "cccccccc-0000-0000-0000-000000000001", "dddddddd-0000-0000-0000-000000000002"
    await _project(session, org, name="实验")
    await _experiment(session, org, eid=1, name="Exp/1", uuid=u1)
    await _experiment(session, org, eid=2, name="Exp:1", uuid=u2)
    f1 = await _file(session, org, 1, "x.txt", await store.put("1/x.txt", b"one"), experiment_id=1, size=3)
    f2 = await _file(session, org, 2, "x.txt", await store.put("2/x.txt", b"two"), experiment_id=2, size=3)
    f3 = await _file(
        session, org, 3, "plan.txt", await store.put("3/p.txt", b"proj"), experiment_id=None, project_id=1, size=4
    )
    await session.commit()

    resp = await _export(client, headers, "/api/projects/1/export/data")
    zf = _zip(resp)

    proot = f"project_{PROJ_UUID}_实验"
    assert zf.read(f"{proot}/experiments/experiment_{u1}_Exp_1/results/{f1}/x.txt") == b"one"
    assert zf.read(f"{proot}/experiments/experiment_{u2}_Exp_1/results/{f2}/x.txt") == b"two"
    assert zf.read(f"{proot}/results/{f3}/plan.txt") == b"proj"
    assert f"{proot}/README.txt" in zf.namelist()
    # No intermediate archives, and every member is a file under the one project root.
    assert not any(n.endswith(".zip") for n in zf.namelist())
    assert all(n.startswith(proot + "/") for n in zf.namelist())
    assert len(zf.namelist()) == len(set(zf.namelist()))


@pytest.mark.asyncio
async def test_a_fastq_export_puts_reads_under_raw(client, session, admin_user, headers, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    fq = await _file(session, org, 1, "r1.fastq.gz", await store.put("r1", b"@r\nACGT\n"), file_type="fastq.gz", size=8)
    await session.commit()

    without = _zip(await _export(client, headers, "/api/experiments/1/export/data"))
    with_fastq = _zip(await _export(client, headers, "/api/experiments/1/export/data", include_fastq=True))

    assert not any("r1.fastq.gz" in n for n in without.namelist())
    assert with_fastq.read(f"experiment_{EXP_UUID}_Exp/raw/{fq}/r1.fastq.gz") == b"@r\nACGT\n"


@pytest.mark.asyncio
async def test_requested_provenance_is_included(client, session, admin_user, headers, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    zf = _zip(await _export(client, headers, "/api/experiments/1/export/data", include_provenance=True))

    provenance = [n for n in zf.namelist() if "/provenance/" in n]
    assert provenance and provenance[0].startswith(f"experiment_{EXP_UUID}_Exp/provenance/")
    assert zf.read(provenance[0])


# -- Missing content fails, never exports placeholders ------------------------------------------


@pytest.mark.parametrize("case", ["missing_object", "storage_deleted", "no_uri"])
@pytest.mark.asyncio
async def test_unavailable_content_fails_the_export_without_a_success_audit(
    client, session, admin_user, headers, store, case
):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    uri = None if case == "no_uri" else store.adapter.build_uri("bioaf-raw-demo", "gone.txt")
    await _file(session, org, 1, "gone.txt", uri, size=3, storage_deleted=case == "storage_deleted")
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data")

    assert resp.status_code == 409
    assert resp.json()["code"] == "export_content_unavailable"
    assert "gone.txt" in resp.json()["detail"]
    assert await _audits(session, "experiment", 1) == []
    assert not any(store.scratch.iterdir()) if store.scratch.exists() else True


@pytest.mark.asyncio
async def test_a_failed_requested_provenance_report_fails_the_export(
    client, session, admin_user, headers, store, monkeypatch
):
    from app.services.provenance.report_service import ProvenanceReportService

    async def broken(**kwargs):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(ProvenanceReportService, "generate", broken)
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data", include_provenance=True)

    assert resp.status_code == 409
    assert "provenance" in resp.json()["detail"].lower()
    assert "exploded" not in resp.text
    assert await _audits(session, "experiment", 1) == []


# -- Audit, headers and request validation -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_direct_export_records_a_preparation_stage_audit(client, session, admin_user, headers, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    _zip(await _export(client, headers, "/api/experiments/1/export/data"))

    audits = await _audits(session, "experiment", 1)
    assert len(audits) == 1
    assert audits[0]["stage"] == "archive_prepared"
    assert audits[0]["delivery_method"] == "direct"


@pytest.mark.parametrize("path", ["/api/experiments/1/export/data", "/api/projects/1/export/data"])
@pytest.mark.asyncio
async def test_unicode_and_hostile_names_give_a_valid_attachment_header(
    client, session, admin_user, headers, store, path
):
    org = admin_user.organization_id
    await _project(session, org, name='实验 "q";\r\n/x')
    await _experiment(session, org, name='实验 "q";\r\n/x')
    await session.commit()

    resp = await _export(client, headers, path)

    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    disposition.encode("latin-1")
    assert disposition.startswith('attachment; filename="')
    extended = disposition.split("filename*=UTF-8''", 1)[1]
    assert "实验" in unquote(extended)
    assert resp.headers["x-accel-buffering"] == "no"
    assert int(resp.headers["content-length"]) == len(resp.content)


@pytest.mark.parametrize("method", ["", "s3", "DIRECT", "email"])
@pytest.mark.asyncio
async def test_an_unknown_delivery_method_is_rejected_before_any_work(
    client, session, admin_user, headers, store, method
):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data", delivery_method=method)

    assert resp.status_code == 422
    assert await _audits(session, "experiment", 1) == []


@pytest.mark.asyncio
async def test_a_real_foreign_experiment_and_project_are_not_found(client, session, admin_user, headers, store):
    from app.models.organization import Organization

    other = Organization(name="Other", setup_complete=True)
    session.add(other)
    await session.flush()
    await _project(session, other.id)
    await _experiment(session, other.id)
    await session.commit()

    for path in ("/api/experiments/1/export", "/api/projects/1/export"):
        assert (await _export(client, headers, f"{path}/data")).status_code == 404
        assert (await client.get(f"{path}/estimate", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_an_api_key_without_download_scope_is_denied(client, session, admin_user, viewer_api_key, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    resp = await _export(client, viewer_api_key["headers"], "/api/experiments/1/export/data")

    assert resp.status_code == 403


# -- Capability, limits, slot and capacity ------------------------------------------------------


@pytest.mark.asyncio
async def test_signed_delivery_on_a_store_without_signed_links_is_refused_before_work(
    client, session, admin_user, headers, store, monkeypatch
):
    monkeypatch.setattr(registry, "get_active_capabilities", lambda: ProviderCapabilities())
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data", delivery_method="gcs")

    assert resp.status_code == 422
    assert resp.json()["code"] == "capability_not_supported"
    assert await _audits(session, "experiment", 1) == []


@pytest.mark.asyncio
async def test_direct_delivery_works_on_an_nfs_store(client, session, admin_user, headers, tmp_path, monkeypatch):
    from app.adapters.storage.nfs import NfsStorageProvider
    from app.config import settings

    nfs = NfsStorageProvider(root=str(tmp_path / "nfs"))
    monkeypatch.setattr(registry, "_storage_adapter", nfs)
    monkeypatch.setattr(settings, "export_scratch_dir", str(tmp_path / "scratch"))
    uri = nfs.build_uri("raw", "a.txt")
    await nfs.write_bytes(uri, b"nfs bytes")
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    fid = await _file(session, org, 1, "a.txt", uri, size=9)
    await session.commit()

    zf = _zip(await _export(client, headers, "/api/experiments/1/export/data"))

    assert zf.read(f"experiment_{EXP_UUID}_Exp/results/{fid}/a.txt") == b"nfs bytes"


@pytest.mark.asyncio
async def test_known_sources_over_the_ceiling_are_refused_before_any_read(client, session, admin_user, headers, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await _file(session, org, 1, "huge.bin", store.adapter.build_uri("bioaf-raw-demo", "absent"), size=3 * 1024**3)
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data")

    assert resp.status_code == 413
    assert resp.json()["code"] == "export_limit_exceeded"
    assert "2 GiB" in resp.json()["detail"]
    assert await _audits(session, "experiment", 1) == []


@pytest.mark.asyncio
async def test_a_busy_slot_refuses_with_429_and_no_side_effects(client, session, admin_user, headers, store):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    lease = resources.SLOT.try_acquire()
    try:
        resp = await _export(client, headers, "/api/experiments/1/export/data")
    finally:
        lease.release()

    assert resp.status_code == 429
    assert resp.json()["code"] == "export_busy"
    assert await _audits(session, "experiment", 1) == []


@pytest.mark.asyncio
async def test_too_little_free_space_refuses_with_503(client, session, admin_user, headers, store, monkeypatch):
    monkeypatch.setattr(
        resources,
        "disk_probe",
        lambda path: lambda: resources.DiskSpace(free=3 * resources.GiB, total=10 * resources.GiB),
    )
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data")

    assert resp.status_code == 503
    assert resp.json()["code"] == "export_capacity_unavailable"
    assert await _audits(session, "experiment", 1) == []


@pytest.mark.asyncio
async def test_an_object_larger_than_its_catalogue_size_is_refused_not_absorbed(
    client, session, admin_user, headers, store, monkeypatch
):
    """Metadata is not trusted capacity: understated sizes exhaust the reservation and fail."""
    monkeypatch.setattr(resources, "GENERATED_ALLOWANCE", 1024)
    monkeypatch.setattr(resources, "MIN_ZIP_OVERHEAD", 1024)
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await _file(session, org, 1, "big.bin", await store.put("big", os.urandom(200_000)), size=10)
    await session.commit()

    resp = await _export(client, headers, "/api/experiments/1/export/data")

    assert resp.status_code == 503
    assert await _audits(session, "experiment", 1) == []
    assert not any(store.scratch.iterdir())


@pytest.mark.asyncio
async def test_scratch_is_removed_and_the_slot_returned_after_a_direct_download(
    client, session, admin_user, headers, store
):
    org = admin_user.organization_id
    await _project(session, org)
    await _experiment(session, org)
    await _file(session, org, 1, "a.txt", await store.put("a", b"abc"), size=3)
    await session.commit()

    _zip(await _export(client, headers, "/api/experiments/1/export/data"))

    assert not any(store.scratch.iterdir())
    assert not resources.SLOT.busy
