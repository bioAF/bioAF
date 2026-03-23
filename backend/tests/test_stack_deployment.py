"""Tests for the stack deployment service (Phase 19, tests 3-11).

3. test_deploy_stack_requires_gcp_configured
4. test_deploy_stack_requires_terraform_initialized
5. test_deploy_stack_requires_not_already_deployed
6. test_deploy_stack_deploys_storage_then_compute
7. test_deploy_stack_skips_storage_if_already_deployed
8. test_deploy_stack_stores_cluster_config_on_success
9. test_deploy_stack_preserves_storage_on_compute_failure
10. test_teardown_stack_requires_deployed
11. test_teardown_stack_clears_config
12. test_deploy_stack_creates_activity_feed_events
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text
from app.services.bootstrap_roles import seed_builtin_roles


async def _seed_org_and_user(session):
    """Create an org and user for tests that need audit logging, return (org_id, user_id)."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="StackTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="stack_test@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return org.id, user.id


async def _set_config(session, key: str, value: str):
    """Helper to set a platform_config key."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k=key, v=value)
    )
    await session.flush()


async def _get_config(session, key: str) -> str | None:
    """Helper to read a platform_config key."""
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))).fetchone()
    return row[0] if row else None


def _make_progress_event(event_type="apply_complete", message="done", **kwargs):
    """Create a mock TerraformProgressEvent."""
    from app.services.terraform_executor import TerraformProgressEvent

    return TerraformProgressEvent(event_type=event_type, message=message, **kwargs)


# -----------------------------------------------------------------------
# deploy_stack tests
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_requires_gcp_configured(session):
    """deploy_stack raises when gcp_credentials_configured is not true."""
    from app.services.stack_deployment import deploy_stack

    await _set_config(session, "gcp_credentials_configured", "false")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await session.commit()

    with pytest.raises(ValueError, match="GCP credentials"):
        async for _ in deploy_stack(session, "kubernetes", user_id=1):
            pass


@pytest.mark.asyncio
async def test_deploy_stack_requires_terraform_initialized(session):
    """deploy_stack raises when terraform_initialized is not true."""
    from app.services.stack_deployment import deploy_stack

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "false")
    await _set_config(session, "compute_deployed", "false")
    await session.commit()

    with pytest.raises(ValueError, match="[Tt]erraform.*initialized"):
        async for _ in deploy_stack(session, "kubernetes", user_id=1):
            pass


@pytest.mark.asyncio
async def test_deploy_stack_requires_not_already_deployed(session):
    """deploy_stack raises when compute_deployed is already true."""
    from app.services.stack_deployment import deploy_stack

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "true")
    await session.commit()

    with pytest.raises(ValueError, match="already deployed"):
        async for _ in deploy_stack(session, "kubernetes", user_id=1):
            pass


@pytest.mark.asyncio
async def test_deploy_stack_deploys_storage_then_compute(session):
    """deploy_stack runs storage module first, then compute module."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    modules_run = []

    async def mock_run_module(sess, uid, module_name):
        modules_run.append(module_name)
        yield _make_progress_event(
            "apply_complete",
            f"{module_name} done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-test"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            }
            if module_name == "compute"
            else {},
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    assert modules_run == ["storage", "compute"]
    assert any(e.event_type == "stack_complete" for e in events)


@pytest.mark.asyncio
async def test_deploy_stack_skips_storage_if_already_deployed(session):
    """deploy_stack only runs compute module when storage is already deployed."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await session.commit()

    modules_run = []

    async def mock_run_module(sess, uid, module_name):
        modules_run.append(module_name)
        yield _make_progress_event(
            "apply_complete",
            f"{module_name} done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-test"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            },
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    assert modules_run == ["compute"]


@pytest.mark.asyncio
async def test_deploy_stack_stores_cluster_config_on_success(session):
    """deploy_stack stores GKE config in platform_config after success."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event(
            "apply_complete",
            "done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-myorg"},
                    "cluster_endpoint": {"value": "https://10.0.0.1"},
                    "cluster_ca_cert": {"value": "dGVzdC1jZXJ0"},
                }
            },
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        async for _ in deploy_stack(session, "kubernetes", user_id=user_id):
            pass

    await session.commit()

    assert await _get_config(session, "compute_stack") == "kubernetes"
    assert await _get_config(session, "compute_deployed") == "true"
    assert await _get_config(session, "gke_cluster_name") == "bioaf-myorg"
    assert await _get_config(session, "gke_cluster_endpoint") == "https://10.0.0.1"
    assert await _get_config(session, "gke_cluster_ca_cert") == "dGVzdC1jZXJ0"


