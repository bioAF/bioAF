"""Tests for GEO SuperSeries export."""

import zipfile
from io import BytesIO

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.project import Project
from app.models.sample import Sample
from app.services.geo.superseries_export_service import SuperSeriesExportService


@pytest_asyncio.fixture
async def project_with_experiments(session, admin_user):
    """Create a project with 2 experiments and samples."""
    project = Project(
        name="Cross Experiment Project",
        description="Test project for SuperSeries",
        organization_id=admin_user.organization_id,
        owner_user_id=admin_user.id,
    )
    session.add(project)
    await session.flush()

    exp1 = Experiment(
        name="Experiment Alpha",
        organization_id=admin_user.organization_id,
        project_id=project.id,
        status="complete",
    )
    exp2 = Experiment(
        name="Experiment Beta",
        organization_id=admin_user.organization_id,
        project_id=project.id,
        status="complete",
    )
    session.add_all([exp1, exp2])
    await session.flush()

    # Samples for exp1
    s1 = Sample(
        experiment_id=exp1.id,
        sample_id_unique="SAMPLE_001",
        organism="Homo sapiens",
        chemistry_version="v3",
    )
    s2 = Sample(
        experiment_id=exp1.id,
        sample_id_unique="SAMPLE_002",
        organism="Homo sapiens",
        chemistry_version="v3",
    )
    # Samples for exp2
    s3 = Sample(
        experiment_id=exp2.id,
        sample_id_unique="SAMPLE_003",
        organism="Homo sapiens",
        chemistry_version="v3",
    )
    session.add_all([s1, s2, s3])
    await session.flush()
    await session.commit()
    return project, [exp1, exp2], [s1, s2, s3]


@pytest_asyncio.fixture
async def project_with_mixed_organisms(session, admin_user):
    """Project with experiments that have different organisms."""
    project = Project(
        name="Mixed Organism Project",
        organization_id=admin_user.organization_id,
    )
    session.add(project)
    await session.flush()

    exp1 = Experiment(
        name="Human Exp",
        organization_id=admin_user.organization_id,
        project_id=project.id,
        status="complete",
    )
    exp2 = Experiment(
        name="Mouse Exp",
        organization_id=admin_user.organization_id,
        project_id=project.id,
        status="complete",
    )
    session.add_all([exp1, exp2])
    await session.flush()

    s1 = Sample(
        experiment_id=exp1.id,
        sample_id_unique="HUMAN_001",
        organism="Homo sapiens",
    )
    s2 = Sample(
        experiment_id=exp2.id,
        sample_id_unique="MOUSE_001",
        organism="Mus musculus",
    )
    session.add_all([s1, s2])
    await session.flush()
    await session.commit()
    return project, [exp1, exp2]


@pytest_asyncio.fixture
async def project_with_duplicate_samples(session, admin_user):
    """Project with duplicate sample IDs across experiments."""
    project = Project(
        name="Duplicate IDs Project",
        organization_id=admin_user.organization_id,
    )
    session.add(project)
    await session.flush()

    exp1 = Experiment(
        name="Exp A",
        organization_id=admin_user.organization_id,
        project_id=project.id,
        status="complete",
    )
    exp2 = Experiment(
        name="Exp B",
        organization_id=admin_user.organization_id,
        project_id=project.id,
        status="complete",
    )
    session.add_all([exp1, exp2])
    await session.flush()

    # Same sample ID in both experiments
    s1 = Sample(
        experiment_id=exp1.id,
        sample_id_unique="DUP_SAMPLE",
        organism="Homo sapiens",
    )
    s2 = Sample(
        experiment_id=exp2.id,
        sample_id_unique="DUP_SAMPLE",
        organism="Homo sapiens",
    )
    session.add_all([s1, s2])
    await session.flush()
    await session.commit()
    return project, [exp1, exp2]


