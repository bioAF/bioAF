"""Sequencing identity below the sample, as typed columns on File.

bioAF models a sample and its files with nothing in between, so a sample
sequenced over several lanes has nowhere to record which sequencing unit a file
came from. Three parts of the code improvised around that absence:
``upload_service`` writes ``lane:``/``read:`` strings into ``tags_json``,
``sample_sheet_service`` re-parses the same convention out of filenames, and
``fetchngs_ingest_service`` fabricates lane numbers so a sample's sibling runs
stay on separate rows.

Untyped strings in a JSONB array are *why* two spellings of one lane can coexist.
``upload_service`` stores ``int("001")`` so its tag reads ``lane:1``; ``fetchngs``
stores ``f"{n:03d}"`` so its tag reads ``lane:001``. Both are dict keys when the
sheet builder pairs mates, so ONE physical lane becomes TWO units and a sample
whose mates arrived by different ingest paths emits two half-empty rows.

These tests pin the typed columns that replace those strings, and the one fact a
string default could never carry: an unknown lane is NULL, not ``"000"``.

Lane and read type are what bioAF can source from a filename today. ``flowcell_id``
and ``index_sequence`` are declared here because the read-group axis is
(flowcell, lane) and a lane number alone collides across flow cells, but nothing
populates them yet: they live in the FASTQ header, and whether bioAF reads that
header is an open decision for the owner.
"""

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.models.file import File
from app.services import sample_sheet_service
from app.services.upload_service import UploadService, _pending_uploads

MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "119_file_sequencing_identity.py"


def _file(**kw):
    """A File-shaped stand-in carrying every attribute the readers consult.

    Deliberately NOT a MagicMock. A mock auto-vivifies ``lane`` and ``read_type``
    into truthy objects, which is exactly the input the readers must reject, so a
    mock here would assert the opposite of what these tests are for.
    """
    attrs = {
        "storage_uri": None,
        "filename": "",
        "tags_json": [],
        "lane": None,
        "read_type": None,
        "flowcell_id": None,
        "index_sequence": None,
        "source_run_accession": None,
    }
    attrs.update(kw)
    return SimpleNamespace(**attrs)


def _sample(files):
    return SimpleNamespace(id=1, external_id="SAMPLE-101", files=files)


# ---------------------------------------------------------------------------
# The readers prefer the typed column
# ---------------------------------------------------------------------------


def test_mates_pair_from_the_typed_columns_alone():
    """A file carrying no tags and an unconventional name still pairs.

    This is the point of typing the columns: identity stops depending on whether
    the lab named its files the way Illumina names files.

    The names are deliberately in the WRONG order. Without the typed columns the
    reader falls back to sorting by filename, which would put the R2 first, so
    this test cannot pass by accident.
    """
    sample = _sample(
        [
            _file(storage_uri="gs://b/z_reads.fastq.gz", filename="z_reads.fastq.gz", lane=1, read_type="R1"),
            _file(storage_uri="gs://b/a_reads.fastq.gz", filename="a_reads.fastq.gz", lane=1, read_type="R2"),
        ]
    )

    assert sample_sheet_service._extract_fastq_lane_pairs(sample) == [
        ("gs://b/z_reads.fastq.gz", "gs://b/a_reads.fastq.gz")
    ]


def test_one_physical_lane_spelled_two_ways_is_one_unit():
    """The verified failure this phase exists to remove.

    A sample whose mates arrived by different ingest paths carries ``lane:1`` on
    one and ``lane:001`` on the other. Read as strings those are two keys, so the
    sheet emitted two rows each holding one mate and an empty partner: both files
    present, neither paired. The typed column is the same integer for both.
    """
    sample = _sample(
        [
            _file(
                storage_uri="gs://b/S_L001_R1_001.fastq.gz",
                filename="S_L001_R1_001.fastq.gz",
                tags_json=["read:R1", "lane:1"],
                lane=1,
                read_type="R1",
            ),
            _file(
                storage_uri="gs://b/S_L001_R2_001.fastq.gz",
                filename="S_L001_R2_001.fastq.gz",
                tags_json=["read:R2", "lane:001"],
                lane=1,
                read_type="R2",
            ),
        ]
    )

    pairs = sample_sheet_service._extract_fastq_lane_pairs(sample)

    assert pairs == [("gs://b/S_L001_R1_001.fastq.gz", "gs://b/S_L001_R2_001.fastq.gz")]


