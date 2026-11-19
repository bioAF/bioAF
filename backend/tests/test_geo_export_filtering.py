"""GEO export sample selection: the QC filter contract and which Samples it selects.

The export filter has exactly two values. ``exclude_failed`` (the default) omits Samples whose QC is
``fail`` and keeps ``pass``, ``warning`` and unset (null) QC. ``include_all`` keeps every Sample. Any
other value, including the historical UI value ``pass``, is refused before export data is gathered.
Readiness and the downloaded package must select the same Samples.
"""

import json
import zipfile
from io import BytesIO

import pytest
import pytest_asyncio
from openpyxl import load_workbook


@pytest_asyncio.fixture
async def qc_experiment(client, admin_token, session, admin_user):
    """One Sample per QC state: pass, warning, fail, and unset."""
    from app.models.pipeline_run import PipelineRun
    from app.models.sample import Sample

    resp = await client.post(
        "/api/experiments",
        json={"name": "GEO QC Selection", "description": "QC filter selection."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]
    samples = {}
    for name, qc in (("QC_PASS", "pass"), ("QC_WARN", "warning"), ("QC_FAIL", "fail"), ("QC_NULL", None)):
        sample = Sample(experiment_id=exp_id, external_id=name, organism="Homo sapiens", qc_status=qc)
        session.add(sample)
        await session.flush()
        samples[name] = sample.id
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp_id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status="completed",
    )
    session.add(run)
    await session.commit()
    return {"experiment_id": exp_id, "org_id": admin_user.organization_id, "samples": samples, "run_id": run.id}


EXCLUDED = ["QC_NULL", "QC_PASS", "QC_WARN"]
EVERYONE = ["QC_FAIL", "QC_NULL", "QC_PASS", "QC_WARN"]


def workbook_sample_names(zip_bytes: bytes) -> list[str]:
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    xlsx = next(n for n in zf.namelist() if n.endswith(".xlsx"))
    ws = load_workbook(BytesIO(zf.read(xlsx)))["SAMPLES"]
    header = [c.value for c in ws[1]]
    col = header.index("Sample_name")
    return sorted(row[col] for row in ws.iter_rows(min_row=2, values_only=True) if row[col])


def report_sample_names(zip_bytes: bytes) -> list[str]:
    report = json.loads(zipfile.ZipFile(BytesIO(zip_bytes)).read("validation_report.json"))
    return sorted(sv["sample_name"] for sv in report["sample_validations"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query, expected",
    [("", EXCLUDED), ("&qc_status_filter=exclude_failed", EXCLUDED), ("&qc_status_filter=include_all", EVERYONE)],
    ids=["omitted", "exclude_failed", "include_all"],
)
async def test_readiness_and_download_select_the_same_samples(client, comp_bio_token, qc_experiment, query, expected):
    base = f"/api/experiments/{qc_experiment['experiment_id']}/export/geo?pipeline_run_id={qc_experiment['run_id']}"
    auth = {"Authorization": f"Bearer {comp_bio_token}"}

    readiness = await client.post(f"{base}&validate_only=true{query}", headers=auth)
    assert readiness.status_code == 200, readiness.text
    assert sorted(sv["sample_name"] for sv in readiness.json()["sample_validations"]) == expected

    download = await client.post(f"{base}{query}", headers=auth)
    assert download.status_code == 200, download.text
    assert workbook_sample_names(download.content) == expected
    assert report_sample_names(download.content) == expected


@pytest.mark.asyncio
async def test_an_all_failed_experiment_excludes_everything_rather_than_falling_back(
    client, comp_bio_token, qc_experiment, session
):
    from sqlalchemy import update

    from app.models.sample import Sample

    await session.execute(
        update(Sample).where(Sample.experiment_id == qc_experiment["experiment_id"]).values(qc_status="fail")
    )
    await session.commit()
    base = f"/api/experiments/{qc_experiment['experiment_id']}/export/geo"
    auth = {"Authorization": f"Bearer {comp_bio_token}"}

    readiness = await client.post(f"{base}?validate_only=true", headers=auth)
    assert readiness.status_code == 200
    assert readiness.json()["sample_validations"] == []

    download = await client.post(base, headers=auth)
    assert download.status_code == 200
    assert workbook_sample_names(download.content) == []


INVALID_FILTERS = ["pass", "fail", "unknown", ""]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", INVALID_FILTERS)
@pytest.mark.parametrize("validate_only", [True, False])
async def test_experiment_endpoint_refuses_unsupported_filters(
    client, comp_bio_token, qc_experiment, session, value, validate_only
):
    from sqlalchemy import text

    url = f"/api/experiments/{qc_experiment['experiment_id']}/export/geo?qc_status_filter={value}"
    if validate_only:
        url += "&validate_only=true"
    resp = await client.post(url, headers={"Authorization": f"Bearer {comp_bio_token}"})

    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    audited = await session.execute(text("SELECT count(*) FROM audit_log WHERE entity_type = 'geo_export'"))
    assert audited.scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", INVALID_FILTERS)
@pytest.mark.parametrize("validate_only", [True, False])
async def test_project_endpoint_refuses_unsupported_filters(client, admin_token, session, admin_user, value, validate_only):
    from app.models.project import Project

    # An empty project: an unvalidated filter would otherwise reach "No experiments found" or succeed.
    project = Project(name="Empty GEO Project", organization_id=admin_user.organization_id)
    session.add(project)
    await session.commit()

    url = f"/api/projects/{project.id}/export/geo?qc_status_filter={value}"
    if validate_only:
        url += "&validate_only=true"
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["exclude_failed", "include_all"])
async def test_project_validation_accepts_both_filters_without_changing_its_checks(
    client, admin_token, session, admin_user, value
):
    """Project validate_only keeps its cross-experiment checks over every eligible Sample, failed included."""
    from app.models.experiment import Experiment
    from app.models.project import Project
    from app.models.sample import Sample

    project = Project(name="Organism Mix", organization_id=admin_user.organization_id)
    session.add(project)
    await session.flush()
    human = Experiment(name="Human", organization_id=admin_user.organization_id, project_id=project.id)
    mouse = Experiment(name="Mouse", organization_id=admin_user.organization_id, project_id=project.id)
    session.add_all([human, mouse])
    await session.flush()
    session.add_all(
        [
            Sample(experiment_id=human.id, external_id="H1", organism="Homo sapiens", qc_status="pass"),
            # Only a failed Sample makes the organisms differ; the warning must not depend on the filter.
            Sample(experiment_id=mouse.id, external_id="M1", organism="Mus musculus", qc_status="fail"),
        ]
    )
    await session.commit()

    resp = await client.post(
        f"/api/projects/{project.id}/export/geo?validate_only=true&qc_status_filter={value}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert any("organisms" in w.lower() for w in resp.json()["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["pass", "unknown", ""])
