"""Project list counts match project detail, the sample picker has a real source,
and an invited user can get in: through the emailed page, or an admin's Activate."""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.project import Project
from app.models.project_sample import ProjectSample
from app.models.role import Role
from app.models.sample import Sample
from app.models.user import User
from app.services import role_service
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


async def _custom_role_headers(session, org_id: int, email: str, grants: list[tuple[str, str]]) -> dict:
    role = await role_service.create_role(session, org_id, f"custom-{email}", permissions=grants)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("customrolepass1"),
        role_id=role.id,
        organization_id=org_id,
        status="active",
    )
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    token = AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def foreign_sample(session, admin_user) -> Sample:
    org = Organization(name="Other Org", setup_complete=True)
    session.add(org)
    await session.flush()
    experiment = Experiment(organization_id=org.id, name="Their Exp")
    session.add(experiment)
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="their-sample", status="registered")
    session.add(sample)
    await session.commit()
    return sample


@pytest_asyncio.fixture
async def linked_project(session, admin_user) -> Project:
    """Own experiment with a sample, an empty own experiment, and another
    experiment's sample linked through project_samples."""
    org_id = admin_user.organization_id
    project = Project(organization_id=org_id, name="Counted", code="CNT", owner_user_id=admin_user.id)
    session.add(project)
    await session.flush()
    own = Experiment(organization_id=org_id, name="Own", project_id=project.id)
    empty = Experiment(organization_id=org_id, name="Empty", project_id=project.id)
    elsewhere = Experiment(organization_id=org_id, name="Elsewhere")
    session.add_all([own, empty, elsewhere])
    await session.flush()
    direct = Sample(experiment_id=own.id, external_id="direct", status="registered")
    linked = Sample(experiment_id=elsewhere.id, external_id="linked", status="registered")
    session.add_all([direct, linked])
    await session.flush()
    session.add_all(
        [
            ProjectSample(project_id=project.id, sample_id=linked.id, added_by_user_id=admin_user.id),
            # Both paths to one sample still count it once.
            ProjectSample(project_id=project.id, sample_id=direct.id, added_by_user_id=admin_user.id),
        ]
    )
    await session.commit()
    return project


async def _both_counts(client, headers, project_id: int) -> tuple[dict, dict]:
    listed = await client.get("/api/projects", headers=headers)
    row = next(p for p in listed.json()["projects"] if p["id"] == project_id)
    detail = (await client.get(f"/api/projects/{project_id}", headers=headers)).json()
    return row, detail


@pytest.mark.asyncio
async def test_project_list_counts_match_detail(client, admin_headers, linked_project):
    row, detail = await _both_counts(client, admin_headers, linked_project.id)

    assert (row["sample_count"], row["experiment_count"]) == (2, 3)
    assert (row["sample_count"], row["experiment_count"]) == (detail["sample_count"], detail["experiment_count"])


@pytest.mark.asyncio
async def test_project_counts_are_zero_with_nothing_linked(client, session, admin_user, admin_headers):
    project = Project(organization_id=admin_user.organization_id, name="Bare", code="BARE", owner_user_id=admin_user.id)
    session.add(project)
    await session.commit()

    row, _ = await _both_counts(client, admin_headers, project.id)

    assert (row["sample_count"], row["experiment_count"]) == (0, 0)


@pytest.mark.asyncio
async def test_project_counts_exclude_a_stored_foreign_link(
    client, session, admin_user, admin_headers, linked_project, foreign_sample
):
    session.add(
        ProjectSample(project_id=linked_project.id, sample_id=foreign_sample.id, added_by_user_id=admin_user.id)
    )
    await session.commit()

    row, detail = await _both_counts(client, admin_headers, linked_project.id)

    assert (row["sample_count"], row["experiment_count"]) == (2, 3)
    assert (row["sample_count"], row["experiment_count"]) == (detail["sample_count"], detail["experiment_count"])


