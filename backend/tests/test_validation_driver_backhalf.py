"""A2 back-half orchestration driver (lit_validation).

The front half (read_and_plan) parks an approved study at ``acquiring_data``. From there a periodic
tick loop (``advance_active_studies``) reacts to committed pipeline-run state and walks the study:

    acquiring_data -> setup -> running -> extracting -> comparing

launching nf-core/fetchngs for data (D1), setting up experiment+samples with their FASTQ (D2),
launching the analysis pipeline (D3), then reading QC metrics (E1) into the evidence bundle. It stops
at ``comparing`` for a human to classify by hand (Phase 1 keeps comparison manual). These tests pin
the orchestration: which pipeline is launched when, how run completion advances the state, and how
metrics land in evidence. The reused primitives (launch_run, ingest/attach, QC extraction) are tested
in their own suites and are faked here.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models.comparison_target import ComparisonTarget
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.services.pipeline_run_service import PipelineRunService
from app.services.fetchngs_ingest_service import FetchngsIngestService
from app.services.qc_dashboard_service import QCDashboardService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService


# ---- fakes ----


class _LaunchSpy:
    """Stand-in for PipelineRunService.launch_run: records each launch and inserts a real run row so
    the driver can look it up by id. ``status`` controls the freshly-launched run's state."""

    def __init__(self, status="running"):
        self.status = status
        self.calls = []  # list of PipelineRunLaunchRequest

    async def __call__(self, session, org_id, user_id, data, *, via_assistant=False):
        self.calls.append(data)
        run = PipelineRun(
            organization_id=org_id,
            experiment_id=data.experiment_id,
            pipeline_name=data.pipeline_key,
            pipeline_version="test",
            status=self.status,
            parameters_json=dict(data.parameters or {}),
            submitted_by_user_id=user_id,
        )
        session.add(run)
        await session.flush()
        return run


async def _run(
    session,
    user,
    exp_id,
    *,
    name,
    status,
    outdir="gs://bioaf-results/run",
    run_id_out=None,
    failure_reason=None,
    error_message=None,
):
    run = PipelineRun(
        organization_id=user.organization_id,
        experiment_id=exp_id,
        pipeline_name=name,
        pipeline_version="1.0",
        status=status,
        parameters_json={"outdir": outdir},
        submitted_by_user_id=user.id,
        failure_reason=failure_reason,
        error_message=error_message,
    )
    session.add(run)
    await session.flush()
    return run


async def _study(session, user, *, state, accessions=("SRR390728",), pipeline_key="nf-core/rnaseq", experiment_id=None):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_doi="10.1/abc")
    await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=list(accessions),
        pipeline_key=pipeline_key,
        pipeline_version="3.14.0",
        reference_genome="GRCh38",
    )
    study.state = state
    study.experiment_id = experiment_id
    await session.flush()
    return study


async def _make_runnable_sample(session, user, exp_id, external_id="SRX079566"):
    """Create a sample with one linked FASTQ file, as ingest+attach would."""
    sample = Sample(experiment_id=exp_id, external_id=external_id, status="registered")
    session.add(sample)
    await session.flush()
    f = File(
        organization_id=user.organization_id,
        storage_uri=f"gs://bioaf-results/run/fastq/{external_id}_1.fastq.gz",
        filename=f"{external_id}_1.fastq.gz",
        file_type="fastq",
        source_type="pipeline_output",
        experiment_id=exp_id,
        tags_json=["read:R1", "lane:001"],
    )
    session.add(f)
    await session.flush()
    await session.execute(
        text("INSERT INTO sample_files (sample_id, file_id) VALUES (:s, :f)"), {"s": sample.id, "f": f.id}
    )
    return sample


async def _experiment_id(session, user):
    from app.models.experiment import Experiment

    exp = Experiment(organization_id=user.organization_id, name="e", owner_user_id=user.id, status="registered")
    session.add(exp)
    await session.flush()
    return exp.id


# ---- acquiring_data ----


