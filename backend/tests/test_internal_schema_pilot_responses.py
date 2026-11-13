"""Real responses of the five transport pilot families agree with the internal schema artifact.

Frontend transport types are generated from ``contracts/internal-openapi.json``, so the
artifact must say what the serializer actually emits. For UserSummary (nested), project
detail, notebook sessions, the project provenance graph and run reviews, every emitted object
must carry exactly its declared properties, every property must be declared required (no route
excludes unset or None fields, so all of them are always present), and a null may appear only
where the schema allows it. A field's value type must match too.

What this cannot prove: a ``string`` in the schema says nothing about which values a validator
accepts (verdicts, statuses), and ``metadata``/``sample_verdicts_json`` are unconstrained JSON.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.project import Project
from app.models.project_sample import ProjectSample
from app.models.sample import Sample

ARTIFACT = Path(__file__).resolve().parents[1] / "contracts" / "internal-openapi.json"
SCHEMA = json.loads(ARTIFACT.read_text())
COMPONENTS = SCHEMA["components"]["schemas"]


def _resolve(schema: dict) -> dict:
    while "$ref" in schema:
        schema = COMPONENTS[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _allows_null(schema: dict) -> bool:
    schema = _resolve(schema)
    return schema.get("type") == "null" or any(_allows_null(s) for s in schema.get("anyOf", []))


def _problems(value, schema: dict, path: str) -> list[str]:
    schema = _resolve(schema)
    if "anyOf" in schema:
        if value is None:
            return [] if _allows_null(schema) else [f"{path}: null where the schema does not allow it"]
        branches = [s for s in schema["anyOf"] if _resolve(s).get("type") != "null"]
        results = [_problems(value, branch, path) for branch in branches]
        return [] if any(not r for r in results) else results[0]
    if value is None:
        return [] if _allows_null(schema) else [f"{path}: null where the schema does not allow it"]
    kind = schema.get("type")
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} not in {schema['enum']}"]
    if kind == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return [f"{path}: expected an object"]
        props = schema.get("properties")
        if props is None:
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict) and extra:
                return [p for k, v in value.items() for p in _problems(v, extra, f"{path}.{k}")]
            return []
        out = []
        if set(value) != set(props):
            out.append(f"{path}: emitted {sorted(value)} but declared {sorted(props)}")
        missing_required = set(props) - set(schema.get("required", []))
        if missing_required:
            out.append(f"{path}: always emitted but not required: {sorted(missing_required)}")
        for key in set(value) & set(props):
            out.extend(_problems(value[key], props[key], f"{path}.{key}"))
        return out
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: expected an array"]
        return [p for i, v in enumerate(value) for p in _problems(v, schema.get("items", {}), f"{path}[{i}]")]
    expected = {"string": str, "integer": int, "number": (int, float), "boolean": bool}.get(kind)
    if expected and (not isinstance(value, expected) or (kind in ("integer", "number") and isinstance(value, bool))):
        return [f"{path}: {value!r} is not {kind}"]
    return []


def _response_schema(path: str, method: str, status: str) -> dict:
    return SCHEMA["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(session, admin_user):
    org = admin_user.organization_id
    project = Project(organization_id=org, name="Atlas", owner_user_id=admin_user.id, created_by_user_id=admin_user.id)
    session.add(project)
    await session.flush()
    experiment = Experiment(organization_id=org, name="Liver", status="pipeline_complete", project_id=project.id)
    bare = Experiment(organization_id=org, name="Bare", status="registered")
    session.add_all([experiment, bare])
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="L-1", organism="Homo sapiens", status="registered")
    session.add(sample)
    await session.flush()
    session.add(ProjectSample(project_id=project.id, sample_id=sample.id, added_by_user_id=admin_user.id))
    run = PipelineRun(
        organization_id=org,
        experiment_id=experiment.id,
        project_id=project.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status="completed",
    )
    session.add(run)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))
    linked = NotebookSession(
        user_id=admin_user.id,
        organization_id=org,
        experiment_id=experiment.id,
        project_id=project.id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="running",
        compute_job_ref="bioaf-notebook-1",
    )
    unlinked = NotebookSession(
        user_id=admin_user.id,
        organization_id=org,
        session_type="rstudio",
        resource_profile="medium",
        cpu_cores=4,
        memory_gb=16,
        status="pending",
    )
    session.add_all([linked, unlinked])
    await session.commit()
    return {"project": project.id, "run": run.id, "sample": sample.id, "linked": linked.id, "unlinked": unlinked.id}


@pytest.mark.asyncio
async def test_project_detail_and_provenance_graph_match_the_schema(client, admin_token, world):
    detail = await client.get(f"/api/projects/{world['project']}", headers=_bearer(admin_token))
    graph = await client.get(f"/api/projects/{world['project']}/provenance", headers=_bearer(admin_token))
    assert detail.status_code == graph.status_code == 200
    assert detail.json()["experiments"] and detail.json()["pipeline_runs"] and detail.json()["samples"]
    assert graph.json()["nodes"] and graph.json()["edges"]
    problems = _problems(detail.json(), _response_schema("/api/projects/{project_id}", "get", "200"), "project")
    problems += _problems(
        graph.json(), _response_schema("/api/projects/{project_id}/provenance", "get", "200"), "provenance"
    )
    assert problems == []


# Both route families stay, each with its own response builder: the notebooks page reads
# /api/v1/notebooks (with a bucket), the experiment activity tab reads /api/notebooks.
FAMILIES = ["/api/v1/notebooks", "/api/notebooks"]


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("which", ["linked", "unlinked"])
@pytest.mark.asyncio
async def test_notebook_session_detail_and_list_match_the_schema(client, admin_token, world, family, which):
    detail = await client.get(f"{family}/sessions/{world[which]}", headers=_bearer(admin_token))
    assert detail.status_code == 200
    body = detail.json()
    assert (body["experiment"] is None) is (which == "unlinked")
    assert body["user"]["email"]
    problems = _problems(body, _response_schema(f"{family}/sessions/{{session_id}}", "get", "200"), "session")
    queries = ["", "?bucket=active", "?bucket=recent", "?bucket=all"] if family == "/api/v1/notebooks" else [""]
    for query in queries:
        listing = await client.get(f"{family}/sessions{query}", headers=_bearer(admin_token))
        assert listing.status_code == 200, query
        problems += _problems(listing.json(), _response_schema(f"{family}/sessions", "get", "200"), f"list{query}")
    assert {
        s["id"] for s in (await client.get(f"{family}/sessions", headers=_bearer(admin_token))).json()["sessions"]
    } >= {
        world["linked"],
        world["unlinked"],
    }
    assert problems == []


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.asyncio
async def test_stopping_a_session_answers_the_schema(client, admin_token, world, family):
    with (
        patch("app.services.notebook_service.get_notebook_adapter", MagicMock()),
        patch("app.api.notebook_sessions.get_notebook_adapter", MagicMock()),
    ):
        stopped = await client.post(f"{family}/sessions/{world['linked']}/stop", headers=_bearer(admin_token))
    assert stopped.status_code == 200, stopped.text
    assert (
        _problems(stopped.json(), _response_schema(f"{family}/sessions/{{session_id}}/stop", "post", "200"), "stop")
        == []
    )


@pytest.mark.asyncio
async def test_run_review_operations_match_the_schema(client, admin_token, world):
    unreviewed = await client.get(f"/api/pipeline-runs/{world['run']}/review", headers=_bearer(admin_token))
    assert unreviewed.status_code == 200 and unreviewed.json() is None
    created = await client.post(
        f"/api/pipeline-runs/{world['run']}/reviews",
        json={"verdict": "approved", "sample_verdicts": {str(world["sample"]): {"verdict": "pass"}}},
        headers=_bearer(admin_token),
    )
    active = await client.get(f"/api/pipeline-runs/{world['run']}/review", headers=_bearer(admin_token))
    listing = await client.get(f"/api/pipeline-runs/{world['run']}/reviews", headers=_bearer(admin_token))
    assert created.status_code == 201
    assert created.json()["sample_verdicts_json"] == {str(world["sample"]): {"verdict": "pass", "notes": None}}
    path = "/api/pipeline-runs/{run_id}/review"
    problems = _problems(created.json(), _response_schema("/api/pipeline-runs/{run_id}/reviews", "post", "201"), "c")
    problems += _problems(active.json(), _response_schema(path, "get", "200"), "active")
    problems += _problems(listing.json(), _response_schema("/api/pipeline-runs/{run_id}/reviews", "get", "200"), "l")
    assert problems == []
    assert _allows_null(_response_schema(path, "get", "200"))


def test_the_checker_rejects_a_drifted_payload():
    schema = {"$ref": "#/components/schemas/UserSummary"}
    assert _problems({"id": 1, "name": None, "email": "a@b.test"}, schema, "u") == []
    assert _problems({"id": 1, "email": "a@b.test"}, schema, "u")
    assert _problems({"id": 1, "name": None, "email": None}, schema, "u")
    assert _problems({"id": "1", "name": None, "email": "a@b.test"}, schema, "u")
    assert _problems({"id": 1, "name": None, "email": "a@b.test", "role": "admin"}, schema, "u")
