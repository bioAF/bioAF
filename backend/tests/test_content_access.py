"""Who may read file and thumbnail bytes, through a content token or a bearer call.

A content token carries no role, so the decision is made when it is issued. The
same helper answers direct bearer calls, and these tests hold the two paths to the
same answer for the same caller and file.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
import pytest_asyncio

from app.api.content_tokens import CONTENT_TOKEN_PURPOSE, create_content_token
from app.config import settings
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.plot_archive_entry import PlotArchiveEntry
from app.models.qc_dashboard import QCDashboard
from app.models.user import User
from app.services import api_key_service, role_service, service_account_service
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

FILES_VIEW = [("files", "view")]
RESULTS_ONLY = [("experiments", "view")]
PIPELINE_RESULTS_ONLY = [("pipelines", "view")]
NEITHER = [("audit_log", "view")]


@pytest.fixture(autouse=True)
def storage():
    adapter = SimpleNamespace(read_bytes=AsyncMock(return_value=b"bytes"))
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        yield adapter


async def _role_headers(session, org_id: int, grants) -> dict:
    email = f"{abs(hash(tuple(grants)))}@test.com"
    role = await role_service.create_role(session, org_id, f"role-{email}", permissions=grants)
    user = User(email=email, password_hash="x", role_id=role.id, organization_id=org_id, status="active")
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    token = AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name)
    return {"Authorization": f"Bearer {token}"}


def _file(org_id: int, name: str) -> File:
    return File(organization_id=org_id, storage_uri=f"gs://b/{name}", filename=name, file_type="image")


async def _dashboard(session, org_id: int, user_id: int, plots_json) -> QCDashboard:
    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="rnaseq",
        pipeline_version="1",
        submitted_by_user_id=user_id,
        status="completed",
    )
    session.add(run)
    await session.flush()
    dashboard = QCDashboard(
        organization_id=org_id, pipeline_run_id=run.id, metrics_json={}, plots_json=plots_json, status="ready"
    )
    session.add(dashboard)
    await session.commit()
    return dashboard


@pytest_asyncio.fixture
async def org(admin_user) -> int:
    return admin_user.organization_id


@pytest_asyncio.fixture
async def files(session, admin_user, org) -> SimpleNamespace:
    plain, plotted, qc = _file(org, "plain.png"), _file(org, "plot.png"), _file(org, "qc.png")
    session.add_all([plain, plotted, qc])
    await session.flush()
    plot = PlotArchiveEntry(
        organization_id=org, file_id=plotted.id, title="Plot", thumbnail_gcs_uri="gs://b/plot-thumb.png"
    )
    session.add(plot)
    await session.commit()
    await _dashboard(session, org, admin_user.id, [{"plot_type": "umap", "title": "UMAP", "file_id": qc.id}])
    return SimpleNamespace(plain=plain, plotted=plotted, qc=qc, plot=plot)


@pytest_asyncio.fixture
async def other_org(session) -> SimpleNamespace:
    organization = Organization(name="Other Org", setup_complete=True)
    session.add(organization)
    await session.flush()
    roles = await seed_builtin_roles(session, organization.id)
    user = User(
        email="owner@other.test",
        password_hash="x",
        role_id=roles["admin"],
        organization_id=organization.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return SimpleNamespace(id=organization.id, user=user)


async def _issue(client, headers, resource_type: str, resource_id: int):
    return await client.post(
        "/api/content-tokens", json={"resource_type": resource_type, "resource_id": resource_id}, headers=headers
    )


async def _both_paths(client, headers, file_id: int) -> tuple[int, int]:
    """(status of issuing a file token, status of the direct bearer call)."""
    issued = await _issue(client, headers, "file", file_id)
    direct = await client.get(f"/api/files/{file_id}/content", headers=headers)
    return issued.status_code, direct.status_code


@pytest.mark.parametrize(
    "grants,name,expected",
    [
        (FILES_VIEW, "plain", 200),
        (FILES_VIEW, "plotted", 200),
        (FILES_VIEW, "qc", 200),
        (RESULTS_ONLY, "plain", 403),
        (RESULTS_ONLY, "plotted", 200),
        (RESULTS_ONLY, "qc", 200),
        (PIPELINE_RESULTS_ONLY, "plain", 403),
        (PIPELINE_RESULTS_ONLY, "plotted", 200),
        (PIPELINE_RESULTS_ONLY, "qc", 200),
        (NEITHER, "plain", 403),
        (NEITHER, "plotted", 403),
        (NEITHER, "qc", 403),
    ],
)
@pytest.mark.asyncio
async def test_token_issue_and_bearer_give_the_same_answer(client, session, org, files, grants, name, expected):
    headers = await _role_headers(session, org, grants)
    assert await _both_paths(client, headers, getattr(files, name).id) == (expected, expected)


@pytest.mark.parametrize("grants,expected", [(RESULTS_ONLY, 200), (PIPELINE_RESULTS_ONLY, 200), (NEITHER, 403)])
@pytest.mark.asyncio
async def test_thumbnails_need_results_view_on_both_paths(client, session, org, files, grants, expected):
    headers = await _role_headers(session, org, grants)
    issued = await _issue(client, headers, "plot_thumbnail", files.plot.id)
    direct = await client.get(f"/api/plots/{files.plot.id}/thumbnail/content", headers=headers)
    assert (issued.status_code, direct.status_code) == (expected, expected)


@pytest.mark.asyncio
async def test_files_view_alone_does_not_open_a_thumbnail(client, session, org, files):
    headers = await _role_headers(session, org, FILES_VIEW)
    issued = await _issue(client, headers, "plot_thumbnail", files.plot.id)
    assert issued.status_code == 403


@pytest.mark.asyncio
async def test_an_issued_token_opens_exactly_what_it_names(client, session, org, files):
    headers = await _role_headers(session, org, RESULTS_ONLY)
    token = (await _issue(client, headers, "file", files.plotted.id)).json()["token"]
    served = await client.get(f"/api/files/{files.plotted.id}/content", params={"token": token})
    assert served.status_code == 200
    assert served.content == b"bytes"

    elsewhere = await client.get(f"/api/files/{files.plain.id}/content", params={"token": token})
    assert elsewhere.status_code == 401


@pytest.mark.asyncio
async def test_a_thumbnail_token_opens_its_thumbnail(client, session, org, files):
    headers = await _role_headers(session, org, RESULTS_ONLY)
    token = (await _issue(client, headers, "plot_thumbnail", files.plot.id)).json()["token"]
    served = await client.get(f"/api/plots/{files.plot.id}/thumbnail/content", params={"token": token})
    assert served.status_code == 200


@pytest.mark.asyncio
async def test_a_thumbnail_token_is_refused_on_the_same_numbered_file(client, org, files):
    token = create_content_token("plot_thumbnail", files.plain.id, org)
    response = await client.get(f"/api/files/{files.plain.id}/content", params={"token": token})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_file_token_is_refused_on_the_same_numbered_thumbnail(client, org, files):
    token = create_content_token("file", files.plot.id, org)
    response = await client.get(f"/api/plots/{files.plot.id}/thumbnail/content", params={"token": token})
    assert response.status_code == 401


def _signed(**claims) -> str:
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _expires(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.mark.parametrize(
    "drop,change",
    [
        (None, {"purpose": "invite"}),
        (None, {"exp": _expires(-5)}),
        ("exp", {}),
        ("org_id", {}),
        ("resource_type", {}),
        (None, {"resource_type": "report"}),
        (None, {"org_id": "not-a-number"}),
    ],
)
@pytest.mark.asyncio
async def test_a_token_failing_any_claim_is_refused(client, org, files, drop, change):
    claims = {
        "purpose": CONTENT_TOKEN_PURPOSE,
        "resource_type": "file",
        "resource_id": files.plain.id,
        "org_id": org,
        "exp": _expires(30),
        **change,
    }
    claims.pop(drop, None)
    response = await client.get(f"/api/files/{files.plain.id}/content", params={"token": _signed(**claims)})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_token_for_another_organization_opens_nothing(client, org, files, other_org):
    token = create_content_token("file", files.plain.id, other_org.id)
    response = await client.get(f"/api/files/{files.plain.id}/content", params={"token": token})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/files/{file}/content"),
        ("get", "/api/files/{file}"),
        ("get", "/api/search?q=x"),
        ("get", "/api/auth/me"),
        ("post", "/api/content-tokens"),
    ],
)
@pytest.mark.asyncio
async def test_a_content_token_is_never_a_session(client, org, files, method, path):
    headers = {"Authorization": f"Bearer {create_content_token('file', files.plain.id, org)}"}
    kwargs = {"json": {"resource_type": "file", "resource_id": files.plain.id}} if method == "post" else {}
    response = await getattr(client, method)(path.format(file=files.plain.id), headers=headers, **kwargs)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_content_token_does_not_authenticate_a_public_route(client, org, files):
    anonymous = await client.get("/api/bootstrap/status")
    headers = {"Authorization": f"Bearer {create_content_token('file', files.plain.id, org)}"}
    assert (await client.get("/api/bootstrap/status", headers=headers)).json() == anonymous.json()


@pytest.mark.parametrize(
    "shape",
    [
        lambda file_id: [{"file_id": str(file_id)}],
        lambda file_id: [{"file_id": None}],
        lambda file_id: [{"plot_type": "umap"}],
        lambda file_id: [file_id],
        lambda file_id: {"file_id": file_id},
        lambda file_id: [],
        lambda file_id: [{"file_id": file_id + 100000}],
    ],
    ids=["string-id", "null-id", "missing-id", "bare-id", "not-a-list", "empty", "dangling"],
)
@pytest.mark.asyncio
async def test_a_malformed_qc_entry_fails_closed_for_results_only(client, session, admin_user, org, files, shape):
    await _dashboard(session, org, admin_user.id, shape(files.plain.id))
    results = await _role_headers(session, org, RESULTS_ONLY)
    assert await _both_paths(client, results, files.plain.id) == (403, 403)
    ordinary = await _role_headers(session, org, FILES_VIEW)
    assert await _both_paths(client, ordinary, files.plain.id) == (200, 200)


@pytest.mark.asyncio
async def test_a_foreign_dashboard_or_plot_cannot_vouch_for_a_file(client, session, org, files, other_org):
    await _dashboard(session, other_org.id, other_org.user.id, [{"file_id": files.plain.id}])
    session.add(PlotArchiveEntry(organization_id=other_org.id, file_id=files.plain.id, title="Theirs"))
    await session.commit()
    headers = await _role_headers(session, org, RESULTS_ONLY)
    assert await _both_paths(client, headers, files.plain.id) == (403, 403)


@pytest.mark.asyncio
async def test_a_foreign_file_answers_404_even_with_an_own_association(client, session, admin_user, org, other_org):
    """A numeric match in our own dashboard never reaches across the boundary."""
    theirs = _file(other_org.id, "theirs.png")
    session.add(theirs)
    await session.commit()
    await _dashboard(session, org, admin_user.id, [{"file_id": theirs.id}])
    session.add(PlotArchiveEntry(organization_id=org, file_id=theirs.id, title="Stale"))
    await session.commit()
    for grants in (RESULTS_ONLY, FILES_VIEW):
        headers = await _role_headers(session, org, grants)
        assert await _both_paths(client, headers, theirs.id) == (404, 404)


@pytest.mark.parametrize(
    "scopes,name,expected",
    [
        (["experiments:view"], "plain", 403),
        (["experiments:view"], "plotted", 200),
        (["files:view"], "plain", 200),
        (["samples:view"], "plotted", 403),
    ],
)
@pytest.mark.asyncio
async def test_a_key_is_held_to_its_scope(client, session, admin_user, org, files, scopes, name, expected):
    account = await service_account_service.create(
        session, org_id=org, display_name="Key", role_id=admin_user.role_id, created_by_user_id=admin_user.id
    )
    _row, secret = await api_key_service.mint(
        session, org_id=org, sa_user_id=account.id, name="k", scopes=scopes, created_by_user_id=admin_user.id
    )
    await session.commit()
    headers = {"Authorization": f"Bearer {secret}"}
    assert await _both_paths(client, headers, getattr(files, name).id) == (expected, expected)
