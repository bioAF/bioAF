"""01B.1 domain contracts: file external IDs, scoped source identity, and reference snapshots."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.exceptions import ConflictError, ValidationError
from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.sample import Sample
from app.services import entity_references
from app.services.file_service import FileService, SourceIdentityAmbiguous

pytestmark = pytest.mark.asyncio


async def _file(session, org_id, uri="s3://raw/x/a.bam", **kw) -> File:
    return await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=None,
        filename=uri.rsplit("/", 1)[-1],
        storage_uri=uri,
        size_bytes=10,
        md5_checksum=None,
        file_type="bam",
        **kw,
    )


async def _org(session, name="Other") -> Organization:
    org = Organization(name=name, setup_complete=True)
    session.add(org)
    await session.flush()
    return org


async def _run(session, org_id, **kw) -> PipelineRun:
    run = PipelineRun(organization_id=org_id, pipeline_name="rnaseq", status="completed", **kw)
    session.add(run)
    await session.flush()
    return run


# --- external IDs -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["007", "LIMS-FILE-9001", "x" * 255, "  spaced  ", "Case"])
async def test_external_id_is_kept_exactly(session, admin_user, value):
    f = await _file(session, admin_user.organization_id, external_id=value)
    await session.commit()
    assert (await session.get(File, f.id)).external_id == value


async def test_no_external_id_is_null_and_never_fabricated(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    await session.commit()
    assert f.external_id is None


@pytest.mark.parametrize("value", ["", "x" * 256])
async def test_external_id_length_is_validated(session, admin_user, value):
    with pytest.raises(ValidationError):
        await _file(session, admin_user.organization_id, external_id=value)


async def test_duplicate_external_id_is_a_409_and_leaves_the_session_usable(session, admin_user):
    org = admin_user.organization_id
    first = await _file(session, org, "s3://raw/x/1.bam", external_id="F-1")
    await session.commit()

    with pytest.raises(ConflictError) as err:
        await _file(session, org, "s3://raw/x/2.bam", external_id="F-1")
    assert err.value.status_code == 409 and str(err.value) == "external_id_already_exists"

    # The failed registration is gone; the session still works for the caller.
    await session.commit()
    rows = (await session.execute(select(File.id).where(File.organization_id == org))).scalars().all()
    assert rows == [first.id]


async def test_a_deleted_files_external_id_is_not_reassigned(session, admin_user):
    org = admin_user.organization_id
    old = await _file(session, org, "s3://raw/x/1.bam", external_id="F-1")
    await session.commit()
    old.deleted_at = old.created_at  # a soft-deleted record (the bytes are irrelevant here)
    await session.commit()
    with pytest.raises(ConflictError):
        await _file(session, org, "s3://raw/x/2.bam", external_id="F-1")


async def test_another_organization_may_reuse_an_external_id(session, admin_user):
    other = await _org(session)
    await _file(session, admin_user.organization_id, "s3://raw/x/1.bam", external_id="F-1")
    await _file(session, other.id, "s3://raw/y/1.bam", external_id="F-1")
    await session.commit()


async def test_a_concurrent_duplicate_is_refused_by_the_index(db_engine, session, admin_user):
    """Two sessions race the same identifier: the second is a conflict, not a 500."""
    from app import database as database_module

    org = admin_user.organization_id
    await session.commit()
    async with database_module.async_session_factory() as a, database_module.async_session_factory() as b:
        await _file(a, org, "s3://raw/x/1.bam", external_id="RACE")
        await a.commit()
        with pytest.raises(ConflictError):
            await _file(b, org, "s3://raw/x/2.bam", external_id="RACE")


# --- source identity --------------------------------------------------------------------------


async def test_pipeline_outputs_record_their_source_identity(session, admin_user):
    run = await _run(session, admin_user.organization_id)
    f = await _file(session, admin_user.organization_id, source_type="pipeline_output", source_pipeline_run_id=run.id)
    assert f.source_storage_uri == f.storage_uri


async def test_a_second_registration_of_one_source_is_refused(session, admin_user):
    run = await _run(session, admin_user.organization_id)
    await _file(session, admin_user.organization_id, source_type="pipeline_output", source_pipeline_run_id=run.id)
    with pytest.raises(ConflictError) as err:
        await _file(session, admin_user.organization_id, source_type="pipeline_output", source_pipeline_run_id=run.id)
    assert str(err.value) == "source_identity_conflict"


async def test_lookup_matches_source_or_canonical_uri_in_scope(session, admin_user):
    org = admin_user.organization_id
    run = await _run(session, org)
    f = await _file(session, org, "s3://raw/run/a.bam", source_type="pipeline_output", source_pipeline_run_id=run.id)
    # The canonical bytes moved (a protected verification copy); the source identity did not.
    f.storage_uri = "s3://raw/verified-files/abc/payload"
    await session.flush()

    by_source = await FileService.find_registered_outputs(session, org, run.id, ["s3://raw/run/a.bam"])
    by_canonical = await FileService.find_registered_outputs(session, org, run.id, [f.storage_uri])
    assert by_source == {"s3://raw/run/a.bam": f}
    assert by_canonical == {f.storage_uri: f}

    other_run = await _run(session, org)
    assert await FileService.find_registered_outputs(session, org, other_run.id, ["s3://raw/run/a.bam"]) == {}
    other = await _org(session)
    assert await FileService.find_registered_outputs(session, other.id, run.id, ["s3://raw/run/a.bam"]) == {}


async def test_lookup_includes_tombstones(session, admin_user):
    org = admin_user.organization_id
    run = await _run(session, org)
    f = await _file(session, org, "s3://raw/run/a.bam", source_type="pipeline_output", source_pipeline_run_id=run.id)
    f.deleted_at = f.created_at
    await session.flush()
    assert await FileService.find_registered_outputs(session, org, run.id, ["s3://raw/run/a.bam"]) == {
        "s3://raw/run/a.bam": f
    }


async def test_ambiguous_legacy_candidates_are_refused(session, admin_user):
    org = admin_user.organization_id
    run = await _run(session, org)
    for _ in range(2):
        session.add(
            File(
                organization_id=org,
                storage_uri="s3://raw/run/dup.bam",
                filename="dup.bam",
                file_type="bam",
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
            )
        )
    await session.flush()
    with pytest.raises(SourceIdentityAmbiguous) as err:
        await FileService.find_registered_outputs(session, org, run.id, ["s3://raw/run/dup.bam"])
    assert err.value.code == "source_identity_ambiguous"


# --- reference snapshots ----------------------------------------------------------------------


async def _tree(session, org_id, suffix=""):
    project = Project(organization_id=org_id, name=f"P{suffix}", external_id=f"LIMS-PRJ{suffix}")
    session.add(project)
    await session.flush()
    exp = Experiment(
        organization_id=org_id,
        project_id=project.id,
        name=f"E{suffix}",
        external_id=f"LIMS-EXP{suffix}",
    )
    session.add(exp)
    await session.flush()
    return project, exp


async def test_file_relationships_name_subject_and_parents_explicitly(session, admin_user):
    org = admin_user.organization_id
    project, exp = await _tree(session, org)
    _, exp2 = await _tree(session, org, "-2")
    exp_no_ext = Experiment(organization_id=org, project_id=project.id, name="internal")
    session.add(exp_no_ext)
    await session.flush()
    # The same sample label in two experiments is two samples.
    s1 = Sample(experiment_id=exp.id, external_id="S-1")
    s2 = Sample(experiment_id=exp2.id, external_id="S-1")
    s3 = Sample(experiment_id=exp_no_ext.id)
    session.add_all([s1, s2, s3])
    await session.flush()

    f = await _file(session, org, experiment_id=exp.id, external_id="LIMS-FILE-1")
    for s in (s3, s1, s2):
        await FileService.link_file_to_sample(session, f.id, s.id)

    rel = await entity_references.file_relationships(session, f)

    assert rel["project_id"] == project.id and rel["project_external_id"] == "LIMS-PRJ"
    assert rel["experiment_id"] == exp.id and rel["experiment_external_id"] == "LIMS-EXP"
    assert rel["sample_ids"] == sorted([s1.id, s2.id, s3.id])
    assert [s["sample_id"] for s in rel["samples"]] == rel["sample_ids"]
    by_id = {s["sample_id"]: s for s in rel["samples"]}
    assert by_id[s1.id] == {
        "sample_id": s1.id,
        "external_id": "S-1",
        "experiment_id": exp.id,
        "experiment_external_id": "LIMS-EXP",
    }
    assert by_id[s2.id]["experiment_id"] == exp2.id and by_id[s2.id]["experiment_external_id"] == "LIMS-EXP-2"
    assert by_id[s3.id] == {
        "sample_id": s3.id,
        "external_id": None,
        "experiment_id": exp_no_ext.id,
        "experiment_external_id": None,
    }


async def test_unassigned_and_project_scoped_files(session, admin_user):
    org = admin_user.organization_id
    project, _ = await _tree(session, org)
    loose = await _file(session, org, "s3://raw/a")
    scoped = await _file(session, org, "s3://raw/b", project_id=project.id)

    loose_rel = await entity_references.file_relationships(session, loose)
    scoped_rel = await entity_references.file_relationships(session, scoped)

    assert loose_rel == {
        "project_id": None,
        "project_external_id": None,
        "experiment_id": None,
        "experiment_external_id": None,
        "sample_ids": [],
        "samples": [],
    }
    assert scoped_rel["project_id"] == project.id and scoped_rel["project_external_id"] == "LIMS-PRJ"
    assert scoped_rel["experiment_id"] is None and scoped_rel["experiment_external_id"] is None


async def test_a_cross_organization_link_is_refused_without_leaking_identifiers(session, admin_user):
    org = admin_user.organization_id
    other = await _org(session)
    _, foreign_exp = await _tree(session, other.id, "-X")
    foreign = Sample(experiment_id=foreign_exp.id, external_id="SECRET")
    session.add(foreign)
    await session.flush()
    f = await _file(session, org)
    await FileService.link_file_to_sample(session, f.id, foreign.id)

    with pytest.raises(entity_references.InconsistentReference) as err:
        await entity_references.file_relationships(session, f)
    assert "SECRET" not in str(err.value) and "LIMS-EXP-X" not in str(err.value)


async def test_experiment_and_sample_references(session, admin_user):
    org = admin_user.organization_id
    project, exp = await _tree(session, org)
    loose_exp = Experiment(organization_id=org, name="no project")
    session.add(loose_exp)
    await session.flush()
    s = Sample(experiment_id=exp.id, external_id="007")
    session.add(s)
    await session.flush()

    assert await entity_references.experiment_reference(session, exp) == {
        "experiment_id": exp.id,
        "external_id": "LIMS-EXP",
        "project_id": project.id,
        "project_external_id": "LIMS-PRJ",
    }
    assert await entity_references.experiment_reference(session, loose_exp) == {
        "experiment_id": loose_exp.id,
        "external_id": None,
        "project_id": None,
        "project_external_id": None,
    }
    refs = await entity_references.sample_references(session, [s.id])
    assert refs == {
        s.id: {"sample_id": s.id, "external_id": "007", "experiment_id": exp.id, "experiment_external_id": "LIMS-EXP"}
    }
