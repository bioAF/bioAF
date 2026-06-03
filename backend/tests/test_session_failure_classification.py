"""Tests for the failure-reason classifier helpers and the launch-path wiring.

Two pure-function classifiers turn raw GCP/K8s errors into the small enum the
UI consumes: resource_exhausted, image_pull_failed, oom_killed,
quota_exceeded, unknown.

Plus: launch paths now record requested_disk_gb and (on failure) failure_reason
+ failure_message, so even before adapters get smarter the service catch
blocks already classify the common cases.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.auth_service import AuthService


# ----- classify_pod_failure -----


def test_classify_pod_failure_resource_exhausted_from_failed_scaleup():
    from app.adapters.failure_classification import classify_pod_failure

    events = [
        {"reason": "FailedScaleUp", "message": "GCE out of resources. Pod is at risk of not being scheduled."},
    ]
    reason, message = classify_pod_failure(events)
    assert reason == "resource_exhausted"
    assert "GCE out of resources" in message


def test_classify_pod_failure_resource_exhausted_from_zone_pool_exhausted():
    from app.adapters.failure_classification import classify_pod_failure

    events = [
        {"reason": "NotTriggerScaleUp", "message": "1 in backoff after failed scale-up, ZONE_RESOURCE_POOL_EXHAUSTED"},
    ]
    reason, _ = classify_pod_failure(events)
    assert reason == "resource_exhausted"


def test_classify_pod_failure_image_pull():
    from app.adapters.failure_classification import classify_pod_failure

    events = [
        {"reason": "ErrImagePull", "message": "rpc error: manifest unknown"},
    ]
    reason, _ = classify_pod_failure(events)
    assert reason == "image_pull_failed"


def test_classify_pod_failure_oom():
    from app.adapters.failure_classification import classify_pod_failure

    events = [
        {"reason": "OOMKilled", "message": "Container was killed due to out of memory"},
    ]
    reason, _ = classify_pod_failure(events)
    assert reason == "oom_killed"


def test_classify_pod_failure_unknown_returns_message_when_provided():
    from app.adapters.failure_classification import classify_pod_failure

    events = [{"reason": "FailedScheduling", "message": "no nodes are available"}]
    reason, message = classify_pod_failure(events)
    # Generic scheduling failure that doesn't match a known mode falls through
    # to "unknown" but we still want the most-informative message in the modal.
    assert reason == "unknown"
    assert "no nodes are available" in message


def test_classify_pod_failure_empty_events():
    from app.adapters.failure_classification import classify_pod_failure

    reason, message = classify_pod_failure([])
    assert reason == "unknown"
    assert message  # non-empty placeholder text


def test_classify_pod_failure_prefers_resource_exhausted_over_unknown():
    """When multiple events exist, the resource_exhausted signal must win even
    if it isn't the most recent event in the list -- the user cares most about
    knowing the cluster is out of capacity."""
    from app.adapters.failure_classification import classify_pod_failure

    events = [
        {"reason": "FailedScheduling", "message": "no nodes are available"},
        {"reason": "FailedScaleUp", "message": "GCE out of resources"},
        {"reason": "FailedScheduling", "message": "no nodes are available"},
    ]
    reason, _ = classify_pod_failure(events)
    assert reason == "resource_exhausted"


# ----- classify_gce_vm_failure -----


def test_classify_gce_vm_failure_resource_exhausted():
    from app.adapters.failure_classification import classify_gce_vm_failure

    reason, message = classify_gce_vm_failure(
        "Instance creation failed: ZONE_RESOURCE_POOL_EXHAUSTED. The zone us-central1-a does not have enough resources."
    )
    assert reason == "resource_exhausted"
    assert "ZONE_RESOURCE_POOL_EXHAUSTED" in message or "does not have enough resources" in message


def test_classify_gce_vm_failure_resource_exhausted_from_service_layer_phrasing():
    from app.adapters.failure_classification import classify_gce_vm_failure

    reason, _ = classify_gce_vm_failure(
        "GCP resources unavailable: no e2-standard-8 capacity in any us-central1 zone. Try again later."
    )
    assert reason == "resource_exhausted"


def test_classify_gce_vm_failure_quota_exceeded():
    from app.adapters.failure_classification import classify_gce_vm_failure

    reason, _ = classify_gce_vm_failure("QUOTA_EXCEEDED: Quota 'E2_CPUS' exceeded. Limit: 24.0 in region us-central1.")
    assert reason == "quota_exceeded"


def test_classify_gce_vm_failure_unknown_passthrough():
    from app.adapters.failure_classification import classify_gce_vm_failure

    reason, message = classify_gce_vm_failure("Something else went wrong")
    assert reason == "unknown"
    assert "Something else went wrong" in message


def test_classify_gce_vm_failure_none_message():
    from app.adapters.failure_classification import classify_gce_vm_failure

    reason, message = classify_gce_vm_failure(None)
    assert reason == "unknown"
    assert message  # placeholder


# ----- launch wires requested_disk_gb + failure_reason via service catch -----


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    user = User(
        email="failclass@test.com",
        password_hash=AuthService.hash_password("pass"),
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def admin_test_token(admin_user) -> str:
    return AuthService.create_token(
        admin_user.id,
        admin_user.email,
        admin_user.role_id,
        admin_user.organization_id,
        role_name="admin",
    )


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest_asyncio.fixture
async def session_credentials(session, comp_bio_user):
    """Seed session credentials so work-node launch passes its precondition."""
    from app.models.session_credential import SessionCredential

    cred = SessionCredential(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        username="bioafuser",
        password_hash="$2b$12$dummyhashfortesting",
    )
    session.add(cred)
    await session.flush()
    await session.commit()
    return cred


async def _seed_environment_version(session, *, org_id, env_type="work_node", status="ready"):
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    # created_by_user_id is required; pull from any existing user in the org
    from sqlalchemy import select as sa_select
    from app.models.user import User

    owner = (
        await session.execute(sa_select(User).where(User.organization_id == org_id).limit(1))
    ).scalar_one()
    env = Environment(
        organization_id=org_id,
        name=f"failclass-env-{env_type}",
        environment_type=env_type,
        created_by_user_id=owner.id,
    )
    session.add(env)
    await session.flush()

    ev = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        build_number=1,
        definition_format="conda",
        definition_content="name: bioaf\n",
        status=status,
        image_uri="us-docker.pkg.dev/proj/repo/img:1",
        created_by_user_id=owner.id,
    )
    session.add(ev)
    await session.flush()
    return ev


@pytest.mark.asyncio
async def test_work_node_launch_sets_requested_disk_gb_from_config(
    client, session, comp_bio_user, comp_bio_token, session_credentials
):
    """Configured work_node_boot_disk_gb must end up on requested_disk_gb."""
    from app.models.project import Project

    org_id = comp_bio_user.organization_id
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('compute_deployed', 'true'), "
            "('work_node_boot_disk_gb', '250'), "
            "('notebook_runner_sa_email', 'sa@example.iam.gserviceaccount.com') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    project = Project(organization_id=org_id, name="failclass-proj", status="active", owner_user_id=comp_bio_user.id)
    session.add(project)
    await session.flush()
    ev = await _seed_environment_version(session, org_id=org_id)
    await session.commit()

    resp = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": project.id,
            "environment_version_id": ev.id,
            "machine_type": "e2-standard-8",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requested_disk_gb"] == 250


@pytest.mark.asyncio
async def test_notebook_launch_sets_requested_disk_gb_default(
    client, session, comp_bio_user, comp_bio_token
):
    """Notebook launch must record requested_disk_gb so the detail modal can show it.

    Notebook pods land on the bioaf-interactive GKE pool, whose boot disk is
    100 GB (terraform default). We surface that value directly on the session
    row rather than asking the frontend to look it up out-of-band.
    """
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('compute_deployed', 'true'), "
            "('bioaf_scrna_image', 'us-central1-docker.pkg.dev/proj/repo/img:1') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    resp = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code in (200, 400), resp.text  # 400 in tests where adapter rejects launch
    # Whether the launch succeeded or the adapter raised, the row must already
    # carry requested_disk_gb (set before the adapter call).
    from app.models.notebook_session import NotebookSession
    from sqlalchemy import select

    result = await session.execute(
        select(NotebookSession).where(NotebookSession.user_id == comp_bio_user.id).order_by(NotebookSession.id.desc())
    )
    ns = result.scalars().first()
    assert ns is not None, "notebook session row must exist even if launch failed"
    assert ns.requested_disk_gb == 100


@pytest.mark.asyncio
async def test_work_node_launch_records_resource_exhausted_when_adapter_says_so(
    client, session, comp_bio_user, comp_bio_token, session_credentials
):
    """When the GCE adapter raises 'GCP resources unavailable', the service must
    classify failure_reason as resource_exhausted and surface the message."""
    from app.models.project import Project

    org_id = comp_bio_user.organization_id
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('compute_deployed', 'true'), "
            "('notebook_runner_sa_email', 'sa@example.iam.gserviceaccount.com') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    project = Project(organization_id=org_id, name="failclass-proj-rx", status="active", owner_user_id=comp_bio_user.id)
    session.add(project)
    await session.flush()
    ev = await _seed_environment_version(session, org_id=org_id)
    await session.commit()

    fake_adapter = MagicMock()
    fake_adapter.launch_vm = AsyncMock(
        side_effect=ValueError(
            "GCP resources unavailable: no e2-standard-8 capacity in any us-central1 zone."
        )
    )

    with patch("app.services.work_node_service.get_work_node_adapter", return_value=fake_adapter):
        resp = await client.post(
            "/api/v1/work-nodes/sessions",
            json={
                "project_id": project.id,
                "environment_version_id": ev.id,
                "machine_type": "e2-standard-8",
            },
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "resource_exhausted"
    assert body["failure_message"] is not None
    assert "GCP resources unavailable" in body["failure_message"]
