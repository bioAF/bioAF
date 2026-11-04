"""A custom pipeline's Job must fit the node pool it is scheduled on, or it is refused up front.

Every custom Job pod is pinned to the pipeline-head pool. A request that pool cannot schedule is
not slow, it is Pending forever while the run reports ``running`` (seen live on blackbird: a version
left at the form's old 2 CPU / 8Gi default never ran). So a version is refused when it is created,
and an older version that asks for too much is refused at launch and at reproduction, before any
run is created or any job submitted.
"""

# ruff: noqa: F811  (fixtures imported from test_custom_pipeline_launch are used as parameters)

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.exceptions import ValidationError
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.pipeline_run import PipelineRun
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineLaunchRequest,
    CustomPipelineVersionCreateRequest,
)
from app.services.custom_pipeline_resources import (
    HEAD_POOL_MACHINE_TYPES,
    check_fits_head_pool,
)
from app.services.custom_pipeline_service import CustomPipelineService
from app.services.pipeline_run_service import PipelineRunService
from tests.test_custom_pipeline_launch import (  # noqa: F401  (fixtures)
    _create_pipeline_with_version,
    _mock_compute_adapter,
    _platform_config,
    experiment,
    input_files,
    project,
    ready_env_version,
)

_TERRAFORM = Path(__file__).resolve().parent.parent / "terraform"


def test_new_versions_default_to_a_request_the_head_pool_can_schedule():
    request = CustomPipelineVersionCreateRequest(
        code_source_type="inline", code_content="x", entrypoint_command="x", environment_version_id=1
    )
    assert (request.cpu_request, request.memory_request) == ("1", "4Gi")
    check_fits_head_pool(request.cpu_request, request.memory_request)


@pytest.mark.parametrize("cpu,memory", [("1", "4Gi"), ("1400m", "5Gi"), ("1.4", "5120Mi"), ("500m", "1Gi")])
def test_requests_within_the_head_pool_fit(cpu, memory):
    check_fits_head_pool(cpu, memory)


@pytest.mark.parametrize(
    "cpu,memory",
    [("2", "4Gi"), ("1401m", "1Gi"), ("1", "5121Mi"), ("1", "8Gi"), ("1", "6G"), ("4", "16Gi")],
)
def test_requests_beyond_the_head_pool_are_refused_in_plain_words(cpu, memory):
    with pytest.raises(ValidationError) as refused:
        check_fits_head_pool(cpu, memory)
    message = str(refused.value)
    assert "at most 1.4 CPU and 5Gi" in message
    assert "cpu_request" not in message and "memory_request" not in message


@pytest.mark.parametrize("cpu,memory", [("two", "1Gi"), ("1", "lots"), ("", "1Gi"), ("1", "")])
def test_unreadable_requests_are_refused_in_plain_words(cpu, memory):
    with pytest.raises(ValidationError) as refused:
        check_fits_head_pool(cpu, memory)
    assert "cpu_request" not in str(refused.value) and "memory_request" not in str(refused.value)


def _terraform_default(path: Path, variable: str) -> str:
    block = re.search(r'variable "' + variable + r'" \{(.*?)\n\}', path.read_text(), re.S)
    assert block, f"{variable} not found in {path}"
    return re.search(r'default\s*=\s*"([^"]+)"', block.group(1)).group(1)


def test_the_limit_is_tied_to_the_head_pool_machine_types_terraform_deploys():
    """The limit was measured on these machine types; changing either one must revisit it."""
    assert HEAD_POOL_MACHINE_TYPES == {
        "gcp": _terraform_default(_TERRAFORM / "modules/compute/variables.tf", "k8s_pipeline_head_machine_type"),
        "aws": _terraform_default(_TERRAFORM / "aws/modules/compute/variables.tf", "pipeline_head_instance_type"),
    }


async def _version_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(CustomPipelineVersion))).scalar_one()


@pytest.mark.asyncio
async def test_creating_a_version_that_cannot_be_scheduled_is_refused(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session, admin_user.organization_id, admin_user.id, CustomPipelineCreateRequest(name="Too Big")
    )
    before = await _version_count(session)

    with pytest.raises(ValidationError, match="at most 1.4 CPU and 5Gi"):
        await CustomPipelineService.create_version(
            session,
            admin_user.organization_id,
            admin_user.id,
            pipeline.id,
            CustomPipelineVersionCreateRequest(
                code_source_type="inline",
                code_content="x",
                entrypoint_command="x",
                environment_version_id=ready_env_version.id,
                cpu_request="2",
                memory_request="8Gi",
            ),
        )

    assert await _version_count(session) == before


async def _oversize(session, version) -> None:
    """Make ``version`` look like one created before the limit existed."""
    version.cpu_request = "2"
    version.memory_request = "8Gi"
    await session.commit()


@pytest.mark.asyncio
async def test_launching_an_older_version_that_cannot_be_scheduled_is_refused_before_any_run(
    session, admin_user, ready_env_version, experiment, input_files
):
    _pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    await _oversize(session, version)
    adapter, _captured = _mock_compute_adapter()

    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        with pytest.raises(ValidationError, match="at most 1.4 CPU and 5Gi"):
            await CustomPipelineService.launch_run(
                session,
                admin_user.organization_id,
                admin_user.id,
                CustomPipelineLaunchRequest(
                    version_id=version.id, experiment_id=experiment.id, input_file_ids=[f.id for f in input_files]
                ),
            )

    await session.rollback()
    assert (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 0
    adapter.submit_job.assert_not_called()


@pytest.mark.asyncio
async def test_reproducing_a_run_whose_version_cannot_be_scheduled_is_refused_before_any_run(
    session, admin_user, ready_env_version, experiment, input_files
):
    _pipeline, version = await _create_pipeline_with_version(session, admin_user, ready_env_version.id)
    adapter, _captured = _mock_compute_adapter()
    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        original = await CustomPipelineService.launch_run(
            session,
            admin_user.organization_id,
            admin_user.id,
            CustomPipelineLaunchRequest(
                version_id=version.id, experiment_id=experiment.id, input_file_ids=[f.id for f in input_files]
            ),
        )
        original.status = "completed"
        await session.commit()
        original_id = original.id
        await _oversize(session, version)
        adapter.submit_job.reset_mock()

        with pytest.raises(ValidationError) as refused:
            await PipelineRunService.reproduce_run(
                session, original_id, admin_user.organization_id, admin_user.id, allow_custom=True
            )

    assert str(refused.value).startswith("This run cannot be reproduced:")
    assert "at most 1.4 CPU and 5Gi" in str(refused.value)
    await session.rollback()
    assert (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 1
    adapter.submit_job.assert_not_called()
