"""TDD: custom-pipeline `reference` parameter type — spec §5.

The custom pipeline editor needs a new variable_type='reference' that, when
launched, renders a searchable dropdown filtered by reference_category.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion


@pytest_asyncio.fixture
async def admin_test(client, admin_user, admin_token, session):
    """Bootstrap a custom pipeline + version row we can attach variables to."""
    pipeline = CustomPipeline(
        organization_id=admin_user.organization_id,
        pipeline_key="ref-param-test",
        name="Ref Param Test",
        created_by_user_id=admin_user.id,
    )
    session.add(pipeline)
    await session.flush()

    # find an environment_version_id; just use any existing — tests can fake
    # by inserting a minimal env_version row.
    await session.execute(
        text(
            "INSERT INTO environments (id, organization_id, name, created_by_user_id, created_at) "
            "VALUES (1, :oid, 'env', :uid, NOW()) ON CONFLICT DO NOTHING"
        ),
        {"oid": admin_user.organization_id, "uid": admin_user.id},
    )
    await session.execute(
        text(
            "INSERT INTO environment_versions "
            "(id, environment_id, version_number, build_number, status, "
            " definition_format, definition_content, created_by_user_id, created_at) "
            "VALUES (1, 1, 1, 1, 'built', 'conda', '{}', :uid, NOW()) ON CONFLICT DO NOTHING"
        ),
        {"uid": admin_user.id},
    )
    await session.commit()

    return pipeline


@pytest.mark.asyncio
async def test_custom_pipeline_variable_can_persist_reference_type(session, admin_test):
    """Schema accepts variable_type='reference' with a reference_category."""
    pipeline = admin_test

    user_id = (await session.execute(text("SELECT id FROM users LIMIT 1"))).scalar_one()
    version = CustomPipelineVersion(
        custom_pipeline_id=pipeline.id,
        version_number=1,
        code_source_type="inline",
        entrypoint_command="echo hi",
        environment_version_id=1,
        cpu_request="1",
        memory_request="1Gi",
        version_trigger="manual",
        status="ready",
        created_by_user_id=user_id,
    )
    session.add(version)
    await session.flush()

    var = CustomPipelineVariable(
        custom_pipeline_version_id=version.id,
        variable_name="genome_ref",
        variable_type="reference",
        reference_category="genome",
        is_required=True,
    )
    session.add(var)
    await session.flush()

    result = await session.execute(
        select(CustomPipelineVariable).where(CustomPipelineVariable.custom_pipeline_version_id == version.id)
    )
    fetched = result.scalar_one()
    assert fetched.variable_type == "reference"
    assert fetched.reference_category == "genome"


def test_variable_definition_schema_accepts_reference_type():
    """Pydantic schema must accept variable_type='reference' with category."""
    from app.schemas.custom_pipeline import CustomPipelineVariableDefinition

    payload = CustomPipelineVariableDefinition(
        variable_name="genome_ref",
        variable_type="reference",
        reference_category="genome",
        is_required=True,
    )
    assert payload.variable_type == "reference"
    assert payload.reference_category == "genome"


def test_variable_definition_rejects_reference_without_category():
    """Reference type must specify a reference_category."""
    from app.schemas.custom_pipeline import CustomPipelineVariableDefinition
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="reference_category"):
        CustomPipelineVariableDefinition(
            variable_name="genome_ref",
            variable_type="reference",
            is_required=True,
        )


def test_variable_definition_rejects_unknown_category():
    """reference_category must be one of the known reference categories or 'any'."""
    from app.schemas.custom_pipeline import CustomPipelineVariableDefinition
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CustomPipelineVariableDefinition(
            variable_name="genome_ref",
            variable_type="reference",
            reference_category="not_a_real_category",
        )
