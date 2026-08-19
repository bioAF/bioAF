"""One reading of the Illumina filename convention.

Three regexes across two modules parsed the same convention and disagreed about
it. Every disagreement below was verified by running the code, not by reading it:

    upload_service        ^(.+?)_S(\\d+)_L(\\d{3})_(R[12I])_(\\d{3})\\.fastq\\.gz$
    sample_sheet_service                  _(R[12]|I[12])_    and    _L(\\d{3})_

- ``R[12I]`` does not match ``_I1_``, so an index read uploaded under the
  convention was stored with no sequencing identity at all, while the sheet
  builder's own regex recognised it and skipped it correctly.
- ``R[12I]`` DOES match ``_RI_``, which is not a read code that exists.
- ``\\.fastq\\.gz$`` rejects ``.fq.gz``, although ``validate_fastq_filename`` has
  always accepted it, and MGI/BGI shops and many CROs deliver it. bioAF took the
  file and then failed to read its own name.
"""

import uuid

import pytest

from app.services import sample_sheet_service
from app.services.sequencing_identity import (
    lane_from_filename,
    parse_illumina_filename,
    read_type_from_filename,
)
from app.services.upload_service import UploadService, _pending_uploads


def test_an_index_read_is_parsed():
    """``R[12I]`` never matched ``_I1_``, so an index read was stored with no
    lane and no read type, and the pairing had to re-derive both from the name."""
    parsed = parse_illumina_filename("PBMC_S1_L001_I1_001.fastq.gz")

    assert parsed is not None
    assert parsed["read"] == "I1"
    assert parsed["lane"] == 1


def test_i2_is_parsed():
    assert parse_illumina_filename("PBMC_S1_L001_I2_001.fastq.gz")["read"] == "I2"


def test_ri_is_not_a_read_code():
    """``R[12I]`` matched it. No sequencer emits it."""
    assert parse_illumina_filename("PBMC_S1_L001_RI_001.fastq.gz") is None


def test_fq_gz_is_parsed():
    """validate_fastq_filename has always accepted .fq.gz, so refusing to read
    the name of a file bioAF had already taken was a disagreement with itself."""
    parsed = parse_illumina_filename("PBMC_S1_L002_R1_001.fq.gz")

    assert parsed is not None
    assert parsed["lane"] == 2
    assert parsed["read"] == "R1"


def test_the_ordinary_name_still_parses_the_same_way():
    parsed = parse_illumina_filename("SampleName_S1_L001_R1_001.fastq.gz")

    assert parsed == {
        "sample_name": "SampleName",
        "sample_number": 1,
        "lane": 1,
        "read": "R1",
        "set_number": 1,
    }


def test_a_name_outside_the_convention_yields_nothing():
    """bioAF enforces no naming standard, so a filename is a hint. Saying
    nothing is the correct answer, and a guess would be the defect."""
    assert parse_illumina_filename("pre_merged_reads.fastq.gz") is None
    assert read_type_from_filename("pre_merged_reads.fastq.gz") is None
    assert lane_from_filename("pre_merged_reads.fastq.gz") is None


def test_lane_zero_is_not_a_lane():
    """``000`` was the sentinel an older reader returned for "I do not know". A
    lane is 1-based, and emitting a fabricated zero once a pipeline's own lane
    column is filled is exactly the failure this phase removes."""
    assert lane_from_filename("PBMC_S1_L000_R1_001.fastq.gz") is None


def test_the_partial_readers_agree_with_the_whole_parser():
    """The reason to have one module: a name the uploader parses and a name the
    sheet builder parses must not resolve to different reads or lanes."""
    for name in (
        "PBMC_S1_L001_R1_001.fastq.gz",
        "PBMC_S1_L002_R2_001.fastq.gz",
        "PBMC_S1_L001_I1_001.fastq.gz",
        "PBMC_S1_L003_R1_001.fq.gz",
    ):
        parsed = parse_illumina_filename(name)
        assert parsed["read"] == read_type_from_filename(name)
        assert parsed["lane"] == lane_from_filename(name)


def test_the_sheet_builder_reads_a_name_through_the_same_parser():
    """A file carrying no tags and no typed columns falls back to its filename,
    and that fallback is the shared parser rather than a fourth regex."""
    f = type(
        "F",
        (),
        {
            "storage_uri": "gs://b/PBMC_S1_L004_R2_001.fq.gz",
            "filename": "PBMC_S1_L004_R2_001.fq.gz",
            "tags_json": [],
            "lane": None,
            "read_type": None,
            "flowcell_id": None,
            "index_sequence": None,
            "source_run_accession": None,
        },
    )()

    assert sample_sheet_service._get_read_type(f) == "R2"
    assert sample_sheet_service._get_lane(f) == 4


# ---------------------------------------------------------------------------
# Through the uploader, which is where the narrow pattern did its damage
# ---------------------------------------------------------------------------


async def _complete_upload(session, admin_user, filename: str):
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
async def test_an_uploaded_index_read_keeps_its_identity(session, admin_user):
    file = await _complete_upload(session, admin_user, "PBMC_S1_L001_I1_001.fastq.gz")

    assert file.read_type == "I1"
    assert file.lane == 1


@pytest.mark.asyncio
async def test_an_uploaded_fq_gz_keeps_its_identity(session, admin_user):
    file = await _complete_upload(session, admin_user, "PBMC_S1_L002_R1_001.fq.gz")

    assert file.read_type == "R1"
    assert file.lane == 2


@pytest.mark.asyncio
async def test_an_uploaded_ri_file_is_given_no_read_type(session, admin_user):
    file = await _complete_upload(session, admin_user, "PBMC_S1_L001_RI_001.fastq.gz")

    assert file.read_type is None
