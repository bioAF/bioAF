import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _mock_httpx_get(payload: dict):
    """Return an AsyncMock that mimics httpx.AsyncClient().get(...) returning payload."""
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value=payload)
    response.raise_for_status = MagicMock()
    get_mock = AsyncMock(return_value=response)
    client_mock = MagicMock()
    client_mock.get = get_mock
    # Async context manager: __aenter__ returns the client itself
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.mark.asyncio
async def test_refresh_registry_populates_rows(session):
    """Refresh fetches the nf-core JSON and writes one row per pipeline."""
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
    from sqlalchemy import select

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        result = await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    assert result["fetched"] == 3
    assert result["archived"] == 0

    rows = (await session.execute(select(NfCoreRegistryPipeline).order_by(NfCoreRegistryPipeline.name))).scalars().all()
    assert [r.name for r in rows] == ["rnaseq", "sarek", "scrnaseq"]

    scrnaseq = next(r for r in rows if r.name == "scrnaseq")
    assert scrnaseq.full_name == "nf-core/scrnaseq"
    assert scrnaseq.stars == 220
    assert scrnaseq.latest_release == "2.7.1"
    # dev pseudo-release filtered out
    tags = [rel["tag_name"] for rel in scrnaseq.releases_json]
    assert "dev" not in tags
    assert tags == ["2.7.1", "2.6.0"]
    assert "single-cell" in scrnaseq.topics


@pytest.mark.asyncio
async def test_refresh_registry_marks_disappeared_rows_archived(session):
    """A second refresh that omits a pipeline marks the prior row as archived."""
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
    from sqlalchemy import select

    initial = _load_fixture("nf_core_pipelines_sample.json")
    second = _load_fixture("nf_core_pipelines_sarek_archived.json")

    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(initial)):
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(second)):
        result = await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    assert result["fetched"] == 2
    assert result["archived"] == 1

    sarek = (
        await session.execute(select(NfCoreRegistryPipeline).where(NfCoreRegistryPipeline.name == "sarek"))
    ).scalar_one()
    assert sarek.archived is True


@pytest.mark.asyncio
async def test_refresh_registry_records_error_on_fetch_failure(session):
    """Network error: rows preserved, last_error populated, no exception bubbles up."""
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
    from app.models.nf_core_registry_refresh import NfCoreRegistryRefresh
    from sqlalchemy import select

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    rows_before = (await session.execute(select(NfCoreRegistryPipeline))).scalars().all()
    assert len(rows_before) == 3

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("boom")

    client_mock = MagicMock()
    client_mock.get = AsyncMock(side_effect=_raise)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=cm):
        result = await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    assert result["error"] is not None

    rows_after = (await session.execute(select(NfCoreRegistryPipeline))).scalars().all()
    assert len(rows_after) == 3  # preserved

    refresh = (
        await session.execute(select(NfCoreRegistryRefresh).where(NfCoreRegistryRefresh.id == 1))
    ).scalar_one()
    assert refresh.last_error is not None
    assert "boom" in refresh.last_error


# ----- list_pipelines_with_status -----


async def _seed_org_with_catalog_entries(session, catalog_entries: list[dict] | None = None):
    """Create an org and optionally pre-seed pipeline_catalog rows so the
    install-status join has data to find. Returns the org_id."""
    from app.models.organization import Organization
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    org = Organization(name="Status Test Org", setup_complete=True)
    session.add(org)
    await session.flush()

    for entry in catalog_entries or []:
        session.add(
            PipelineCatalogEntry(
                organization_id=org.id,
                pipeline_key=entry["pipeline_key"],
                name=entry.get("name", entry["pipeline_key"]),
                source_type=entry.get("source_type", "nf-core"),
                source_url=entry.get("source_url"),
                version=entry.get("version"),
                is_builtin=entry.get("is_builtin", False),
                enabled=True,
            )
        )
    await session.flush()
    return org.id


@pytest.mark.asyncio
async def test_list_pipelines_with_status_marks_installed_and_update_available(session):
    """LEFT JOIN to pipeline_catalog computes installed/update_available per org."""
    from app.services.nf_core_registry_service import NfCoreRegistryService

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)

    org_id = await _seed_org_with_catalog_entries(
        session,
        [
            # Installed at the same version as the registry's latest -> no update available.
            {"pipeline_key": "nf-core/rnaseq", "source_url": "https://github.com/nf-core/rnaseq", "version": "3.14.0"},
            # Installed at an older version than the registry latest -> update available.
            {"pipeline_key": "nf-core/scrnaseq", "source_url": "https://github.com/nf-core/scrnaseq", "version": "2.6.0"},
        ],
    )
    await session.commit()

    rows = await NfCoreRegistryService.list_pipelines_with_status(session, org_id)
    by_name = {r["name"]: r for r in rows}

    assert by_name["rnaseq"]["installed"] is True
    assert by_name["rnaseq"]["installed_version"] == "3.14.0"
    assert by_name["rnaseq"]["update_available"] is False

    assert by_name["scrnaseq"]["installed"] is True
    assert by_name["scrnaseq"]["installed_version"] == "2.6.0"
    assert by_name["scrnaseq"]["update_available"] is True
    assert by_name["scrnaseq"]["latest_release"] == "2.7.1"

    assert by_name["sarek"]["installed"] is False
    assert by_name["sarek"]["installed_version"] is None
    assert by_name["sarek"]["update_available"] is False