async def test_direct_service_calls_refuse_unsupported_filters_before_gathering(session, value):
    """A non-HTTP caller gets the service's validation error, not a lookup: the filter is checked first."""
    from app.exceptions import ValidationError
    from app.services.geo.geo_export_service import GeoExportService
    from app.services.geo.superseries_export_service import SuperSeriesExportService

    # Ids that do not exist: gathering first would raise NotFoundError instead.
    with pytest.raises(ValidationError):
        await GeoExportService.validate(session, 999_999, 999_999, qc_status_filter=value)
    with pytest.raises(ValidationError):
        await GeoExportService.export(session, 999_999, 999_999, qc_status_filter=value)
    with pytest.raises(ValidationError):
        await SuperSeriesExportService.export(session, 999_999, 999_999, qc_status_filter=value)


@pytest.mark.asyncio
async def test_superseries_refuses_an_unsupported_filter_instead_of_a_partial_archive(session, admin_user):
    """The per-experiment exception handler must never see (and swallow) an invalid filter."""
    from app.exceptions import ValidationError
    from app.models.experiment import Experiment
    from app.models.project import Project
    from app.models.sample import Sample
    from app.services.geo.superseries_export_service import SuperSeriesExportService

    project = Project(name="Valid Project", organization_id=admin_user.organization_id)
    session.add(project)
    await session.flush()
    exp = Experiment(name="Only", organization_id=admin_user.organization_id, project_id=project.id)
    session.add(exp)
    await session.flush()
    session.add(Sample(experiment_id=exp.id, external_id="ONLY_1", organism="Homo sapiens"))
    await session.commit()

    with pytest.raises(ValidationError):
        await SuperSeriesExportService.export(session, project.id, admin_user.organization_id, qc_status_filter="pass")


@pytest.mark.asyncio
@pytest.mark.parametrize("value, expected", [("exclude_failed", ["P1"]), ("include_all", ["F1", "P1"])])
async def test_superseries_passes_the_filter_to_each_nested_export(session, admin_user, value, expected):
    from app.models.experiment import Experiment
    from app.models.project import Project
    from app.models.sample import Sample
    from app.services.geo.superseries_export_service import SuperSeriesExportService

    project = Project(name="Nested Filter", organization_id=admin_user.organization_id)
    session.add(project)
    await session.flush()
    exp = Experiment(name="Nested", organization_id=admin_user.organization_id, project_id=project.id)
    session.add(exp)
    await session.flush()
    session.add_all(
        [
            Sample(experiment_id=exp.id, external_id="P1", organism="Homo sapiens", qc_status="pass"),
            Sample(experiment_id=exp.id, external_id="F1", organism="Homo sapiens", qc_status="fail"),
        ]
    )
    await session.commit()

    zip_bytes, _ = await SuperSeriesExportService.export(
        session, project.id, admin_user.organization_id, qc_status_filter=value
    )
    outer = zipfile.ZipFile(BytesIO(zip_bytes))
    [nested] = [n for n in outer.namelist() if n.startswith("experiments/")]
    assert workbook_sample_names(outer.read(nested)) == expected
