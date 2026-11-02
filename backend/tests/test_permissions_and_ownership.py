"""Behavioral grant, key-scope, and Organization-boundary regressions.

test_route_permission_contract.py proves each route declares its gate; this file
proves the gate does what it says for JWT users, custom roles, and API keys, and
that a record owned by another Organization answers 404.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.experiment import Experiment
from app.models.file import File
from app.models.notebook_session import ComputeSession
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.project_sample import ProjectSample
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


async def _audit_rows(session, entity_type: str, entity_id: int) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog).where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_a_write_through_an_api_key_is_attributed_to_the_key(client, session, admin_user, own_records):
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:edit"])
    key_result = await session.execute(select(ApiKey).where(ApiKey.created_by_user_id == admin_user.id))
    key = key_result.scalar_one()

    response = await client.patch(f"/api/samples/{own_records.sample.id}", headers=headers, json={"organism": "mouse"})
    assert response.status_code == 200

    [row] = await _audit_rows(session, "sample", own_records.sample.id)
    assert row.api_key_id == key.id
    assert row.user_id == key.service_account_user_id


@pytest.mark.asyncio
async def test_a_write_through_a_session_has_no_key_attribution(
    client, session, admin_headers, admin_user, own_records
):
    response = await client.patch(
        f"/api/samples/{own_records.sample.id}", headers=admin_headers, json={"organism": "mouse"}
    )
    assert response.status_code == 200

    [row] = await _audit_rows(session, "sample", own_records.sample.id)
    assert row.api_key_id is None
    assert row.user_id == admin_user.id


@pytest.mark.asyncio
async def test_key_attribution_does_not_outlive_the_request(client, session, admin_headers, admin_user, own_records):
    """A session request right after a key request must not inherit the key."""
    key_headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:edit"])
    url = f"/api/samples/{own_records.sample.id}"
    assert (await client.patch(url, headers=key_headers, json={"organism": "mouse"})).status_code == 200
    assert (await client.patch(url, headers=admin_headers, json={"organism": "rat"})).status_code == 200

    rows = await _audit_rows(session, "sample", own_records.sample.id)
    latest = max(rows, key=lambda row: row.id)
    assert latest.api_key_id is None


@pytest.mark.asyncio
async def test_a_denied_key_leaves_no_audit_trail(client, session, admin_user, own_records):
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:view"])
    url = f"/api/samples/{own_records.sample.id}"
    assert (await client.patch(url, headers=headers, json={"organism": "mouse"})).status_code == 403
    assert await _audit_rows(session, "sample", own_records.sample.id) == []


@pytest_asyncio.fixture
async def own_document(session, admin_user) -> Document:
    file = File(
        organization_id=admin_user.organization_id,
        filename="protocol.pdf",
        storage_uri="gs://bucket/protocol.pdf",
        file_type="pdf",
        source_type="upload",
    )
    session.add(file)
    await session.flush()
    document = Document(organization_id=admin_user.organization_id, file_id=file.id, title="Protocol")
    session.add(document)
    await session.commit()
    return document


@pytest.mark.parametrize(
    "path,grant",
    [
        ("/api/documents", ("files", "view")),
        ("/api/documents/{document}", ("files", "view")),
        ("/api/documents/{document}/download", ("files", "download")),
    ],
)
@pytest.mark.asyncio
async def test_document_reads_follow_their_grant(client, session, admin_user, own_document, path, grant):
    org_id = admin_user.organization_id
    allowed = await _custom_role_headers(session, org_id, "d-allow@test.com", [grant])
    denied = await _custom_role_headers(session, org_id, "d-deny@test.com", [("audit_log", "view")])
    url = path.format(document=own_document.id)
    assert (await client.get(url, headers=allowed)).status_code == 200
    assert (await client.get(url, headers=denied)).status_code == 403


@pytest.mark.asyncio
async def test_viewing_a_document_does_not_grant_its_download(client, session, admin_user, own_document):
    headers = await _custom_role_headers(session, admin_user.organization_id, "view-only@test.com", [("files", "view")])
    assert (await client.get(f"/api/documents/{own_document.id}/download", headers=headers)).status_code == 403


@pytest.mark.parametrize("path", ["/api/plots", "/api/cellxgene", "/api/cellxgene/publishable-files"])
@pytest.mark.parametrize("grant", [("experiments", "view"), ("pipelines", "view")])
@pytest.mark.asyncio
async def test_results_routes_accept_either_results_grant(client, session, admin_user, path, grant):
    headers = await _custom_role_headers(session, admin_user.organization_id, "r@test.com", [grant])
    assert (await client.get(path, headers=headers)).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/plots",
        "/api/plots/1",
        "/api/plots/1/thumbnail",
        "/api/cellxgene",
        "/api/cellxgene/1",
        "/api/cellxgene/publishable-files",
        "/api/cellxgene/inspect/1",
    ],
)
@pytest.mark.asyncio
async def test_results_routes_refuse_a_role_with_neither_grant(client, session, admin_user, path):
    headers = await _custom_role_headers(session, admin_user.organization_id, "none@test.com", [("audit_log", "view")])
    assert (await client.get(path, headers=headers)).status_code == 403


@pytest.mark.parametrize(
    "path", ["/api/storage/stats", "/api/storage/lifecycle", "/api/health/status", "/api/health/services"]
)
@pytest.mark.asyncio
async def test_platform_state_needs_infrastructure_view(client, session, admin_user, viewer_token, path):
    org_id = admin_user.organization_id
    allowed = await _custom_role_headers(session, org_id, "i-allow@test.com", [("infrastructure", "view")])
    assert (await client.get(path, headers=allowed)).status_code == 200
    viewer = {"Authorization": f"Bearer {viewer_token}"}
    assert (await client.get(path, headers=viewer)).status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_can_still_list_components(client, viewer_token):
    response = await client.get("/api/components", headers={"Authorization": f"Bearer {viewer_token}"})
    assert response.status_code == 200


def _snapshot(org_id: int, user_id: int, label: str, **links) -> AnalysisSnapshot:
    return AnalysisSnapshot(organization_id=org_id, user_id=user_id, label=label, object_type="anndata", **links)


@pytest_asyncio.fixture
async def snapshots(session, admin_user, own_records) -> SimpleNamespace:
    """One snapshot for each way a snapshot can reach a project or experiment."""
    org_id = admin_user.organization_id
    loose = Experiment(organization_id=org_id, name="No project")
    session.add(loose)
    await session.flush()
    records = {
        "project_only": _snapshot(org_id, admin_user.id, "project only", project_id=own_records.project.id),
        "experiment_only": _snapshot(org_id, admin_user.id, "experiment only", experiment_id=loose.id),
        "dual": _snapshot(
            org_id,
            admin_user.id,
            "dual",
            project_id=own_records.project.id,
            experiment_id=own_records.experiment.id,
        ),
        "nested": _snapshot(org_id, admin_user.id, "nested", experiment_id=own_records.experiment.id),
    }
    session.add_all(records.values())
    await session.commit()
    return SimpleNamespace(**records, loose=loose, records=records)


SNAPSHOT_VISIBILITY = {
    "projects": {"project_only", "dual", "nested"},
    "experiments": {"experiment_only", "dual", "nested"},
}


@pytest.mark.parametrize("resource", sorted(SNAPSHOT_VISIBILITY))
@pytest.mark.asyncio
async def test_snapshot_list_shows_what_either_path_grants(client, session, admin_user, snapshots, resource):
    headers = await _custom_role_headers(
        session, admin_user.organization_id, f"{resource}@test.com", [(resource, "view")]
    )
    response = await client.get("/api/snapshots", headers=headers)
    assert response.status_code == 200
    expected = {snapshots.records[name].id for name in SNAPSHOT_VISIBILITY[resource]}
    assert {s["id"] for s in response.json()["snapshots"]} == expected
    assert response.json()["total"] == len(expected)


@pytest.mark.parametrize("resource", sorted(SNAPSHOT_VISIBILITY))
@pytest.mark.parametrize("name", ["project_only", "experiment_only", "dual", "nested"])
@pytest.mark.asyncio
async def test_snapshot_detail_matches_the_list(client, session, admin_user, snapshots, resource, name):
    headers = await _custom_role_headers(
        session, admin_user.organization_id, f"{resource}{name}@test.com", [(resource, "view")]
    )
    response = await client.get(f"/api/snapshots/{snapshots.records[name].id}", headers=headers)
    assert response.status_code == (200 if name in SNAPSHOT_VISIBILITY[resource] else 403)


@pytest.mark.parametrize("resource", sorted(SNAPSHOT_VISIBILITY))
@pytest.mark.asyncio
async def test_snapshot_compare_matches_the_list(client, session, admin_user, snapshots, resource):
    headers = await _custom_role_headers(
        session, admin_user.organization_id, f"compare-{resource}@test.com", [(resource, "view")]
    )
    allowed = ",".join(str(snapshots.records[n].id) for n in ("dual", "nested"))
    assert (await client.get(f"/api/snapshots/compare?ids={allowed}", headers=headers)).status_code == 200

    hidden = "experiment_only" if resource == "projects" else "project_only"
    mixed = f"{snapshots.dual.id},{snapshots.records[hidden].id}"
    assert (await client.get(f"/api/snapshots/compare?ids={mixed}", headers=headers)).status_code == 403


@pytest.mark.parametrize("path", ["/api/snapshots", "/api/snapshots/{id}", "/api/snapshots/compare?ids={id},{id2}"])
@pytest.mark.asyncio
async def test_snapshots_refuse_a_role_with_neither_path(client, session, admin_user, snapshots, path):
    headers = await _custom_role_headers(
        session, admin_user.organization_id, "neither@test.com", [("audit_log", "view")]
    )
    url = path.format(id=snapshots.dual.id, id2=snapshots.nested.id)
    assert (await client.get(url, headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_a_snapshot_key_is_narrowed_by_its_scope(client, session, admin_user, snapshots):
    """The role holds both paths; the key's scope leaves only the project path."""
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["projects:view"])
    listed = await client.get("/api/snapshots", headers=headers)
    expected = {snapshots.records[name].id for name in SNAPSHOT_VISIBILITY["projects"]}
    assert {s["id"] for s in listed.json()["snapshots"]} == expected
    denied = await client.get(f"/api/snapshots/{snapshots.experiment_only.id}", headers=headers)
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_a_foreign_snapshot_answers_404(client, session, admin_headers, foreign_records, foreign, snapshots):
    theirs = _snapshot(foreign.org.id, foreign.user.id, "theirs", project_id=foreign_records.project.id)
    session.add(theirs)
    await session.commit()

    assert (await client.get(f"/api/snapshots/{theirs.id}", headers=admin_headers)).status_code == 404
    compare = await client.get(f"/api/snapshots/compare?ids={snapshots.dual.id},{theirs.id}", headers=admin_headers)
    assert compare.status_code == 404
    listed = await client.get("/api/snapshots", headers=admin_headers)
    assert theirs.id not in {s["id"] for s in listed.json()["snapshots"]}