@pytest.mark.asyncio
async def test_acquiring_data_launches_fetchngs(session, admin_user, monkeypatch):
    spy = _LaunchSpy(status="running")
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    study = await _study(session, admin_user, state="acquiring_data")

    await ValidationDriverService.advance_active_studies(session)

    assert len(spy.calls) == 1
    assert "fetchngs" in spy.calls[0].pipeline_key
    assert spy.calls[0].parameters["accessions"] == ["SRR390728"]
    await session.refresh(study)
    assert study.data_run_id is not None
    assert study.experiment_id is not None  # A3: an experiment was created and linked
    assert study.state == "acquiring_data"  # still waiting for the fetch to finish


@pytest.mark.asyncio
async def test_acquiring_data_runs_d2_and_advances_to_setup_when_fetch_completes(session, admin_user, monkeypatch):
    # No new fetchngs launch should happen once data_run_id is set; instead D2 (ingest + FASTQ attach)
    # runs and, if the data is usable, the study advances to setup.
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    ingest_calls, attach_calls = [], []

    async def _fake_ingest(session, run, **k):
        ingest_calls.append(run.id)
        return []

    async def _fake_attach(session, run, **k):
        attach_calls.append(run.id)
        return []

    monkeypatch.setattr(FetchngsIngestService, "ingest_for_run", _fake_ingest)
    monkeypatch.setattr(FetchngsIngestService, "attach_fastq_files", _fake_attach)

    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    fetch = await _run(session, admin_user, exp_id, name="nf-core/fetchngs", status="completed")
    study.data_run_id = fetch.id
    await _make_runnable_sample(session, admin_user, exp_id)  # the D2 effect: a sample WITH a FASTQ
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    assert ingest_calls == [fetch.id] and attach_calls == [fetch.id]  # D2 ran on the fetch run
    await session.refresh(study)
    assert study.state == "setup"
    assert spy.calls == []  # did not relaunch fetchngs


@pytest.mark.asyncio
async def test_acquiring_data_classifies_missing_data_when_fetched_data_unusable(session, admin_user, monkeypatch):
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(FetchngsIngestService, "ingest_for_run", _noop)
    monkeypatch.setattr(FetchngsIngestService, "attach_fastq_files", _noop)

    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    fetch = await _run(session, admin_user, exp_id, name="nf-core/fetchngs", status="completed")
    study.data_run_id = fetch.id
    await session.flush()  # no samples/files landed: the fetched data was not usable

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "classified"
    assert study.classification == "missing_data"
    assert spy.calls == []  # never launched an analysis run


@pytest.mark.asyncio
async def test_run_still_running_is_left_untouched(session, admin_user, monkeypatch):
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    fetch = await _run(session, admin_user, exp_id, name="nf-core/fetchngs", status="running")
    study.data_run_id = fetch.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "acquiring_data"  # unchanged while the fetch is in flight


# ---- acquiring_data: transient-failure auto-retry (ADR-069 pre-PR) ----


def test_classify_acquisition_failure_transient_vs_permanent():
    from app.services.validation_driver_service import classify_acquisition_failure

    # Network / infra outages are transient (retry).
    assert classify_acquisition_failure("task_error", "EBI ENA returned HTTP 500; connection refused") == "transient"
    assert classify_acquisition_failure("oom", None) == "transient"
    # An unrecognized failure defaults to transient, so a real outage is never falsely called missing_data.
    assert classify_acquisition_failure("task_error", "something we have never seen") == "transient"
    # A genuinely unavailable accession is permanent (-> missing_data).
    assert classify_acquisition_failure("task_error", "SRA_IDS_TO_RUNINFO: no records for SRX999") == "permanent"
    assert classify_acquisition_failure("task_error", "invalid accession: not a valid SRA id") == "permanent"


