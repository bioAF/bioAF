"""Tests for the full search endpoint (GET /api/search) used by the /search page.

Covers multi-type results + per-type counts + per-hit url for an admin, permission
hiding for a viewer (no pipelines:view), and the entity_types filter. The looser
contract in test_search.py (empty / nonexistent queries) is left untouched.
"""

import pytest

from app.models.experiment import Experiment
from app.models.file import File
from app.models.literature import PROVENANCE_USER_UPLOAD, LiteraturePaper
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.project import Project


@pytest.mark.asyncio
async def test_full_search_endpoint_returns_multi_type_hits_and_counts(session, admin_user, client, admin_token):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Alpha Study")
    session.add(exp)
    await session.flush()
    session.add_all(
        [
            File(organization_id=org_id, gcs_uri="gs://b/alpha.csv", filename="alpha.csv", file_type="csv"),
            Project(organization_id=org_id, name="Alpha Project"),
            LiteraturePaper(
                organization_id=org_id,
                title="Alpha in cells",
                title_normalized="alpha in cells",
                provenance=PROVENANCE_USER_UPLOAD,
            ),
        ]
    )
    await session.commit()

    resp = await client.get("/api/search?query=alpha", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    types = {hit["entity_type"] for hit in data["results"]}
    assert {"experiment", "file", "project", "literature_paper"} <= types
    # Every hit carries a server-computed destination url.
    assert all(hit["url"] for hit in data["results"])
    exp_hit = next(h for h in data["results"] if h["entity_type"] == "experiment")
    assert exp_hit["url"] == f"/experiments/{exp.id}"
    # Per-type counts label the type filter.
    assert data["type_counts"]["experiment"] >= 1
    assert data["type_counts"]["file"] >= 1


@pytest.mark.asyncio
async def test_full_search_endpoint_hides_types_the_viewer_cannot_view(session, admin_user, client, viewer_token):
    """The built-in viewer role has no pipelines:view, so pipeline runs and pipeline
    definitions must not appear in its search results."""
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Beta Study")
    session.add(exp)
    await session.flush()
    session.add_all(
        [
            PipelineRun(organization_id=org_id, experiment_id=exp.id, pipeline_name="beta-run"),
            PipelineCatalogEntry(
                organization_id=org_id,
                pipeline_key="beta/key",
                name="Beta Pipeline",
                source_type="nf-core",
                is_builtin=True,
            ),
        ]
    )
    await session.commit()

    resp = await client.get("/api/search?query=beta", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 200
    data = resp.json()
    types = {hit["entity_type"] for hit in data["results"]}
    assert "experiment" in types
    assert "pipeline_run" not in types
    assert "pipeline_definition" not in types
    assert "pipeline_run" not in data["type_counts"]
    assert "pipeline_definition" not in data["type_counts"]


@pytest.mark.asyncio
async def test_full_search_endpoint_entity_types_filter(session, admin_user, client, admin_token):
    org_id = admin_user.organization_id
    exp = Experiment(organization_id=org_id, name="Gamma Study")
    session.add(exp)
    await session.flush()
    session.add(File(organization_id=org_id, gcs_uri="gs://b/gamma.txt", filename="gamma.txt", file_type="txt"))
    await session.commit()

    resp = await client.get(
        "/api/search?query=gamma&entity_types=file",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert {hit["entity_type"] for hit in data["results"]} == {"file"}
    # Counts still cover every permitted type so the dropdown stays complete while
    # results are narrowed to one type.
    assert data["type_counts"]["experiment"] >= 1
    assert data["type_counts"]["file"] >= 1
