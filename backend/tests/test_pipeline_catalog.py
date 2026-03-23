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