@pytest.mark.asyncio
async def test_transient_fetch_failure_schedules_a_backoff_retry(session, admin_user, monkeypatch):
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    fetch = await _run(
        session, admin_user, exp_id, name="nf-core/fetchngs", status="failed",
        failure_reason="task_error", error_message="ENA HTTP 503; connection refused",
    )
    study.data_run_id = fetch.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    # Not parked in error: still acquiring_data, with the failed run cleared so the next eligible tick relaunches.
    assert study.state == "acquiring_data"
    assert study.data_run_id is None
    assert (study.evidence_json or {}).get("acquire_retries") == 1
    assert (study.evidence_json or {}).get("acquire_retry_at")  # a backoff timestamp was set
    assert spy.calls == []  # the relaunch waits for the backoff; it does not fire this tick


@pytest.mark.asyncio
async def test_permanent_fetch_failure_is_missing_data(session, admin_user, monkeypatch):
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    fetch = await _run(
        session, admin_user, exp_id, name="nf-core/fetchngs", status="failed",
        failure_reason="task_error", error_message="SRA_IDS_TO_RUNINFO: no records for the accession",
    )
    study.data_run_id = fetch.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "classified"
    assert study.classification == "missing_data"  # a genuinely unavailable accession, not a retry
    assert spy.calls == []


@pytest.mark.asyncio
async def test_retry_backoff_is_honored_before_relaunch(session, admin_user, monkeypatch):
    from datetime import datetime, timedelta, timezone

    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    study.data_run_id = None  # a retry was scheduled; the prior run was cleared
    study.evidence_json = {
        "acquire_retries": 1,
        "acquire_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    }
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "acquiring_data"
    assert spy.calls == []  # backoff not elapsed -> no relaunch


@pytest.mark.asyncio
async def test_retry_relaunches_after_backoff_elapses(session, admin_user, monkeypatch):
    from datetime import datetime, timedelta, timezone

    spy = _LaunchSpy(status="running")
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    study.data_run_id = None
    study.evidence_json = {
        "acquire_retries": 1,
        "acquire_retry_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert len(spy.calls) == 1  # backoff elapsed -> fetchngs relaunched
    assert "fetchngs" in spy.calls[0].pipeline_key
    assert study.data_run_id is not None


@pytest.mark.asyncio
async def test_transient_retries_exhaust_to_error(session, admin_user, monkeypatch):
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="acquiring_data", experiment_id=exp_id)
    study.evidence_json = {"acquire_retries": 3}  # budget already spent
    fetch = await _run(
        session, admin_user, exp_id, name="nf-core/fetchngs", status="failed",
        failure_reason="task_error", error_message="ENA still down (HTTP 500)",
    )
    study.data_run_id = fetch.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "error"  # transient budget exhausted -> honest terminal error (not missing_data)
    assert study.classification is None


# ---- setup ----


@pytest.mark.asyncio
async def test_setup_launches_analysis_and_advances_to_running(session, admin_user, monkeypatch):
    spy = _LaunchSpy(status="running")
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)

    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="setup", experiment_id=exp_id, pipeline_key="nf-core/rnaseq")
    await _make_runnable_sample(session, admin_user, exp_id)  # experiment set up with a runnable sample
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    assert len(spy.calls) == 1
    assert spy.calls[0].pipeline_key == "nf-core/rnaseq"  # the analysis pipeline, not fetchngs
    assert spy.calls[0].experiment_id == exp_id
    # The fetched FASTQ are pipeline_output (derived) files; launch_run's per-sample gate filters
    # derived inputs OUT unless include_derived_inputs is set, so the analysis run must opt in or every
    # sample is dropped as "lacking input files". (Live smoke, 2026-07-05.)
    assert spy.calls[0].include_derived_inputs is True
    await session.refresh(study)
    assert study.analysis_run_id is not None
    assert study.state == "running"


# ---- running ----


@pytest.mark.asyncio
async def test_running_advances_to_extracting_when_analysis_completes(session, admin_user, monkeypatch):
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="running", experiment_id=exp_id)
    analysis = await _run(session, admin_user, exp_id, name="nf-core/rnaseq", status="completed")
    study.analysis_run_id = analysis.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "extracting"


@pytest.mark.asyncio
async def test_analysis_failure_moves_study_to_error(session, admin_user, monkeypatch):
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="running", experiment_id=exp_id)
    analysis = await _run(session, admin_user, exp_id, name="nf-core/rnaseq", status="failed")
    study.analysis_run_id = analysis.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "error"
    assert study.failure_reason