@pytest.mark.asyncio
async def test_a_snapshot_linked_only_to_a_foreign_experiment_gives_no_project_path(
    client, session, admin_user, foreign_records
):
    """A stored link into another Organization must not open a path of its own."""
    stale = _snapshot(admin_user.organization_id, admin_user.id, "stale", experiment_id=foreign_records.experiment.id)
    session.add(stale)
    await session.commit()
    headers = await _custom_role_headers(session, admin_user.organization_id, "stale@test.com", [("projects", "view")])
    assert (await client.get(f"/api/snapshots/{stale.id}", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_a_nested_snapshot_through_a_foreign_project_gives_no_project_path(
    client, session, admin_user, foreign_records
):
    org_id = admin_user.organization_id
    experiment = Experiment(organization_id=org_id, name="stale link", project_id=foreign_records.project.id)
    session.add(experiment)
    await session.flush()
    stale = _snapshot(org_id, admin_user.id, "stale nested", experiment_id=experiment.id)
    session.add(stale)
    await session.commit()
    headers = await _custom_role_headers(session, org_id, "stale-nested@test.com", [("projects", "view")])

    assert (await client.get(f"/api/snapshots/{stale.id}", headers=headers)).status_code == 403
    listed = await client.get("/api/snapshots", headers=headers)
    assert stale.id not in {s["id"] for s in listed.json()["snapshots"]}


async def _project_snapshot_view(client, headers, project_id: int) -> tuple[set[int], int, int]:
    """(timeline IDs, list count, detail count) for one project."""
    timeline = await client.get(f"/api/snapshots?project_id={project_id}", headers=headers)
    listing = await client.get("/api/projects", headers=headers)
    detail = await client.get(f"/api/projects/{project_id}", headers=headers)
    listed = next(p for p in listing.json()["projects"] if p["id"] == project_id)
    return {s["id"] for s in timeline.json()["snapshots"]}, listed["snapshot_count"], detail.json()["snapshot_count"]


@pytest.mark.asyncio
async def test_project_timeline_and_both_counts_include_nested_snapshots(client, admin_headers, own_records, snapshots):
    ids, list_count, detail_count = await _project_snapshot_view(client, admin_headers, own_records.project.id)
    assert ids == {snapshots.project_only.id, snapshots.dual.id, snapshots.nested.id}
    assert list_count == detail_count == 3


@pytest.mark.asyncio
async def test_a_dual_linked_snapshot_counts_once(client, admin_headers, own_records, snapshots):
    ids, list_count, detail_count = await _project_snapshot_view(client, admin_headers, own_records.project.id)
    assert len(ids) == list_count == detail_count


@pytest.mark.asyncio
async def test_a_cross_project_snapshot_appears_on_both_projects(
    client, session, admin_headers, admin_user, own_records
):
    org_id = admin_user.organization_id
    other = Project(organization_id=org_id, name="Other", code="OTH", owner_user_id=admin_user.id)
    session.add(other)
    await session.flush()
    cross = _snapshot(org_id, admin_user.id, "cross", project_id=other.id, experiment_id=own_records.experiment.id)
    session.add(cross)
    await session.commit()

    for project_id in (other.id, own_records.project.id):
        ids, list_count, detail_count = await _project_snapshot_view(client, admin_headers, project_id)
        assert ids == {cross.id}
        assert list_count == detail_count == 1


@pytest.mark.asyncio
async def test_project_counts_ignore_foreign_associations(
    client, session, admin_headers, own_records, foreign, foreign_records
):
    """Neither a foreign snapshot naming our project, nor one on an experiment
    that points at our project from outside, is ours to count."""
    foreign_records.experiment.project_id = own_records.project.id
    session.add_all(
        [
            _snapshot(foreign.org.id, foreign.user.id, "direct", project_id=own_records.project.id),
            _snapshot(foreign.org.id, foreign.user.id, "nested", experiment_id=foreign_records.experiment.id),
        ]
    )
    await session.commit()

    ids, list_count, detail_count = await _project_snapshot_view(client, admin_headers, own_records.project.id)
    assert ids == set()
    assert list_count == detail_count == 0


@pytest.mark.asyncio
async def test_a_project_with_no_snapshots_counts_zero(client, admin_headers, own_records):
    ids, list_count, detail_count = await _project_snapshot_view(client, admin_headers, own_records.project.id)
    assert ids == set()
    assert list_count == detail_count == 0


@pytest.mark.asyncio
async def test_project_provenance_does_not_expand_with_the_timeline(client, admin_headers, own_records, snapshots):
    response = await client.get(f"/api/projects/{own_records.project.id}/provenance", headers=admin_headers)
    assert response.status_code == 200
    snapshot_nodes = [n for n in response.json()["nodes"] if n["type"] == "snapshot"]
    assert {n["label"] for n in snapshot_nodes} == {"project only", "dual"}


@pytest.mark.asyncio
async def test_the_project_list_needs_projects_view(client, session, admin_user, own_records):
    org_id = admin_user.organization_id
    allowed = await _custom_role_headers(session, org_id, "p-allow@test.com", [("projects", "view")])
    denied = await _custom_role_headers(session, org_id, "p-deny@test.com", [("experiments", "view")])
    response = await client.get("/api/projects", headers=allowed)
    assert [p["id"] for p in response.json()["projects"]] == [own_records.project.id]
    assert (await client.get("/api/projects", headers=denied)).status_code == 403


def _snapshot_body(**links) -> dict:
    return {"label": "new", "object_type": "anndata", **links}


async def _snapshot_labels(session) -> list[str]:
    result = await session.execute(select(AnalysisSnapshot.label))
    return list(result.scalars().all())


@pytest_asyncio.fixture
async def foreign_references(session, foreign, foreign_records) -> SimpleNamespace:
    """Each kind of record a new snapshot can point at, in the other Organization."""
    file = File(organization_id=foreign.org.id, storage_uri="gs://b/f.png", filename="f.png", file_type="image")
    notebook = ComputeSession(
        user_id=foreign.user.id,
        organization_id=foreign.org.id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=1,
        memory_gb=1,
    )
    session.add_all([file, notebook])
    await session.commit()
    return SimpleNamespace(
        experiment_id=foreign_records.experiment.id,
        project_id=foreign_records.project.id,
        figure_file_id=file.id,
        checkpoint_file_id=file.id,
        notebook_session_id=notebook.id,
    )


@pytest.mark.parametrize(
    "field", ["experiment_id", "project_id", "figure_file_id", "checkpoint_file_id", "notebook_session_id"]
)
@pytest.mark.asyncio
async def test_a_new_snapshot_cannot_reference_a_foreign_record(
    client, session, admin_headers, own_records, foreign_references, field
):
    links = {"experiment_id": own_records.experiment.id, field: getattr(foreign_references, field)}
    response = await client.post("/api/snapshots", headers=admin_headers, json=_snapshot_body(**links))
    assert response.status_code == 404
    assert await _snapshot_labels(session) == []


@pytest.mark.asyncio
async def test_a_new_snapshot_can_reference_its_own_records(client, session, admin_headers, admin_user, own_records):
    org_id = admin_user.organization_id
    file = File(organization_id=org_id, storage_uri="gs://b/o.png", filename="o.png", file_type="image")
    notebook = ComputeSession(
        user_id=admin_user.id,
        organization_id=org_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=1,
        memory_gb=1,
    )
    session.add_all([file, notebook])
    await session.commit()
    response = await client.post(
        "/api/snapshots",
        headers=admin_headers,
        json=_snapshot_body(
            experiment_id=own_records.experiment.id,
            project_id=own_records.project.id,
            figure_file_id=file.id,
            checkpoint_file_id=file.id,
            notebook_session_id=notebook.id,
        ),
    )
    assert response.status_code == 200
    assert await _snapshot_labels(session) == ["new"]


@pytest.mark.asyncio
async def test_a_snapshot_may_name_an_experiment_outside_its_project(
    client, session, admin_headers, admin_user, own_records
):
    """Decision 5c.1: cross-project work within one Organization stays allowed."""
    other = Project(organization_id=admin_user.organization_id, name="Other", code="OTH", owner_user_id=admin_user.id)
    session.add(other)
    await session.commit()
    response = await client.post(
        "/api/snapshots",
        headers=admin_headers,
        json=_snapshot_body(experiment_id=own_records.experiment.id, project_id=other.id),
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_foreign_snapshot_cannot_be_starred(client, session, admin_headers, foreign, foreign_records):
    theirs = _snapshot(foreign.org.id, foreign.user.id, "theirs", project_id=foreign_records.project.id)
    session.add(theirs)
    await session.commit()

    assert (await client.post(f"/api/snapshots/{theirs.id}/star", headers=admin_headers)).status_code == 404
    await session.refresh(theirs)
    assert theirs.starred is False
    assert await _audit_rows(session, "analysis_snapshot", theirs.id) == []


@pytest.mark.asyncio
async def test_an_own_snapshot_can_be_starred(client, admin_headers, snapshots):
    response = await client.post(f"/api/snapshots/{snapshots.dual.id}/star", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["starred"] is True


async def _project_names(session) -> list[str]:
    result = await session.execute(select(Project.name).order_by(Project.name))
    return list(result.scalars().all())


@pytest.mark.parametrize("mixed", [False, True], ids=["all-foreign", "mixed"])
@pytest.mark.asyncio
async def test_a_new_project_cannot_take_foreign_samples(
    client, session, admin_headers, own_records, foreign_records, mixed
):
    sample_ids = [foreign_records.sample.id] + ([own_records.sample.id] if mixed else [])
    before = await _project_names(session)
    response = await client.post(
        "/api/projects", headers=admin_headers, json={"name": "Smuggler", "sample_ids": sample_ids}
    )
    assert response.status_code == 404
    assert await _project_names(session) == before


@pytest.mark.asyncio
async def test_a_new_project_can_take_samples_across_its_own_experiments(
    client, session, admin_headers, admin_user, own_records
):
    second = Experiment(organization_id=admin_user.organization_id, name="Second")
    session.add(second)
    await session.flush()
    sample = Sample(experiment_id=second.id, external_id="second", status="registered")
    session.add(sample)
    await session.commit()
    response = await client.post(
        "/api/projects",
        headers=admin_headers,
        json={"name": "Cross", "sample_ids": [own_records.sample.id, sample.id]},
    )
    assert response.status_code == 200
    assert response.json()["sample_count"] == 2


@pytest.mark.parametrize("mixed", [False, True], ids=["all-foreign", "mixed"])
@pytest.mark.asyncio
async def test_a_project_cannot_add_foreign_samples(
    client, session, admin_headers, own_records, foreign_records, mixed
):
    sample_ids = [foreign_records.sample.id] + ([own_records.sample.id] if mixed else [])
    response = await client.post(
        f"/api/projects/{own_records.project.id}/samples", headers=admin_headers, json={"sample_ids": sample_ids}
    )
    assert response.status_code == 404
    rows = await session.execute(select(ProjectSample).where(ProjectSample.project_id == own_records.project.id))
    assert rows.scalars().all() == []


@pytest.mark.asyncio
async def test_a_project_can_add_its_organizations_samples(client, session, admin_headers, own_records):
    response = await client.post(
        f"/api/projects/{own_records.project.id}/samples",
        headers=admin_headers,
        json={"sample_ids": [own_records.sample.id]},
    )
    assert response.status_code == 200
    assert response.json()["added"] == 1


@pytest.mark.asyncio
async def test_project_detail_hides_previously_stored_foreign_links(
    client, session, admin_headers, admin_user, own_records, foreign, foreign_records
):
    """Rows written before this boundary existed must not disclose anything."""
    session.add(
        ProjectSample(
            project_id=own_records.project.id, sample_id=foreign_records.sample.id, added_by_user_id=foreign.user.id
        )
    )
    linked_foreign = Experiment(organization_id=foreign.org.id, name="Linked", project_id=own_records.project.id)
    session.add(linked_foreign)
    await session.flush()
    session.add(
        Sample(experiment_id=linked_foreign.id, external_id="linked-foreign", status="registered"),
    )
    session.add(
        PipelineRun(
            organization_id=foreign.org.id,
            pipeline_name="foreign-run",
            pipeline_version="1",
            experiment_id=own_records.experiment.id,
            status="completed",
        )
    )
    session.add(
        ProjectSample(
            project_id=own_records.project.id, sample_id=own_records.sample.id, added_by_user_id=foreign.user.id
        )
    )
    await session.commit()

    response = await client.get(f"/api/projects/{own_records.project.id}", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert {s["external_id"] for group in body["samples"] for s in group["samples"]} == {"own-sample"}
    assert {group["experiment_name"] for group in body["samples"]} == {"Own Exp"}
    assert [e["name"] for e in body["experiments"]] == ["Own Exp"]
    assert body["sample_count"] == 1
    assert body["experiment_count"] == 1
    assert body["pipeline_runs"] == []
    assert body["pipeline_run_count"] == 0


@pytest.mark.asyncio
async def test_project_detail_keeps_same_organization_cross_project_samples(
    client, session, admin_headers, admin_user, own_records
):
    second = Experiment(organization_id=admin_user.organization_id, name="Elsewhere")
    session.add(second)
    await session.flush()
    sample = Sample(experiment_id=second.id, external_id="elsewhere", status="registered")
    session.add(sample)
    await session.flush()
    session.add(ProjectSample(project_id=own_records.project.id, sample_id=sample.id, added_by_user_id=admin_user.id))
    await session.commit()

    response = await client.get(f"/api/projects/{own_records.project.id}", headers=admin_headers)
    external_ids = {s["external_id"] for group in response.json()["samples"] for s in group["samples"]}
    assert external_ids == {"own-sample", "elsewhere"}


@pytest.mark.parametrize(
    "path",
    ["/api/health/live", "/api/health/ready", "/api/bootstrap/status"],
)
@pytest.mark.asyncio
async def test_deliberate_public_routes_answer_with_no_credentials(client, path):
    assert (await client.get(path)).status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/api/users", "/api/experiments", "/api/samples/1", "/api/projects", "/api/snapshots", "/api/health/status"],
)
@pytest.mark.asyncio
async def test_protected_routes_refuse_no_credentials(client, path):
    assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("status", ["deactivated", "locked"])
@pytest.mark.asyncio
async def test_a_key_stops_working_when_its_service_account_does(client, session, admin_user, own_records, status):
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["samples:view"])
    url = f"/api/samples/{own_records.sample.id}"
    assert (await client.get(url, headers=headers)).status_code == 200

    account = await session.scalar(select(User).where(User.is_service_account.is_(True)))
    account.status = status
    await session.commit()
    assert (await client.get(url, headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_a_deactivated_user_cannot_refresh_a_session(client, session, viewer_user, viewer_token):
    viewer_user.status = "deactivated"
    await session.commit()
    response = await client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {viewer_token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("scopes,expected", [(["experiments:view"], 200), (["projects:view"], 403)])
@pytest.mark.asyncio
async def test_a_custom_role_key_needs_both_the_grant_and_the_scope(
    client, session, admin_user, own_records, scopes, expected
):
    role = await role_service.create_role(
        session,
        admin_user.organization_id,
        "both-grants",
        permissions=[("experiments", "view"), ("projects", "view")],
    )
    await session.commit()
    role_service.invalidate_cache()
    headers = await _api_key_headers(session, admin_user, role.id, scopes)
    assert (await client.get("/api/experiments", headers=headers)).status_code == expected


@pytest.mark.asyncio
async def test_a_scope_without_the_role_grant_is_not_enough(client, session, admin_user, viewer_user):
    headers = await _api_key_headers(session, admin_user, viewer_user.role_id, ["samples:edit"])
    response = await client.post("/api/samples/bulk/delete", headers=headers, json={"sample_ids": []})
    assert response.status_code == 403
    assert response.json()["detail"] == "role_missing"
