"""Complete HTTP error bodies for the services whose failures became semantic errors.

Eight service modules used to raise ``HTTPException``. They now raise domain
errors, and every route that reaches them must answer exactly as before:
the same status and the same ``{"detail": ...}`` body, with the ambiguous
sample-ID metadata still nested under ``detail`` and the deliberate 422s
still 422. Already-semantic routes keep their ``code`` envelope.
"""

import pytest
import pytest_asyncio

from app.models.controlled_vocabulary import ControlledVocabulary
from app.models.experiment import Experiment
from app.models.experiment_template import ExperimentTemplate
from app.models.project import Project
from app.models.reproduction_plan import ReproductionPlan
from app.models.sample import Sample
from app.services import vocabulary_validator
from app.services.pipeline_mapper import library_strategy_conflict
from app.services.validation_study_service import ValidationStudyService
from tests.test_relabeled_samples import _refused, _relabelled_experiment


def _body(response) -> tuple[int, object]:
    return response.status_code, response.json()


@pytest.fixture
def headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def experiment(session, admin_user) -> Experiment:
    exp = Experiment(organization_id=admin_user.organization_id, name="Errors", owner_user_id=admin_user.id)
    session.add(exp)
    await session.commit()
    return exp


# --- sample_service and vocabulary_validator ----------------------------------------------


@pytest.mark.asyncio
async def test_an_ambiguous_label_keeps_its_nested_409_on_every_path(
    client, session, admin_user, headers, integration_api_key
):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    expected = (409, {"detail": _refused(data["ids"])})
    exp = data["experiment"]
    assert _body(
        await client.post(f"/api/experiments/{exp}/samples", headers=headers, json={"external_id": "DUP"})
    ) == (expected)
    assert (
        _body(
            await client.post(
                f"/api/experiments/{exp}/samples/bulk", headers=headers, json={"samples": [{"external_id": "DUP"}]}
            )
        )
        == expected
    )
    assert (
        _body(await client.patch(f"/api/samples/{data['plain']}", headers=headers, json={"external_id": "DUP"}))
        == expected
    )
    integration = await client.post(
        "/api/v1/integrations/samples",
        headers=integration_api_key["headers"],
        json={"experiment_id": exp, "external_id": "DUP"},
    )
    assert _body(integration) == expected


@pytest.mark.asyncio
async def test_a_template_required_field_answers_400(client, session, admin_user, headers):
    template = ExperimentTemplate(
        organization_id=admin_user.organization_id,
        name="Needs organism",
        required_fields_json={"sample_fields": ["organism"]},
        created_by_user_id=admin_user.id,
    )
    session.add(template)
    await session.flush()
    exp = Experiment(organization_id=admin_user.organization_id, name="Templated", template_id=template.id)
    session.add(exp)
    await session.commit()
    one = await client.post(f"/api/experiments/{exp.id}/samples", headers=headers, json={"external_id": "S1"})
    assert _body(one) == (400, {"detail": "Required field 'organism' is missing or empty"})
    bulk = await client.post(
        f"/api/experiments/{exp.id}/samples/bulk",
        headers=headers,
        json={"samples": [{"external_id": "S1", "organism": "x"}, {"external_id": "S2"}]},
    )
    assert _body(bulk) == (400, {"detail": "Sample 2: Required field 'organism' is missing or empty"})


@pytest.mark.asyncio
async def test_a_value_outside_the_vocabulary_answers_422(client, session, headers, experiment):
    session.add(ControlledVocabulary(field_name="library_layout", allowed_value="paired"))
    session.add(ControlledVocabulary(field_name="library_layout", allowed_value="single"))
    await session.commit()
    vocabulary_validator.invalidate_cache()
    try:
        response = await client.post(
            f"/api/experiments/{experiment.id}/samples",
            headers=headers,
            json={"external_id": "V1", "library_layout": "triple"},
        )
    finally:
        vocabulary_validator.invalidate_cache()
    assert _body(response) == (
        422,
        {"detail": "Invalid value 'triple' for field 'library_layout'. Allowed values: paired, single"},
    )


@pytest.mark.asyncio
async def test_an_invalid_sample_status_step_answers_400(client, session, headers, experiment):
    sample = Sample(experiment_id=experiment.id, external_id="ST", status="registered")
    session.add(sample)
    await session.commit()
    response = await client.patch(f"/api/samples/{sample.id}/status", headers=headers, json={"status": "sequenced"})
    assert _body(response) == (
        400,
        {"detail": "Cannot transition from 'registered' to 'sequenced'. Next valid status: library_prepped."},
    )


