"""Tests for FileService.get_provenance_for_files.

The provenance map drives the breadcrumb shown on file tiles. It must
walk the hierarchy correctly: project name from explicit project OR
inherited via experiment OR via sample.experiment.project; pipeline
runs and compute sessions resolve their launchers; the resolved
"creator" prefers the launcher of the run/session over the uploader,
falling back to the uploader for plain uploads.
"""

import pytest

from app.services.file_service import FileService


@pytest.mark.asyncio
async def test_provenance_for_uploaded_file_with_full_hierarchy(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.project import Project
    from app.models.sample import Sample

    org_id = admin_user.organization_id

    proj = Project(organization_id=org_id, name="Project Alpha", code="PA")
    session.add(proj)
    await session.flush()

    exp = Experiment(
        organization_id=org_id,
        project_id=proj.id,
        name="Exp X",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    sample = Sample(experiment_id=exp.id, sample_id_unique="S001", status="registered")
    session.add(sample)
    await session.flush()
    await session.commit()

    from app.models.file import File

    f = File(
        organization_id=org_id,
        gcs_uri="gs://b/upload.csv",
        filename="upload.csv",
        file_type="csv",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    await FileService.link_file_to_sample(session, f.id, sample.id)
    await session.commit()

    prov_map = await FileService.get_provenance_for_files(session, [f])
    prov = prov_map[f.id]

    # Project + experiment must be inferred via sample.experiment.project
    assert prov["project_name"] == "Project Alpha"
    assert prov["experiment_name"] == "Exp X"
    assert prov["sample_labels"] == ["S001"]
    assert prov["pipeline_run"] is None
    assert prov["compute_session"] is None
    assert prov["creator"]["id"] == admin_user.id


@pytest.mark.asyncio
async def test_provenance_for_pipeline_output_resolves_launcher(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.pipeline_run import PipelineRun
    from app.models.user import User
    from app.services.auth_service import AuthService

    org_id = admin_user.organization_id

    launcher = User(
        email="launcher@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=admin_user.role_id,
        organization_id=org_id,
        status="active",
    )
    session.add(launcher)
    await session.flush()

    exp = Experiment(
        organization_id=org_id,
        name="Pipe Exp",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="cpsam",
        status="complete",
        submitted_by_user_id=launcher.id,
    )
    session.add(run)
    await session.flush()

    f = File(
        organization_id=org_id,
        gcs_uri="gs://b/pipe.txt",
        filename="pipe.txt",
        file_type="txt",
        uploader_user_id=launcher.id,  # set by pipeline_output_service
        experiment_id=exp.id,
        source_type="pipeline_output",
        source_pipeline_run_id=run.id,
    )
    session.add(f)
    await session.flush()
    await session.commit()

    prov_map = await FileService.get_provenance_for_files(session, [f])
    prov = prov_map[f.id]

    assert prov["experiment_name"] == "Pipe Exp"
    assert prov["pipeline_run"]["id"] == run.id
    assert prov["pipeline_run"]["pipeline_name"] == "cpsam"
    assert prov["pipeline_run"]["launcher"]["email"] == "launcher@test.com"
    assert prov["creator"]["email"] == "launcher@test.com"


@pytest.mark.asyncio
async def test_provenance_for_compute_sessions(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.notebook_session import ComputeSession

    org_id = admin_user.organization_id

    exp = Experiment(
        organization_id=org_id,
        name="Notebook Exp",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    rstudio = ComputeSession(
        user_id=admin_user.id,
        organization_id=org_id,
        session_type="rstudio",
        experiment_id=exp.id,
        resource_profile="small",
        cpu_cores=1,
        memory_gb=4,
        status="running",
    )
    work_node = ComputeSession(
        user_id=admin_user.id,
        organization_id=org_id,
        session_type="ssh",
        experiment_id=exp.id,
        resource_profile="medium",
        cpu_cores=2,
        memory_gb=8,
        status="running",
    )
    session.add_all([rstudio, work_node])
    await session.flush()

    f_nb = File(
        organization_id=org_id,
        gcs_uri="gs://b/nb.html",
        filename="nb.html",
        file_type="html",
        experiment_id=exp.id,
        source_type="notebook_output",
        source_notebook_session_id=rstudio.id,
    )
    f_wn = File(
        organization_id=org_id,
        gcs_uri="gs://b/wn.txt",
        filename="wn.txt",
        file_type="txt",
        experiment_id=exp.id,
        source_type="notebook_output",
        source_notebook_session_id=work_node.id,
    )
    session.add_all([f_nb, f_wn])
    await session.flush()
    await session.commit()

    prov_map = await FileService.get_provenance_for_files(session, [f_nb, f_wn])

    nb = prov_map[f_nb.id]["compute_session"]
    assert nb["kind"] == "notebook"
    assert nb["notebook_type"] == "rstudio"
    assert nb["launcher"]["id"] == admin_user.id

    wn = prov_map[f_wn.id]["compute_session"]
    assert wn["kind"] == "work_node"
    assert wn["notebook_type"] is None
    assert wn["launcher"]["id"] == admin_user.id
