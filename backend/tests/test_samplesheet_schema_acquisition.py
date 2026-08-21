"""Fetching and storing a pipeline's samplesheet contract.

The contract is fetched at install, next to the nextflow_schema.json fetch that
already happens there and pinned to the same version, so the stored schema is
the one the installed release actually uses.

The load-bearing property is that NONE of this can break a launch: a GitHub
outage, a rate limit, or a pipeline that ships no such file must all fall back
to today's behavior rather than refuse to run.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.samplesheet_schema import SCHEMA_ABSENT, is_absent_marker

SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["sample", "fastq_1"],
        "properties": {"sample": {"type": "string"}, "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"}},
    },
}


def _response(status_code: int, payload=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload if payload is not None else {}
    return r


def _client(response=None, side_effect=None):
    client = AsyncMock()
    if side_effect is not None:
        client.get.side_effect = side_effect
    else:
        client.get.return_value = response
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_fetches_the_schema_for_the_pinned_version():
    ctx, client = _client(_response(200, SCHEMA))
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")

    assert result == SCHEMA
    url = client.get.call_args[0][0]
    assert url == "https://raw.githubusercontent.com/nf-core/sarek/3.9.0/assets/schema_input.json"


@pytest.mark.asyncio
async def test_a_pipeline_that_ships_no_schema_returns_the_absent_marker():
    """A 404 is a fact about the pipeline, not a failure. Recording it stops the
    lazy path from re-requesting a known-missing file on every launch."""
    ctx, _ = _client(_response(404))
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/eager", "2.5.0")

    assert is_absent_marker(result)


@pytest.mark.asyncio
async def test_a_network_failure_returns_nothing_and_does_not_raise():
    """Distinct from absent: nothing is recorded, so the next launch retries."""
    ctx, _ = _client(side_effect=RuntimeError("connection reset"))
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")

    assert result is None
    assert not is_absent_marker(result)


@pytest.mark.asyncio
async def test_malformed_json_is_treated_as_a_failure_not_as_a_contract():
    response = _response(200)
    response.json.side_effect = ValueError("not json")
    ctx, _ = _client(response)
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")

    assert result is None


@pytest.mark.asyncio
async def test_a_server_error_returns_nothing_so_the_next_launch_retries():
    ctx, _ = _client(_response(500))
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await PipelineCatalogService.fetch_input_schema("https://github.com/nf-core/sarek", "3.9.0")

    assert result is None


@pytest.mark.asyncio
async def test_no_source_url_makes_no_request():
    """Custom pipelines have no registry URL, so there is nothing to fetch."""
    ctx, client = _client(_response(200, SCHEMA))
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await PipelineCatalogService.fetch_input_schema(None, "1.0")

    assert result is None
    client.get.assert_not_called()


def test_the_absent_marker_is_distinguishable_from_a_real_schema():
    assert is_absent_marker(SCHEMA_ABSENT)
    assert not is_absent_marker(SCHEMA)
    assert not is_absent_marker(None)
    assert not is_absent_marker({})
