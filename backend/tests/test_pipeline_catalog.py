import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest.mark.asyncio
async def test_list_pipelines_initializes_builtins(client, admin_token):
    """First call to list pipelines initializes built-in nf-core pipelines."""
    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 3
    keys = [p["pipeline_key"] for p in data["pipelines"]]
    assert "nf-core/scrnaseq" in keys
    assert "nf-core/rnaseq" in keys
    assert "nf-core/fetchngs" in keys


@pytest.mark.asyncio
async def test_list_pipelines_idempotent(client, admin_token):
    """Calling list pipelines twice doesn't duplicate entries."""
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})
    response = await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    data = response.json()
    keys = [p["pipeline_key"] for p in data["pipelines"]]
    assert keys.count("nf-core/scrnaseq") == 1


@pytest.mark.asyncio
async def test_get_pipeline_detail(client, admin_token):
    """Get a specific pipeline by key."""
    # Initialize first
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})

    response = await client.get(
        "/api/pipelines/nf-core/scrnaseq",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pipeline_key"] == "nf-core/scrnaseq"
    assert data["is_builtin"] is True
    assert data["source_type"] == "nf-core"


@pytest.mark.asyncio
async def test_add_custom_pipeline(client, admin_token):
    """Admin can add a custom pipeline."""
    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new_callable=AsyncMock,
        return_value={"definitions": {}},
    ):
        response = await client.post(
            "/api/pipelines/custom",
            json={
                "name": "My Custom Pipeline",
                "source_url": "https://github.com/myorg/my-pipeline",
                "version": "1.0.0",
                "description": "A custom test pipeline",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "My Custom Pipeline"
        assert data["source_type"] == "git"
        assert data["is_builtin"] is False


@pytest.mark.asyncio
async def test_add_custom_pipeline_creates_audit_entry(client, admin_token, session):
    """Adding a custom pipeline writes an audit log entry."""
    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new_callable=AsyncMock,
        return_value={},
    ):
        response = await client.post(
            "/api/pipelines/custom",
            json={"name": "Audit Test Pipeline", "source_url": "https://github.com/test/pipeline"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "pipeline_catalog",
            AuditLog.action == "add_custom",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_update_pipeline_version(client, admin_token):
    """Admin can update a pipeline's version."""
    # Initialize
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})

    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new_callable=AsyncMock,
        return_value={"definitions": {"new": {}}},
    ):
        response = await client.patch(
            "/api/pipelines/version/nf-core/scrnaseq",
            json={"version": "3.0.0"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "3.0.0"


@pytest.mark.asyncio
async def test_comp_bio_can_list_pipelines(client, comp_bio_token):
    """Comp Bio users can list pipelines."""
    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_comp_bio_cannot_add_custom(client, comp_bio_token):
    """Comp Bio users cannot add custom pipelines (admin only)."""
    response = await client.post(
        "/api/pipelines/custom",
        json={"name": "Not Allowed", "source_url": "https://github.com/test"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_access_pipelines(client, viewer_token):
    """Viewer users cannot access pipeline endpoints."""
    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# --- Custom pipeline catalog integration ---


@pytest_asyncio.fixture
async def custom_pipeline_env(session, admin_user):
    """A ready pipeline environment version that custom pipelines can attach to."""
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    env = Environment(
        name="Catalog Test Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        image_uri="projects/test/global/images/bioaf-catalog-env-v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest.mark.asyncio
async def test_catalog_lists_both_nfcore_and_custom(client, admin_token, session, admin_user, custom_pipeline_env):
    """GET /api/pipelines returns both NF-Core and custom catalog entries."""
    from app.schemas.custom_pipeline import (
        CustomPipelineCreateRequest,
        CustomPipelineVersionCreateRequest,
    )
    from app.services.custom_pipeline_service import CustomPipelineService

    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="My Custom", description="custom desc"),
    )
    await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print('hi')",
            entrypoint_command="python script.py",
            environment_version_id=custom_pipeline_env.id,
        ),
    )
    await session.commit()

    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    source_types = {p["source_type"] for p in data["pipelines"]}
    assert "nf-core" in source_types
    assert "custom" in source_types

    keys = [p["pipeline_key"] for p in data["pipelines"]]
    assert "nf-core/scrnaseq" in keys
    assert "my-custom" in keys


@pytest.mark.asyncio
async def test_custom_entries_include_creator_and_latest_version(
    client, admin_token, session, admin_user, custom_pipeline_env
):
    """Custom catalog entries include creator username and latest active version number."""
    from app.schemas.custom_pipeline import (
        CustomPipelineCreateRequest,
        CustomPipelineVersionCreateRequest,
    )
    from app.services.custom_pipeline_service import CustomPipelineService

    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Versioned Custom"),
    )
    payload = CustomPipelineVersionCreateRequest(
        code_source_type="inline",
        code_content="print('v')",
        entrypoint_command="python script.py",
        environment_version_id=custom_pipeline_env.id,
    )
    await CustomPipelineService.create_version(session, admin_user.organization_id, admin_user.id, pipeline.id, payload)
    await CustomPipelineService.create_version(session, admin_user.organization_id, admin_user.id, pipeline.id, payload)
    await CustomPipelineService.create_version(session, admin_user.organization_id, admin_user.id, pipeline.id, payload)
    await session.commit()

    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    custom = next(p for p in data["pipelines"] if p["pipeline_key"] == "versioned-custom")
    assert custom["source_type"] == "custom"
    assert custom["custom_pipeline_id"] == pipeline.id
    assert custom["created_by_username"] == "admin"  # admin@test.com -> "admin"
    assert custom["latest_version_number"] == 3


@pytest.mark.asyncio
async def test_nfcore_entries_have_null_custom_fields(client, admin_token):
    """NF-Core / builtin entries return null for custom_pipeline_id, created_by_username, latest_version_number."""
    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    nfcore = [p for p in data["pipelines"] if p["source_type"] in ("nf-core", "builtin")]
    assert len(nfcore) >= 3
    for entry in nfcore:
        assert entry["custom_pipeline_id"] is None
        assert entry["created_by_username"] is None
        assert entry["latest_version_number"] is None


@pytest.mark.asyncio
async def test_disabled_custom_pipeline_excluded_from_listing(
    client, admin_token, session, admin_user, custom_pipeline_env
):
    """Soft-deleted (disabled) custom pipelines are excluded from the catalog listing."""
    from app.schemas.custom_pipeline import CustomPipelineCreateRequest
    from app.services.custom_pipeline_service import CustomPipelineService

    keep = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Keep Me"),
    )
    drop = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Drop Me"),
    )
    await session.commit()

    await CustomPipelineService.delete_pipeline(session, admin_user.organization_id, admin_user.id, drop.id)
    await session.commit()

    response = await client.get(
        "/api/pipelines",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    keys = [p["pipeline_key"] for p in data["pipelines"]]
    assert "keep-me" in keys
    assert "drop-me" not in keys
    # sanity: the kept pipeline is preserved as a custom entry
    keep_entry = next(p for p in data["pipelines"] if p["pipeline_key"] == "keep-me")
    assert keep_entry["custom_pipeline_id"] == keep.id
    assert keep_entry["enabled"] is True