@pytest.mark.asyncio
async def test_deploy_stack_preserves_storage_on_compute_failure(session):
    """Storage remains deployed even if compute module fails."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        if module_name == "storage":
            yield _make_progress_event("apply_complete", "storage done")
        else:
            yield _make_progress_event("apply_error", "compute failed")

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    await session.commit()

    # Storage should still be marked as deployed (the hook ran for storage)
    assert await _get_config(session, "storage_deployed") == "true"
    assert await _get_config(session, "compute_deployed") == "false"
    assert any(e.event_type == "stack_error" for e in events)


# -----------------------------------------------------------------------
# teardown_stack tests
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_stack_requires_deployed(session):
    """teardown_stack raises when compute_deployed is false."""
    from app.services.stack_deployment import teardown_stack

    await _set_config(session, "compute_deployed", "false")
    await session.commit()

    with pytest.raises(ValueError, match="not deployed"):
        async for _ in teardown_stack(session, user_id=1):
            pass


@pytest.mark.asyncio
async def test_teardown_stack_clears_config(session):
    """teardown_stack clears GKE config and sets compute_deployed to false."""
    from app.services.stack_deployment import teardown_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "compute_stack", "kubernetes")
    await _set_config(session, "gke_cluster_name", "bioaf-myorg")
    await _set_config(session, "gke_cluster_endpoint", "https://10.0.0.1")
    await _set_config(session, "gke_cluster_ca_cert", "dGVzdC1jZXJ0")
    # Insert kubernetes_cluster component_state
    await session.execute(
        text("""
        INSERT INTO component_states (component_key, enabled, status, config_json)
        VALUES ('kubernetes_cluster', true, 'running', '{}')
        ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'running'
        """)
    )
    await session.commit()

    async def mock_run_destroy(sess, uid, module_name):
        yield _make_progress_event("apply_complete", "destroy done")

    with patch("app.services.stack_deployment._run_destroy", side_effect=mock_run_destroy):
        events = []
        async for event in teardown_stack(session, user_id=user_id):
            events.append(event)

    await session.commit()

    assert await _get_config(session, "compute_deployed") == "false"
    assert await _get_config(session, "gke_cluster_name") == "null"
    assert await _get_config(session, "gke_cluster_endpoint") == "null"
    assert await _get_config(session, "gke_cluster_ca_cert") == "null"

    # kubernetes_cluster component_state should be disabled
    row = (
        await session.execute(
            text("SELECT enabled, status FROM component_states WHERE component_key = 'kubernetes_cluster'")
        )
    ).fetchone()
    assert row is not None
    assert row[0] is False
    assert row[1] == "disabled"


# -----------------------------------------------------------------------
# Activity feed tests
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_creates_activity_feed_events(session):
    """deploy_stack writes activity feed events for storage and compute deploy."""
    from app.services.stack_deployment import deploy_stack

    org_id, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event(
            "apply_complete",
            f"{module_name} done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-test"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            }
            if module_name == "compute"
            else {},
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        async for _ in deploy_stack(session, "kubernetes", user_id=user_id, org_id=org_id):
            pass

    await session.commit()

    # Check activity feed has storage and compute events
    rows = (
        await session.execute(
            text(
                "SELECT event_type, summary FROM activity_feed WHERE organization_id = :org_id ORDER BY created_at"
            ).bindparams(org_id=org_id)
        )
    ).fetchall()

    event_types = [r[0] for r in rows]
    assert "infrastructure.storage_deployed" in event_types
    assert "infrastructure.compute_deployed" in event_types

    # Check audit log has entries
    audit_rows = (
        await session.execute(
            text("SELECT action FROM audit_log WHERE user_id = :uid AND entity_type = 'infrastructure'").bindparams(
                uid=user_id
            )
        )
    ).fetchall()
    actions = [r[0] for r in audit_rows]
    assert "deploy_storage" in actions
    assert "deploy_compute" in actions


# -----------------------------------------------------------------------
# Event remapping: apply_complete -> phase_complete
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_remaps_apply_complete_to_phase_complete(session):
    """Module-level apply_complete events are remapped to phase_complete so
    the frontend does not prematurely show 'Complete'."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event(
            "apply_complete",
            f"{module_name} done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-test"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            }
            if module_name == "compute"
            else {},
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    event_types = [e.event_type for e in events]
    # Raw apply_complete must NOT appear -- it should be remapped
    assert "apply_complete" not in event_types
    # phase_complete events for each module
    assert event_types.count("phase_complete") == 2
    # Final stack_complete marks the real end
    assert event_types[-1] == "stack_complete"


