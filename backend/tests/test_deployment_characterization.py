"""Behavior pinned before the deployment refactor (oct refactor plan 2, step 1).

Each test states what callers rely on today: event order, phase totals, which
platform_config keys survive a commit, what a failed module leaves behind, and
what the component toggle returns. Transaction ownership at these boundaries:

- ``_run_module`` commits the finished plan so pollers can see it. Everything
  else in the deployment generators only flushes.
- The caller that owns the session (SSE route, background drain, test) commits.
  A test that asserts persistence reads through a second connection, so an
  uncommitted write cannot pass.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.terraform_executor import TerraformProgressEvent

NOTEBOOK_BUILD = "app.api.stack_deploy.build_notebook_image"
CELLXGENE_BUILD = "app.services.cellxgene_image_service.build_cellxgene_image"
TOGGLE_URL = "/api/v1/infrastructure/stack/components/{key}/toggle"


async def _seed_user(session):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="CharacterizationOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="characterization@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return user.id


async def _seed_config(session, **values):
    for key, value in values.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


async def _seed_gcp_ready(session, **overrides):
    await _seed_config(
        session,
        **{
            "gcp_credentials_configured": "true",
            "terraform_initialized": "true",
            "compute_deployed": "false",
            "storage_deployed": "false",
            "gcp_project_id": "proj",
            "gcp_region": "us-central1",
            "org_slug": "acme",
            **overrides,
        },
    )


async def _committed(db_engine, key):
    """Read a platform_config value through a separate session."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        row = (
            await other.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))
        ).fetchone()
    return row[0] if row else None


def _event(event_type, **kwargs):
    return TerraformProgressEvent(event_type=event_type, **kwargs)


def _outputs(**values):
    return {name: {"value": value} for name, value in values.items()}


def _run_module_script(scripts):
    """A `_run_module` replacement that replays one event list per module."""

    async def run_module(_session, _user_id, module_name):
        for event in scripts.get(module_name, [_event("apply_complete")]):
            yield event

    return run_module


async def _deploy(session, user_id, scripts, **kwargs):
    from app.services.stack_deployment import deploy_stack

    with (
        patch("app.services.stack_deployment._run_module", side_effect=_run_module_script(scripts)),
        patch("app.services.stack_deployment._select_default_pool_zone", AsyncMock(return_value="us-central1-a")),
    ):
        return [event async for event in deploy_stack(session, "kubernetes", user_id=user_id, **kwargs)]


# -----------------------------------------------------------------------
# Progress ordering and phase totals
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_then_compute_event_order_and_cumulative_totals(session):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session)

    events = await _deploy(
        session,
        user_id,
        {
            "storage": [
                _event("resource_complete", resources_completed=1, resources_total=2),
                _event("resource_complete", resources_completed=2, resources_total=2),
                _event("apply_complete", resources_completed=2, resources_total=2),
            ],
            "compute": [
                _event("resource_complete", resources_completed=1, resources_total=3),
                _event("apply_complete", resources_completed=3, resources_total=3),
            ],
        },
    )

    assert [e.event_type for e in events if e.event_type != "progress"] == [
        "resource_complete",
        "resource_complete",
        "phase_complete",
        "resource_complete",
        "phase_complete",
        "stack_complete",
    ]
    compute_resource = [e for e in events if e.event_type == "resource_complete"][-1]
    assert (compute_resource.resources_completed, compute_resource.resources_total) == (3, 5)
    phases = [e for e in events if e.event_type == "phase_complete"]
    assert [(p.resources_completed, p.resources_total) for p in phases] == [(2, 2), (5, 5)]
    assert phases[0].message == "Storage deployment complete"
    assert phases[1].message == "Compute deployment complete"
    assert (events[-1].resources_completed, events[-1].resources_total) == (5, 5)


@pytest.mark.asyncio
async def test_storage_already_deployed_totals_cover_compute_only(session):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session, storage_deployed="true")

    events = await _deploy(
        session, user_id, {"compute": [_event("apply_complete", resources_completed=4, resources_total=4)]}
    )

    assert not any(e.message == "Deploying storage infrastructure..." for e in events)
    assert (events[-1].event_type, events[-1].resources_completed, events[-1].resources_total) == (
        "stack_complete",
        4,
        4,
    )


