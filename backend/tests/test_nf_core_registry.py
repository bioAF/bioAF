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