# ---- extracting ----


@pytest.mark.asyncio
async def test_extracting_stashes_metrics_and_advances_to_comparing(session, admin_user, monkeypatch):
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())

    metrics = {"reads_mapped_genome_unique": 0.834, "cell_count": 5000}

    async def _fake_get(session, org_id, run_id):
        return SimpleNamespace(id=7, status="ready", metrics_json=metrics)

    monkeypatch.setattr(QCDashboardService, "get_dashboard_by_run", _fake_get)

    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="extracting", experiment_id=exp_id)
    # A claimed metric from the paper (a ComparisonTarget), to sit beside the computed one.
    session.add(
        ComparisonTarget(
            reproduction_plan_id=study.reproduction_plan_id,
            metric_key="alignment_rate",
            claimed_value=85.0,
            unit="%",
        )
    )
    analysis = await _run(session, admin_user, exp_id, name="nf-core/rnaseq", status="completed")
    study.analysis_run_id = analysis.id
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "comparing"
    ev = study.evidence_json
    assert ev is not None
    assert ev["computed_metrics"] == metrics
    claimed = {t["metric_key"]: t["claimed_value"] for t in ev["comparison_targets"]}
    assert claimed["alignment_rate"] == 85.0
    assert ev["analysis_run_id"] == analysis.id


@pytest.mark.asyncio
async def test_extracting_activates_level3_and_routes_to_reproducing(session, admin_user, monkeypatch):
    """When the plan carries a confirmed finding claim (B4) + a differential design (B2e) and the
    analysis run produced the count matrix, extracting assembles evidence["level3"] and routes to the
    reproducing state instead of straight to comparing."""
    from app.models.template_notebook import TemplateNotebook

    async def _fake_get(session, org_id, run_id):
        return SimpleNamespace(id=7, status="ready", metrics_json={"reads_mapped_genome_unique": 0.9})

    monkeypatch.setattr(QCDashboardService, "get_dashboard_by_run", _fake_get)

    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="extracting", experiment_id=exp_id)
    analysis = await _run(session, admin_user, exp_id, name="nf-core/rnaseq", status="completed")
    study.analysis_run_id = analysis.id

    # B2e design + B4 confirmed finding claim on the plan.
    plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
    plan.differential_design_json = {
        "contrasts": [{"name": "t vs c", "test_samples": ["S1"], "reference_samples": ["S2"]}],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
    }
    plan.finding_claim_json = {
        "kind": "gene",
        "confirmed": True,
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
        "finding_set": {"kind": "gene", "namespace": "symbol", "n_sig": 1, "entities": [{"id": "A1BG"}]},
    }
    # The salmon gene-count matrix from the analysis run + the builtin DE template.
    session.add(
        File(
            organization_id=admin_user.organization_id,
            gcs_uri="gs://b/counts.tsv",
            storage_uri="gs://b/counts.tsv",
            filename="salmon.merged.gene_counts.tsv",
            file_type="count_matrix",
            source_pipeline_run_id=analysis.id,
        )
    )
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="DE",
        category="differential_expression",
        notebook_path="notebooks/de_bulk_deseq2.ipynb",
        parameters_json={},
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()

    await ValidationDriverService._handle_extracting(session, study)

    assert study.state == "reproducing"
    level3 = study.evidence_json["level3"]
    assert level3["template_id"] == tmpl.id
    assert level3["kind"] == "gene"
    assert level3["parameters"]["test_samples"] == "S1"