def test_an_unknown_lane_is_one_implicit_unit():
    """NULL means "not known", and every file that does not know is one unit.

    A lab receiving pre-merged FASTQs from a CRO has no lane at all, and must be
    wholly unaffected: one row, both mates.
    """
    sample = _sample(
        [
            _file(storage_uri="gs://b/x_R1.fastq.gz", filename="x_R1.fastq.gz", read_type="R1"),
            _file(storage_uri="gs://b/x_R2.fastq.gz", filename="x_R2.fastq.gz", read_type="R2"),
        ]
    )

    assert sample_sheet_service._extract_fastq_lane_pairs(sample) == [("gs://b/x_R1.fastq.gz", "gs://b/x_R2.fastq.gz")]


def test_two_typed_lanes_stay_two_units():
    sample = _sample(
        [
            _file(storage_uri="gs://b/l1_R1.fastq.gz", lane=1, read_type="R1"),
            _file(storage_uri="gs://b/l1_R2.fastq.gz", lane=1, read_type="R2"),
            _file(storage_uri="gs://b/l2_R1.fastq.gz", lane=2, read_type="R1"),
            _file(storage_uri="gs://b/l2_R2.fastq.gz", lane=2, read_type="R2"),
        ]
    )

    assert sample_sheet_service._extract_fastq_lane_pairs(sample) == [
        ("gs://b/l1_R1.fastq.gz", "gs://b/l1_R2.fastq.gz"),
        ("gs://b/l2_R1.fastq.gz", "gs://b/l2_R2.fastq.gz"),
    ]


def test_a_typed_index_read_is_still_excluded():
    sample = _sample(
        [
            _file(storage_uri="gs://b/i1.fastq.gz", lane=1, read_type="I1"),
            _file(storage_uri="gs://b/r1.fastq.gz", lane=1, read_type="R1"),
            _file(storage_uri="gs://b/r2.fastq.gz", lane=1, read_type="R2"),
        ]
    )

    assert sample_sheet_service._extract_fastq_lane_pairs(sample) == [("gs://b/r1.fastq.gz", "gs://b/r2.fastq.gz")]


def test_a_file_with_no_typed_columns_still_reads_its_tags():
    """The tag readers stay as a fallback for one release, so a file written
    before this migration keeps pairing exactly as it does today."""
    sample = _sample(
        [
            _file(storage_uri="gs://b/a.fastq.gz", tags_json=["read:R1", "lane:001"]),
            _file(storage_uri="gs://b/b.fastq.gz", tags_json=["read:R2", "lane:001"]),
        ]
    )

    assert sample_sheet_service._extract_fastq_lane_pairs(sample) == [("gs://b/a.fastq.gz", "gs://b/b.fastq.gz")]


def test_a_non_integer_lane_attribute_is_not_trusted():
    """Guard the guard. Half this suite builds files out of MagicMock, which
    auto-vivifies ``lane`` into a truthy object; reading that as a lane would
    scatter every mock-built sample across as many units as it has files."""
    sample = _sample(
        [
            _file(storage_uri="gs://b/a.fastq.gz", tags_json=["read:R1"], lane=object(), read_type=object()),
            _file(storage_uri="gs://b/b.fastq.gz", tags_json=["read:R2"], lane=object(), read_type=object()),
        ]
    )

    assert sample_sheet_service._extract_fastq_lane_pairs(sample) == [("gs://b/a.fastq.gz", "gs://b/b.fastq.gz")]


# ---------------------------------------------------------------------------
# The writers
# ---------------------------------------------------------------------------


async def _complete_upload(session, admin_user, filename: str) -> File:
    upload_id = str(uuid.uuid4())
    _pending_uploads[upload_id] = {
        "org_id": admin_user.organization_id,
        "user_id": admin_user.id,
        "filename": filename,
        "gcs_uri": f"gs://bioaf-ingest-test/uploads/{upload_id}/{filename}",
        "expected_size": None,
        "expected_md5": None,
        "project_id": None,
        "experiment_id": None,
        "sample_ids": [],
        "is_global": False,
    }
    return await UploadService.complete_upload(session, admin_user.organization_id, upload_id, "md5")