# -----------------------------------------------------------------------
# Output persistence through a real commit
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_deploy_skips_blank_outputs(session, db_engine):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session, literature_bucket_name="keep-me")

    await _deploy(
        session,
        user_id,
        {
            "storage": [
                _event(
                    "apply_complete",
                    extra={
                        "outputs": _outputs(
                            raw_bucket_name="bioaf-raw",
                            literature_bucket_name="",
                            pubsub_topic_name="topic",
                        )
                    },
                )
            ]
        },
    )
    await session.commit()

    assert await _committed(db_engine, "raw_bucket_name") == "bioaf-raw"
    assert await _committed(db_engine, "pubsub_topic_name") == "topic"
    assert await _committed(db_engine, "literature_bucket_name") == "keep-me"
    assert await _committed(db_engine, "storage_deployed") == "true"


@pytest.mark.asyncio
async def test_compute_deploy_writes_null_for_blank_outputs(session, db_engine):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session, storage_deployed="true")

    await _deploy(
        session,
        user_id,
        {"compute": [_event("apply_complete", extra={"outputs": _outputs(cluster_name="bioaf-x")})]},
    )
    await session.commit()

    assert await _committed(db_engine, "gke_cluster_name") == "bioaf-x"
    assert await _committed(db_engine, "gke_cluster_endpoint") == "null"
    assert await _committed(db_engine, "notebook_runner_sa_email") == "null"
    assert await _committed(db_engine, "compute_deployed") == "true"
    assert await _committed(db_engine, "compute_stack") == "kubernetes"
    # AWS-only keys are never written on GCP.
    assert await _committed(db_engine, "pipeline_runner_role_arn") is None


@pytest.mark.asyncio
async def test_aws_image_build_writes_null_for_blank_project(session, db_engine):
    user_id = await _seed_user(session)
    await _seed_config(
        session,
        cloud_provider="aws",
        aws_account_id="111122223333",
        aws_region="us-west-1",
        terraform_initialized="true",
        compute_deployed="false",
        storage_deployed="true",
    )

    with patch("app.services.stack_deployment._ensure_aws_cluster_autoscaler", AsyncMock(return_value="installed")):
        await _deploy(session, user_id, {"image_build": [_event("apply_complete")]})
    await session.commit()

    assert await _committed(db_engine, "aws_codebuild_project") == "null"
    assert await _committed(db_engine, "aws_image_build_deployed") == "true"


@pytest.mark.asyncio
async def test_sync_storage_excludes_pubsub_and_skips_blanks(session, db_engine):
    from app.services.stack_deployment import sync_storage_config

    await _seed_config(session, pubsub_topic_name="original", raw_bucket_name="old")
    outputs = _outputs(raw_bucket_name="new", results_bucket_name="", pubsub_topic_name="replaced")

    with patch("app.services.stack_deployment.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)):
        populated = await sync_storage_config(session)
    await session.commit()

    assert populated == {"raw_bucket_name": "new"}
    assert await _committed(db_engine, "raw_bucket_name") == "new"
    assert await _committed(db_engine, "pubsub_topic_name") == "original"


@pytest.mark.asyncio
async def test_storage_route_update_persists_pubsub(session, db_engine):
    from app.api.storage_deploy import _store_storage_outputs

    outputs = _outputs(raw_bucket_name="r", pubsub_subscription_name="sub", working_bucket_name="")
    with patch(
        "app.services.terraform_executor.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)
    ):
        await _store_storage_outputs(session)

    assert await _committed(db_engine, "raw_bucket_name") == "r"
    assert await _committed(db_engine, "pubsub_subscription_name") == "sub"
    assert await _committed(db_engine, "working_bucket_name") is None


# -----------------------------------------------------------------------
# Terminal outcomes
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_failure_records_orphans_restores_region_and_keeps_storage(session, db_engine):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session, storage_deployed="true", raw_bucket_name="bioaf-raw")

    events = await _deploy(
        session,
        user_id,
        {"compute": [_event("apply_error", message="quota exceeded")]},
        compute_region="europe-west1",
    )
    await session.commit()

    assert [e.event_type for e in events][-2:] == ["apply_error", "stack_error"]
    assert await _committed(db_engine, "gcp_region") == "us-central1"
    assert await _committed(db_engine, "compute_deployed") == "false"
    assert await _committed(db_engine, "raw_bucket_name") == "bioaf-raw"
    rows = (await session.execute(text("SELECT resource_type FROM orphaned_resources ORDER BY id"))).fetchall()
    assert [r[0] for r in rows] == ["gke_cluster", "service_account"]


