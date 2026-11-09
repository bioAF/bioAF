"""Pagination on the ten list routes that declare no application bound.

Recorded decision (owner decision pending): these keep their currently accepted
range. Defaults stay per route, page_size 0 answers an empty page, and an
oversized page_size is honored, not clamped or rejected. Routes that already
declare bounds, and the assistant's 1-100 clamp, keep them.
"""

import pytest
import pytest_asyncio

from app.models.experiment import Experiment

# path, default page_size, whether the envelope echoes page and page_size
UNBOUNDED = [
    ("/api/datasets", 25),
    ("/api/documents", 25),
    ("/api/experiments", 25),
    ("/api/experiments/{e}/files", 25),
    ("/api/experiments/{e}/audit", 25),
    ("/api/files", 25),
    ("/api/plots", 25),
    ("/api/samples", 100),
    ("/api/search?q=x", 25),
]
OPENAPI_UNBOUNDED = {
    ("/api/datasets", 25),
    ("/api/documents", 25),
    ("/api/experiments", 25),
    ("/api/experiments/{experiment_id}/files", 25),
    ("/api/experiments/{experiment_id}/audit", 25),
    ("/api/files", 25),
    ("/api/gitops/commits", 20),
    ("/api/plots", 25),
    ("/api/samples", 100),
    ("/api/search", 25),
}


@pytest.fixture
def headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def experiment_id(session, admin_user) -> int:
    exp = Experiment(organization_id=admin_user.organization_id, name="Paging")
    session.add(exp)
    await session.commit()
    return exp.id


def _url(path: str, experiment_id: int, query: str = "") -> str:
    path = path.format(e=experiment_id)
    if not query:
        return path
    return f"{path}{'&' if '?' in path else '?'}{query}"


@pytest.mark.parametrize("path,default_size", UNBOUNDED)
@pytest.mark.asyncio
async def test_defaults_are_echoed(client, headers, experiment_id, path, default_size):
    response = await client.get(_url(path, experiment_id), headers=headers)
    assert response.status_code == 200
    assert (response.json()["page"], response.json()["page_size"]) == (1, default_size)


@pytest.mark.parametrize("path,default_size", UNBOUNDED)
@pytest.mark.parametrize("size", [0, 1, 200, 100000])
@pytest.mark.asyncio
async def test_any_non_negative_page_size_is_honored(client, headers, experiment_id, path, default_size, size):
    response = await client.get(_url(path, experiment_id, f"page_size={size}&page=2"), headers=headers)
    assert response.status_code == 200
    assert (response.json()["page"], response.json()["page_size"]) == (2, size)


@pytest.mark.parametrize("size,returned", [(0, 1), (1, 1), (100, 100), (101, 100), (100000, 100)])
@pytest.mark.asyncio
async def test_assistant_conversations_still_clamp_to_1_through_100(
    client, session, admin_user, headers, size, returned
):
    from app.models.assistant import AssistantConversation

    session.add_all(
        AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id) for _ in range(105)
    )
    await session.commit()
    response = await client.get(f"/api/assistant/conversations?page_size={size}", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 105
    assert len(response.json()["conversations"]) == returned


def _query_param(spec: dict, name: str) -> dict:
    return next(p for p in spec.get("parameters", []) if p["name"] == name)["schema"]


def test_openapi_declares_no_bound_on_the_unbounded_routes():
    from app.main import app

    paths = app.openapi()["paths"]
    for path, default_size in OPENAPI_UNBOUNDED:
        spec = paths[path]["get"]
        page, size = _query_param(spec, "page"), _query_param(spec, "page_size")
        assert (page.get("default"), size.get("default")) == (1, default_size), path
        for schema in (page, size):
            assert not {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"} & set(schema), path


def test_bounded_routes_keep_their_bounds():
    from app.main import app

    paths = app.openapi()["paths"]
    assert _query_param(paths["/api/pipeline-runs"]["get"], "page_size") == {
        "type": "integer",
        "maximum": 200,
        "minimum": 1,
        "default": 25,
        "title": "Page Size",
    }
    assert _query_param(paths["/api/audit"]["get"], "page_size")["maximum"] == 100