@pytest.mark.asyncio
async def test_an_import_reports_a_vocabulary_failure_as_a_row_error(client, session, headers, experiment):
    import io

    session.add(ControlledVocabulary(field_name="library_layout", allowed_value="paired"))
    await session.commit()
    vocabulary_validator.invalidate_cache()
    try:
        response = await client.post(
            f"/api/experiments/{experiment.id}/samples/upload",
            headers=headers,
            files={"file": ("s.csv", io.BytesIO(b"external_id,library_layout\nA,paired\nB,triple\n"), "text/csv")},
        )
    finally:
        vocabulary_validator.invalidate_cache()
    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    assert response.json()["errors"] == [
        "Sample 2: Invalid value 'triple' for field 'library_layout'. Allowed values: paired"
    ]


# --- experiment_service -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_invalid_experiment_status_step_answers_400(client, headers, experiment):
    response = await client.patch(
        f"/api/experiments/{experiment.id}/status", headers=headers, json={"status": "complete"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": response.json()["detail"]}
    assert response.json()["detail"].startswith("Cannot transition from 'registered' to 'complete'. Next valid status:")


@pytest.mark.asyncio
async def test_a_status_step_on_a_missing_experiment_answers_404(client, headers):
    response = await client.patch("/api/experiments/999999/status", headers=headers, json={"status": "complete"})
    assert _body(response) == (404, {"detail": "Experiment not found"})


# --- project_service ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def project(session, admin_user) -> Project:
    p = Project(organization_id=admin_user.organization_id, name="P", code="P1", owner_user_id=admin_user.id)
    session.add(p)
    await session.commit()
    return p


@pytest.mark.asyncio
async def test_project_sample_errors_keep_their_bodies(client, session, headers, experiment, project):
    sample = Sample(experiment_id=experiment.id, external_id="PS", status="registered")
    session.add(sample)
    await session.commit()
    missing = await client.post(f"/api/projects/{project.id}/samples", headers=headers, json={"sample_ids": [999998]})
    assert _body(missing) == (404, {"detail": "Samples not found: [999998]"})
    created = await client.post(
        "/api/projects", headers=headers, json={"name": "With samples", "sample_ids": [sample.id, 999997]}
    )
    assert _body(created) == (404, {"detail": "Samples not found: [999997]"})
    added = await client.post(f"/api/projects/{project.id}/samples", headers=headers, json={"sample_ids": [sample.id]})
    assert added.status_code == 200
    again = await client.post(f"/api/projects/{project.id}/samples", headers=headers, json={"sample_ids": [sample.id]})
    assert _body(again) == (409, {"detail": f"Samples already in project: [{sample.id}]"})
    removed = await client.delete(f"/api/projects/{project.id}/samples/999996", headers=headers)
    assert _body(removed) == (404, {"detail": "Sample not in project"})


# --- snapshot_service ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_errors_keep_their_bodies_and_422s(client, headers):
    neither = await client.post("/api/snapshots", headers=headers, json={"label": "x", "object_type": "anndata"})
    assert _body(neither) == (422, {"detail": "At least one of experiment_id or project_id is required"})
    foreign = await client.post(
        "/api/snapshots", headers=headers, json={"label": "x", "object_type": "anndata", "experiment_id": 999999}
    )
    assert _body(foreign) == (404, {"detail": "Experiment not found"})
    one = await client.get("/api/snapshots/compare?ids=1", headers=headers)
    assert _body(one) == (422, {"detail": "Must compare between 2 and 5 snapshots"})
    missing = await client.get("/api/snapshots/compare?ids=999998,999999", headers=headers)
    assert _body(missing) == (404, {"detail": "Snapshots not found: [999998, 999999]"})


# --- validation_study_service and reproduction_plan_service --------------------------------


@pytest_asyncio.fixture
async def lit_validation(session):
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


async def _study(session, admin_user, *, state: str, plan: dict | None = None):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1234/x", source_accession="GSE1"
    )
    study.state = state
    await session.flush()
    if plan is not None:
        row = ReproductionPlan(validation_study_id=study.id, accessions_json=["GSE1"], **plan)
        session.add(row)
        await session.flush()
        study.reproduction_plan_id = row.id
    await session.commit()
    return study.id


BISULFITE = {
    "pipeline_key": "nf-core/atacseq",
    "pipeline_version": "2.1.2",
    "blockers_json": [library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")],
    "library_strategy": "Bisulfite-Seq",
}
UNROUTED = {
    "pipeline_key": "nf-core/atacseq",
    "pipeline_version": "2.1.2",
    "blockers_json": [library_strategy_conflict("nf-core/atacseq", "RNA-Seq")],
    "library_strategy": "RNA-Seq",
}
CLEAN = {"pipeline_key": "nf-core/rnaseq", "pipeline_version": "3.14.0", "blockers_json": []}

V = "/api/validation-studies"


@pytest.mark.asyncio
async def test_missing_studies_answer_404(client, headers, lit_validation):
    for method, suffix, body in [
        ("post", "/retry", None),
        ("post", "/approve", None),
        ("post", "/classify", {"classification": "reproduced"}),
        ("post", "/decline", {}),
        ("post", "/override-deposit", {"reason": "mislabelled"}),
    ]:
        kwargs = {"json": body} if body is not None else {}
        response = await getattr(client, method)(f"{V}/999999{suffix}", headers=headers, **kwargs)
        assert _body(response) == (404, {"detail": "Validation study not found"}), suffix
    design = await client.put(f"{V}/999999/differential-design", headers=headers, json={"contrasts": []})
    assert _body(design) == (404, {"detail": "Validation study not found"})
    finding = await client.post(
        f"{V}/999999/finding-set", headers=headers, json={"kind": "gene", "table_text": "gene,log2fc\n"}
    )
    assert _body(finding) == (404, {"detail": "Validation study not found"})


@pytest.mark.asyncio
async def test_wrong_state_errors_name_the_state(client, session, admin_user, headers, lit_validation):
    sid = await _study(session, admin_user, state="requested")
    expectations = [
        ("post", "/retry", None, "Only a study in 'error' can be retried; this one is in 'requested'."),
        ("post", "/approve", None, "Cannot approve a plan from 'requested'; the study must be in 'plan_ready'."),
        (
            "post",
            "/override-deposit",
            {"reason": "mislabelled"},
            "Cannot override the deposit from 'requested'; the study must be in 'plan_ready'.",
        ),
        (
            "post",
            "/classify",
            {"classification": "reproduced"},
            "Cannot classify from 'requested'; the study must be in 'comparing'.",
        ),
        ("post", "/decline", {}, "Cannot decline a plan from 'requested'; the study must be in 'plan_ready'."),
        (
            "post",
            "/use-deposit-pipeline",
            None,
            "Cannot change the plan's pipeline from 'requested'; the study must be in 'plan_ready'.",
        ),
    ]
    for method, suffix, body, detail in expectations:
        kwargs = {"json": body} if body is not None else {}
        response = await getattr(client, method)(f"{V}/{sid}{suffix}", headers=headers, **kwargs)
        assert _body(response) == (400, {"detail": detail}), suffix
    design = await client.put(f"{V}/{sid}/differential-design", headers=headers, json={"contrasts": []})
    assert _body(design) == (
        400,
        {"detail": "Cannot edit the differential design from 'requested'; the study must be in 'plan_ready'."},
    )
    finding = await client.post(
        f"{V}/{sid}/finding-set", headers=headers, json={"kind": "gene", "table_text": "gene,log2fc\n"}
    )
    assert _body(finding) == (
        400,
        {"detail": "Cannot confirm a ground-truth set from 'requested'; the study must be in 'plan_ready'."},
    )


@pytest.mark.asyncio
async def test_plan_ready_without_a_plan_answers_404(client, session, admin_user, headers, lit_validation):
    sid = await _study(session, admin_user, state="plan_ready")
    pipeline = await client.post(f"{V}/{sid}/use-deposit-pipeline", headers=headers)
    assert _body(pipeline) == (404, {"detail": "No reproduction plan for this study."})
    design = await client.put(f"{V}/{sid}/differential-design", headers=headers, json={"contrasts": []})
    assert _body(design) == (404, {"detail": "No reproduction plan to attach the differential design to."})
    finding = await client.post(
        f"{V}/{sid}/finding-set", headers=headers, json={"kind": "gene", "table_text": "gene,log2fc\n"}
    )
    assert _body(finding) == (404, {"detail": "No reproduction plan to attach the ground-truth set to."})


@pytest.mark.asyncio
async def test_plan_and_deposit_errors_keep_their_bodies(client, session, admin_user, headers, lit_validation):
    clean = await _study(session, admin_user, state="plan_ready", plan=CLEAN)
    no_conflict = "This plan does not contradict what the deposit says its data is."
    assert _body(await client.post(f"{V}/{clean}/use-deposit-pipeline", headers=headers)) == (
        400,
        {"detail": no_conflict},
    )
    assert _body(
        await client.post(f"{V}/{clean}/override-deposit", headers=headers, json={"reason": "mislabelled"})
    ) == (400, {"detail": no_conflict})
    assert _body(await client.post(f"{V}/{clean}/override-deposit", headers=headers, json={"reason": "   "})) == (
        400,
        {"detail": "Say why the deposit should be overruled; the reason goes on the record."},
    )
    kind = await client.post(f"{V}/{clean}/finding-set", headers=headers, json={"kind": "peak", "table_text": "x"})
    assert _body(kind) == (400, {"detail": "Unknown finding-set kind 'peak'; expected 'gene' or 'interval'."})
    design = await client.put(
        f"{V}/{clean}/differential-design",
        headers=headers,
        json={"contrasts": [{"name": "a vs b", "test_samples": ["S1"], "reference_samples": ["S2"]}]},
    )
    assert design.status_code == 400
    assert design.json()["detail"].startswith("Invalid differential design. ")
    assert set(design.json()) == {"detail"}

    unrouted = await _study(session, admin_user, state="plan_ready", plan=UNROUTED)
    response = await client.post(f"{V}/{unrouted}/use-deposit-pipeline", headers=headers)
    assert response.status_code == 400
    assert set(response.json()) == {"detail"}
    assert "names no single pipeline to move to" in response.json()["detail"]

    bisulfite = await _study(session, admin_user, state="plan_ready", plan=BISULFITE)
    response = await client.post(f"{V}/{bisulfite}/use-deposit-pipeline", headers=headers)
    assert _body(response) == (
        400,
        {
            "detail": "nf-core/methylseq reads Bisulfite-Seq data, but this bioAF has no version of it to move to: "
            "it is neither installed nor in the nf-core registry cache."
        },
    )
    approve = await client.post(f"{V}/{bisulfite}/approve", headers=headers)
    assert approve.status_code == 400
    assert set(approve.json()) == {"detail"}


@pytest.mark.asyncio
async def test_an_invalid_classification_answers_400(client, session, admin_user, headers, lit_validation):
    sid = await _study(session, admin_user, state="comparing")
    response = await client.post(f"{V}/{sid}/classify", headers=headers, json={"classification": "maybe"})
    assert response.status_code == 400
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"].startswith("Invalid classification 'maybe'. One of: ")


# --- handlers on both apps ----------------------------------------------------------------


def test_the_domain_handler_is_registered_on_both_apps():
    from app.exceptions import DomainError
    from app.main import app

    integrations = next(r.app for r in app.routes if getattr(r, "path", "") == "/api/v1/integrations")
    assert DomainError in app.exception_handlers
    assert DomainError in integrations.exception_handlers


# --- assistant action-plan confirmation ---------------------------------------------------


async def _plan(session, admin_user, steps: list[dict]) -> int:
    from app.models.assistant import AssistantActionPlan, AssistantConversation

    conversation = AssistantConversation(
        organization_id=admin_user.organization_id, user_id=admin_user.id, status="active"
    )
    session.add(conversation)
    await session.flush()
    plan = AssistantActionPlan(conversation_id=conversation.id, steps_json=steps, status="proposed")
    session.add(plan)
    await session.commit()
    return plan.id


@pytest.mark.asyncio
async def test_confirm_keeps_a_migrated_service_error_body(client, session, admin_user, headers):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    plan_id = await _plan(
        session,
        admin_user,
        [{"tool": "create_sample", "args": {"experiment_id": data["experiment"], "external_id": "DUP"}}],
    )
    response = await client.post(f"/api/assistant/action-plans/{plan_id}/confirm", headers=headers)
    assert _body(response) == (409, {"detail": _refused(data["ids"])})


def _stub_tool(exc: Exception):
    from types import SimpleNamespace

    async def handler(session, **kwargs):
        raise exc

    return SimpleNamespace(permission=("experiments", "view"), consequence_class="mutating", handler=handler)


@pytest.mark.asyncio
async def test_confirm_keeps_a_semantic_errors_status_and_envelope(client, session, admin_user, headers, monkeypatch):
    from app.exceptions import ConflictError

    monkeypatch.setattr(
        "app.api.assistant.get_tool", lambda name: _stub_tool(ConflictError("Busy", details={"holder": 3}))
    )
    plan_id = await _plan(session, admin_user, [{"tool": "stub", "args": {}}])
    response = await client.post(f"/api/assistant/action-plans/{plan_id}/confirm", headers=headers)
    assert _body(response) == (409, {"detail": "Busy", "code": "conflict", "details": {"holder": 3}})


@pytest.mark.asyncio
async def test_confirm_still_reports_other_failures_as_400(client, session, admin_user, headers, monkeypatch):
    monkeypatch.setattr("app.api.assistant.get_tool", lambda name: _stub_tool(RuntimeError("boom")))
    plan_id = await _plan(session, admin_user, [{"tool": "stub", "args": {}}])
    response = await client.post(f"/api/assistant/action-plans/{plan_id}/confirm", headers=headers)
    assert _body(response) == (400, {"detail": "action 'stub' failed: boom"})


def test_no_service_module_raises_http_exceptions():
    """Services raise domain errors; only the API layer speaks HTTP."""
    from pathlib import Path

    import app.services

    root = Path(app.services.__file__).parent
    offenders = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if "HTTPException" in p.read_text())
    assert offenders == []
