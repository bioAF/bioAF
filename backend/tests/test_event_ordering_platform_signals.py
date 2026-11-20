"""08C: platform and resource announcements follow the commit of what they describe.

Environment build completion, reference deprecation, budget thresholds, and quota denials announce
state their transaction writes (or, for budgets, spend that the same transaction synced). Nothing is
announced before that commit, and a rollback announces nothing. A quota denial that refuses a launch
keeps its audit and its warning even though the launch rolls back.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.exceptions import ConflictError
from app.models.audit_log import AuditLog
from app.models.budget_config import BudgetConfig
from app.models.cost_record import CostRecord
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.reference_dataset import ReferenceDataset
from app.models.template_notebook import TemplateNotebook
from app.schemas.reference_dataset import ReferenceDeprecateRequest
from app.services.environment_build_service import EnvironmentBuildService
from app.services.event_types import (
    BUDGET_THRESHOLD_50,
    BUDGET_THRESHOLD_80,
    BUDGET_THRESHOLD_100,
    ENVIRONMENT_BUILD_COMPLETED,
    QUOTA_WARNING,
    REFERENCE_DEPRECATED,
)
from app.services.quota_service import QuotaService
from app.services.reference_data_service import ReferenceDataService
from tests.support.committed_view import CommittedView

pytestmark = pytest.mark.asyncio


# --- environment builds -------------------------------------------------------------------------


async def _version_status(s, payload):
    version = await s.get(EnvironmentVersion, payload["environment_version_id"])
    return None if version is None else version.status


@pytest_asyncio.fixture
async def builds():
    view = CommittedView([ENVIRONMENT_BUILD_COMPLETED], read=_version_status)
    yield view
    view.close()


@pytest_asyncio.fixture
async def building(session, admin_user):
    env = Environment(
        name="Pipeline Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="building",
        build_id="build-1",
        definition_format="conda",
        definition_content="name: p\ndependencies: [python=3.11]\n",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    for key, value in (("gcp_project_id", "test-project"), ("gcp_region", "us-central1")):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()
    return env, version


async def test_a_build_completion_reaches_subscribers_only_once_ready_is_committed(
    session, admin_user, building, builds
):
    env, version = building
    with patch("app.services.notebook_image_service.check_build_status", return_value="SUCCESS"):
        assert await EnvironmentBuildService.poll_in_progress_builds(session) == 1
    await builds.settle()
    assert builds.events == []

    await session.commit()
    await builds.settle()

    [(payload, committed_status)] = builds.seen(ENVIRONMENT_BUILD_COMPLETED)
    assert committed_status == "ready"
    assert payload == {
        "environment_id": env.id,
        "environment_version_id": version.id,
        "environment_type": "pipeline",
        "organization_id": admin_user.organization_id,
    }


async def test_a_rolled_back_build_poll_announces_nothing(session, building, builds):
    with patch("app.services.notebook_image_service.check_build_status", return_value="SUCCESS"):
        await EnvironmentBuildService.poll_in_progress_builds(session)
    await session.rollback()
    await builds.settle()

    assert builds.events == []


# --- reference deprecation ----------------------------------------------------------------------


async def _reference_status(s, payload):
    ref = await s.get(ReferenceDataset, payload["entity_id"])
    return None if ref is None else ref.status


@pytest_asyncio.fixture
async def references():
    view = CommittedView([REFERENCE_DEPRECATED], read=_reference_status)
    yield view
    view.close()


async def _reference(session, admin_user, scope: str, status: str = "active") -> ReferenceDataset:
    ref = ReferenceDataset(
        organization_id=admin_user.organization_id,
        name="GRCh38",
        category="genome",
        scope=scope,
        version=f"v-{scope}",
        gcs_prefix="genomes/GRCh38/",
        status=status,
    )
    session.add(ref)
    await session.commit()
    return ref


async def test_an_internal_deprecation_is_announced_once_committed(session, admin_user, references):
    ref = await _reference(session, admin_user, "internal")
    await ReferenceDataService.deprecate_reference(
        session, ref.id, admin_user.organization_id, admin_user.id, ReferenceDeprecateRequest(deprecation_note="v2")
    )
    await references.settle()
    assert references.events == []

    await session.commit()
    await references.settle()
    [(payload, committed_status)] = references.seen(REFERENCE_DEPRECATED)
    assert committed_status == "deprecated"
    assert (payload["org_id"], payload["entity_id"]) == (admin_user.organization_id, ref.id)


async def test_a_rolled_back_deprecation_or_approval_announces_nothing(session, admin_user, references):
    internal = await _reference(session, admin_user, "internal")
    pending = await _reference(session, admin_user, "public", status="pending_approval")
    org = admin_user.organization_id

    await ReferenceDataService.deprecate_reference(
        session, internal.id, org, admin_user.id, ReferenceDeprecateRequest(deprecation_note="v2")
    )
    await ReferenceDataService.approve_deprecation(session, pending.id, org, admin_user.id)
    await references.settle()
    await session.rollback()
    await references.settle()

    assert references.events == []


async def test_an_approved_deprecation_is_announced_once_committed(session, admin_user, references):
    ref = await _reference(session, admin_user, "public", status="pending_approval")
    await ReferenceDataService.approve_deprecation(session, ref.id, admin_user.organization_id, admin_user.id)
    await session.commit()
    await references.settle()

    [(payload, committed_status)] = references.seen(REFERENCE_DEPRECATED)
    assert committed_status == "deprecated" and payload["entity_id"] == ref.id


# --- budget thresholds --------------------------------------------------------------------------

BUDGETS = [BUDGET_THRESHOLD_50, BUDGET_THRESHOLD_80, BUDGET_THRESHOLD_100]


async def _spend(s, payload):
    return (
        await s.execute(
            text("SELECT coalesce(sum(cost_amount), 0) FROM cost_records WHERE organization_id = :o"),
            {"o": payload["org_id"]},
        )
    ).scalar()


@pytest_asyncio.fixture
async def budgets():
    view = CommittedView(BUDGETS, read=_spend)
    yield view
    view.close()


async def _budget(session, admin_user, amount: str) -> None:
    session.add(BudgetConfig(organization_id=admin_user.organization_id, monthly_budget=Decimal(amount)))
    await session.commit()


def _synced(session, admin_user, amount: str) -> None:
    """Cost rows the billing sync has written in the same, not yet committed, transaction."""
    session.add(
        CostRecord(
            organization_id=admin_user.organization_id,
            record_date=datetime.now(timezone.utc).date(),
            component="compute",
            cost_amount=Decimal(amount),
        )
    )


async def test_a_threshold_over_synced_spend_is_announced_once_that_spend_commits(session, admin_user, budgets):
    from app.services.cost_service import CostService

    await _budget(session, admin_user, "100.00")
    _synced(session, admin_user, "85.00")

    await CostService.check_budget_thresholds(session, admin_user.organization_id)
    await budgets.settle()
    assert budgets.events == []

    await session.commit()
    await budgets.settle()
    [(payload, committed_spend)] = budgets.seen(BUDGET_THRESHOLD_80)
    assert committed_spend == Decimal("85.00")
    assert payload["org_id"] == admin_user.organization_id


async def test_a_threshold_over_a_rolled_back_sync_announces_nothing(session, admin_user, budgets):
    from app.services.cost_service import CostService

    await _budget(session, admin_user, "100.00")
    _synced(session, admin_user, "120.00")

    await CostService.check_budget_thresholds(session, admin_user.organization_id)
    await session.rollback()
    await budgets.settle()

    assert budgets.events == []


# --- quota denials ------------------------------------------------------------------------------


async def _denial_audits(s, payload):
    rows = (
        await s.execute(
            select(AuditLog.entity_id).where(
                AuditLog.action == "quota_exceeded", AuditLog.user_id == payload["user_id"]
            )
        )
    ).all()
    return len(rows)


@pytest_asyncio.fixture
async def quota_warnings():
    view = CommittedView([QUOTA_WARNING], read=_denial_audits)
    yield view
    view.close()


async def _exhausted(session, user) -> None:
    await QuotaService.set_quota(session, user_id=user.id, admin_user_id=user.id, org_id=user.organization_id, limit=1)
    await QuotaService.update_usage(session, user.id, 1.0)
    await session.commit()


async def test_a_joined_denial_is_announced_with_its_audit_when_the_caller_commits(session, admin_user, quota_warnings):
    await _exhausted(session, admin_user)

    allowed, _ = await QuotaService.check_quota(session, admin_user.id, 1.0)
    assert allowed is False
    await quota_warnings.settle()
    assert quota_warnings.events == []

    await session.commit()
    await quota_warnings.settle()
    [(payload, audits_at_delivery)] = quota_warnings.seen(QUOTA_WARNING)
    assert audits_at_delivery == 1
    assert (payload["org_id"], payload["target_user_id"]) == (admin_user.organization_id, admin_user.id)


async def test_a_joined_denial_rolled_back_by_its_caller_announces_nothing(session, admin_user, quota_warnings):
    await _exhausted(session, admin_user)

    await QuotaService.check_quota(session, admin_user.id, 1.0)
    await session.rollback()
    await quota_warnings.settle()

    assert quota_warnings.events == []


async def test_a_refused_notebook_launch_keeps_its_denial_audit_and_warning(session, admin_user, quota_warnings):
    from app.services.notebook_service import NotebookService

    await _exhausted(session, admin_user)

    with pytest.raises(ConflictError):
        await NotebookService.launch_session(session, admin_user.id, admin_user.organization_id, "jupyter", "small")
    await session.rollback()
    await quota_warnings.settle()

    [(payload, audits_at_delivery)] = quota_warnings.seen(QUOTA_WARNING)
    assert audits_at_delivery == 1
    assert payload["title"] == "Compute quota exceeded"


async def test_a_refused_headless_execution_keeps_its_denial_audit_and_warning(session, admin_user, quota_warnings):
    from app.services.notebook_execution_service import NotebookExecutionService

    template = TemplateNotebook(
        organization_id=admin_user.organization_id, name="QC", notebook_path="qc.ipynb", is_builtin=True
    )
    session.add(template)
    await session.commit()
    await _exhausted(session, admin_user)

    with pytest.raises(ConflictError):
        await NotebookExecutionService.execute_template(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, template_id=template.id
        )
    await session.rollback()
    await quota_warnings.settle()

    [(_payload, audits_at_delivery)] = quota_warnings.seen(QUOTA_WARNING)
    assert audits_at_delivery == 1