@pytest.mark.asyncio
async def test_sample_list_names_each_samples_experiment(client, admin_headers, linked_project):
    response = await client.get("/api/samples", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    by_id = {s["external_id"]: s for s in body["samples"]}
    assert by_id["linked"]["experiment_name"] == "Elsewhere"
    assert set(by_id["direct"]) >= {"id", "organism", "tissue_type", "qc_status", "experiment_id"}


@pytest.mark.asyncio
async def test_sample_list_pages(client, admin_headers, linked_project):
    first = (await client.get("/api/samples?page=1&page_size=1", headers=admin_headers)).json()
    second = (await client.get("/api/samples?page=2&page_size=1", headers=admin_headers)).json()

    assert first["total"] == second["total"] == 2
    assert first["samples"][0]["id"] != second["samples"][0]["id"]


@pytest.mark.asyncio
async def test_sample_list_excludes_another_organization(client, admin_headers, linked_project, foreign_sample):
    body = (await client.get("/api/samples", headers=admin_headers)).json()

    assert foreign_sample.id not in {s["id"] for s in body["samples"]}


@pytest.mark.asyncio
async def test_sample_list_needs_samples_view(client, session, admin_user):
    denied = await _custom_role_headers(
        session, admin_user.organization_id, "nosample@test.com", [("projects", "view")]
    )
    allowed = await _custom_role_headers(session, admin_user.organization_id, "sample@test.com", [("samples", "view")])

    assert (await client.get("/api/samples", headers=denied)).status_code == 403
    assert (await client.get("/api/samples", headers=allowed)).status_code == 200


async def _role_id(session, org_id: int, name: str = "viewer") -> int:
    result = await session.execute(select(Role.id).where(Role.organization_id == org_id, Role.name == name))
    return result.scalar_one()


@pytest_asyncio.fixture
async def invited_user(session, admin_user) -> User:
    user = User(
        email="invited@test.com",
        password_hash=AuthService.hash_password("placeholderpass1"),
        role_id=await _role_id(session, admin_user.organization_id),
        organization_id=admin_user.organization_id,
        status="invited",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _sent_link(send) -> str:
    return send.call_args.args[1]


@pytest.mark.asyncio
async def test_an_invitation_links_to_the_accept_page(client, session, admin_user, admin_headers):
    role_id = await _role_id(session, admin_user.organization_id)
    with patch("app.api.users.EmailService.send_invitation", return_value=True) as send:
        response = await client.post(
            "/api/users", json={"email": "new@test.com", "role_id": role_id}, headers=admin_headers
        )

    assert response.status_code == 200
    link = _sent_link(send)
    assert link.startswith("http") and "/accept-invite?token=" in link
    assert "/api/users/" not in link


@pytest.mark.asyncio
async def test_a_resent_invitation_links_to_the_accept_page(client, admin_headers, invited_user):
    with patch("app.api.users.EmailService.send_invitation", return_value=True) as send:
        response = await client.post(f"/api/users/{invited_user.id}/resend-invite", headers=admin_headers)

    assert response.status_code == 200
    assert "/accept-invite?token=" in _sent_link(send) and "/api/users/" not in _sent_link(send)


@pytest.mark.asyncio
async def test_a_bulk_invitation_emails_each_new_user(client, session, admin_user, admin_headers):
    role_id = await _role_id(session, admin_user.organization_id)
    invites = [{"email": "one@test.com", "role_id": role_id}, {"email": "two@test.com", "role_id": role_id}]
    with patch("app.api.users.EmailService.send_invitation", return_value=True) as send:
        response = await client.post("/api/users/bulk-invite", json={"invites": invites}, headers=admin_headers)

    assert response.status_code == 200
    assert sorted(call.args[0] for call in send.call_args_list) == ["one@test.com", "two@test.com"]
    assert all("/accept-invite?token=" in call.args[1] for call in send.call_args_list)


@pytest.mark.asyncio
async def test_the_emailed_token_accepts_the_invitation(client, session, admin_user, admin_headers):
    role_id = await _role_id(session, admin_user.organization_id)
    with patch("app.api.users.EmailService.send_invitation", return_value=True) as send:
        await client.post("/api/users", json={"email": "flow@test.com", "role_id": role_id}, headers=admin_headers)
    token = _sent_link(send).split("token=")[1]

    accepted = await client.post("/api/users/accept-invite", json={"token": token, "password": "chosenpassword1"})
    login = await client.post("/api/auth/login", json={"email": "flow@test.com", "password": "chosenpassword1"})

    assert accepted.status_code == 200
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_an_admin_can_activate_an_invited_user(client, session, admin_headers, invited_user):
    response = await client.post(f"/api/users/{invited_user.id}/activate", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    login = await client.post("/api/auth/login", json={"email": invited_user.email, "password": "placeholderpass1"})
    assert login.status_code == 200
    audit = await session.execute(
        select(AuditLog).where(AuditLog.entity_id == invited_user.id, AuditLog.action == "activate")
    )
    assert audit.scalars().first() is not None


@pytest.mark.asyncio
async def test_activate_refuses_a_user_who_is_not_invited(client, session, admin_user, admin_headers):
    response = await client.post(f"/api/users/{admin_user.id}/activate", headers=admin_headers)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_activate_answers_404_for_another_organizations_user(client, session, admin_user, admin_headers):
    org = Organization(name="Other Org", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    theirs = User(
        email="theirs@test.com",
        password_hash=AuthService.hash_password("theirpassword1"),
        role_id=roles["viewer"],
        organization_id=org.id,
        status="invited",
    )
    session.add(theirs)
    await session.commit()

    response = await client.post(f"/api/users/{theirs.id}/activate", headers=admin_headers)

    assert response.status_code == 404
    await session.refresh(theirs)
    assert theirs.status == "invited"


@pytest.mark.asyncio
async def test_activate_needs_the_deactivate_grant(client, session, admin_user, invited_user):
    headers = await _custom_role_headers(session, admin_user.organization_id, "noact@test.com", [("users", "view")])

    response = await client.post(f"/api/users/{invited_user.id}/activate", headers=headers)

    assert response.status_code == 403
    await session.refresh(invited_user)
    assert invited_user.status == "invited"
