"""A custom pipeline version's variables each need their own name.

Two definitions with one name cannot both reach the job: it gets one PARAM_<NAME> and one
params.json key. Seen live on blackbird, where the version form let a copied variable be added
again and the launch then sent the value twice.
"""

# ruff: noqa: F811  (fixtures imported from test_custom_pipeline_launch are used as parameters)

import pytest
from sqlalchemy import func, select

from app.exceptions import ValidationError
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineVariableDefinition,
    CustomPipelineVersionCreateRequest,
)
from app.services.custom_pipeline_service import CustomPipelineService
from tests.test_custom_pipeline_launch import _platform_config, ready_env_version  # noqa: F401  (fixtures)


@pytest.mark.asyncio
async def test_a_version_with_two_variables_of_one_name_is_refused(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session, admin_user.organization_id, admin_user.id, CustomPipelineCreateRequest(name="Dupes")
    )
    before = (await session.execute(select(func.count()).select_from(CustomPipelineVersion))).scalar_one()

    with pytest.raises(ValidationError) as refused:
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
                variables=[
                    CustomPipelineVariableDefinition(
                        variable_name="greeting", default_value="a", variable_type="string"
                    ),
                    CustomPipelineVariableDefinition(
                        variable_name="greeting", default_value="b", variable_type="string"
                    ),
                ],
            ),
        )

    assert "greeting" in str(refused.value)
    assert (await session.execute(select(func.count()).select_from(CustomPipelineVersion))).scalar_one() == before