@pytest.mark.asyncio
async def test_storage_failure_stops_before_compute(session):
    user_id = await _seed_user(session)
    await _seed_gcp_ready(session)

    events = await _deploy(session, user_id, {"storage": [_event("apply_error", message="denied")]})

    assert [e.event_type for e in events][-2:] == ["apply_error", "stack_error"]
    assert events[-1].message == "Stack deployment failed during storage module"
    assert not any(e.message == "Deploying compute infrastructure..." for e in events)


@pytest.mark.asyncio
async def test_teardown_clears_only_its_contract_keys(session, db_engine):
    from app.services.stack_deployment import teardown_stack

    user_id = await _seed_user(session)
    await _seed_gcp_ready(
        session,
        compute_deployed="true",
        gke_cluster_name="bioaf-x",
        gke_cluster_endpoint="https://1.2.3.4",
        gke_cluster_ca_cert="cert",
        notebook_runner_sa_email="runner@sa",
        aws_codebuild_project="keep",
    )

    async def destroy(_s, _u, _m):
        yield _event("apply_complete")

    with patch("app.services.stack_deployment._run_destroy", side_effect=destroy):
        events = [e async for e in teardown_stack(session, user_id=user_id)]
    await session.commit()

    assert events[-1].event_type == "stack_complete"
    assert await _committed(db_engine, "compute_deployed") == "false"
    assert await _committed(db_engine, "gke_cluster_name") == "null"
    assert await _committed(db_engine, "gke_cluster_endpoint") == "null"
    assert await _committed(db_engine, "gke_cluster_ca_cert") == "null"
    assert await _committed(db_engine, "notebook_runner_sa_email") == "runner@sa"
    assert await _committed(db_engine, "aws_codebuild_project") == "keep"


@pytest.mark.asyncio
async def test_teardown_failure_keeps_identity_and_records_orphans(session, db_engine):
    from app.services.stack_deployment import teardown_stack

    user_id = await _seed_user(session)
    await _seed_gcp_ready(session, compute_deployed="true", gke_cluster_name="bioaf-acme-abc123")

    async def destroy(_s, _u, _m):
        yield _event("apply_error", message="boom")

    with patch("app.services.stack_deployment._run_destroy", side_effect=destroy):
        events = [e async for e in teardown_stack(session, user_id=user_id)]
    await session.commit()

    assert events[-1].event_type == "stack_error"
    assert await _committed(db_engine, "gke_cluster_name") == "bioaf-acme-abc123"
    assert await _committed(db_engine, "compute_deployed") == "true"
    rows = (await session.execute(text("SELECT resource_type FROM orphaned_resources"))).fetchall()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_storage_destroy_empties_only_buckets(session):
    from app.services.stack_deployment import destroy_storage

    user_id = await _seed_user(session)
    await _seed_gcp_ready(
        session,
        storage_deployed="true",
        raw_bucket_name="bioaf-raw",
        literature_bucket_name="bioaf-lit",
        pubsub_topic_name="a-topic",
    )
    emptied = []

    async def empty(_session, bucket):
        emptied.append(bucket)
        return 0

    async def destroy(_s, _u, _m):
        yield _event("apply_complete")

    with (
        patch("app.services.stack_deployment._empty_gcs_bucket", side_effect=empty),
        patch("app.services.stack_deployment._run_destroy", side_effect=destroy),
    ):
        async for _ in destroy_storage(session, user_id=user_id):
            pass

    assert sorted(emptied) == ["bioaf-lit", "bioaf-raw"]


# -----------------------------------------------------------------------
# Component toggle
# -----------------------------------------------------------------------


async def _component(session, key, enabled=False, status="disabled"):
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES (:key, :enabled, :status, '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = EXCLUDED.enabled, status = EXCLUDED.status"
        ).bindparams(key=key, enabled=enabled, status=status)
    )
    await session.commit()


