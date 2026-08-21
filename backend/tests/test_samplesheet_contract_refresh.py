"""Keeping a pipeline's stored contract in step with the version installed.

A samplesheet contract is fetched once, at install, pinned to the release tag
that was current then. Nothing re-fetched it, so upgrading a pipeline left bioAF
validating the new version against the OLD version's rules: it would go on
requiring a column that had been dropped, and stay blind to one that had been
added or had gained a constraint.

That is a silent wrongness of the kind this whole project exists to remove, and
it is also a precondition for carrying a saved design across an upgrade. bioAF
cannot flag "this column no longer exists" while it believes the contract is
whatever it was at install.

Re-fetching happens when the VERSION CHANGES, not on every launch. Fetching per
launch puts a network call on the launch path and fails runs when GitHub is
unreachable; a background schedule finds drift late and adds a moving part; a
manual refresh button leaves an upgraded pipeline wrong until somebody remembers,
which is the failure this closes.

A fetch that fails records nothing and changes nothing, so the launch proceeds on
the contract bioAF already has. A GitHub outage must never block a run that would
work.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.pipeline_run_service import PipelineRunService

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _schema(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest_asyncio.fixture
async def entry(session):
    org = Organization(name="RefreshOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    pipeline = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="nf-core/mag",
        name="nf-core/mag",
        source_type="nf-core",
        source_url="https://github.com/nf-core/mag",
        version="5.5.0",
        default_params_json={},
        input_schema_json=_schema("mag"),
        input_schema_version="5.5.0",
        enabled=True,
    )
    session.add(pipeline)
    await session.flush()
    return pipeline


class TestItRefetchesWhenTheVersionMoves:
    @pytest.mark.asyncio
    async def test_an_upgraded_pipeline_gets_its_new_contract(self, session, entry):
        """The hole this closes. Before it, mag upgraded to 6.0 was still
        validated against 5.5's columns."""
        entry.version = "6.0.0"

        with patch.object(
            PipelineCatalogService, "fetch_input_schema", new=AsyncMock(return_value=_schema("rnasplice"))
        ) as fetch:
            contract = await PipelineRunService._resolve_contract(session, entry)

        assert fetch.await_count == 1
        assert "condition" in contract.columns
        assert entry.input_schema_version == "6.0.0"

    @pytest.mark.asyncio
    async def test_an_unchanged_version_is_not_refetched(self, session, entry):
        """Re-fetching per launch puts a network call on the launch path and
        fails runs when GitHub is unreachable."""
        with patch.object(PipelineCatalogService, "fetch_input_schema", new=AsyncMock()) as fetch:
            contract = await PipelineRunService._resolve_contract(session, entry)

        assert fetch.await_count == 0
        assert "group" in contract.columns

    @pytest.mark.asyncio
    async def test_a_contract_stored_before_versions_were_tracked_is_left_alone(self, session, entry):
        """Entries installed before this revision carry a null schema version.
        Treating that as a mismatch would re-fetch the whole catalog on its next
        launch, so an unknown version means "assume current" and gets stamped
        rather than re-fetched."""
        entry.input_schema_version = None

        with patch.object(PipelineCatalogService, "fetch_input_schema", new=AsyncMock()) as fetch:
            await PipelineRunService._resolve_contract(session, entry)

        assert fetch.await_count == 0
        assert entry.input_schema_version == "5.5.0"


class TestAFailedRefetchChangesNothing:
    @pytest.mark.asyncio
    async def test_the_old_contract_still_applies(self, session, entry):
        """A GitHub outage must never block a launch that works today, so a
        failed fetch keeps the contract bioAF already holds."""
        entry.version = "6.0.0"

        with patch.object(PipelineCatalogService, "fetch_input_schema", new=AsyncMock(return_value=None)):
            contract = await PipelineRunService._resolve_contract(session, entry)

        assert "group" in contract.columns
        assert entry.input_schema_json == _schema("mag")

    @pytest.mark.asyncio
    async def test_the_stored_version_is_not_advanced(self, session, entry):
        """Stamping the new version after a failed fetch would record that the
        contract had been refreshed when it had not, and the pipeline would stay
        wrong until its version moved again."""
        entry.version = "6.0.0"

        with patch.object(PipelineCatalogService, "fetch_input_schema", new=AsyncMock(return_value=None)):
            await PipelineRunService._resolve_contract(session, entry)

        assert entry.input_schema_version == "5.5.0"


class TestFirstFetchStillWorks:
    @pytest.mark.asyncio
    async def test_an_entry_with_no_contract_fetches_and_records_the_version(self, session, entry):
        """The existing lazy path, which is how entries installed before
        contracts existed acquire one."""
        entry.input_schema_json = None
        entry.input_schema_version = None

        with patch.object(PipelineCatalogService, "fetch_input_schema", new=AsyncMock(return_value=_schema("mag"))):
            contract = await PipelineRunService._resolve_contract(session, entry)

        assert "group" in contract.columns
        assert entry.input_schema_version == "5.5.0"
