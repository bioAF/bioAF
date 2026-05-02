"""Tests for CustomPipelineService.launch_run (spec-launch-orchestration.md)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text as sa_text

from app.models.audit_log import AuditLog
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.file import File
from app.models.github_repo import GitHubRepo
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.project import Project
from app.models.session_credential import SessionCredential
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineLaunchRequest,
    CustomPipelineVariableDefinition,
    CustomPipelineVariableValue,
    CustomPipelineVersionCreateRequest,
)
from app.services.custom_pipeline_service import CustomPipelineService
from app.services.event_bus import event_bus


@pytest_asyncio.fixture(autouse=True)
def _clear_event_bus():
    event_bus.clear()
    yield
    event_bus.clear()


@pytest_asyncio.fixture(autouse=True)
async def _platform_config(session):
    """Provide a results bucket name so the entrypoint wrapper has a sync target."""
    await session.execute(
        sa_text(
            "INSERT INTO platform_config (key, value) VALUES ('results_bucket_name', 'bioaf-results-test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


@pytest_asyncio.fixture
async def ready_env_version(session, admin_user):
    env = Environment(
        name="Launch Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        image_uri="us-central1-docker.pkg.dev/test/bioaf/pipeline-launch:v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest_asyncio.fixture
async def github_repo(session, admin_user):
    repo = GitHubRepo(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        git_ssh_url="git@github.com:example/myrepo.git",
        display_name="myrepo",
    )
    session.add(repo)
    await session.flush()
    await session.commit()
    return repo


@pytest_asyncio.fixture
async def session_creds(session, admin_user):
    cred = SessionCredential(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        username="adminuser",
        password_hash="hash",
        ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nFAKEKEY\n-----END OPENSSH PRIVATE KEY-----",
    )
    session.add(cred)
    await session.flush()
    await session.commit()
    return cred


@pytest_asyncio.fixture
async def project(session, admin_user):
    p = Project(
        organization_id=admin_user.organization_id,
        name="Launch Project",
        owner_user_id=admin_user.id,
    )
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest_asyncio.fixture
async def experiment(session, admin_user, project):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="Launch Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def input_files(session, admin_user, project, experiment):
    f1 = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-raw-test/uploads/sample1_R1.fastq.gz",
        filename="sample1_R1.fastq.gz",
        file_type="fastq",
        project_id=project.id,
        experiment_id=experiment.id,
        source_type="upload",
    )
    f2 = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-raw-test/uploads/sample1_R2.fastq.gz",
        filename="sample1_R2.fastq.gz",
        file_type="fastq",
        project_id=project.id,
        experiment_id=experiment.id,
        source_type="upload",
    )
    session.add_all([f1, f2])
    await session.flush()
    await session.commit()
    return [f1, f2]


async def _create_pipeline_with_version(
    session,
    admin_user,
    env_version_id,
    *,
    code_source_type: str = "inline",
    code_content: str | None = "print('hello')",
    github_repo_id: int | None = None,
    entrypoint_command: str = "python /code/script.py",
    variables: list[CustomPipelineVariableDefinition] | None = None,
):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name=f"Pipeline {code_source_type}"),
    )
    version = await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type=code_source_type,
            code_content=code_content if code_source_type != "github_repo" else None,
            github_repo_id=github_repo_id if code_source_type == "github_repo" else None,
            entrypoint_command=entrypoint_command,
            environment_version_id=env_version_id,
            cpu_request="2",
            memory_request="4Gi",
            variables=variables or [],
        ),
    )
    await session.commit()
    return pipeline, version


def _mock_compute_adapter():
    captured: dict = {}

    async def capture_submit(job_spec):
        captured["job_spec"] = job_spec
        return {
            "job_id": "bioaf-pipeline-test-123",
            "namespace": "bioaf-pipelines",
            "status": "queued",
            "estimated_cost": {"estimated_cost_usd": 0.25},
        }

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=capture_submit)
    return adapter, captured


# --- Source type tests ---


@pytest.mark.asyncio
async def test_launch_inline_source(session, admin_user, ready_env_version, project, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(
        session, admin_user, ready_env_version.id, code_source_type="inline"
    )
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()

    spec = captured["job_spec"]
    assert spec["working_dir"] == "/data"
    assert spec["has_code_dir"] is False
    assert spec["has_outputs_dir"] is True
    init_names = [ic["name"] for ic in spec["extra_init_containers"]]
    assert "write-manifest" in init_names
    assert "write-params" in init_names
    assert "clone-repo" not in init_names
    assert "write-code" not in init_names
    assert spec["ssh_private_key"] is None
    assert run.status == "running"
    assert run.custom_pipeline_version_id == version.id


@pytest.mark.asyncio
async def test_launch_code_blob_source(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(
        session,
        admin_user,
        ready_env_version.id,
        code_source_type="code_blob",
        code_content="echo hi",
    )
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()

    spec = captured["job_spec"]
    assert spec["working_dir"] == "/code"
    assert spec["has_code_dir"] is True
    init_names = [ic["name"] for ic in spec["extra_init_containers"]]
    assert "write-code" in init_names
    write_code = next(ic for ic in spec["extra_init_containers"] if ic["name"] == "write-code")
    assert "echo hi" in write_code["command"][2]


@pytest.mark.asyncio
async def test_launch_github_repo_source(
    session, admin_user, ready_env_version, github_repo, session_creds, experiment, input_files
):
    pipeline, version = await _create_pipeline_with_version(
        session,
        admin_user,
        ready_env_version.id,
        code_source_type="github_repo",
        github_repo_id=github_repo.id,
    )
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()

    spec = captured["job_spec"]
    assert spec["working_dir"] == f"/code/{github_repo.display_name}"
    assert spec["has_code_dir"] is True
    assert spec["ssh_private_key"] == session_creds.ssh_private_key
    init_names = [ic["name"] for ic in spec["extra_init_containers"]]
    assert "clone-repo" in init_names
    clone = next(ic for ic in spec["extra_init_containers"] if ic["name"] == "clone-repo")
    assert github_repo.git_ssh_url in clone["command"][2]


@pytest.mark.asyncio
async def test_launch_github_repo_missing_ssh_key(
    session, admin_user, ready_env_version, github_repo, experiment, input_files
):
    pipeline, version = await _create_pipeline_with_version(
        session,
        admin_user,
        ready_env_version.id,
        code_source_type="github_repo",
        github_repo_id=github_repo.id,
    )
    adapter, _ = _mock_compute_adapter()

    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValueError, match="No SSH private key"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id,
                    experiment_id=experiment.id,
                    input_file_ids=[f.id for f in input_files],
                ),
            )


# --- Manifest / params / staging ---


@pytest.mark.asyncio
async def test_manifest_contains_file_mappings(
    session, admin_user, ready_env_version, project, experiment, input_files
):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()

    spec = captured["job_spec"]
    manifest_init = next(ic for ic in spec["extra_init_containers"] if ic["name"] == "write-manifest")
    cmd = manifest_init["command"][2]
    parts = cmd.split("'")
    raw_json = parts[3].replace("'\\''", "'")
    manifest = json.loads(raw_json)
    assert manifest["pipeline_run_id"] == run.id
    assert len(manifest["files"]) == 2
    file_names = {f["filename"] for f in manifest["files"]}
    assert file_names == {"sample1_R1.fastq.gz", "sample1_R2.fastq.gz"}
    for entry in manifest["files"]:
        assert entry["project_id"] == project.id
        assert entry["experiment_id"] == experiment.id
        assert entry["relative_path"]


@pytest.mark.asyncio
async def test_stage_commands_use_relative_paths(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )

    spec = captured["job_spec"]
    assert spec["stage_commands"][0].startswith("gcloud auth activate-service-account")
    cp_commands = spec["stage_commands"][1:]
    assert len(cp_commands) == 2
    for cmd in cp_commands:
        assert cmd.startswith("mkdir -p /data/")
        assert "gcloud storage cp" in cmd


# --- Variable resolution ---


@pytest.mark.asyncio
async def test_variables_use_defaults_and_overrides(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(
        session,
        admin_user,
        ready_env_version.id,
        variables=[
            CustomPipelineVariableDefinition(
                variable_name="threshold",
                default_value="0.5",
                variable_type="number",
                is_required=True,
            ),
            CustomPipelineVariableDefinition(
                variable_name="mode",
                default_value="fast",
                variable_type="string",
                is_required=False,
            ),
        ],
    )
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
                variables=[
                    CustomPipelineVariableValue(variable_name="threshold", variable_value="0.9"),
                ],
            ),
        )
        await session.commit()

    assert run.parameters_json == {"threshold": "0.9", "mode": "fast"}
    spec = captured["job_spec"]
    env_pairs = {e["name"]: e["value"] for e in spec["extra_env"]}
    assert env_pairs["PARAM_THRESHOLD"] == "0.9"
    assert env_pairs["PARAM_MODE"] == "fast"


@pytest.mark.asyncio
async def test_required_variable_missing(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(
        session,
        admin_user,
        ready_env_version.id,
        variables=[
            CustomPipelineVariableDefinition(
                variable_name="must_have",
                variable_type="string",
                is_required=True,
            ),
        ],
    )
    adapter, _ = _mock_compute_adapter()

    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValueError, match="Required variable missing"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id,
                    experiment_id=experiment.id,
                    input_file_ids=[f.id for f in input_files],
                ),
            )


@pytest.mark.asyncio
async def test_unknown_variable_rejected(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _ = _mock_compute_adapter()

    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValueError, match="Unknown variable"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id,
                    experiment_id=experiment.id,
                    input_file_ids=[f.id for f in input_files],
                    variables=[
                        CustomPipelineVariableValue(variable_name="not_defined", variable_value="x"),
                    ],
                ),
            )


@pytest.mark.asyncio
async def test_number_variable_type_check(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(
        session,
        admin_user,
        ready_env_version.id,
        variables=[
            CustomPipelineVariableDefinition(
                variable_name="threshold",
                variable_type="number",
                is_required=True,
            ),
        ],
    )
    adapter, _ = _mock_compute_adapter()

    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValueError, match="must be a number"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id,
                    experiment_id=experiment.id,
                    input_file_ids=[f.id for f in input_files],
                    variables=[
                        CustomPipelineVariableValue(variable_name="threshold", variable_value="abc"),
                    ],
                ),
            )


# --- PipelineRun creation, input file linkage ---


@pytest.mark.asyncio
async def test_pipeline_run_fields(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _ = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()

    db_run = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert db_run.organization_id == admin_user.organization_id
    assert db_run.submitted_by_user_id == admin_user.id
    assert db_run.experiment_id == experiment.id
    assert db_run.custom_pipeline_version_id == version.id
    assert db_run.pipeline_name == pipeline.name
    assert db_run.pipeline_version == "1"
    assert db_run.status == "running"
    assert db_run.k8s_job_name == "bioaf-pipeline-test-123"

    input_link_count = (
        (await session.execute(select(PipelineRunInputFile).where(PipelineRunInputFile.pipeline_run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(input_link_count) == 2


# --- Experiment status advancement ---


@pytest.mark.asyncio
async def test_experiment_status_advances_when_experiment_set(
    session, admin_user, ready_env_version, experiment, input_files
):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _ = _mock_compute_adapter()
    update_mock = AsyncMock()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", update_mock),
    ):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )

    update_mock.assert_called_once()
    args = update_mock.call_args
    assert args.args[1] == experiment.id
    assert args.args[2] == admin_user.organization_id
    assert args.args[4] == "processing"


@pytest.mark.asyncio
async def test_experiment_status_unchanged_when_only_project(
    session, admin_user, ready_env_version, project, input_files
):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, captured = _mock_compute_adapter()
    update_mock = AsyncMock()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", update_mock),
    ):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                project_id=project.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )

    update_mock.assert_not_called()
    spec = captured["job_spec"]
    # Output prefix should target projects, not experiments
    wrapped = spec["command"][2]
    assert f"gs://bioaf-results-test/projects/{project.id}/pipeline-runs/" in wrapped
    assert "experiments/" not in wrapped


@pytest.mark.asyncio
async def test_experiment_scoped_output_path(session, admin_user, ready_env_version, experiment, input_files):
    """Experiment-scoped runs use the experiments/{id}/pipeline-runs/ GCS prefix."""
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, captured = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", AsyncMock()),
    ):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )

    spec = captured["job_spec"]
    wrapped = spec["command"][2]
    assert f"gs://bioaf-results-test/experiments/{experiment.id}/pipeline-runs/" in wrapped
    assert "projects/" not in wrapped


# --- Validation guards ---


@pytest.mark.asyncio
async def test_deprecated_version_rejected(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    await CustomPipelineService.deprecate_version(
        session, admin_user.organization_id, admin_user.id, pipeline.id, version.id
    )
    await session.commit()

    adapter, _ = _mock_compute_adapter()
    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValueError, match="active"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id,
                    experiment_id=experiment.id,
                    input_file_ids=[f.id for f in input_files],
                ),
            )


@pytest.mark.asyncio
async def test_other_org_version_rejected(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _ = _mock_compute_adapter()

    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValueError, match="organization"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id + 999,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id,
                    experiment_id=experiment.id,
                    input_file_ids=[f.id for f in input_files],
                ),
            )


@pytest.mark.asyncio
async def test_no_target_rejected(session, admin_user, ready_env_version):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    with pytest.raises(ValueError, match="experiment_id or project_id"):
        await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                input_file_ids=[],
            ),
        )


# --- Audit log ---


@pytest.mark.asyncio
async def test_audit_log_written(session, admin_user, ready_env_version, experiment, input_files):
    pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _ = _mock_compute_adapter()

    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                input_file_ids=[f.id for f in input_files],
            ),
        )
        await session.commit()

    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "pipeline_run",
                AuditLog.entity_id == run.id,
                AuditLog.action == "launch",
            )
        )
    ).scalar_one()
    assert audit.user_id == admin_user.id
    assert audit.details_json["custom_pipeline_version_id"] == version.id