@pytest.mark.asyncio
async def test_list_pipelines_with_status_filters_search_query(session):
    from app.services.nf_core_registry_service import NfCoreRegistryService

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
    org_id = await _seed_org_with_catalog_entries(session)
    await session.commit()

    rows = await NfCoreRegistryService.list_pipelines_with_status(session, org_id, q="rna")
    names = {r["name"] for r in rows}
    assert names == {"rnaseq", "scrnaseq"}

    rows = await NfCoreRegistryService.list_pipelines_with_status(session, org_id, q="variant")
    names = {r["name"] for r in rows}
    assert names == {"sarek"}


@pytest.mark.asyncio
async def test_list_pipelines_with_status_hides_archived_by_default(session):
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
    from sqlalchemy import select, update

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
    await session.execute(
        update(NfCoreRegistryPipeline).where(NfCoreRegistryPipeline.name == "sarek").values(archived=True)
    )
    await session.flush()
    org_id = await _seed_org_with_catalog_entries(session)
    await session.commit()

    rows = await NfCoreRegistryService.list_pipelines_with_status(session, org_id)
    assert {r["name"] for r in rows} == {"scrnaseq", "rnaseq"}

    rows = await NfCoreRegistryService.list_pipelines_with_status(session, org_id, include_archived=True)
    assert {r["name"] for r in rows} == {"scrnaseq", "rnaseq", "sarek"}
    sarek = next(r for r in rows if r["name"] == "sarek")
    assert sarek["archived"] is True

    # Sanity: select() executed inside the test must be importable
    assert select is not None


# ----- get_pipeline_versions -----


@pytest.mark.asyncio
async def test_get_pipeline_versions_returns_releases_sorted_newest_first(session):
    from app.services.nf_core_registry_service import NfCoreRegistryService

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
    await session.commit()

    versions = await NfCoreRegistryService.get_pipeline_versions(session, "scrnaseq")
    tags = [v["tag_name"] for v in versions]
    assert tags == ["2.7.1", "2.6.0"]
    assert "dev" not in tags


@pytest.mark.asyncio
async def test_get_pipeline_versions_unknown_pipeline_returns_empty(session):
    from app.services.nf_core_registry_service import NfCoreRegistryService

    versions = await NfCoreRegistryService.get_pipeline_versions(session, "does-not-exist")
    assert versions == []


# ----- install_pipeline -----


@pytest.mark.asyncio
async def test_install_pipeline_creates_catalog_entry(session, admin_user):
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry
    from sqlalchemy import select

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new_callable=AsyncMock,
        return_value={"definitions": {"core": {}}},
    ):
        entry = await NfCoreRegistryService.install_pipeline(
            session, admin_user.organization_id, admin_user.id, "scrnaseq", "2.7.1"
        )
        await session.commit()

    assert entry.pipeline_key == "nf-core/scrnaseq"
    assert entry.source_type == "nf-core"
    assert entry.source_url == "https://github.com/nf-core/scrnaseq"
    assert entry.version == "2.7.1"
    assert entry.is_builtin is False
    assert entry.qc_template == "scrnaseq"  # QC_TEMPLATE_MAP lookup
    assert entry.schema_json == {"definitions": {"core": {}}}

    fetched = (
        await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.organization_id == admin_user.organization_id,
                PipelineCatalogEntry.pipeline_key == "nf-core/scrnaseq",
            )
        )
    ).scalar_one()
    assert fetched.id == entry.id


@pytest.mark.asyncio
async def test_install_pipeline_unknown_qc_template_falls_back_to_generic(session, admin_user):
    from app.services.nf_core_registry_service import NfCoreRegistryService

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new_callable=AsyncMock,
        return_value={},
    ):
        entry = await NfCoreRegistryService.install_pipeline(
            session, admin_user.organization_id, admin_user.id, "sarek", "3.4.0"
        )
        await session.commit()

    assert entry.qc_template == "generic"


@pytest.mark.asyncio
async def test_install_pipeline_raises_on_collision(session, admin_user):
    """Re-installing an already-installed pipeline raises (callers map to 409)."""
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/scrnaseq",
            name="nf-core/scrnaseq",
            source_type="nf-core",
            source_url="https://github.com/nf-core/scrnaseq",
            version="2.6.0",
            is_builtin=True,
            enabled=True,
        )
    )
    await session.commit()

    with pytest.raises(NfCoreRegistryService.PipelineAlreadyInstalledError):
        with patch(
            "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
            new_callable=AsyncMock,
            return_value={},
        ):
            await NfCoreRegistryService.install_pipeline(
                session, admin_user.organization_id, admin_user.id, "scrnaseq", "2.7.1"
            )


@pytest.mark.asyncio
async def test_install_pipeline_raises_when_not_in_registry(session, admin_user):
    from app.services.nf_core_registry_service import NfCoreRegistryService

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    with pytest.raises(NfCoreRegistryService.PipelineNotInRegistryError):
        await NfCoreRegistryService.install_pipeline(
            session, admin_user.organization_id, admin_user.id, "does-not-exist", "1.0.0"
        )


@pytest.mark.asyncio
async def test_install_pipeline_writes_audit_log(session, admin_user):
    from app.services.nf_core_registry_service import NfCoreRegistryService
    from app.models.audit_log import AuditLog
    from sqlalchemy import select

    payload = _load_fixture("nf_core_pipelines_sample.json")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(payload)):
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()

    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new_callable=AsyncMock,
        return_value={},
    ):
        await NfCoreRegistryService.install_pipeline(
            session, admin_user.organization_id, admin_user.id, "rnaseq", "3.14.0"
        )
        await session.commit()

    entries = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "pipeline_catalog",
                AuditLog.action == "install_from_nf_core_registry",
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].details_json["name"] == "rnaseq"
    assert entries[0].details_json["version"] == "3.14.0"
