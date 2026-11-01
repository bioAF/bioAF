"""Behavioral grant, key-scope, and Organization-boundary regressions.

test_route_permission_contract.py proves each route declares its gate; this file
proves the gate does what it says for JWT users, custom roles, and API keys, and
that a record owned by another Organization answers 404.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.project import Project
from app.models.role import Role
from app.models.sample import Sample
from app.models.sample_batch import SampleBatch
from app.models.sequencing_batch import SequencingBatch
from app.models.user import User
from app.services import api_key_service, role_service, service_account_service
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles


async def _custom_role_headers(session, org_id: int, email: str, grants: list[tuple[str, str]]) -> dict:
    """A signed-in JWT user holding a custom role with exactly these grants."""
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


async def _api_key_headers(session, admin_user, role_id: int, scopes: list[str]) -> dict:
    """An API key on a service account with this role, narrowed to these scopes."""
    account = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name=f"Key {','.join(scopes)}",
        role_id=role_id,
        created_by_user_id=admin_user.id,
    )
    _row, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=account.id,
        name="key",
        scopes=scopes,
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def foreign(session, admin_user) -> SimpleNamespace:
    """A second Organization with its own admin, and role, in the same install."""
    org = Organization(name="Other Org", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="admin@other.test",
        password_hash=AuthService.hash_password("otherpassword1"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return SimpleNamespace(org=org, roles=roles, user=user)


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/users"),
        ("get", "/api/users/{me}"),
        ("post", "/api/users/{me}/deactivate"),
        ("post", "/api/components/select-batch"),
        ("get", "/api/bootstrap/smtp-settings"),
        ("post", "/api/bootstrap/complete"),
    ],
)
@pytest.mark.asyncio
async def test_a_narrow_key_cannot_use_its_admin_roles_other_grants(client, session, admin_user, method, path):
    """Role holds the grant, key scope does not: the narrower of the two wins."""
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:view"])
    kwargs = {"json": {"keys": []}} if path.endswith("select-batch") else {}
    response = await getattr(client, method)(path.format(me=admin_user.id), headers=headers, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"] == "key_scope_missing"


@pytest.mark.asyncio
async def test_a_custom_role_gets_only_the_user_routes_it_was_granted(client, session, admin_user):
    headers = await _custom_role_headers(session, admin_user.organization_id, "cr@test.com", [("users", "view")])
    assert (await client.get("/api/users", headers=headers)).status_code == 200
    denied = await client.post(f"/api/users/{admin_user.id}/deactivate", headers=headers)
    assert denied.status_code == 403


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("get", "", None),
        ("patch", "", {"name": "Taken Over"}),
        ("post", "/deactivate", None),
        ("post", "/lock", None),
        ("post", "/reactivate", None),
        ("delete", "", None),
        ("post", "/resend-invite", None),
        ("post", "/admin-reset-password", {"mode": "temporary", "temporary_password": "Pwned-password-1"}),
    ],
)
@pytest.mark.asyncio
async def test_a_user_in_another_organization_answers_404(
    client, session, admin_headers, foreign, method, suffix, body
):
    original_hash, original_name, original_status = foreign.user.password_hash, foreign.user.name, foreign.user.status
    kwargs = {"json": body} if body is not None else {}
    response = await getattr(client, method)(f"/api/users/{foreign.user.id}{suffix}", headers=admin_headers, **kwargs)
    assert response.status_code == 404

    await session.refresh(foreign.user)
    assert foreign.user.status == original_status
    assert foreign.user.name == original_name
    assert foreign.user.password_hash == original_hash


@pytest.mark.asyncio
async def test_a_user_cannot_be_given_another_organizations_role(client, session, admin_headers, admin_user, foreign):
    viewer = admin_user._test_role_map["viewer"]
    target = User(email="t@test.com", password_hash="x", role_id=viewer, organization_id=admin_user.organization_id)
    session.add(target)
    await session.commit()

    response = await client.patch(
        f"/api/users/{target.id}", headers=admin_headers, json={"role_id": foreign.roles["admin"]}
    )
    assert response.status_code == 404
    await session.refresh(target)
    assert target.role_id == viewer


@pytest.mark.asyncio
async def test_an_invitation_cannot_carry_another_organizations_role(client, session, admin_headers, foreign):
    single = await client.post(
        "/api/users", headers=admin_headers, json={"email": "new@test.com", "role_id": foreign.roles["admin"]}
    )
    assert single.status_code == 404

    bulk = await client.post(
        "/api/users/bulk-invite",
        headers=admin_headers,
        json={"invites": [{"email": "bulk@test.com", "role_id": foreign.roles["admin"]}]},
    )
    assert bulk.status_code == 404

    invited = await session.execute(select(User).where(User.email.in_(["new@test.com", "bulk@test.com"])))
    assert invited.scalars().all() == []


@pytest.mark.asyncio
async def test_a_bulk_invitation_with_one_foreign_role_invites_nobody(
    client, session, admin_headers, admin_user, foreign
):
    response = await client.post(
        "/api/users/bulk-invite",
        headers=admin_headers,
        json={
            "invites": [
                {"email": "ok@test.com", "role_id": admin_user._test_role_map["viewer"]},
                {"email": "bad@test.com", "role_id": foreign.roles["admin"]},
            ]
        },
    )
    assert response.status_code == 404
    invited = await session.execute(select(User).where(User.email == "ok@test.com"))
    assert invited.scalars().all() == []


@pytest.mark.asyncio
async def test_same_organization_user_management_still_works(client, session, admin_headers, admin_user):
    viewer = admin_user._test_role_map["viewer"]
    comp_bio = admin_user._test_role_map["comp_bio"]
    target = User(email="same@test.com", password_hash="x", role_id=viewer, organization_id=admin_user.organization_id)
    session.add(target)
    await session.commit()

    assert (await client.get(f"/api/users/{target.id}", headers=admin_headers)).status_code == 200
    changed = await client.patch(f"/api/users/{target.id}", headers=admin_headers, json={"role_id": comp_bio})
    assert changed.status_code == 200
    assert changed.json()["role_id"] == comp_bio
    roles = await session.execute(select(Role.id).where(Role.organization_id == admin_user.organization_id))
    assert comp_bio in roles.scalars().all()


@pytest_asyncio.fixture
async def foreign_records(session, foreign) -> SimpleNamespace:
    """Records that belong to the second Organization."""
    project = Project(organization_id=foreign.org.id, name="Foreign Project", code="FP", owner_user_id=foreign.user.id)
    session.add(project)
    await session.flush()
    experiment = Experiment(organization_id=foreign.org.id, name="Foreign Exp", project_id=project.id)
    session.add(experiment)
    await session.flush()
    sample_batch = SampleBatch(experiment_id=experiment.id, name="foreign-batch")
    sequencing_batch = SequencingBatch(organization_id=foreign.org.id, name="fs", code="FSEQ", status="pending")
    session.add_all([sample_batch, sequencing_batch])
    await session.flush()
    sample = Sample(
        experiment_id=experiment.id,
        external_id="foreign-sample",
        status="registered",
        sample_batch_id=sample_batch.id,
        sequencing_batch_id=sequencing_batch.id,
    )
    session.add(sample)
    await session.commit()
    return SimpleNamespace(
        project=project,
        experiment=experiment,
        sample=sample,
        sample_batch=sample_batch,
        sequencing_batch=sequencing_batch,
    )


@pytest_asyncio.fixture
async def own_records(session, admin_user) -> SimpleNamespace:
    """The same shape of records in the caller's Organization, as success controls."""
    project = Project(
        organization_id=admin_user.organization_id, name="Own Project", code="OP", owner_user_id=admin_user.id
    )
    session.add(project)
    await session.flush()
    experiment = Experiment(organization_id=admin_user.organization_id, name="Own Exp", project_id=project.id)
    session.add(experiment)
    await session.flush()
    sample_batch = SampleBatch(experiment_id=experiment.id, name="own-batch")
    sequencing_batch = SequencingBatch(
        organization_id=admin_user.organization_id, name="os", code="OSEQ", status="pending"
    )
    session.add_all([sample_batch, sequencing_batch])
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="own-sample", status="registered")
    session.add(sample)
    await session.commit()
    return SimpleNamespace(
        project=project,
        experiment=experiment,
        sample=sample,
        sample_batch=sample_batch,
        sequencing_batch=sequencing_batch,
    )