# -----------------------------------------------------------------------
# Teardown activity feed
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_stack_creates_activity_feed_event(session):
    """teardown_stack writes an activity feed event for the teardown."""
    from app.services.stack_deployment import teardown_stack

    org_id, user_id = await _seed_org_and_user(session)

    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "compute_stack", "kubernetes")
    await session.execute(
        text("""
        INSERT INTO component_states (component_key, enabled, status, config_json)
        VALUES ('kubernetes_cluster', true, 'running', '{}')
        ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'running'
        """)
    )
    await session.commit()

    async def mock_run_destroy(sess, uid, module_name):
        yield _make_progress_event("apply_complete", "destroy done")

    with patch("app.services.stack_deployment._run_destroy", side_effect=mock_run_destroy):
        async for _ in teardown_stack(session, user_id=user_id, org_id=org_id):
            pass

    await session.commit()

    rows = (
        await session.execute(
            text("SELECT event_type, summary FROM activity_feed WHERE organization_id = :org_id").bindparams(
                org_id=org_id
            )
        )
    ).fetchall()

    event_types = [r[0] for r in rows]
    assert "infrastructure.compute_teardown" in event_types

    # Check audit log
    audit_rows = (
        await session.execute(
            text("SELECT action FROM audit_log WHERE user_id = :uid AND entity_type = 'infrastructure'").bindparams(
                uid=user_id
            )
        )
    ).fetchall()
    assert "teardown_compute" in [r[0] for r in audit_rows]


