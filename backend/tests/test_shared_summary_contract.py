"""Equivalent summary schemas share one definition; distinct ones stay distinct.

A project link (id, name) was declared three times and an experiment link twice, and the
review API's ReviewerSummary repeated UserSummary field for field. Each now has one class,
re-exported under the old import paths. The project-detail ExperimentSummary carries status,
sample count and creation time, so it stays its own contract. Wire fields do not change; only
the OpenAPI component names do (no more ``app__schemas__..._ExperimentSummary``).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.schemas import experiment, notebook_session, pipeline_run, pipeline_run_review, project, work_node
from app.schemas.entity_reference import ExperimentReference, ProjectSummary
from app.schemas.user_summary import UserSummary


def test_each_link_has_one_definition_under_every_old_import_path():
    assert experiment.ProjectSummary is ProjectSummary
    assert notebook_session.ProjectSummary is ProjectSummary
    assert work_node.ProjectSummary is ProjectSummary
    assert notebook_session.ExperimentSummary is ExperimentReference
    assert pipeline_run.ExperimentSummary is ExperimentReference
    assert pipeline_run_review.ReviewerSummary is UserSummary


def test_the_project_detail_experiment_summary_stays_distinct():
    assert project.ExperimentSummary is not ExperimentReference
    rich = project.ExperimentSummary(id=1, name="Atlas", status="registered")
    assert rich.model_dump() == {
        "id": 1,
        "name": "Atlas",
        "status": "registered",
        "sample_count": 0,
        "created_at": None,
    }


@pytest.mark.parametrize("model", [ProjectSummary, ExperimentReference])
def test_a_link_serializes_id_and_name_only_from_kwargs_or_attributes(model):
    assert model(id=3, name="Liver").model_dump() == {"id": 3, "name": "Liver"}
    row = SimpleNamespace(id=3, name="Liver", status="ignored", organization_id=9)
    assert model.model_validate(row).model_dump() == {"id": 3, "name": "Liver"}


def test_a_reviewer_keeps_the_user_summary_fields_and_nullable_name():
    reviewer = pipeline_run_review.ReviewerSummary(id=2, name=None, email="r@lab.test")
    assert reviewer.model_dump() == {"id": 2, "name": None, "email": "r@lab.test"}
    response = pipeline_run_review.PipelineRunReviewResponse(
        id=1,
        pipeline_run_id=4,
        reviewer=reviewer,
        verdict="approved",
        notes=None,
        sample_verdicts_json=None,
        recommended_exclusions=None,
        reviewed_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
        is_active=True,
        created_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
    )
    assert response.model_dump()["reviewer"] == {"id": 2, "name": None, "email": "r@lab.test"}


def test_the_internal_schema_names_each_contract_once():
    from app.main import create_app

    components = create_app().openapi()["components"]["schemas"]
    assert components["ExperimentReference"]["required"] == ["id", "name"]
    assert set(components["ExperimentSummary"]["properties"]) == {"id", "name", "status", "sample_count", "created_at"}
    assert set(components["ProjectSummary"]["properties"]) == {"id", "name"}
    assert "ReviewerSummary" not in components
    assert not [name for name in components if name.startswith("app__schemas__") and "Summary" in name]