@pytest.mark.asyncio
async def test_extracting_stays_level2_without_finding_claim(session, admin_user, monkeypatch):
    """A study with a differential design but no confirmed ground-truth set stays Level-2: extracting
    routes straight to comparing, no reproducing."""

    async def _fake_get(session, org_id, run_id):
        return SimpleNamespace(id=7, status="ready", metrics_json={})

    monkeypatch.setattr(QCDashboardService, "get_dashboard_by_run", _fake_get)
    exp_id = await _experiment_id(session, admin_user)
    study = await _study(session, admin_user, state="extracting", experiment_id=exp_id)
    analysis = await _run(session, admin_user, exp_id, name="nf-core/rnaseq", status="completed")
    study.analysis_run_id = analysis.id
    await session.flush()

    await ValidationDriverService._handle_extracting(session, study)

    assert study.state == "comparing"
    assert "level3" not in study.evidence_json


# ---- comparing (E2/E3/E4 automatic classifier) ----


@pytest.mark.asyncio
async def test_comparing_auto_finalizes_a_clean_validated(session, admin_user, monkeypatch):
    """Solid agreement with a finding reproduced (peak count + mapping rate) auto-finalizes."""
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())
    study = await _study(session, admin_user, state="comparing")
    study.evidence_json = {
        "computed_metrics": {"peak_count": 25_000, "reads_mapped_genome": 0.834},
        "comparison_targets": [
            {"metric_key": "num_peaks", "claimed_value": 24_000, "unit": None, "tolerance": None},
            {"metric_key": "alignment_rate", "claimed_value": 83.4, "unit": "%", "tolerance": None},
        ],
    }
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "classified"
    assert study.classification == "validated"
    assert study.evidence_json["classification_result"]["auto_finalize"] is True


@pytest.mark.asyncio
async def test_comparing_holds_divergence_for_a_human_with_a_suggested_verdict(session, admin_user, monkeypatch):
    """A divergence our side cannot clear (partial mapping) stays at comparing as suggested inconclusive."""
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())
    study = await _study(session, admin_user, state="comparing")
    plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
    plan.mapping_confidence = "partial"
    study.evidence_json = {
        "computed_metrics": {"cell_count": 2000},
        "comparison_targets": [{"metric_key": "cell_count", "claimed_value": 10000, "unit": None, "tolerance": None}],
    }
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study)
    assert study.state == "comparing"  # held for the human gate
    assert study.classification is None
    result = study.evidence_json["classification_result"]
    assert result["classification"] == "inconclusive"
    assert result["auto_finalize"] is False


@pytest.mark.asyncio
async def test_comparing_classifier_runs_once_then_waits(session, admin_user, monkeypatch):
    """The classifier computes the verdict exactly once; a later tick does not recompute or re-change."""
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())
    study = await _study(session, admin_user, state="comparing")
    plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
    plan.mapping_confidence = "partial"
    study.evidence_json = {
        "computed_metrics": {"cell_count": 2000},
        "comparison_targets": [{"metric_key": "cell_count", "claimed_value": 10000, "unit": None, "tolerance": None}],
    }
    await session.flush()

    first = await ValidationDriverService.advance_active_studies(session)
    second = await ValidationDriverService.advance_active_studies(session)

    assert first == 1  # computed + recorded the verdict
    assert second == 0  # already classified; left alone for the human
    await session.refresh(study)
    assert study.state == "comparing"


@pytest.mark.asyncio
async def test_advance_is_isolated_per_study(session, admin_user, monkeypatch):
    """One study's failure does not stop the others from advancing (per-study isolation)."""
    monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())

    # Study A: a running analysis that completed -> should reach extracting.
    exp_a = await _experiment_id(session, admin_user)
    study_a = await _study(session, admin_user, state="running", experiment_id=exp_a)
    run_a = await _run(session, admin_user, exp_a, name="nf-core/rnaseq", status="completed")
    study_a.analysis_run_id = run_a.id

    # Study B: at running but its analysis_run_id points nowhere -> its advance errors.
    exp_b = await _experiment_id(session, admin_user)
    study_b = await _study(session, admin_user, state="running", experiment_id=exp_b)
    study_b.analysis_run_id = 10**9  # no such run
    await session.flush()

    await ValidationDriverService.advance_active_studies(session)

    await session.refresh(study_a)
    await session.refresh(study_b)
    assert study_a.state == "extracting"  # A advanced despite B failing
    assert study_b.state == "error"
