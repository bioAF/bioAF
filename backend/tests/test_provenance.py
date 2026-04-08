import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.project_sample import ProjectSample
from app.models.sample import Sample


@pytest_asyncio.fixture
async def provenance_project(session, admin_user):
    """Create a project with samples from 2 experiments and a project-scoped run."""
    exp1 = Experiment(
        organization_id=admin_user.organization_id,
        name="Tumor Experiment",
        status="registered",
    )
    exp2 = Experiment(
        organization_id=admin_user.organization_id,
        name="Healthy Experiment",
        status="registered",
    )
    session.add_all([exp1, exp2])
    await session.flush()

    samples = []
    for i in range(2):
        s = Sample(
            experiment_id=exp1.id,
            sample_id_unique=f"T-{i + 1}",
            organism="Homo sapiens",
            tissue_type="brain",
            status="registered",
        )
        samples.append(s)
    for i in range(2):
        s = Sample(
            experiment_id=exp2.id,
            sample_id_unique=f"H-{i + 1}",
            organism="Homo sapiens",
            tissue_type="blood",
            status="registered",
        )
        samples.append(s)
    session.add_all(samples)
    await session.flush()

    project = Project(
        organization_id=admin_user.organization_id,
        name="GBM Atlas",
        status="active",
        owner_user_id=admin_user.id,
        created_by_user_id=admin_user.id,
    )
    session.add(project)
    await session.flush()

    # Add all samples to project
    for s in samples:
        ps = ProjectSample(
            project_id=project.id,
            sample_id=s.id,
            added_by_user_id=admin_user.id,
        )
        session.add(ps)
    await session.flush()

    # Create a project-scoped pipeline run
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp1.id,
        project_id=project.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        status="completed",
    )
    session.add(run)

    # Create an experiment-only run
    exp_run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp1.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="cellranger",
        pipeline_version="8.0",
        status="completed",
    )
    session.add(exp_run)

    await session.flush()
    await session.commit()

    return project, exp1, exp2, samples, run


@pytest.mark.asyncio
async def test_project_provenance_nodes(client, admin_token, provenance_project):
    project, exp1, exp2, samples, run = provenance_project

    response = await client.get(
        f"/api/projects/{project.id}/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    node_ids = {n["id"] for n in data["nodes"]}
    node_types = {n["id"]: n["type"] for n in data["nodes"]}

    # Project node exists
    assert f"project:{project.id}" in node_ids

    # Both experiment nodes exist
    assert f"experiment:{exp1.id}" in node_ids
    assert f"experiment:{exp2.id}" in node_ids

    # All sample nodes exist
    for s in samples:
        assert f"sample:{s.id}" in node_ids

    # Pipeline run node exists
    assert f"pipeline_run:{run.id}" in node_ids

    # Verify node types
    assert node_types[f"project:{project.id}"] == "project"
    assert node_types[f"experiment:{exp1.id}"] == "experiment"
    assert node_types[f"sample:{samples[0].id}"] == "sample"
    assert node_types[f"pipeline_run:{run.id}"] == "pipeline_run"


@pytest.mark.asyncio
async def test_project_provenance_edges(client, admin_token, provenance_project):
    project, exp1, exp2, samples, run = provenance_project

    response = await client.get(
        f"/api/projects/{project.id}/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()

    edge_tuples = {(e["source"], e["target"], e["relationship"]) for e in data["edges"]}

    # Experiments -> project edges
    assert (f"experiment:{exp1.id}", f"project:{project.id}", "contains") in edge_tuples
    assert (f"experiment:{exp2.id}", f"project:{project.id}", "contains") in edge_tuples

    # Experiment -> sample edges
    assert (f"experiment:{exp1.id}", f"sample:{samples[0].id}", "contains") in edge_tuples
    assert (f"experiment:{exp2.id}", f"sample:{samples[2].id}", "contains") in edge_tuples

    # Project -> pipeline run edge (project-scoped run)
    assert (f"project:{project.id}", f"pipeline_run:{run.id}", "input_to") in edge_tuples


@pytest.mark.asyncio
async def test_project_provenance_includes_experiment_runs(client, admin_token, provenance_project):
    project, exp1, _, _, _ = provenance_project

    response = await client.get(
        f"/api/projects/{project.id}/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()

    # Should include experiment-only runs from source experiments
    run_nodes = [n for n in data["nodes"] if n["type"] == "pipeline_run"]
    assert len(run_nodes) >= 2  # project run + experiment run


@pytest_asyncio.fixture
async def provenance_project_via_experiment_link(session, admin_user):
    """Project with experiments linked via experiment.project_id (no project_samples rows)."""
    project = Project(
        organization_id=admin_user.organization_id,
        name="Linked Project",
        status="active",
        owner_user_id=admin_user.id,
        created_by_user_id=admin_user.id,
    )
    session.add(project)
    await session.flush()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Linked Experiment",
        status="registered",
        project_id=project.id,
    )
    session.add(exp)
    await session.flush()

    sample = Sample(
        experiment_id=exp.id,
        sample_id_unique="L-1",
        organism="Homo sapiens",
        tissue_type="liver",
        status="registered",
    )
    session.add(sample)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14",
        status="completed",
    )
    session.add(run)
    await session.flush()
    await session.commit()

    return project, exp, sample, run


@pytest.mark.asyncio
async def test_provenance_includes_experiments_linked_by_project_id(
    client, admin_token, provenance_project_via_experiment_link
):
    """Experiments linked via experiment.project_id should appear in DAG."""
    project, exp, sample, run = provenance_project_via_experiment_link

    response = await client.get(
        f"/api/projects/{project.id}/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    node_ids = {n["id"] for n in data["nodes"]}
    assert f"project:{project.id}" in node_ids
    assert f"experiment:{exp.id}" in node_ids
    assert f"sample:{sample.id}" in node_ids
    assert f"pipeline_run:{run.id}" in node_ids

    edge_tuples = {(e["source"], e["target"], e["relationship"]) for e in data["edges"]}
    assert (f"experiment:{exp.id}", f"project:{project.id}", "contains") in edge_tuples
    assert (f"experiment:{exp.id}", f"sample:{sample.id}", "contains") in edge_tuples
    assert (f"experiment:{exp.id}", f"pipeline_run:{run.id}", "input_to") in edge_tuples


@pytest.mark.asyncio
async def test_provenance_404_for_missing_project(client, admin_token):
    response = await client.get(
        "/api/projects/99999/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