async def _toggle(client, token, key, **params):
    return await client.post(
        TOGGLE_URL.format(key=key),
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _state(db_engine, key):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        row = (
            await other.execute(
                text("SELECT enabled, status FROM component_states WHERE component_key = :k").bindparams(k=key)
            )
        ).fetchone()
    return tuple(row) if row else None


@pytest.mark.asyncio
async def test_toggle_refuses_cluster_with_exact_message(client, admin_token):
    response = await _toggle(client, admin_token, "kubernetes_cluster")

    assert response.status_code == 400
    assert response.json()["detail"] == "Cannot toggle kubernetes_cluster directly. Use stack deploy/teardown."


@pytest.mark.asyncio
async def test_toggle_unknown_component_is_404_with_exact_message(client, admin_token):
    response = await _toggle(client, admin_token, "no_such_thing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Component 'no_such_thing' not found"


@pytest.mark.asyncio
async def test_toggle_missing_dependency_exact_message_and_no_state_change(client, admin_token, session, db_engine):
    await _component(session, "nextflow", enabled=False)
    await _component(session, "qc_dashboard", enabled=False)

    response = await _toggle(client, admin_token, "qc_dashboard")

    assert response.status_code == 400
    assert response.json()["detail"] == "Dependency not met: nextflow must be enabled first"
    assert await _state(db_engine, "qc_dashboard") == (False, "disabled")


@pytest.mark.asyncio
async def test_toggle_requires_configure_permission(client, viewer_token, session):
    await _component(session, "meilisearch")

    response = await _toggle(client, viewer_token, "meilisearch")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_toggle_enable_disable_response_state_and_audit(client, admin_token, admin_user, session, db_engine):
    await _component(session, "meilisearch")

    enabled = await _toggle(client, admin_token, "meilisearch")
    assert enabled.json() == {"component_key": "meilisearch", "enabled": True, "status": "enabled"}
    assert await _state(db_engine, "meilisearch") == (True, "enabled")

    disabled = await _toggle(client, admin_token, "meilisearch")
    assert disabled.json() == {"component_key": "meilisearch", "enabled": False, "status": "disabled"}
    assert await _state(db_engine, "meilisearch") == (False, "disabled")

    audit = (await session.execute(AuditLog.__table__.select().order_by(AuditLog.id))).fetchall()
    rows = [(a.user_id, a.entity_type, a.action, a.details_json) for a in audit]
    assert [r[:3] for r in rows] == [
        (admin_user.id, "component", "enable"),
        (admin_user.id, "component", "disable"),
    ]
    assert rows[0][3] == {"component_key": "meilisearch", "component_name": "Meilisearch", "status": "enabled"}


@pytest.mark.asyncio
async def test_toggle_existing_image_does_not_rebuild(client, admin_token, session):
    await _component(session, "kubernetes_cluster", enabled=True, status="running")
    await _component(session, "rstudio")
    await _seed_config(session, bioaf_scrna_image="img:1", notebook_image_build_status="SUCCESS")

    with patch(NOTEBOOK_BUILD, new_callable=AsyncMock) as build:
        response = await _toggle(client, admin_token, "rstudio")

    assert response.json()["status"] == "enabled"
    build.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_force_rebuild_builds_over_existing_image(client, admin_token, session):
    await _component(session, "kubernetes_cluster", enabled=True, status="running")
    await _component(session, "jupyterhub")
    await _seed_config(session, bioaf_scrna_image="img:1", notebook_image_build_status="SUCCESS")

    with patch(NOTEBOOK_BUILD, new_callable=AsyncMock, return_value="b-1") as build:
        response = await _toggle(client, admin_token, "jupyterhub", force_rebuild="true")

    assert response.json()["status"] == "provisioning"
    build.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_cellxgene_build_failure_still_enables(client, admin_token, session, db_engine):
    await _component(session, "cellxgene")

    with patch(CELLXGENE_BUILD, new_callable=AsyncMock, side_effect=RuntimeError("no project")):
        response = await _toggle(client, admin_token, "cellxgene")

    assert response.json() == {"component_key": "cellxgene", "enabled": True, "status": "build_failed"}
    assert await _state(db_engine, "cellxgene") == (True, "build_failed")