@pytest.mark.asyncio
async def test_superseries_export_two_experiments(session, admin_user, project_with_experiments):
    """Test SuperSeries export with a project containing 2 experiments."""
    project, experiments, _ = project_with_experiments
    zip_bytes, filename = await SuperSeriesExportService.export(session, project.id, admin_user.organization_id)
    assert filename.startswith("geo_superseries_")
    assert filename.endswith(".zip")

    # Verify ZIP structure
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    names = zf.namelist()
    assert "SuperSeries_metadata.txt" in names
    assert "unified_file_manifest.tsv" in names
    assert "validation_report.json" in names


@pytest.mark.asyncio
async def test_superseries_metadata_links_sub_series(session, admin_user, project_with_experiments):
    """Test SuperSeries metadata links sub-Series correctly."""
    project, experiments, _ = project_with_experiments
    zip_bytes, _ = await SuperSeriesExportService.export(session, project.id, admin_user.organization_id)
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    metadata = zf.read("SuperSeries_metadata.txt").decode()
    assert "^SUPERSERIES" in metadata
    assert project.name in metadata


@pytest.mark.asyncio
async def test_unified_file_manifest(session, admin_user, project_with_experiments):
    """Test unified file manifest contains entries from all experiments."""
    project, experiments, _ = project_with_experiments
    zip_bytes, _ = await SuperSeriesExportService.export(session, project.id, admin_user.organization_id)
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    manifest = zf.read("unified_file_manifest.tsv").decode()
    for exp in experiments:
        assert exp.name in manifest


@pytest.mark.asyncio
async def test_cross_validation_warns_different_organisms(session, admin_user, project_with_mixed_organisms):
    """Test cross-experiment validation: warn when organisms differ."""
    project, _ = project_with_mixed_organisms
    validation = await SuperSeriesExportService.validate_cross_experiment(
        session, project.id, admin_user.organization_id
    )
    organism_warnings = [w for w in validation.warnings if "organisms" in w.lower()]
    assert len(organism_warnings) > 0


@pytest.mark.asyncio
async def test_cross_validation_error_on_duplicate_sample_ids(session, admin_user, project_with_duplicate_samples):
    """Test cross-experiment validation: error on duplicate sample IDs."""
    project, _ = project_with_duplicate_samples
    validation = await SuperSeriesExportService.validate_cross_experiment(
        session, project.id, admin_user.organization_id
    )
    assert validation.has_errors
    dup_errors = [e for e in validation.errors if "duplicate" in e.lower()]
    assert len(dup_errors) > 0


@pytest.mark.asyncio
async def test_exclude_unclaimed_filters_experiments(session, admin_user, project_with_experiments):
    """Test exclude_unclaimed parameter filters out unclaimed experiments."""
    project, experiments, _ = project_with_experiments
    # Mark one experiment as unclaimed
    experiments[1].is_unclaimed = True
    session.add(experiments[1])
    await session.commit()

    zip_bytes, _ = await SuperSeriesExportService.export(
        session, project.id, admin_user.organization_id, exclude_unclaimed=True
    )
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    manifest = zf.read("unified_file_manifest.tsv").decode()
    assert experiments[0].name in manifest
    assert experiments[1].name not in manifest


@pytest.mark.asyncio
async def test_superseries_zip_has_correct_structure(session, admin_user, project_with_experiments):
    """Test that the ZIP has the correct directory structure."""
    project, _, _ = project_with_experiments
    zip_bytes, _ = await SuperSeriesExportService.export(session, project.id, admin_user.organization_id)
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    names = zf.namelist()

    # Should have top-level metadata files
    assert "SuperSeries_metadata.txt" in names
    assert "unified_file_manifest.tsv" in names
    assert "validation_report.json" in names

    # Should have experiment subdirectories
    exp_dirs = [n for n in names if n.startswith("experiments/")]
    assert len(exp_dirs) > 0


@pytest.mark.asyncio
async def test_superseries_api_endpoint(client, admin_token, project_with_experiments):
    """Test the SuperSeries export API endpoint."""
    project, _, _ = project_with_experiments
    resp = await client.post(
        f"/api/projects/{project.id}/export/geo?validate_only=true",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert "errors" in data