@pytest.mark.asyncio
async def test_upload_records_lane_and_read_type_as_typed_columns(session, admin_user):
    file = await _complete_upload(session, admin_user, "PBMC_S1_L001_R1_001.fastq.gz")

    assert file.lane == 1
    assert file.read_type == "R1"


@pytest.mark.asyncio
async def test_upload_leaves_the_columns_null_for_a_name_outside_the_convention(session, admin_user):
    """bioAF enforces no naming standard, so a filename is a hint. When it says
    nothing, the columns say nothing: NULL, never a fabricated lane."""
    file = await _complete_upload(session, admin_user, "pre_merged_reads.fastq.gz")

    assert file.lane is None
    assert file.read_type is None


@pytest.mark.asyncio
async def test_upload_never_invents_a_flowcell_or_an_index(session, admin_user):
    """Both live in the FASTQ header, not in the name. A value parsed from a
    filename that does not carry one would be a guess."""
    file = await _complete_upload(session, admin_user, "PBMC_S1_L001_R1_001.fastq.gz")

    assert file.flowcell_id is None
    assert file.index_sequence is None


# ---------------------------------------------------------------------------
# fetchngs: its synthetic value is not a lane
# ---------------------------------------------------------------------------

_MULTIRUN_SAMPLESHEET = (
    "sample,fastq_1,fastq_2,run_accession,experiment_accession,library_layout,scientific_name\n"
    "SRX079566,gs://x/SRR390728_1.fastq.gz,gs://x/SRR390728_2.fastq.gz,SRR390728,SRX079566,PAIRED,Homo sapiens\n"
    "SRX079566,gs://x/SRR292241_1.fastq.gz,gs://x/SRR292241_2.fastq.gz,SRR292241,SRX079566,PAIRED,Homo sapiens\n"
)


@pytest.mark.asyncio
async def test_fetchngs_records_the_source_run_accession_and_no_lane(session, admin_user):
    """A public archive's run accession is not a lane. Writing it into ``lane``
    promoted a fiction into a typed column; it gets its own field instead."""
    from tests.test_fetchngs_ingest import _FakeStorage, _experiment, _fetchngs_run

    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_MULTIRUN_SAMPLESHEET)
    outdir = run.parameters_json["outdir"]

    from app.services.fetchngs_ingest_service import FetchngsIngestService

    await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir, storage_adapter=storage)
    created = await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()

    assert len(created) == 4
    assert {f.lane for f in created} == {None}
    assert {f.source_run_accession for f in created} == {"SRR390728", "SRR292241"}
    assert {f.read_type for f in created} == {"R1", "R2"}


@pytest.mark.asyncio
async def test_fetchngs_sibling_runs_still_emit_separate_rows(session, admin_user):
    """The behavior the fabricated lane was there to produce must survive the
    move: two sibling runs under one sample are two mergeable rows, each with
    both of its mates. Now it comes from the accession rather than a fiction."""
    from tests.test_fetchngs_ingest import (
        _FakeStorage,
        _experiment,
        _fetchngs_run,
        _files_for_sample,
        _sample_by_external,
    )

    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_MULTIRUN_SAMPLESHEET)
    outdir = run.parameters_json["outdir"]

    from app.services.fetchngs_ingest_service import FetchngsIngestService

    await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir, storage_adapter=storage)
    await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()

    sample = await _sample_by_external(session, exp.id, "SRX079566")
    sample._input_files = await _files_for_sample(session, sample.id)

    pairs = sample_sheet_service._extract_fastq_lane_pairs(sample)

    assert len(pairs) == 2
    assert all(r1 and r2 for r1, r2 in pairs)


# ---------------------------------------------------------------------------
# The backfill, run as the migration runs it
# ---------------------------------------------------------------------------


