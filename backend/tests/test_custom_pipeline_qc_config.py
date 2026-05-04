"""Tests for qc_template + qc_config_json on custom-pipeline version
create/read paths."""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def pipeline_setup(session, admin_user):
    from app.models.custom_pipeline import CustomPipeline
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    org_id = admin_user.organization_id

    env = Environment(organization_id=org_id, name="default", created_by_user_id=admin_user.id)
    session.add(env)
    await session.flush()
    env_v = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        definition_format="dockerfile",
        definition_content="FROM python:3.12",
        status="ready",
        created_by_user_id=admin_user.id,
    )
    session.add(env_v)
    await session.flush()

    cp = CustomPipeline(
        organization_id=org_id,
        name="QC Cfg Pipeline",
        pipeline_key="qc-cfg-pipeline",
        created_by_user_id=admin_user.id,
    )
    session.add(cp)
    await session.flush()
    await session.commit()
    return cp, env_v


@pytest.mark.asyncio
async def test_create_version_persists_qc_config_json(client, admin_token, pipeline_setup):
    cp, env_v = pipeline_setup

    qc_config = {
        "template": "custom",
        "sections": [{"id": "main", "metrics": ["foo"]}],
        "metrics": {"foo": {"label": "Foo", "format": "decimal"}},
    }

    resp = await client.post(
        f"/api/v1/custom-pipelines/{cp.id}/versions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "code_source_type": "inline",
            "code_content": "echo hi",
            "entrypoint_command": "bash run.sh",
            "environment_version_id": env_v.id,
            "variables": [],
            "qc_template": "custom",
            "qc_config_json": qc_config,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["qc_template"] == "custom"
    assert body["qc_config_json"] == qc_config


@pytest.mark.asyncio
async def test_create_version_qc_config_optional(client, admin_token, pipeline_setup):
    """qc_template + qc_config_json are both optional. Omitting them keeps
    the row valid (qc_template defaults to None on the model; the resolver
    falls back to 'custom' for runs of this version)."""
    cp, env_v = pipeline_setup

    resp = await client.post(
        f"/api/v1/custom-pipelines/{cp.id}/versions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "code_source_type": "inline",
            "code_content": "echo hi",
            "entrypoint_command": "bash run.sh",
            "environment_version_id": env_v.id,
            "variables": [],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["qc_template"] is None
    assert body["qc_config_json"] is None


@pytest.mark.asyncio
async def test_create_version_rejects_invalid_qc_config_json_shape(client, admin_token, pipeline_setup):
    """qc_config_json must be a JSON object (dict) -- arrays and scalars
    are rejected before they hit the DB."""
    cp, env_v = pipeline_setup

    resp = await client.post(
        f"/api/v1/custom-pipelines/{cp.id}/versions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "code_source_type": "inline",
            "code_content": "echo hi",
            "entrypoint_command": "bash run.sh",
            "environment_version_id": env_v.id,
            "variables": [],
            "qc_config_json": [1, 2, 3],
        },
    )
    assert resp.status_code in (400, 422), resp.text
