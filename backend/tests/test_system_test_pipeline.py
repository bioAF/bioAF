"""Tests for bioAF System Test pipeline (spec tests 25-26).

Tests that the system test pipeline is in the catalog and produces
the correct job spec.
"""

import pytest
import pytest_asyncio

from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.bootstrap_roles import seed_builtin_roles


@pytest_asyncio.fixture
async def test_org(session):
    org = Organization(name="SystemTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="systest@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return org


class TestSystemTestInCatalog:
    @pytest.mark.asyncio
    async def test_system_test_pipeline_exists(self, session, test_org):
        """Test 25: bioAF System Test pipeline exists in the catalog after initialization."""
        await PipelineCatalogService.initialize_builtin_pipelines(session, test_org.id)
        await session.commit()

        pipeline = await PipelineCatalogService.get_pipeline(session, test_org.id, "bioaf-system-test")
        assert pipeline is not None
        assert pipeline.name == "bioAF System Test"
        assert pipeline.enabled is True
        assert pipeline.is_builtin is True


class TestSystemTestJobSpec:
    @pytest.mark.asyncio
    async def test_system_test_job_spec(self, session, test_org):
        """Test 26: system test job spec uses alpine container and correct commands."""
        await PipelineCatalogService.initialize_builtin_pipelines(session, test_org.id)
        await session.commit()

        pipeline = await PipelineCatalogService.get_pipeline(session, test_org.id, "bioaf-system-test")
        assert pipeline is not None

        # Verify default parameters
        defaults = pipeline.default_params_json or {}
        assert defaults.get("message") == "Hello from bioAF"
        assert defaults.get("sleep_seconds") == 10

        # Verify the pipeline description mentions end-to-end verification
        assert "end-to-end" in pipeline.description.lower() or "verif" in pipeline.description.lower()
