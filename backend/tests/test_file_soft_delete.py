"""Deleting a file removes it from view, never from the catalogue.

Decision 5 of 2026-08-19: **soft delete. The row and its UUID are never
removed.** It is what data catalogues and LIMS do, and bioAF already leaned this
way: ``File.storage_deleted`` separates "the bytes are gone from storage" from
"the record is gone", so storage can be freed while the catalogue entry survives
and an exported dataset or a published provenance record never dangles.

It is also what makes the identity work of migration 120 mean anything. A UUID
that stops resolving the moment somebody tidies up is not a catalogue number:
the owner's framing for the whole scheme was "It's an ISBN. I didn't write the
book, but need to catalog it for later recall."

The boundary this draws, recorded because it is a choice rather than an
oversight: the file's own record and identity survive forever, while the working
rows that pointed at it (a plot archive entry, a parse result) are still cleaned
up exactly as they were. What survives is the catalogue entry and the run
provenance that references it.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles


def _headers(user, role_name: str) -> dict:
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(session):
    org = Organization(name="SoftDeleteOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    admin = User(
        email="admin@softdelete.test",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(admin)
    await session.flush()
    exp = Experiment(name="E", organization_id=org.id, status="fastq_uploaded", owner_user_id=admin.id)
    session.add(exp)
    await session.flush()
    doomed = File(
        organization_id=org.id,
        experiment_id=exp.id,
        filename="doomed.fastq.gz",
        storage_uri="gs://bucket/doomed.fastq.gz",
        file_type="fastq",
        source_type="upload",
        size_bytes=10,
        uploader_user_id=admin.id,
    )
    kept = File(
        organization_id=org.id,
        experiment_id=exp.id,
        filename="kept.fastq.gz",
        storage_uri="gs://bucket/kept.fastq.gz",
        file_type="fastq",
        source_type="upload",
        size_bytes=10,
        uploader_user_id=admin.id,
    )
    session.add_all([doomed, kept])
    await session.flush()
    await session.commit()
    return {"org": org, "admin": admin, "exp": exp, "doomed": doomed, "kept": kept}


async def _delete(client, world):
    return await client.delete(
        f"/api/files/{world['doomed'].id}", headers=_headers(world["admin"], "admin")
    )


class TestTheRecordSurvives:
    @pytest.mark.asyncio
    async def test_the_row_and_its_identity_are_still_there(self, client, session, world):
        uid_before = world["doomed"].uuid

        assert (await _delete(client, world)).status_code == 200

        row = (
            await session.execute(
                text("SELECT uuid, deleted_at FROM files WHERE id = :fid").bindparams(fid=world["doomed"].id)
            )
        ).first()
        assert row is not None
        assert row.uuid == uid_before
        assert row.deleted_at is not None

    @pytest.mark.asyncio
    async def test_it_records_who_deleted_it(self, client, session, world):
        """An audited lab asks who, not only when."""
        await _delete(client, world)

        deleted_by = (
            await session.execute(
                text("SELECT deleted_by_user_id FROM files WHERE id = :fid").bindparams(fid=world["doomed"].id)
            )
        ).scalar()
        assert deleted_by == world["admin"].id

    @pytest.mark.asyncio
    async def test_a_run_that_used_it_still_records_that_it_did(self, client, session, world):
        """The payoff. A hard delete cascaded this away, so a result could no
        longer say which file fed it."""
        run = PipelineRun(
            organization_id=world["org"].id,
            experiment_id=world["exp"].id,
            submitted_by_user_id=world["admin"].id,
            pipeline_name="nf-core/demo",
            pipeline_version="1.0.0",
            status="completed",
            parameters_json={},
        )
        session.add(run)
        await session.flush()
        session.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=world["doomed"].id))
        await session.flush()
        await session.commit()

        await _delete(client, world)

        surviving = (
            await session.execute(
                select(PipelineRunInputFile).where(PipelineRunInputFile.pipeline_run_id == run.id)
            )
        ).scalars().all()
        assert [r.file_id for r in surviving] == [world["doomed"].id]

    @pytest.mark.asyncio
    async def test_deleting_the_record_does_not_claim_the_bytes_are_gone(self, client, session, world):
        """Two different facts, and bioAF already separated them. Conflating
        them would say the storage was freed when nothing touched it."""
        await _delete(client, world)

        storage_deleted = (
            await session.execute(
                text("SELECT storage_deleted FROM files WHERE id = :fid").bindparams(fid=world["doomed"].id)
            )
        ).scalar()
        assert storage_deleted is False


class TestItIsGoneFromEveryWorkingView:
    @pytest.mark.asyncio
    async def test_it_is_no_longer_listed(self, client, world):
        await _delete(client, world)

        r = await client.get(
            "/api/files", params={"experiment_id": world["exp"].id}, headers=_headers(world["admin"], "admin")
        )

        assert [f["id"] for f in r.json()["files"]] == [world["kept"].id]
        assert r.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_fetching_it_by_id_is_a_404(self, client, world):
        await _delete(client, world)

        r = await client.get(f"/api/files/{world['doomed'].id}", headers=_headers(world["admin"], "admin"))

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_deleting_it_twice_is_a_404_rather_than_a_second_deletion(self, client, world):
        assert (await _delete(client, world)).status_code == 200

        assert (await _delete(client, world)).status_code == 404

    @pytest.mark.asyncio
    async def test_a_deleted_file_is_never_fed_to_a_pipeline(self, session, world):
        """The one that would be a scientific error rather than a tidiness
        one: a run taking an input its scientist believes is gone."""
        from app.services.pipeline_run_service import PipelineRunService

        await session.refresh(world["doomed"])
        world["doomed"].deleted_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        await session.flush()

        eligible = PipelineRunService._input_eligible_files([world["doomed"], world["kept"]], True)

        assert [f.id for f in eligible] == [world["kept"].id]