EXPERIMENT_READS = [
    "/api/experiments",
    "/api/experiments/{experiment_id}",
    "/api/experiments/{experiment_id}/audit",
    "/api/experiments/{experiment_id}/files",
    "/api/experiments/{experiment_id}/sample-batches",
    "/api/experiments/{experiment_id}/samples",
    "/api/experiments/{experiment_id}/samples/csv-template",
    "/api/experiments/{experiment_id}/sequencing-batches",
]


@pytest.mark.parametrize("path", EXPERIMENT_READS)
@pytest.mark.asyncio
async def test_a_samples_view_key_cannot_read_experiments(client, session, admin_user, own_records, path):
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:view"])
    response = await client.get(path.format(experiment_id=own_records.experiment.id), headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize("path", EXPERIMENT_READS)
@pytest.mark.asyncio
async def test_experiment_reads_follow_the_custom_role_grant(client, session, admin_user, own_records, path):
    org_id = admin_user.organization_id
    allowed = await _custom_role_headers(session, org_id, "allow@test.com", [("experiments", "view")])
    denied = await _custom_role_headers(session, org_id, "deny@test.com", [("samples", "view")])
    url = path.format(experiment_id=own_records.experiment.id)
    assert (await client.get(url, headers=allowed)).status_code == 200
    assert (await client.get(url, headers=denied)).status_code == 403


@pytest.mark.parametrize("path", [p for p in EXPERIMENT_READS if "{" in p and "csv-template" not in p])
@pytest.mark.asyncio
async def test_a_foreign_experiment_answers_404_on_nested_reads(client, admin_headers, foreign_records, path):
    response = await client.get(path.format(experiment_id=foreign_records.experiment.id), headers=admin_headers)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "suffix,kwargs",
    [
        ("/samples", {"json": {"external_id": "smuggled"}}),
        ("/samples/bulk", {"json": {"samples": [{"external_id": "smuggled"}]}}),
        ("/samples/upload/preview", {"files": {"file": ("s.csv", b"external_id\nsmuggled\n", "text/csv")}}),
        ("/samples/upload/confirm", {"files": {"file": ("s.csv", b"external_id\nsmuggled\n", "text/csv")}}),
        ("/samples/upload", {"files": {"file": ("s.csv", b"external_id\nsmuggled\n", "text/csv")}}),
        ("/sample-batches", {"json": {"name": "smuggled"}}),
    ],
)
@pytest.mark.asyncio
async def test_a_foreign_experiment_answers_404_on_nested_writes(
    client, session, admin_headers, foreign_records, suffix, kwargs
):
    url = f"/api/experiments/{foreign_records.experiment.id}{suffix}"
    assert (await client.post(url, headers=admin_headers, **kwargs)).status_code == 404

    samples = await session.execute(select(Sample.id).where(Sample.experiment_id == foreign_records.experiment.id))
    batches = await session.execute(
        select(SampleBatch.id).where(SampleBatch.experiment_id == foreign_records.experiment.id)
    )
    assert samples.scalars().all() == [foreign_records.sample.id]
    assert batches.scalars().all() == [foreign_records.sample_batch.id]