# -----------------------------------------------------------------------
# stack_uid generation
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_generates_stack_uid(session):
    """deploy_stack generates and persists a stack_uid on first deploy."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event(
            "apply_complete",
            f"{module_name} done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-test"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            }
            if module_name == "compute"
            else {},
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        async for _ in deploy_stack(session, "kubernetes", user_id=user_id):
            pass

    await session.commit()

    uid = await _get_config(session, "stack_uid")
    assert uid is not None
    assert len(uid) == 6  # secrets.token_hex(3) -> 6 hex chars


@pytest.mark.asyncio
async def test_deploy_stack_reuses_existing_stack_uid(session):
    """deploy_stack does not overwrite an existing stack_uid."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "stack_uid", "abc123")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event(
            "apply_complete",
            f"{module_name} done",
            extra={
                "outputs": {
                    "cluster_name": {"value": "bioaf-test"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            },
        )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        async for _ in deploy_stack(session, "kubernetes", user_id=user_id):
            pass

    await session.commit()

    uid = await _get_config(session, "stack_uid")
    assert uid == "abc123"


# -----------------------------------------------------------------------
# Progress counter accumulation
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_accumulates_resource_counts(session):
    """stack_complete event carries accumulated resource counts from both phases."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        if module_name == "storage":
            yield _make_progress_event(
                "resource_complete",
                "Applied: bucket",
                resources_completed=1,
                resources_total=2,
            )
            yield _make_progress_event(
                "apply_complete",
                "storage done",
                resources_completed=2,
                resources_total=2,
            )
        else:
            yield _make_progress_event(
                "resource_complete",
                "Applied: cluster",
                resources_completed=1,
                resources_total=3,
            )
            yield _make_progress_event(
                "apply_complete",
                "compute done",
                resources_completed=3,
                resources_total=3,
                extra={
                    "outputs": {
                        "cluster_name": {"value": "bioaf-test"},
                        "cluster_endpoint": {"value": "https://1.2.3.4"},
                        "cluster_ca_cert": {"value": "Y2VydA=="},
                    }
                },
            )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    stack_complete = [e for e in events if e.event_type == "stack_complete"]
    assert len(stack_complete) == 1
    # 2 from storage + 3 from compute = 5 total
    assert stack_complete[0].resources_completed == 5
    assert stack_complete[0].resources_total == 5


# -----------------------------------------------------------------------
# Bucket names written to platform_config after storage deploy
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_stores_bucket_names_after_storage(session):
    """Storage apply_complete event writes all 5 bucket names to platform_config."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    storage_outputs = {
        "ingest_bucket_name": {"value": "bioaf-abc123-ingest"},
        "raw_bucket_name": {"value": "bioaf-abc123-raw"},
        "working_bucket_name": {"value": "bioaf-abc123-working"},
        "results_bucket_name": {"value": "bioaf-abc123-results"},
        "config_backups_bucket_name": {"value": "bioaf-abc123-config-backups"},
    }

    async def mock_run_module(sess, uid, module_name):
        if module_name == "storage":
            yield _make_progress_event(
                "apply_complete",
                "storage done",
                extra={"outputs": storage_outputs},
            )
        else:
            yield _make_progress_event(
                "apply_complete",
                "compute done",
                extra={
                    "outputs": {
                        "cluster_name": {"value": "bioaf-test"},
                        "cluster_endpoint": {"value": "https://1.2.3.4"},
                        "cluster_ca_cert": {"value": "Y2VydA=="},
                    }
                },
            )

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        async for _ in deploy_stack(session, "kubernetes", user_id=user_id):
            pass

    await session.commit()

    assert await _get_config(session, "ingest_bucket_name") == "bioaf-abc123-ingest"
    assert await _get_config(session, "raw_bucket_name") == "bioaf-abc123-raw"
    assert await _get_config(session, "working_bucket_name") == "bioaf-abc123-working"
    assert await _get_config(session, "results_bucket_name") == "bioaf-abc123-results"
    assert await _get_config(session, "config_backups_bucket_name") == "bioaf-abc123-config-backups"


# -----------------------------------------------------------------------
# destroy_storage tests
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destroy_storage_requires_compute_down(session):
    """destroy_storage raises when compute is still deployed."""
    from app.services.stack_deployment import destroy_storage

    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "storage_deployed", "true")
    await session.commit()

    with pytest.raises(ValueError, match="compute"):
        async for _ in destroy_storage(session, user_id=1):
            pass


@pytest.mark.asyncio
async def test_destroy_storage_requires_storage_deployed(session):
    """destroy_storage raises when storage is not deployed."""
    from app.services.stack_deployment import destroy_storage

    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()

    with pytest.raises(ValueError, match="not deployed"):
        async for _ in destroy_storage(session, user_id=1):
            pass


@pytest.mark.asyncio
async def test_destroy_storage_clears_config_and_resets_stack_uid(session):
    """destroy_storage clears all storage config keys and resets stack_uid."""
    from app.services.stack_deployment import destroy_storage

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "stack_uid", "abc123")
    await _set_config(session, "ingest_bucket_name", "bioaf-ingest-abc123")
    await _set_config(session, "raw_bucket_name", "bioaf-raw-abc123")
    await _set_config(session, "working_bucket_name", "bioaf-working-abc123")
    await _set_config(session, "results_bucket_name", "bioaf-results-abc123")
    await _set_config(session, "config_backups_bucket_name", "bioaf-config-backups-abc123")
    await session.commit()

    async def mock_run_destroy(sess, uid, module_name):
        yield _make_progress_event("apply_complete", "destroy done")

    with patch("app.services.stack_deployment._run_destroy", side_effect=mock_run_destroy):
        events = []
        async for event in destroy_storage(session, user_id=user_id):
            events.append(event)

    await session.commit()

    assert await _get_config(session, "storage_deployed") == "null"
    assert await _get_config(session, "stack_uid") == "null"
    assert await _get_config(session, "ingest_bucket_name") == "null"
    assert await _get_config(session, "raw_bucket_name") == "null"
    assert await _get_config(session, "working_bucket_name") == "null"
    assert await _get_config(session, "results_bucket_name") == "null"
    assert await _get_config(session, "config_backups_bucket_name") == "null"

    event_types = [e.event_type for e in events]
    assert "stack_complete" in event_types


