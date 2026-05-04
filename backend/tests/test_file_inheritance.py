"""Tests for FileService.list_files association inheritance.

A file may be linked at any of: organization (global), project,
experiment, or sample. Searching at a higher level should inherit
files at lower levels:

  project filter   -> files on project + files on experiments under
                       the project + files on samples under those
                       experiments
  experiment filter -> files on the experiment + files on samples
                       under the experiment
  sample filter    -> files linked to that sample only
"""

import pytest

from app.services.file_service import FileService


async def _setup_hierarchy(session, admin_user):
    """Create Project A with Experiments N and O, each with three samples."""
    from app.models.experiment import Experiment
    from app.models.project import Project
    from app.models.sample import Sample

    org_id = admin_user.organization_id

    project = Project(organization_id=org_id, name="Project A", code="PRJA")
    session.add(project)
    await session.flush()

    exp_n = Experiment(
        organization_id=org_id,
        project_id=project.id,
        name="Experiment N",
        owner_user_id=admin_user.id,
        status="registered",
    )
    exp_o = Experiment(
        organization_id=org_id,
        project_id=project.id,
        name="Experiment O",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add_all([exp_n, exp_o])
    await session.flush()

    samples_n = [Sample(experiment_id=exp_n.id, status="registered") for _ in range(3)]
    samples_o = [Sample(experiment_id=exp_o.id, status="registered") for _ in range(3)]
    session.add_all(samples_n + samples_o)
    await session.flush()
    await session.commit()

    return project, exp_n, exp_o, samples_n, samples_o


async def _make_file(session, org_id, name, *, project_id=None, experiment_id=None, sample_id=None):
    from app.models.file import File

    f = File(
        organization_id=org_id,
        gcs_uri=f"gs://test/{name}",
        filename=name,
        file_type="csv",
        project_id=project_id,
        experiment_id=experiment_id,
    )
    session.add(f)
    await session.flush()
    if sample_id is not None:
        await FileService.link_file_to_sample(session, f.id, sample_id)
    await session.commit()
    return f


@pytest.mark.asyncio
async def test_experiment_filter_includes_sample_files(session, admin_user):
    project, exp_n, exp_o, samples_n, samples_o = await _setup_hierarchy(session, admin_user)
    org_id = admin_user.organization_id

    on_exp_n = await _make_file(session, org_id, "exp_n_direct.csv", experiment_id=exp_n.id)
    on_sample_n1 = await _make_file(session, org_id, "sample_n1.csv", sample_id=samples_n[0].id)
    on_sample_n2 = await _make_file(session, org_id, "sample_n2.csv", sample_id=samples_n[1].id)
    on_exp_o = await _make_file(session, org_id, "exp_o_direct.csv", experiment_id=exp_o.id)
    on_sample_o1 = await _make_file(session, org_id, "sample_o1.csv", sample_id=samples_o[0].id)

    files, total = await FileService.list_files(session, org_id, experiment_id=exp_n.id)
    names = {f.filename for f in files}

    assert on_exp_n.filename in names
    assert on_sample_n1.filename in names
    assert on_sample_n2.filename in names
    assert on_exp_o.filename not in names
    assert on_sample_o1.filename not in names
    assert total == len(names)


@pytest.mark.asyncio
async def test_project_filter_includes_experiment_and_sample_files(session, admin_user):
    project, exp_n, exp_o, samples_n, samples_o = await _setup_hierarchy(session, admin_user)
    org_id = admin_user.organization_id

    on_project = await _make_file(session, org_id, "project_direct.csv", project_id=project.id)
    on_exp_n = await _make_file(session, org_id, "exp_n_direct.csv", experiment_id=exp_n.id)
    on_sample_n1 = await _make_file(session, org_id, "sample_n1.csv", sample_id=samples_n[0].id)
    on_exp_o = await _make_file(session, org_id, "exp_o_direct.csv", experiment_id=exp_o.id)
    on_sample_o1 = await _make_file(session, org_id, "sample_o1.csv", sample_id=samples_o[0].id)

    # A file with no association at all should NOT appear under the project filter
    global_file = await _make_file(session, org_id, "global.csv")

    files, total = await FileService.list_files(session, org_id, project_id=project.id)
    names = {f.filename for f in files}

    assert on_project.filename in names
    assert on_exp_n.filename in names
    assert on_sample_n1.filename in names
    assert on_exp_o.filename in names
    assert on_sample_o1.filename in names
    assert global_file.filename not in names
    assert total == len(names)


@pytest.mark.asyncio
async def test_sample_filter_returns_only_that_sample(session, admin_user):
    project, exp_n, exp_o, samples_n, samples_o = await _setup_hierarchy(session, admin_user)
    org_id = admin_user.organization_id

    on_sample_n1 = await _make_file(session, org_id, "sample_n1.csv", sample_id=samples_n[0].id)
    on_sample_n2 = await _make_file(session, org_id, "sample_n2.csv", sample_id=samples_n[1].id)
    await _make_file(session, org_id, "exp_n_direct.csv", experiment_id=exp_n.id)

    files, total = await FileService.list_files(session, org_id, sample_id=samples_n[0].id)
    names = {f.filename for f in files}

    assert on_sample_n1.filename in names
    assert on_sample_n2.filename not in names
    assert total == 1


@pytest.mark.asyncio
async def test_no_filter_returns_global_and_associated(session, admin_user):
    project, exp_n, exp_o, samples_n, samples_o = await _setup_hierarchy(session, admin_user)
    org_id = admin_user.organization_id

    await _make_file(session, org_id, "global.csv")
    await _make_file(session, org_id, "exp_n_direct.csv", experiment_id=exp_n.id)
    await _make_file(session, org_id, "sample_n1.csv", sample_id=samples_n[0].id)

    files, total = await FileService.list_files(session, org_id)
    assert total == 3
    assert {f.filename for f in files} == {"global.csv", "exp_n_direct.csv", "sample_n1.csv"}