def _backfill_statements():
    """The migration's own SQL, imported rather than restated.

    A backfill nobody executes is a claim, not a change, and the suite builds its
    tables from ``Base.metadata`` so the chain never runs here. Importing the
    statements means these tests exercise the text that will run on the demo.
    """
    spec = importlib.util.spec_from_file_location("migration_119", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BACKFILL_STATEMENTS


async def _seed(session, admin_user, **kw) -> File:
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri=kw.get("storage_uri", "gs://b/x.fastq.gz"),
        storage_uri=kw.get("storage_uri", "gs://b/x.fastq.gz"),
        filename=kw.get("filename", "x.fastq.gz"),
        file_type="fastq",
        tags_json=kw.get("tags_json", []),
    )
    session.add(f)
    await session.flush()
    return f


async def _run_backfill(session):
    for statement in _backfill_statements():
        await session.execute(text(statement))
    await session.flush()


@pytest.mark.asyncio
async def test_backfill_reads_the_legacy_read_tag(session, admin_user):
    """Both writers spelled the read tag the same way, so it is trustworthy."""
    r1 = await _seed(
        session, admin_user, filename="a.fastq.gz", storage_uri="gs://b/a.fastq.gz", tags_json=["read:R1", "lane:001"]
    )
    r2 = await _seed(
        session, admin_user, filename="b.fastq.gz", storage_uri="gs://b/b.fastq.gz", tags_json=["read:R2", "lane:1"]
    )

    await _run_backfill(session)
    await session.refresh(r1)
    await session.refresh(r2)

    assert r1.read_type == "R1"
    assert r2.read_type == "R2"


@pytest.mark.asyncio
async def test_backfill_never_takes_a_lane_from_a_tag(session, admin_user):
    """Two writers produced lane tags and only one meant a lane.

    Measured on the demo before this was written: 41 files carry a lane tag, 37
    of them fetchngs fabrications whose names carry no lane at all, and ZERO
    files carry a real lane tag without ``_LNNN_`` in the name. So a lane comes
    from the filename, which is the only evidence that distinguishes the two, and
    a tagged file whose name says nothing keeps a NULL lane rather than
    inheriting a number it was never sequenced in.
    """
    fetched = await _seed(
        session,
        admin_user,
        filename="SRX25642458_SRR30176122_1.fastq.gz",
        storage_uri="gs://b/SRX25642458_SRR30176122_1.fastq.gz",
        tags_json=["read:R1", "lane:001"],
    )

    await _run_backfill(session)
    await session.refresh(fetched)

    assert fetched.lane is None
    assert fetched.read_type == "R1"


@pytest.mark.asyncio
async def test_backfill_recovers_the_accession_the_fabricated_lane_stood_in_for(session, admin_user):
    """Dropping the fabricated lane without recovering this would be a silent
    loss, not a fix: two sibling runs under one sample would collapse into one
    implicit unit, emit one row instead of two, and drop a file with no error."""
    a = await _seed(
        session,
        admin_user,
        filename="SRX25642458_SRR30176122_1.fastq.gz",
        storage_uri="gs://b/SRX25642458_SRR30176122_1.fastq.gz",
        tags_json=["read:R1", "lane:001"],
    )
    b = await _seed(
        session,
        admin_user,
        filename="SRX25642461_SRR30176116_1.fastq.gz",
        storage_uri="gs://b/SRX25642461_SRR30176116_1.fastq.gz",
        tags_json=["read:R1", "lane:002"],
    )

    await _run_backfill(session)
    await session.refresh(a)
    await session.refresh(b)

    assert a.source_run_accession == "SRR30176122"
    assert b.source_run_accession == "SRR30176116"


@pytest.mark.asyncio
async def test_backfill_reads_a_real_lane_from_the_filename(session, admin_user):
    """The owner's own upload: a genuine two-lane sample, whose tags spell the
    lane bare (``lane:1``) and whose names carry ``_L001_``/``_L002_``."""
    one = await _seed(
        session,
        admin_user,
        filename="pbmc_1k_v3_S1_L001_R1_001.fastq.gz",
        storage_uri="gs://b/pbmc_1k_v3_S1_L001_R1_001.fastq.gz",
        tags_json=["lane:1", "read:R1", "sample:pbmc_1k_v3"],
    )
    two = await _seed(
        session,
        admin_user,
        filename="pbmc_1k_v3_S1_L002_R2_001.fastq.gz",
        storage_uri="gs://b/pbmc_1k_v3_S1_L002_R2_001.fastq.gz",
        tags_json=["lane:2", "read:R2", "sample:pbmc_1k_v3"],
    )

    await _run_backfill(session)
    await session.refresh(one)
    await session.refresh(two)

    assert one.lane == 1
    assert two.lane == 2
    assert one.source_run_accession is None