@pytest.mark.asyncio
async def test_destroy_storage_yields_stack_error_on_tf_failure(session):
    """destroy_storage yields stack_error if terraform destroy fails."""
    from app.services.stack_deployment import destroy_storage

    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "stack_uid", "abc123")
    await session.commit()

    async def mock_run_destroy(sess, uid, module_name):
        yield _make_progress_event("apply_error", "destroy failed")

    with patch("app.services.stack_deployment._run_destroy", side_effect=mock_run_destroy):
        events = []
        async for event in destroy_storage(session, user_id=1):
            events.append(event)

    event_types = [e.event_type for e in events]
    assert "stack_error" in event_types
    # Config must NOT be cleared when destroy fails
    assert await _get_config(session, "storage_deployed") == "true"
    assert await _get_config(session, "stack_uid") == "abc123"


# -----------------------------------------------------------------------
# sync_compute_config tests
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_compute_config_writes_cluster_values(session):
    """sync_compute_config reads terraform outputs and writes to platform_config."""
    from unittest.mock import AsyncMock

    from app.services.stack_deployment import sync_compute_config

    mock_outputs = {
        "cluster_name": {"value": "bioaf-test-abc"},
        "cluster_endpoint": {"value": "10.0.0.1"},
        "cluster_ca_cert": {"value": "dGVzdC1jZXJ0"},
    }

    with patch(
        "app.services.stack_deployment.TerraformExecutor.read_module_outputs",
        new=AsyncMock(return_value=mock_outputs),
    ):
        populated = await sync_compute_config(session)

    await session.commit()

    assert populated["gke_cluster_name"] == "bioaf-test-abc"
    assert populated["gke_cluster_endpoint"] == "10.0.0.1"
    assert populated["gke_cluster_ca_cert"] == "dGVzdC1jZXJ0"
    assert await _get_config(session, "gke_cluster_endpoint") == "10.0.0.1"
    assert await _get_config(session, "gke_cluster_ca_cert") == "dGVzdC1jZXJ0"


@pytest.mark.asyncio
async def test_sync_compute_config_skips_empty_outputs(session):
    """sync_compute_config does not overwrite config with empty values."""
    from unittest.mock import AsyncMock

    from app.services.stack_deployment import sync_compute_config

    await _set_config(session, "gke_cluster_endpoint", "existing-value")
    await session.commit()

    # Outputs with empty cluster_endpoint
    mock_outputs = {
        "cluster_name": {"value": "bioaf-test"},
        "cluster_endpoint": {"value": ""},
        "cluster_ca_cert": {"value": "Y2VydA=="},
    }

    with patch(
        "app.services.stack_deployment.TerraformExecutor.read_module_outputs",
        new=AsyncMock(return_value=mock_outputs),
    ):
        populated = await sync_compute_config(session)

    await session.commit()

    assert "gke_cluster_endpoint" not in populated
    # Original value preserved
    assert await _get_config(session, "gke_cluster_endpoint") == "existing-value"


