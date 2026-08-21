"""Reading a FASTQ's header when it arrives, not guessing from its name.

Migration 119 filled a file's sequencing identity from its FILENAME, because
that was all bioAF had. Decision 4 of 2026-08-19: extract on ingest, from the
header, which carries flow cell and lane regardless of naming discipline. Never
the barcode.

The properties that matter, all of them about not making things worse:

**It never fails an upload.** The enrichment is optional by design. A file bioAF
cannot read, or storage it cannot reach, leaves the columns NULL and the sheet
then treats everything as one implicit unit, which is the pre-merged case that
has to stay untouched.

**It reads a PREFIX.** A FASTQ can be hundreds of GB and the answer is in the
first record.

**The header wins over the filename**, because the filename is a hint and the
header is the fact.
"""

import gzip
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.sequencing_enrichment import enrich_from_header

HEADER = "@A00228:279:HFWFVDMXX:3:1101:8486:1000 1:N:0:NCATTACT"


def _fastq_bytes(header: str = HEADER) -> bytes:
    return gzip.compress(("\n".join([header, "ACGT", "+", "IIII"]) + "\n").encode())


def _file(filename="SAMPLE_S1_L001_R1_001.fastq.gz", file_type="fastq", **identity):
    f = MagicMock()
    f.filename = filename
    f.file_type = file_type
    f.storage_uri = f"gs://bucket/{filename}"
    for column in ("lane", "flowcell_id", "read_type", "index_sequence", "source_run_accession"):
        setattr(f, column, identity.get(column))
    return f


def _adapter(data: bytes | None = None, error: Exception | None = None):
    adapter = MagicMock()
    adapter.read_prefix = AsyncMock(side_effect=error) if error else AsyncMock(return_value=data)
    return adapter


class TestWhatItWrites:
    @pytest.mark.asyncio
    async def test_it_fills_the_flow_cell_and_the_lane(self):
        f = _file()

        await enrich_from_header(f, adapter=_adapter(_fastq_bytes()))

        assert f.flowcell_id == "HFWFVDMXX"
        assert f.lane == 3

    @pytest.mark.asyncio
    async def test_the_header_wins_over_the_filename(self):
        """The filename says lane 1 and the header says lane 3. A filename is a
        hint, and a file renamed after ingest loses it entirely; the header is
        the fact."""
        f = _file("SAMPLE_S1_L001_R1_001.fastq.gz", lane=1)

        await enrich_from_header(f, adapter=_adapter(_fastq_bytes()))

        assert f.lane == 3

    @pytest.mark.asyncio
    async def test_it_never_writes_a_barcode(self):
        f = _file()

        await enrich_from_header(f, adapter=_adapter(_fastq_bytes()))

        assert f.index_sequence is None

    @pytest.mark.asyncio
    async def test_it_reads_a_prefix_rather_than_the_file(self):
        adapter = _adapter(_fastq_bytes())
        f = _file()

        await enrich_from_header(f, adapter=adapter)

        uri, length = adapter.read_prefix.await_args.args
        assert uri == f.storage_uri
        assert 0 < length <= 1_000_000


class TestWhatItLeavesAlone:
    @pytest.mark.asyncio
    async def test_a_header_that_says_nothing_leaves_the_columns_null(self):
        f = _file()

        await enrich_from_header(f, adapter=_adapter(_fastq_bytes("@SRR000001.1 1 length=36")))

        assert f.flowcell_id is None
        assert f.lane is None

    @pytest.mark.asyncio
    async def test_a_filename_lane_survives_a_header_that_says_nothing(self):
        """Monotonic: the header adds a fact or it adds nothing. It never
        removes one bioAF already had."""
        f = _file(lane=1)

        await enrich_from_header(f, adapter=_adapter(_fastq_bytes("@SRR000001.1 1 length=36")))

        assert f.lane == 1

    @pytest.mark.asyncio
    async def test_storage_it_cannot_reach_is_not_an_error(self):
        """An upload must not fail because an optional enrichment could not
        run."""
        f = _file()

        await enrich_from_header(f, adapter=_adapter(error=RuntimeError("no such object")))

        assert f.flowcell_id is None

    @pytest.mark.asyncio
    async def test_a_file_that_is_not_a_fastq_is_not_read_at_all(self):
        """No point spending a storage round trip on a BAM, and reading one as
        a FASTQ would be a category error."""
        adapter = _adapter(_fastq_bytes())
        f = _file("alignment.bam", file_type="bam")

        await enrich_from_header(f, adapter=adapter)

        adapter.read_prefix.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_file_with_no_storage_uri_is_skipped(self):
        adapter = _adapter(_fastq_bytes())
        f = _file()
        f.storage_uri = None

        await enrich_from_header(f, adapter=adapter)

        adapter.read_prefix.assert_not_awaited()