@pytest.mark.asyncio
async def test_same_organization_nested_writes_still_succeed(client, session, admin_headers, own_records):
    url = f"/api/experiments/{own_records.experiment.id}/samples"
    created = await client.post(url, headers=admin_headers, json={"external_id": "fresh"})
    assert created.status_code == 200
    assert created.json()["external_id"] == "fresh"
    listed = await client.get(url, headers=admin_headers)
    assert {s["external_id"] for s in listed.json()} == {"own-sample", "fresh"}


@pytest.mark.parametrize("path", ["/api/samples/{id}", "/api/samples/{id}/read-groups"])
@pytest.mark.asyncio
async def test_sample_reads_need_samples_view(client, session, admin_user, own_records, path):
    org_id = admin_user.organization_id
    allowed = await _custom_role_headers(session, org_id, "s-allow@test.com", [("samples", "view")])
    denied = await _custom_role_headers(session, org_id, "s-deny@test.com", [("experiments", "view")])
    url = path.format(id=own_records.sample.id)
    assert (await client.get(url, headers=allowed)).status_code == 200
    assert (await client.get(url, headers=denied)).status_code == 403


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("get", "", None),
        ("get", "/read-groups", None),
        ("patch", "", {"organism": "tampered"}),
        ("patch", "/qc", {"qc_status": "fail"}),
        ("patch", "/status", {"status": "library_prepped"}),
    ],
)
@pytest.mark.asyncio
async def test_a_foreign_sample_answers_404(client, session, admin_headers, foreign_records, method, suffix, body):
    kwargs = {"json": body} if body is not None else {}
    url = f"/api/samples/{foreign_records.sample.id}{suffix}"
    assert (await getattr(client, method)(url, headers=admin_headers, **kwargs)).status_code == 404

    await session.refresh(foreign_records.sample)
    assert foreign_records.sample.status == "registered"
    assert foreign_records.sample.organism is None
    assert foreign_records.sample.qc_status is None