@pytest.mark.asyncio
async def test_backfill_falls_back_to_the_filename(session, admin_user):
    f = await _seed(
        session,
        admin_user,
        filename="PBMC_S1_L002_R2_001.fastq.gz",
        storage_uri="gs://b/PBMC_S1_L002_R2_001.fastq.gz",
        tags_json=[],
    )

    await _run_backfill(session)
    await session.refresh(f)

    assert f.lane == 2
    assert f.read_type == "R2"


@pytest.mark.asyncio
async def test_backfill_leaves_an_unparseable_file_null(session, admin_user):
    f = await _seed(
        session, admin_user, filename="pre_merged.fastq.gz", storage_uri="gs://b/pre_merged.fastq.gz", tags_json=[]
    )

    await _run_backfill(session)
    await session.refresh(f)

    assert f.lane is None
    assert f.read_type is None


@pytest.mark.asyncio
async def test_backfill_does_not_resurrect_the_000_sentinel_as_lane_zero(session, admin_user):
    """``"000"`` was ``_get_lane``'s "I do not know" default. Read as a number it
    becomes lane 0, a lane no sequencer has, and the moment sarek's ``lane``
    column is filled bioAF would emit it as a real one."""
    f = await _seed(
        session, admin_user, filename="c.fastq.gz", storage_uri="gs://b/c.fastq.gz", tags_json=["read:R1", "lane:000"]
    )

    await _run_backfill(session)
    await session.refresh(f)

    assert f.lane is None
    assert f.read_type == "R1"


@pytest.mark.asyncio
async def test_backfill_survives_a_file_whose_tags_are_not_an_array(session, admin_user):
    """``tags_json`` is JSONB with an array default, but nothing constrains it to
    one, and ``jsonb_array_elements_text`` raises on an object. A migration that
    dies mid-chain on one odd row takes the whole deploy with it."""
    f = await _seed(session, admin_user, filename="d.fastq.gz", storage_uri="gs://b/d.fastq.gz")
    await session.execute(text("UPDATE files SET tags_json = '{\"a\": 1}'::jsonb WHERE id = :i"), {"i": f.id})

    await _run_backfill(session)
    await session.refresh(f)

    assert f.lane is None


@pytest.mark.asyncio
async def test_backfill_is_idempotent(session, admin_user):
    f = await _seed(
        session,
        admin_user,
        filename="PBMC_S1_L001_R1_001.fastq.gz",
        storage_uri="gs://b/PBMC_S1_L001_R1_001.fastq.gz",
        tags_json=["read:R1", "lane:1"],
    )

    await _run_backfill(session)
    await _run_backfill(session)
    await session.refresh(f)

    assert f.lane == 1
    assert f.read_type == "R1"


@pytest.mark.asyncio
async def test_backfill_never_overwrites_a_value_already_typed(session, admin_user):
    """A row written by the new code path is authoritative. The backfill fills
    holes; it does not re-derive what a writer already stated."""
    f = await _seed(
        session,
        admin_user,
        filename="PBMC_S1_L004_R1_001.fastq.gz",
        storage_uri="gs://b/PBMC_S1_L004_R1_001.fastq.gz",
        tags_json=["read:R2", "lane:7"],
    )
    f.lane = 2
    f.read_type = "I1"
    await session.flush()

    await _run_backfill(session)
    await session.refresh(f)

    assert f.lane == 2
    assert f.read_type == "I1"


@pytest.mark.asyncio
async def test_typed_columns_are_queryable(session, admin_user):
    """The point of a column over a JSONB string: the database can group on it,
    which is what the read-group axis needs in phase C."""
    await _seed(
        session,
        admin_user,
        filename="e_S1_L003_R1_001.fastq.gz",
        storage_uri="gs://b/e_S1_L003_R1_001.fastq.gz",
        tags_json=[],
    )
    await _run_backfill(session)

    found = (await session.execute(select(File).where(File.lane == 3))).scalars().all()

    assert len(found) == 1
