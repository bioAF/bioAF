"""Public metadata reads share the read policy and keep their fallbacks.

nf-core registry refresh and pipeline schema fetches retry transient failures
(429/502/503/504, connect/read failures) under METADATA_READ. The registry
keeps its rows when the fetch finally fails; a schema 404 is still the absent
marker after one request; any other final failure is still None.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from app.services.nf_core_registry_service import NfCoreRegistryService
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.samplesheet_schema import is_absent_marker

pytestmark = pytest.mark.asyncio

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "nf_core_pipelines_sample.json").read_text())
INPUT_SCHEMA_URL = "https://raw.githubusercontent.com/nf-core/sarek/3.9.0/assets/schema_input.json"
PIPELINE_SCHEMA_URL = "https://raw.githubusercontent.com/nf-core/sarek/3.9.0/nextflow_schema.json"


def _route(url, *steps):
    script = list(steps)
    route = respx.get(url)

    def handler(request):
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return step

    route.mock(side_effect=handler)
    return route


async def test_a_transient_registry_failure_is_retried(session):
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline

    with respx.mock:
        route = _route(NfCoreRegistryService.REGISTRY_URL, httpx.Response(503), httpx.Response(200, json=FIXTURE))
        result = await NfCoreRegistryService.refresh_registry(session)
    assert result["error"] is None
    assert route.call_count == 2
    assert len((await session.execute(select(NfCoreRegistryPipeline))).scalars().all()) == 3


async def test_a_registry_outage_keeps_existing_rows_after_bounded_retries(session):
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline

    with respx.mock:
        _route(NfCoreRegistryService.REGISTRY_URL, httpx.Response(200, json=FIXTURE))
        await NfCoreRegistryService.refresh_registry(session)
        await session.commit()
    with respx.mock:
        route = _route(NfCoreRegistryService.REGISTRY_URL, httpx.ConnectError("down"))
        result = await NfCoreRegistryService.refresh_registry(session)
    assert result["error"]
    assert route.call_count == 3
    assert len((await session.execute(select(NfCoreRegistryPipeline))).scalars().all()) == 3


async def test_a_transient_input_schema_failure_is_retried():
    with respx.mock:
        route = _route(INPUT_SCHEMA_URL, httpx.Response(502), httpx.Response(200, json={"type": "array"}))
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")
    assert result == {"type": "array"}
    assert route.call_count == 2


async def test_a_missing_input_schema_is_absent_after_one_request():
    with respx.mock:
        route = _route(INPUT_SCHEMA_URL, httpx.Response(404))
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")
    assert is_absent_marker(result)
    assert route.call_count == 1


@pytest.mark.parametrize("failure", [httpx.Response(503), httpx.ConnectError("down")])
async def test_an_unavailable_input_schema_is_none_after_bounded_retries(failure):
    with respx.mock:
        route = _route(INPUT_SCHEMA_URL, failure)
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")
    assert result is None
    assert route.call_count == 3


async def test_a_transient_pipeline_schema_failure_is_retried():
    with respx.mock:
        route = _route(PIPELINE_SCHEMA_URL, httpx.Response(504), httpx.Response(200, json={"definitions": {}}))
        result = await PipelineCatalogService.fetch_pipeline_schema("https://github.com/nf-core/sarek", "3.9.0")
    assert result == {"definitions": {}}
    assert route.call_count == 2
