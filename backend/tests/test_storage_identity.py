"""Store health and identity evidence (storage lifecycle stage 11C, approved D3).

Recorded Terraform output, observed provider identity and administrator
enrollment are separate facts. A bucket is trusted only when provider evidence
and recorded deployment state agree, or an administrator enrolled what was
observed. Inspection never creates resources, adopts ambiguous buckets, or
rewrites configuration from Terraform outputs alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from app.adapters.models import ContainerObservation, ContainerPresence
from app.exceptions import StateError
from app.services.bootstrap_roles import seed_builtin_roles
from app.services import storage_identity
from app.services.storage_identity import StoreHealth

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)


class FakeAdapter:
    """Answers inspect_container from a {bucket: ContainerObservation} table."""

    named_containers = True

    def __init__(self, observations: dict[str, ContainerObservation]):
        self.observations = observations
        self.inspected: list[str] = []

    def build_uri(self, bucket: str, key: str) -> str:
        return f"gs://{bucket}/{key}"

    def parse_uri(self, uri: str) -> tuple[str, str]:
        rest = uri.removeprefix("gs://")
        bucket, _, key = rest.partition("/")
        return bucket, key

    async def inspect_container(self, uri: str) -> ContainerObservation:
        bucket = self.parse_uri(uri)[0]
        self.inspected.append(bucket)
        return self.observations.get(bucket, ContainerObservation(presence=ContainerPresence.MISSING))


def _present(created=T0 + timedelta(minutes=1), owner="1234", labels=None):
    return ContainerObservation(
        presence=ContainerPresence.PRESENT,
        owner=owner,
        created_at=created,
        labels={"managed_by": "bioaf", "purpose": "raw-data"} if labels is None else labels,
    )


async def _seed_user(session) -> int:
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="IdentityOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="identity@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user.id


async def _config(session, **kv) -> None:
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


async def _apply_run(session, user_id, *, uid="abc123", started=T0, completed=T0 + timedelta(minutes=5)):
    from app.models.component import TerraformRun

    run = TerraformRun(
        triggered_by_user_id=user_id,
        action="apply",
        module_name="storage",
        status="completed",
        tfvars_json={"stack_uid": uid},
    )
    session.add(run)
    await session.flush()
    run.started_at = started
    run.completed_at = completed
    await session.commit()


RAW = "bioaf-raw-acme-abc123"


def _patched(adapter):
    return patch("app.adapters.registry.get_storage_adapter", return_value=adapter)


async def _raw_report(session):
    reports = await storage_identity.inspect_stores(session)
    return next(r for r in reports if r.store == "raw")


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "presence, health",
    [
        (ContainerPresence.MISSING, StoreHealth.NOT_FOUND),
        (ContainerPresence.FORBIDDEN, StoreHealth.NO_ACCESS),
        (ContainerPresence.UNAVAILABLE, StoreHealth.NOT_RESPONDING),
        (ContainerPresence.NOT_MOUNTED, StoreHealth.NOT_MOUNTED),
        (ContainerPresence.UNSUPPORTED, StoreHealth.CANNOT_CHECK),
    ],
)
@pytest.mark.asyncio
async def test_provider_answers_map_to_health(session, presence, health):
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    with _patched(FakeAdapter({RAW: ContainerObservation(presence=presence)})):
        report = await _raw_report(session)
    assert report.health is health
    assert report.bucket_name == RAW


@pytest.mark.asyncio
async def test_unconfigured_stores_are_not_inspected(session):
    await _config(session, raw_bucket_name=RAW, results_bucket_name="null")
    adapter = FakeAdapter({RAW: _present()})
    with _patched(adapter):
        reports = await storage_identity.inspect_stores(session)
    assert [r.store for r in reports] == ["raw"]
    assert adapter.inspected == [RAW]


@pytest.mark.asyncio
async def test_inspection_results_are_persisted_with_a_check_time(session):
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({RAW: ContainerObservation(presence=ContainerPresence.MISSING)})):
        await storage_identity.inspect_stores(session)
    await session.commit()
    stored = await storage_identity.latest_reports(session)
    assert [(r.store, r.health) for r in stored] == [("raw", StoreHealth.NOT_FOUND)]
    assert stored[0].checked_at is not None


# ---------------------------------------------------------------------------
# discovery: provider evidence agrees with recorded deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bucket_created_by_a_recorded_apply_is_discovered_and_recorded(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)

    with _patched(FakeAdapter({RAW: _present()})):
        report = await _raw_report(session)
    await session.commit()

    assert report.health is StoreHealth.AVAILABLE
    assert report.basis == "discovered"
    from app.models.storage_identity import StorageIdentity

    row = (await session.execute(select(StorageIdentity))).scalar_one()
    assert (row.bucket_name, row.store, row.storage_uid, row.owner, row.basis) == (
        RAW,
        "raw",
        "abc123",
        "1234",
        "discovered",
    )


@pytest.mark.asyncio
async def test_bucket_created_outside_any_recorded_apply_is_not_confirmed(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)

    with _patched(FakeAdapter({RAW: _present(created=T0 + timedelta(days=30))})):
        report = await _raw_report(session)

    assert report.health is StoreHealth.NOT_CONFIRMED
    assert report.basis is None


@pytest.mark.asyncio
async def test_bucket_of_another_generation_is_not_confirmed(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id, uid="ffffff")

    with _patched(FakeAdapter({RAW: _present()})):
        report = await _raw_report(session)

    assert report.health is StoreHealth.NOT_CONFIRMED


@pytest.mark.asyncio
async def test_bucket_without_bioaf_labels_is_not_confirmed(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)

    with _patched(FakeAdapter({RAW: _present(labels={"owner": "someone"})})):
        report = await _raw_report(session)

    assert report.health is StoreHealth.NOT_CONFIRMED


@pytest.mark.asyncio
async def test_legacy_store_without_apply_history_stays_usable_but_not_confirmed(session):
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    with _patched(FakeAdapter({RAW: _present()})):
        report = await _raw_report(session)
    assert report.health is StoreHealth.NOT_CONFIRMED
    assert report.usable is True
    assert report.trusted is False


@pytest.mark.asyncio
async def test_recreated_bucket_with_the_same_name_belongs_to_another_installation(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)
    with _patched(FakeAdapter({RAW: _present()})):
        await _raw_report(session)
    await session.commit()

    with _patched(FakeAdapter({RAW: _present(created=T0 + timedelta(days=40))})):
        report = await _raw_report(session)

    assert report.health is StoreHealth.WRONG_OWNER
    assert report.usable is False


@pytest.mark.asyncio
async def test_bucket_in_another_project_belongs_to_another_installation(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)
    with _patched(FakeAdapter({RAW: _present()})):
        await _raw_report(session)
    await session.commit()

    with _patched(FakeAdapter({RAW: _present(owner="9999")})):
        report = await _raw_report(session)

    assert report.health is StoreHealth.WRONG_OWNER


@pytest.mark.asyncio
async def test_s3_bucket_owned_by_another_account_is_a_known_mismatch(session):
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({RAW: _present(owner="other-account")})):
        report = await _raw_report(session)
    assert report.health is StoreHealth.WRONG_OWNER


@pytest.mark.asyncio
async def test_simulated_and_identity_limited_stores_are_available(session):
    await _config(session, raw_bucket_name=RAW)
    simulated = ContainerObservation(presence=ContainerPresence.PRESENT, simulated=True)
    with _patched(FakeAdapter({RAW: simulated})):
        assert (await _raw_report(session)).health is StoreHealth.AVAILABLE


@pytest.mark.parametrize(
    "unreadable",
    [
        {"owner": None},
        {"created": None},
        {"owner": None, "created": None},
    ],
)
@pytest.mark.asyncio
async def test_a_recorded_bucket_whose_evidence_cannot_be_read_is_not_trusted(session, unreadable):
    """Missing current evidence is not matching evidence: destroy must not act on it."""
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)
    with _patched(FakeAdapter({RAW: _present()})):
        assert (await _raw_report(session)).health is StoreHealth.AVAILABLE
    await session.commit()

    with _patched(FakeAdapter({RAW: _present(**unreadable)})):
        report = await _raw_report(session)

    assert report.health is StoreHealth.CANNOT_CHECK
    assert report.trusted is False


@pytest.mark.asyncio
async def test_enrollment_is_refused_when_ownership_cannot_be_read(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({RAW: _present(owner=None)})):
        with pytest.raises(StateError, match="could not read"):
            await storage_identity.enroll_stores(session, user_id, ["raw"], reason="trust me")


# ---------------------------------------------------------------------------
# enrollment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrollment_records_an_audited_baseline(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")

    with _patched(FakeAdapter({RAW: _present()})):
        reports = await storage_identity.enroll_stores(session, user_id, ["raw"], reason="Checked our project")
    await session.commit()

    assert [(r.store, r.health, r.basis) for r in reports] == [("raw", StoreHealth.AVAILABLE, "enrolled")]
    from app.models.storage_identity import StorageIdentity

    row = (await session.execute(select(StorageIdentity))).scalar_one()
    assert (row.basis, row.reason, row.recorded_by_user_id) == ("enrolled", "Checked our project", user_id)
    audit = (
        await session.execute(text("SELECT action FROM audit_log WHERE action = 'storage_identity_enrolled'"))
    ).all()
    assert len(audit) == 1


@pytest.mark.asyncio
async def test_enrollment_is_refused_for_a_known_mismatch(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({RAW: _present(owner="other-account")})):
        with pytest.raises(StateError, match="another installation"):
            await storage_identity.enroll_stores(session, user_id, ["raw"], reason="trust me")


@pytest.mark.asyncio
async def test_enrollment_is_refused_for_a_missing_bucket(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({})):
        with pytest.raises(StateError):
            await storage_identity.enroll_stores(session, user_id, ["raw"], reason="trust me")


@pytest.mark.asyncio
async def test_enrollment_requires_a_reason(session):
    from app.exceptions import ValidationError

    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({RAW: _present()})):
        with pytest.raises(ValidationError):
            await storage_identity.enroll_stores(session, user_id, ["raw"], reason="   ")


# ---------------------------------------------------------------------------
# Terraform outputs never establish identity on their own
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_does_not_record_a_missing_output_bucket(session):
    from app.services.stack_deployment import sync_storage_config

    await _config(session, storage_stack_uid="abc123")
    outputs = {"raw_bucket_name": {"value": RAW}}
    with (
        _patched(FakeAdapter({})),
        patch("app.services.stack_deployment.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)),
    ):
        populated = await sync_storage_config(session)
    assert populated == {}
    assert (
        await session.execute(text("SELECT value FROM platform_config WHERE key='raw_bucket_name'"))
    ).first() is None


@pytest.mark.asyncio
async def test_sync_records_an_unrecorded_bucket_with_agreeing_evidence(session):
    from app.services.stack_deployment import sync_storage_config

    user_id = await _seed_user(session)
    await _config(session, storage_stack_uid="abc123")
    await _apply_run(session, user_id)
    outputs = {"raw_bucket_name": {"value": RAW}}
    with (
        _patched(FakeAdapter({RAW: _present()})),
        patch("app.services.stack_deployment.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)),
    ):
        populated = await sync_storage_config(session)
    assert populated == {"raw_bucket_name": RAW}


@pytest.mark.asyncio
async def test_sync_never_replaces_a_different_recorded_bucket(session):
    from app.services.stack_deployment import sync_storage_config

    user_id = await _seed_user(session)
    await _config(session, storage_stack_uid="abc123", raw_bucket_name="bioaf-raw-acme-000000")
    await _apply_run(session, user_id)
    outputs = {"raw_bucket_name": {"value": RAW}}
    with (
        _patched(FakeAdapter({RAW: _present()})),
        patch("app.services.stack_deployment.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)),
    ):
        populated = await sync_storage_config(session)
    assert populated == {}
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='raw_bucket_name'"))).scalar_one()
    assert row == "bioaf-raw-acme-000000"


@pytest.mark.asyncio
async def test_realign_does_not_take_identity_from_terraform_outputs(session):
    from app.services import infra_update_service

    await _config(session, raw_bucket_name="null")
    outputs = {"raw_bucket_name": {"value": RAW}}
    with patch(
        "app.services.infra_update_service.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)
    ):
        assert await infra_update_service.realign_storage_naming(session) is None
    assert (
        await session.execute(text("SELECT value FROM platform_config WHERE key='storage_stack_uid'"))
    ).first() is None


@pytest.mark.asyncio
async def test_retiring_the_storage_generation_retires_bucket_identities(session):
    from app.models.storage_identity import StorageIdentity
    from app.services.storage_generation import retire_storage_generation

    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _apply_run(session, user_id)
    with _patched(FakeAdapter({RAW: _present()})):
        await storage_identity.inspect_stores(session)
    await session.commit()

    await retire_storage_generation(session)
    await session.commit()

    rows = (await session.execute(select(StorageIdentity))).scalars().all()
    assert len(rows) == 1 and rows[0].retired_at is not None
    assert await storage_identity.latest_reports(session) == []


# ---------------------------------------------------------------------------
# identity markers and AWS evidence (no provider creation date)
# ---------------------------------------------------------------------------

MARKER = "9f1c2e3d4b5a6978"


def _aws(marker=MARKER, owner="own-account", extra=None):
    labels = {"managed_by": "bioaf", "storage_uid": "abc123"}
    if marker is not None:
        labels["bioaf_marker"] = marker
    labels.update(extra or {})
    return ContainerObservation(presence=ContainerPresence.PRESENT, owner=owner, created_at=None, labels=labels)


async def _markers(session, origin, marker=MARKER):
    import json

    await _config(
        session,
        storage_bucket_markers=json.dumps({"generation": "abc123", "origin": origin, "markers": {"raw": marker}}),
    )


@pytest.mark.asyncio
async def test_a_bucket_created_with_its_deployment_marker_is_discovered_without_a_creation_date(session):
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _markers(session, "deploy")
    with _patched(FakeAdapter({RAW: _aws()})):
        report = await _raw_report(session)
    assert (report.health, report.basis) == (StoreHealth.AVAILABLE, "discovered")


@pytest.mark.asyncio
async def test_a_legacy_bucket_without_a_marker_or_history_stays_usable_but_not_confirmed(session):
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    with _patched(FakeAdapter({RAW: _aws(marker=None)})):
        report = await _raw_report(session)
    assert report.health is StoreHealth.NOT_CONFIRMED
    assert report.usable is True


@pytest.mark.asyncio
async def test_a_marker_added_by_an_update_needs_an_administrators_confirmation(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _markers(session, "update")
    with _patched(FakeAdapter({RAW: _aws()})):
        assert (await _raw_report(session)).health is StoreHealth.NOT_CONFIRMED
        [report] = await storage_identity.enroll_stores(session, user_id, ["raw"], reason="Checked in the console")
    await session.commit()
    assert (report.health, report.basis) == (StoreHealth.AVAILABLE, "enrolled")
    from app.models.storage_identity import StorageIdentity

    row = (await session.execute(select(StorageIdentity))).scalar_one()
    assert (row.marker, row.owner) == (MARKER, "own-account")


@pytest.mark.asyncio
async def test_confirming_a_bucket_with_no_identity_evidence_asks_for_the_marker_first(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    with _patched(FakeAdapter({RAW: _aws(marker=None)})):
        with pytest.raises(StateError, match="Check for Infrastructure Updates"):
            await storage_identity.enroll_stores(session, user_id, ["raw"], reason="trust me")


@pytest.mark.asyncio
async def test_a_bucket_of_another_account_still_cannot_be_confirmed(session):
    user_id = await _seed_user(session)
    await _config(session, raw_bucket_name=RAW)
    with _patched(FakeAdapter({RAW: _aws(owner="other-account")})):
        with pytest.raises(StateError, match="another installation"):
            await storage_identity.enroll_stores(session, user_id, ["raw"], reason="trust me")


async def _established(session):
    await _config(session, raw_bucket_name=RAW, storage_stack_uid="abc123")
    await _markers(session, "deploy")
    with _patched(FakeAdapter({RAW: _aws()})):
        assert (await _raw_report(session)).health is StoreHealth.AVAILABLE
    await session.commit()


@pytest.mark.asyncio
async def test_a_recreated_bucket_with_a_different_marker_is_a_known_mismatch(session):
    await _established(session)
    with _patched(FakeAdapter({RAW: _aws(marker="0000000000000000")})):
        assert (await _raw_report(session)).health is StoreHealth.WRONG_OWNER


@pytest.mark.asyncio
async def test_a_bucket_whose_recorded_marker_is_gone_cannot_be_checked(session):
    await _established(session)
    with _patched(FakeAdapter({RAW: _aws(marker=None)})):
        assert (await _raw_report(session)).health is StoreHealth.CANNOT_CHECK


@pytest.mark.asyncio
async def test_a_bucket_policy_edit_does_not_change_its_identity(session):
    """AWS can report a new creation date after a policy edit; the marker and owner still match."""
    await _established(session)
    edited = _aws(extra={"policy_edited": "yes"})
    edited.created_at = T0 + timedelta(days=90)
    with _patched(FakeAdapter({RAW: edited})):
        assert (await _raw_report(session)).health is StoreHealth.AVAILABLE
