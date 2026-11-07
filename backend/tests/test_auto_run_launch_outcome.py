"""What the auto-run launcher records and announces for each launch it attempts.

A pending auto-run is marked ``launched`` only when the compute provider accepted the job. A refused
submission still produces a committed failed run (the launcher links it), but the pending entry is
``cancelled`` with reason ``launch_failed``, and AUTO_RUN_LAUNCHED is not announced. A successful
launch is announced only after the launcher's transaction commits.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.compute.kubernetes import _job_submit_result_from_dict
from app.models.experiment import Experiment
from app.models.experiment_auto_run import ExperimentAutoRun
from app.models.file import File
from app.models.pending_auto_run import PendingAutoRun
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample, sample_files
from app.services.auto_run_service import AutoRunService
from app.services.event_bus import event_bus
from app.services.event_types import AUTO_RUN_LAUNCHED
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.post_commit_events import wait_for_deferred_events


@pytest.fixture(autouse=True)
def launched_events():
    event_bus.clear()
    seen: list[dict] = []

    async def record(payload):
        seen.append(payload)

    event_bus.subscribe(AUTO_RUN_LAUNCHED, record)
    yield seen
    event_bus.clear()


@pytest_asyncio.fixture
async def due_pending_run(session, admin_user) -> int:
    org_id = admin_user.organization_id
    await PipelineCatalogService.initialize_builtin_pipelines(session, org_id)
    experiment = Experiment(
        organization_id=org_id, name="Auto Exp", owner_user_id=admin_user.id, status="fastq_uploaded"
    )
    session.add(experiment)
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="A1", organism="Homo sapiens", tissue_type="PBMC")
    session.add(sample)
    await session.flush()
    for read in ("R1", "R2"):
        f = File(
            organization_id=org_id,
            experiment_id=experiment.id,
            gcs_uri=f"gs://bucket/A1_{read}_001.fastq.gz",
            filename=f"A1_{read}_001.fastq.gz",
            file_type="fastq",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    config = ExperimentAutoRun(
        organization_id=org_id,
        experiment_id=experiment.id,
        pipeline_key="nf-core/scrnaseq",
        parameters_json={"aligner": "cellranger"},
        configured_by_user_id=admin_user.id,
    )
    session.add(config)
    await session.flush()
    now = datetime.now(timezone.utc)
    pending = PendingAutoRun(
        organization_id=org_id,
        auto_run_config_id=config.id,
        experiment_id=experiment.id,
        sample_id=sample.id,
        sample_completed_at=now,
        scheduled_at=now - timedelta(minutes=1),
        status="waiting",
    )
    session.add(pending)
    await session.commit()
    return pending.id


def _adapter(*, accept: bool):
    adapter = MagicMock()
    if accept:
        adapter.submit_job = AsyncMock(
            return_value=_job_submit_result_from_dict(
                {"job_id": "bioaf-auto-1", "namespace": "bioaf-pipelines", "status": "queued"}
            )
        )
    else:
        adapter.submit_job = AsyncMock(side_effect=RuntimeError("admission webhook denied the request"))
    return adapter


@pytest.mark.asyncio
async def test_a_refused_auto_run_is_not_marked_launched_and_is_not_announced(
    session, due_pending_run, launched_events
):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=False)):
        processed = await AutoRunService.process_pending_runs(session)
    await session.commit()
    await wait_for_deferred_events()

    assert processed == 1
    pending = await session.get(PendingAutoRun, due_pending_run)
    assert pending.status == "cancelled"
    assert pending.cancelled_reason == "launch_failed"
    run = await session.get(PipelineRun, pending.pipeline_run_id)
    assert run.status == "failed"
    assert launched_events == []


@pytest.mark.asyncio
async def test_an_accepted_auto_run_is_announced_only_after_commit(session, due_pending_run, launched_events):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=True)):
        await AutoRunService.process_pending_runs(session)
    await wait_for_deferred_events()
    assert launched_events == []

    await session.commit()
    await wait_for_deferred_events()

    pending = (await session.execute(select(PendingAutoRun))).scalar_one()
    assert pending.status == "launched"
    assert [e["entity_id"] for e in launched_events] == [pending.pipeline_run_id]


@pytest.mark.asyncio
async def test_an_auto_run_batch_that_rolls_back_announces_nothing(session, due_pending_run, launched_events):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=True)):
        await AutoRunService.process_pending_runs(session)
    await session.rollback()
    await wait_for_deferred_events()

    assert launched_events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("in_month_spend, gated", [("85.00", False), ("95.00", True)])
async def test_the_budget_gate_counts_only_the_utc_usage_month(
    session, admin_user, due_pending_run, billing_clock, in_month_spend, gated
):
    """The 90% gate reads the summary's UTC usage month: neighbouring months do not count.

    20:00 UTC on October 31 is already November 1 on a Tokyo host.
    """
    from datetime import date
    from decimal import Decimal

    from app.models.budget_config import BudgetConfig
    from app.models.cost_record import CostRecord

    billing_clock(datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc), "Asia/Tokyo")
    org_id = admin_user.organization_id
    session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal("100.00")))
    for record_date, amount in [
        (date(2026, 9, 30), "500.00"),
        (date(2026, 10, 31), in_month_spend),
        (date(2026, 11, 1), "500.00"),
    ]:
        session.add(
            CostRecord(
                organization_id=org_id, record_date=record_date, component="compute", cost_amount=Decimal(amount)
            )
        )
    await session.commit()

    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=False)):
        await AutoRunService.process_pending_runs(session)
    await session.commit()

    pending = await session.get(PendingAutoRun, due_pending_run)
    assert (pending.cancelled_reason == "budget_limit") is gated


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, gated",
    [
        # One invoice slice ($20 of a $50 usage day) would have read 36% of $55.
        ("understated", True),
        # A stale $50 node estimate surviving beside $45 of actuals would have read 95%.
        ("overstated", False),
    ],
)
async def test_the_budget_gate_follows_corrected_provider_imports(
    session, admin_user, due_pending_run, billing_clock, monkeypatch, case, gated
):
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from sqlalchemy import text

    from app.models.budget_config import BudgetConfig
    from app.models.cost_record import CostRecord
    from app.services import cost_service
    from app.services.cost_service import CostService
    from tests.support.billing_export_table import BillingExportTable, ExportRow

    utc = timezone.utc
    billing_clock(datetime(2026, 11, 6, 12, 0, tzinfo=utc))
    org_id = admin_user.organization_id
    for key, value in {
        "billing_export_configured": "true",
        "billing_export_dataset": "billing_export",
        "billing_export_table": "gcp_billing_export_v1_ABC123",
        "gcp_project_id": "test-project",
    }.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )

    async def no_cluster_cost():
        return SimpleNamespace(node_pools=[])

    async def no_storage_cost():
        return SimpleNamespace(total_cost_monthly_usd=0.0)

    monkeypatch.setattr(
        cost_service, "get_compute_adapter", lambda: SimpleNamespace(get_cluster_metrics=no_cluster_cost)
    )
    monkeypatch.setattr(
        cost_service, "get_storage_adapter", lambda: SimpleNamespace(get_storage_metrics=no_storage_cost)
    )

    nov_2 = date(2026, 11, 2)
    exported = datetime(2026, 11, 3, 20, tzinfo=utc)
    used = datetime(2026, 11, 2, 12, tzinfo=utc)
    if case == "understated":
        budget = "55.00"
        rows = [
            ExportRow("Compute Engine", 30.0, used, "202610", exported),
            ExportRow("Compute Engine", 20.0, used, "202611", exported),
        ]
    else:
        budget = "100.00"
        for component, amount in [("compute", "40.00"), ("storage", "5.00"), ("node", "50.00")]:
            session.add(
                CostRecord(organization_id=org_id, record_date=nov_2, component=component, cost_amount=Decimal(amount))
            )
        rows = [
            ExportRow("Compute Engine", 40.0, used, "202611", exported),
            ExportRow("Cloud Storage", 5.0, used, "202611", exported),
        ]
    session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal(budget)))
    await session.commit()

    table = BillingExportTable(rows)
    with patch("app.adapters.billing.gcp.bigquery.Client", side_effect=table.client):
        await CostService.sync_billing_data(session, org_id)
        await session.commit()
        with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=False)):
            await AutoRunService.process_pending_runs(session)
        await session.commit()

    pending = await session.get(PendingAutoRun, due_pending_run)
    assert (pending.cancelled_reason == "budget_limit") is gated
