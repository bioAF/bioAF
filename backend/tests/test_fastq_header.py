"""Reading flow cell and lane out of a FASTQ, which is where they really live.

Migration 119 gave a file typed columns for its sequencing identity and filled
them from the FILENAME, because that is all bioAF had. A filename is a hint:
bioAF enforces no naming standard, a CRO delivers what it delivers, and a file
renamed after ingest loses the hint entirely.

Decision 4 of 2026-08-19: extract on ingest, from the header, which carries these
facts regardless of naming discipline.

    @A00228:279:HFWFVDMXX:1:1101:8486:1000 1:N:0:NCATTACT
             run  flowcell lane                    index

**Flowcell and lane only. Never the barcode**, deliberately: a single read's
index can be a no-call (the example above begins `N`), and the barcode is a
property of the demultiplexing rather than of one record.

The lane alone was never enough. `L001` on two flow cells is two different lanes,
so a lane number without its flow cell collides, which is exactly what the
read-group axis needs both halves for.
"""

import gzip
import zlib

from app.services import fastq_header

MODERN = "@A00228:279:HFWFVDMXX:1:1101:8486:1000 1:N:0:NCATTACT"


def _gzipped(*lines: str) -> bytes:
    return gzip.compress(("\n".join(lines) + "\n").encode())


def _record(header: str) -> tuple[str, ...]:
    return (header, "ACGTACGTAC", "+", "IIIIIIIIII")


class TestWhatItReads:
    def test_it_reads_the_flow_cell_and_the_lane(self):
        assert fastq_header.parse(_gzipped(*_record(MODERN))) == {
            "flowcell_id": "HFWFVDMXX",
            "lane": 1,
        }

    def test_it_never_reads_the_barcode(self):
        """A single read's index can be a no-call, and the barcode belongs to
        the demultiplexing rather than to one record. Stated as a test so a
        later 'while we are here' cannot quietly add it."""
        assert "index_sequence" not in fastq_header.parse(_gzipped(*_record(MODERN)))

    def test_an_uncompressed_fastq_reads_the_same(self):
        assert fastq_header.parse(("\n".join(_record(MODERN)) + "\n").encode()) == {
            "flowcell_id": "HFWFVDMXX",
            "lane": 1,
        }

    def test_only_the_first_record_is_needed(self):
        """The cost of this is a few KB of a file that can be hundreds of GB."""
        prefix = _gzipped(*_record(MODERN), *_record("@A00228:279:HFWFVDMXX:2:1101:1:1 1:N:0:AAAA"))

        assert fastq_header.parse(prefix[:200])["lane"] == 1


class TestWhatItRefusesToGuess:
    def test_a_header_that_is_not_the_convention_yields_nothing(self):
        """An archive-downloaded FASTQ carries `@SRR000001.1 1 length=36`, which
        names neither a flow cell nor a lane. Nothing is a real answer here: the
        columns stay NULL and the sheet groups everything as one unit, which is
        the pre-merged case that must be unaffected."""
        assert fastq_header.parse(_gzipped(*_record("@SRR000001.1 1 length=36"))) == {}

    def test_the_older_casava_layout_is_deliberately_not_read(self):
        """`@HWUSI-EAS100R:6:73:941:1973#0/1` puts the lane second and names no
        flow cell. bioAF could count fields and guess, but a lane without its
        flow cell is the collision the read-group axis exists to remove, and
        reading a tile as a lane would write a fiction into a typed column."""
        assert fastq_header.parse(_gzipped(*_record("@HWUSI-EAS100R:6:73:941:1973#0/1"))) == {}

    def test_a_lane_that_is_not_a_number_is_not_a_lane(self):
        assert fastq_header.parse(_gzipped(*_record("@A00228:279:HFWFVDMXX:X:1101:8486:1000 1:N:0:A"))) == {}

    def test_a_zero_lane_is_not_a_lane(self):
        """A lane is 1-based, the same rule the filename reader already holds."""
        assert fastq_header.parse(_gzipped(*_record("@A00228:279:HFWFVDMXX:0:1101:8486:1000 1:N:0:A"))) == {}

    def test_an_empty_flow_cell_field_yields_nothing(self):
        assert fastq_header.parse(_gzipped(*_record("@A00228:279::1:1101:8486:1000 1:N:0:A"))) == {}

    def test_nothing_at_all_is_not_an_error(self):
        """A file bioAF cannot read must never fail an upload. The whole feature
        is optional by design, and everything unknown collapses to one implicit
        unit."""
        assert fastq_header.parse(b"") == {}
        assert fastq_header.parse(b"\x1f\x8b garbage that is not a gzip stream") == {}
        assert fastq_header.parse(None) == {}

    def test_a_truncated_gzip_stream_is_still_read(self):
        """A prefix read is a truncated stream BY CONSTRUCTION, so treating a
        truncation as corruption would refuse every file."""
        truncated = _gzipped(*_record(MODERN))[:120]

        assert fastq_header.parse(truncated)["flowcell_id"] == "HFWFVDMXX"

    def test_a_stream_that_decompresses_to_nothing_useful_yields_nothing(self):
        assert fastq_header.parse(zlib.compress(b"not a fastq")) == {}
