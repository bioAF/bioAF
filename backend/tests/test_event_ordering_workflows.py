"""08D: workflow announcements follow the commit of the state they describe.

The auto-run budget gate cancels waiting runs and disables auto-run configurations, and a validation
study that stops records its error stamp; both then announce it. Nothing may be announced before
that commit, and a rollback announces nothing. Interactive literature search already announces its
outcome after committing it; that is demonstrated here so a later change cannot quietly regress it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.budget_config import BudgetConfig
from app.models.cost_record import CostRecord
from app.models.experiment import Experiment
from app.models.experiment_auto_run import ExperimentAutoRun
from app.models.literature import LiteratureSearch
from app.models.pending_auto_run import PendingAutoRun
from app.models.sample import Sample
from app.models.validation_study import ValidationStudy
from app.services.auto_run_service import AutoRunService
from app.services.event_types import (
    AUTO_RUN_BUDGET_DISABLED,
    LITERATURE_SEARCH_COMPLETED,
    LITERATURE_SEARCH_FAILED,
    VALIDATION_STUDY_ERROR,
)
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService
from tests.support.committed_view import CommittedView

pytestmark = pytest.mark.asyncio


# --- auto-run budget gate -----------------------------------------------------------------------


async def _gate_outcome(s, payload):
    enabled = (
        await s.execute(
            select(func.count())
            .select_from(ExperimentAutoRun)
            .where(ExperimentAutoRun.organization_id == payload["org_id"], ExperimentAutoRun.enabled.is_(True))
        )
    ).scalar()
    waiting = (
        await s.execute(
            select(func.count())
            .select_from(PendingAutoRun)
            .where(PendingAutoRun.organization_id == payload["org_id"], PendingAutoRun.status == "waiting")
        )
    ).scalar()
    return enabled, waiting


@pytest_asyncio.fixture
async def gates():
    view = CommittedView([AUTO_RUN_BUDGET_DISABLED], read=_gate_outcome)
    yield view
    view.close()


@pytest_asyncio.fixture
async def over_budget(session, admin_user):
    """A due auto-run in an organization that has spent 95% of its monthly budget."""
    org_id = admin_user.organization_id
    experiment = Experiment(organization_id=org_id, name="Auto", owner_user_id=admin_user.id, status="fastq_uploaded")
    session.add(experiment)
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="A1")
    session.add(sample)
    config = ExperimentAutoRun(
        organization_id=org_id,
        experiment_id=experiment.id,
        pipeline_key="nf-core/scrnaseq",
        configured_by_user_id=admin_user.id,
    )
    session.add(config)
    await session.flush()
    now = datetime.now(timezone.utc)
    session.add(
        PendingAutoRun(
            organization_id=org_id,
            auto_run_config_id=config.id,
            experiment_id=experiment.id,
            sample_id=sample.id,
            sample_completed_at=now,
            scheduled_at=now - timedelta(minutes=1),
            status="waiting",
        )
    )
    session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal("100.00")))
    session.add(
        CostRecord(organization_id=org_id, record_date=now.date(), component="compute", cost_amount=Decimal("95.00"))
    )
    await session.commit()


async def test_the_budget_gate_is_announced_once_its_cancellations_commit(session, admin_user, over_budget, gates):
    assert await AutoRunService.process_pending_runs(session) == 1
    await gates.settle()
    assert gates.events == []

    await session.commit()
    await gates.settle()

    [(payload, (enabled, waiting))] = gates.seen(AUTO_RUN_BUDGET_DISABLED)
    assert (enabled, waiting) == (0, 0)
    assert payload["org_id"] == admin_user.organization_id
    assert payload["metadata"]["cancelled_count"] == 1


async def test_a_rolled_back_budget_gate_announces_nothing(session, over_budget, gates):
    await AutoRunService.process_pending_runs(session)
    await session.rollback()
    await gates.settle()

    assert gates.events == []


# --- validation study errors --------------------------------------------------------------------


async def _study_state(s, payload):
    study = await s.get(ValidationStudy, payload["entity_id"])
    return None if study is None else (study.state, "fetch_reap_after" in (study.evidence_json or {}))


@pytest_asyncio.fixture
async def study_errors():
    view = CommittedView([VALIDATION_STUDY_ERROR], read=_study_state)
    yield view
    view.close()


async def _running_study(session, admin_user) -> int:
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1234/x"
    )
    study.state = "running"
    await session.commit()
    return study.id


async def test_a_stopped_study_is_announced_once_its_error_and_deadline_commit(session, admin_user, study_errors):
    study_id = await _running_study(session, admin_user)

    await ValidationStudyService.transition(
        session, study_id, admin_user.organization_id, admin_user.id, "error", failure_reason="analysis run failed"
    )
    await study_errors.settle()
    assert study_errors.events == []

    await session.commit()
    await study_errors.settle()
    [(payload, committed)] = study_errors.seen(VALIDATION_STUDY_ERROR)
    assert committed == ("error", True)
    assert (payload["org_id"], payload["target_user_id"]) == (admin_user.organization_id, admin_user.id)
    assert "analysis run failed" in payload["message"]


async def test_a_rolled_back_stop_from_either_path_announces_nothing(session, admin_user, study_errors):
    first = await _running_study(session, admin_user)
    second = await _running_study(session, admin_user)

    await ValidationStudyService.transition(
        session, first, admin_user.organization_id, admin_user.id, "error", failure_reason="boom"
    )
    await ValidationDriverService._mark_error(session, second, "kubernetes went away")
    await session.rollback()
    await study_errors.settle()

    assert study_errors.events == []


# --- interactive literature search (already committed) ------------------------------------------


async def _search_status(s, payload):
    search = await s.get(LiteratureSearch, payload["search_id"])
    return None if search is None else search.status


async def test_a_search_outcome_is_announced_after_it_is_committed(session, admin_user):
    from app.services.bootstrap_literature import seed_literature_sources
    from app.services.literature import search_service, sources_config_service

    await seed_literature_sources(session, admin_user.organization_id)
    search = await search_service.create_search(
        session, org_id=admin_user.organization_id, user_id=admin_user.id, query="q", sources=["pubmed"]
    )
    await sources_config_service.update(
        session, org_id=admin_user.organization_id, source="pubmed", user_id=admin_user.id, enabled=False
    )
    await session.commit()
    view = CommittedView([LITERATURE_SEARCH_COMPLETED, LITERATURE_SEARCH_FAILED], read=_search_status)
    try:
        await search_service._run_search(search.id, admin_user.id, 5)
        await view.settle()
        [(payload, committed_status)] = view.seen(LITERATURE_SEARCH_FAILED)
    finally:
        view.close()

    assert committed_status == "failed" == payload["status"]
    assert payload["organization_id"] == admin_user.organization_id
