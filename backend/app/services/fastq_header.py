"""Flow cell and lane, read from the FASTQ itself rather than from its name.

bioAF enforces no naming standard, so a filename is a HINT: a CRO delivers what
it delivers, and a file renamed after ingest loses the hint entirely. The header
carries these facts regardless of naming discipline:

    @A00228:279:HFWFVDMXX:1:1101:8486:1000 1:N:0:NCATTACT
     instrument
        run
            flowcell
                     lane
                       tile:x:y  read:filtered:control:index

**Flow cell and lane only. Never the barcode**, deliberately: a single read's
index can be a no-call (the example above begins ``N``), and the barcode is a
property of the demultiplexing rather than of one record.

The lane alone was never enough. ``L001`` on two flow cells is two different
lanes, so a lane number without its flow cell collides, which is precisely what
the read-group axis needs both halves for.

Everything here returns "nothing" rather than raising. This is an OPTIONAL
enrichment: a file bioAF cannot read must never fail an upload, and an unknown
fact leaves a NULL column, which the sheet builder already treats as one implicit
sequencing unit.
"""

import gzip
import logging
import zlib

logger = logging.getLogger("bioaf.fastq_header")

# How much of the object to fetch. One record is ~200 bytes uncompressed and the
# gzip header costs a few dozen more, so this is generous by orders of magnitude
# while staying negligible against a file that may be hundreds of GB.
PREFIX_BYTES = 65536

# The read-name layout every current Illumina instrument writes, and the only one
# read here. The older Casava layout (`@HWUSI-EAS100R:6:73:941:1973#0/1`) puts the
# lane second and names no flow cell at all; bioAF could count fields and guess,
# but a lane without its flow cell is the exact collision the read-group axis
# exists to remove, and reading a tile as a lane would write a fiction into a
# typed column.
_MODERN_FIELDS = 7
_FLOWCELL_AT = 2
_LANE_AT = 3


def _first_line(data: bytes) -> str:
    """The first line of a FASTQ, whether or not it arrived gzipped.

    A PREFIX of a gzip stream is a truncated stream by construction, so a
    truncation is the normal case here and never an error: ``decompressobj``
    yields what it has and the rest is simply absent.
    """
    if not data:
        return ""

    text = b""
    if data[:2] == b"\x1f\x8b":
        try:
            text = zlib.decompressobj(wbits=31).decompress(data)
        except (zlib.error, OSError, EOFError):
            return ""
    else:
        try:
            # An ordinary uncompressed FASTQ. gzip.decompress would reject it,
            # and a lab that stores them uncompressed is not doing anything
            # wrong.
            text = data
        except (gzip.BadGzipFile, OSError):
            return ""

    line, _, _ = text.partition(b"\n")
    return line.decode("utf-8", errors="replace").strip()


def parse(data: object) -> dict:
    """The flow cell and lane a FASTQ's first record names, or an empty dict.

    Empty means "this file does not say", which is a real answer and not a
    failure: an archive-downloaded FASTQ carries ``@SRR000001.1 1 length=36``
    and names neither.
    """
    if not isinstance(data, (bytes, bytearray)):
        return {}

    line = _first_line(bytes(data))
    if not line.startswith("@"):
        return {}

    # EVERY whitespace-delimited token, not just the first. Measured on the
    # demo rather than assumed: `fastq-dump` writes its own accession first and
    # keeps the instrument's read name AFTER it, so reading only the first token
    # threw away a flow cell and lane that were right there, on exactly the
    # population that has no filename convention to fall back on.
    #
    #     @SRR30176122.1 A00609:829:HLK3VDSX7:1:1101:20202:1000 length=150
    #
    # This stays exact rather than becoming a guess: a token qualifies only by
    # having seven colon-separated fields with a numeric lane, which neither
    # `SRR30176122.1` nor `length=150` can satisfy.
    for token in line[1:].split():
        read = _read_name(token)
        if read:
            return read
    return {}


def _read_name(token: str) -> dict:
    """What one whitespace-delimited token says, if it is an Illumina read name."""
    fields = token.split(":")
    if len(fields) != _MODERN_FIELDS:
        return {}

    flowcell = fields[_FLOWCELL_AT].strip()
    lane = fields[_LANE_AT].strip()
    if not flowcell or not lane.isdigit():
        return {}
    # A lane is 1-based, the same rule the filename reader holds. A parsed zero
    # is not a lane.
    if int(lane) <= 0:
        return {}

    return {"flowcell_id": flowcell, "lane": int(lane)}