# -----------------------------------------------------------------------
# get_cluster_status uses SA credentials
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cluster_status_uses_sa_credentials(session):
    """get_cluster_status passes SA credentials to the GKE client."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.stack_deployment import get_cluster_status

    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "compute_stack", "kubernetes")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "gke_cluster_name", "bioaf-test")
    await _set_config(session, "gcp_project_id", "my-project")
    await _set_config(session, "gcp_zone", "us-central1-a")
    await _set_config(session, "gcp_credential_source", "service_account_key")
    await _set_config(session, "gcp_service_account_key", '{"type": "service_account"}')
    await session.commit()

    mock_creds = MagicMock()

    mock_pool = MagicMock()
    mock_pool.name = "bioaf-pipelines"
    mock_pool.config.machine_type = "n2-highmem-16"
    mock_pool.autoscaling.min_node_count = 0
    mock_pool.autoscaling.max_node_count = 10
    mock_pool.initial_node_count = 0
    mock_pool.config.spot = True
    mock_pool.status = 2

    mock_pool2 = MagicMock()
    mock_pool2.name = "bioaf-interactive"
    mock_pool2.config.machine_type = "n2-standard-4"
    mock_pool2.autoscaling.min_node_count = 0
    mock_pool2.autoscaling.max_node_count = 3
    mock_pool2.initial_node_count = 0
    mock_pool2.config.spot = False
    mock_pool2.status = 2

    mock_cluster = MagicMock()
    mock_cluster.name = "bioaf-test"
    mock_cluster.status = 2
    mock_cluster.current_node_count = 0
    mock_cluster.node_pools = [mock_pool, mock_pool2]

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = mock_cluster

    with (
        patch(
            "app.services.stack_deployment._get_gke_credentials",
            new=AsyncMock(return_value=mock_creds),
        ),
        patch(
            "app.services.stack_deployment._get_gke_client",
            return_value=mock_client,
        ) as mock_get_client,
    ):
        result = await get_cluster_status(session)

    mock_get_client.assert_called_once_with(mock_creds)
    assert result.compute_deployed is True
    assert result.cluster is not None
    assert result.cluster.cluster_name == "bioaf-test"


# -----------------------------------------------------------------------
# Orphaned resource logging tests
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_stack_logs_orphan_on_compute_failure(session):
    """When compute fails, the expected cluster is logged as an orphaned resource."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "org_slug", "demo")
    await _set_config(session, "stack_uid", "abc123")
    await _set_config(session, "gcp_project_id", "test-project")
    await _set_config(session, "gcp_zone", "us-central1-a")
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event("apply_error", "compute failed")

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    assert any(e.event_type == "stack_error" for e in events)

    # Verify orphaned resource was logged
    row = (
        await session.execute(text("SELECT resource_type, resource_name, stack_uid FROM orphaned_resources LIMIT 1"))
    ).fetchone()
    assert row is not None
    assert row[0] == "gke_cluster"
    assert row[1] == "bioaf-demo-abc123"
    assert row[2] == "abc123"


@pytest.mark.asyncio
async def test_deploy_stack_reseeds_uid_when_orphans_exist(session):
    """If the current stack_uid has orphaned resources, deploy_stack generates a new one."""
    from app.services.stack_deployment import deploy_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "org_slug", "demo")
    await _set_config(session, "stack_uid", "old123")
    await _set_config(session, "gcp_project_id", "test-project")
    await _set_config(session, "gcp_zone", "us-central1-a")
    await session.commit()

    # Seed an orphaned resource for old UID
    from app.services.orphaned_resource_service import OrphanedResourceService

    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-old123",
        gcp_project_id="test-project",
        stack_uid="old123",
    )
    await session.flush()
    await session.commit()

    async def mock_run_module(sess, uid, module_name):
        yield _make_progress_event("apply_complete", "done")

    with patch("app.services.stack_deployment._run_module", side_effect=mock_run_module):
        events = []
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)

    new_uid = await _get_config(session, "stack_uid")
    assert new_uid != "old123"
    assert len(new_uid) == 6  # secrets.token_hex(3) produces 6 hex chars


@pytest.mark.asyncio
async def test_teardown_stack_logs_orphan_on_failure(session):
    """When teardown fails, the cluster is logged as orphaned."""
    from app.services.stack_deployment import teardown_stack

    _, user_id = await _seed_org_and_user(session)

    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "compute_stack", "kubernetes")
    await _set_config(session, "gke_cluster_name", "bioaf-demo-abc123")
    await _set_config(session, "org_slug", "demo")
    await _set_config(session, "stack_uid", "abc123")
    await _set_config(session, "gcp_project_id", "test-project")
    await _set_config(session, "gcp_zone", "us-central1-a")
    await session.commit()

    async def mock_destroy(sess, uid, module_name):
        yield _make_progress_event("apply_error", "destroy failed")

    with patch("app.services.stack_deployment._run_destroy", side_effect=mock_destroy):
        events = []
        async for event in teardown_stack(session, user_id=user_id):
            events.append(event)

    assert any(e.event_type == "stack_error" for e in events)

    row = (
        await session.execute(text("SELECT resource_type, resource_name FROM orphaned_resources LIMIT 1"))
    ).fetchone()
    assert row is not None
    assert row[0] == "gke_cluster"
    assert row[1] == "bioaf-demo-abc123"