@pytest.mark.asyncio
async def test_bulk_sample_routes_skip_foreign_samples(client, session, admin_headers, foreign_records, own_records):
    update = await client.patch(
        "/api/samples/bulk/update",
        headers=admin_headers,
        json={
            "sample_ids": [own_records.sample.id, foreign_records.sample.id],
            "update": {"organism": "mouse"},
        },
    )
    assert update.status_code == 200
    assert update.json() == {"updated": 1, "errors": [f"Sample {foreign_records.sample.id} not found"]}

    deleted = await client.post(
        "/api/samples/bulk/delete",
        headers=admin_headers,
        json={"sample_ids": [own_records.sample.id, foreign_records.sample.id]},
    )
    assert deleted.json() == {"deleted": 1}

    foreign_id = foreign_records.sample.id
    session.expire_all()
    remaining = await session.get(Sample, foreign_id)
    assert remaining is not None
    assert remaining.organism is None


@pytest.mark.parametrize(
    "path,grant",
    [
        ("/api/projects/{project}", ("projects", "view")),
        ("/api/projects/{project}/provenance", ("projects", "view")),
        ("/api/files", ("files", "view")),
        ("/api/files/stats", ("files", "view")),
        ("/api/datasets", ("experiments", "view")),
        ("/api/datasets/filter-options", ("experiments", "view")),
        ("/api/datasets/{experiment}", ("experiments", "view")),
        ("/api/templates", ("experiments", "view")),
        ("/api/sample-batches/{sample_batch}", ("experiments", "view")),
        ("/api/sequencing-batches", ("experiments", "view")),
        ("/api/sequencing-batches/{sequencing_batch}", ("experiments", "view")),
    ],
)
@pytest.mark.asyncio
async def test_read_routes_follow_their_grant(client, session, admin_user, own_records, path, grant):
    org_id = admin_user.organization_id
    other_grant = ("audit_log", "view")
    allowed = await _custom_role_headers(session, org_id, "g-allow@test.com", [grant])
    denied = await _custom_role_headers(session, org_id, "g-deny@test.com", [other_grant])
    url = path.format(
        project=own_records.project.id,
        experiment=own_records.experiment.id,
        sample_batch=own_records.sample_batch.id,
        sequencing_batch=own_records.sequencing_batch.id,
    )
    assert (await client.get(url, headers=allowed)).status_code == 200
    assert (await client.get(url, headers=denied)).status_code == 403


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/sample-batches/{sample_batch}", None),
        ("patch", "/api/sample-batches/{sample_batch}", {"name": "tampered"}),
        ("post", "/api/sample-batches/{sample_batch}/assign-samples", {"sample_ids": []}),
        ("get", "/api/sequencing-batches/{sequencing_batch}", None),
        ("patch", "/api/sequencing-batches/{sequencing_batch}", {"name": "tampered"}),
    ],
)
@pytest.mark.asyncio
async def test_a_foreign_batch_answers_404(client, session, admin_headers, foreign_records, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    url = path.format(
        sample_batch=foreign_records.sample_batch.id, sequencing_batch=foreign_records.sequencing_batch.id
    )
    assert (await getattr(client, method)(url, headers=admin_headers, **kwargs)).status_code == 404

    await session.refresh(foreign_records.sample_batch)
    await session.refresh(foreign_records.sequencing_batch)
    assert foreign_records.sample_batch.name == "foreign-batch"
    assert foreign_records.sequencing_batch.name == "fs"
