"""Every way of associating a file (link, upload, service) checks all its targets first.

A file may only be associated with a Project, Experiment and Samples of the caller's Organization. Each
target is checked before any database change or storage operation, so a refused request changes
nothing: no Experiment move, no storage write, no file record, no Sample link.
"""

import io
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.test_upload_ingest_bucket import _storage_adapter

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(session, admin_user, admin_token):
    from app.models.component import PlatformConfig
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.organization import Organization
    from app.models.project import Project
    from app.models.sample import Sample

    org = admin_user.organization_id
    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    mine_exp, target_exp = Experiment(name="Mine", organization_id=org), Experiment(name="Target", organization_id=org)
    session.add_all([mine_exp, target_exp])
    await session.flush()
    mine_sample = Sample(experiment_id=mine_exp.id, external_id="MINE1", organism="Homo sapiens")
    uri = f"gs://raw/experiments/{mine_exp.id}/reads.fastq.gz"
    file = File(organization_id=org, gcs_uri=uri, storage_uri=uri, filename="reads.fastq.gz", file_type="fastq.gz")
    session.add_all([mine_sample, file])

    foreign = Organization(name="Foreign", setup_complete=True)
    session.add(foreign)
    await session.flush()
    theirs_project = Project(name="Theirs", organization_id=foreign.id)
    theirs_exp = Experiment(name="Theirs", organization_id=foreign.id)
    session.add_all([theirs_project, theirs_exp])
    await session.flush()
    theirs_sample = Sample(experiment_id=theirs_exp.id, external_id="THEIRS1", organism="Homo sapiens")
    session.add(theirs_sample)
    await session.commit()
    return {
        "auth": {"Authorization": f"Bearer {admin_token}"},
        "file": file.id,
        "uri": uri,
        "mine_exp": mine_exp.id,
        "target_exp": target_exp.id,
        "mine_sample": mine_sample.id,
        "theirs_project": theirs_project.id,
        "theirs_exp": theirs_exp.id,
        "theirs_sample": theirs_sample.id,
    }


async def _file_row(session, file_id):
    session.expire_all()
    return (
        await session.execute(
            text("SELECT experiment_id, project_id, storage_uri FROM files WHERE id = :f"), {"f": file_id}
        )
    ).one()


async def _links(session) -> int:
    return (await session.execute(text("SELECT count(*) FROM sample_files"))).scalar_one()


@pytest.mark.parametrize(
    "body",
    [
        lambda w: {"experiment_id": w["target_exp"], "sample_id": w["theirs_sample"]},
        lambda w: {"experiment_id": w["theirs_exp"]},
        lambda w: {"project_id": w["theirs_project"], "sample_id": w["mine_sample"]},
        lambda w: {"experiment_id": w["target_exp"], "project_id": w["theirs_project"]},
    ],
    ids=["move-then-foreign-sample", "foreign-experiment", "foreign-project", "move-with-foreign-project"],
)
async def test_a_refused_link_changes_nothing(client, session, world, body):
    before = await _file_row(session, world["file"])
    adapter = _storage_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(f"/api/files/{world['file']}/link", json=body(world), headers=world["auth"])

    assert resp.status_code == 404, resp.text
    assert await _file_row(session, world["file"]) == before
    assert await _links(session) == 0
    adapter.move.assert_not_called()
    adapter.copy_create_only.assert_not_called()
    adapter.object_identity.assert_not_called()


async def test_a_link_to_own_targets_still_works(client, session, world):
    adapter = _storage_adapter()
    adapter.put(world["uri"], b"reads")  # moving a file needs its stored bytes
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            f"/api/files/{world['file']}/link",
            json={"experiment_id": world["target_exp"], "sample_id": world["mine_sample"]},
            headers=world["auth"],
        )
    assert resp.status_code == 200, resp.text
    assert (await _file_row(session, world["file"]))[0] == world["target_exp"]
    assert await _links(session) == 1


@pytest.mark.parametrize(
    "query",
    [
        lambda w: f"experiment_id={w['mine_exp']}&sample_ids={w['theirs_sample']}",
        lambda w: f"experiment_id={w['theirs_exp']}",
        lambda w: f"project_id={w['theirs_project']}",
    ],
    ids=["foreign-sample", "foreign-experiment", "foreign-project"],
)
async def test_a_refused_simple_upload_writes_nothing(client, session, world, query):
    adapter = _storage_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            f"/api/files/upload/simple?{query(world)}",
            files={"file": ("new.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers=world["auth"],
        )
    assert resp.status_code == 404, resp.text
    adapter.upload_file.assert_not_called()
    files = (await session.execute(text("SELECT count(*) FROM files WHERE filename = 'new.fastq.gz'"))).scalar_one()
    assert files == 0 and await _links(session) == 0


@pytest.mark.parametrize(
    "fields",
    [
        lambda w: {"experiment_id": w["mine_exp"], "sample_ids": [w["theirs_sample"]]},
        lambda w: {"experiment_id": w["theirs_exp"]},
        lambda w: {"project_id": w["theirs_project"]},
    ],
    ids=["foreign-sample", "foreign-experiment", "foreign-project"],
)
async def test_a_refused_upload_initiation_signs_nothing(client, world, fields):
    adapter = _storage_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/initiate",
            json={"filename": "new.fastq.gz", "expected_size_bytes": 4, **fields(world)},
            headers=world["auth"],
        )
    assert resp.status_code == 404, resp.text
    adapter.generate_signed_url.assert_not_called()


async def test_the_link_service_refuses_a_sample_of_another_organization(session, world):
    from app.exceptions import NotFoundError
    from app.services.file_service import FileService

    with pytest.raises(NotFoundError):
        await FileService.link_file_to_sample(session, world["file"], world["theirs_sample"])
    await session.rollback()
    assert await _links(session) == 0
