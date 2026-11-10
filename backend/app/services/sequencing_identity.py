"""One reading of the Illumina filename convention, for everything that needs it.

Three regexes across two modules used to parse the same convention and disagree
about it:

    upload_service      ^(.+?)_S(\\d+)_L(\\d{3})_(R[12I])_(\\d{3})\\.fastq\\.gz$
    sample_sheet_service                    _(R[12]|I[12])_     and    _L(\\d{3})_

Each disagreement was a real defect, verified by running them rather than by
reading them:

- ``R[12I]`` does not match ``_I1_``, so an index read uploaded under the
  convention was stored with no sequencing identity at all, while the sheet
  builder's own regex recognised it perfectly well and skipped it.
- ``R[12I]`` DOES match ``_RI_``, which is not a read code that exists.
- ``\\.fastq\\.gz$`` rejects ``.fq.gz``, although ``validate_fastq_filename`` has
  always accepted it and MGI/BGI shops and many CROs deliver it. The uploader
  took the file and then failed to read its name.

A filename is a HINT and never a source of truth: bioAF enforces no naming
standard, so a name that does not carry the convention yields nothing rather than
a guess. The authoritative values live in the FASTQ header, which carries flow
cell, lane and index regardless of naming discipline.
"""

import re

# The read codes that exist. Anything else a pattern happens to match is a parse
# artifact, and a typed column holding one is worse than a NULL.
READ_TYPES = ("R1", "R2", "I1", "I2")

# SampleName_S1_L001_R1_001.fastq.gz, and the same name delivered as .fq.gz.
ILLUMINA_PATTERN = re.compile(
    r"^(?P<sample_name>.+?)_S(?P<sample_number>\d+)_L(?P<lane>\d{3})_"
    r"(?P<read>R[12]|I[12])_(?P<set_number>\d{3})\.f(?:ast)?q\.gz$"
)

# The same two facts read out of a name that does not follow the whole
# convention, which is most of them: labs name files the way their lab names
# files, and a partial match is still evidence.
_READ_RE = re.compile(r"_(R[12]|I[12])_")
_LANE_RE = re.compile(r"_L(\d{3})_")


def parse_illumina_filename(filename: str) -> dict | None:
    """Sample name, sample number, lane, read and set number, or None.

    None means the name does not follow the convention, which is not an error:
    a pre-merged FASTQ from a CRO carries none of this and must be unaffected.
    """
    match = ILLUMINA_PATTERN.match(filename or "")
    if not match:
        return None
    return {
        "sample_name": match.group("sample_name"),
        "sample_number": int(match.group("sample_number")),
        "lane": int(match.group("lane")),
        "read": match.group("read"),
        "set_number": int(match.group("set_number")),
    }


def read_type_from_filename(filename: str) -> str | None:
    """R1, R2, I1 or I2 read out of a filename, or None."""
    match = _READ_RE.search(filename or "")
    return match.group(1) if match else None


def lane_from_filename(filename: str) -> int | None:
    """The physical lane read out of a filename, or None when it says nothing.

    A lane is 1-based, so ``_L000_`` is not a lane. It was the sentinel an older
    reader returned for "I do not know", and a fabricated value that would be
    emitted as a real lane the moment a pipeline's own ``lane`` column is filled.
    """
    match = _LANE_RE.search(filename or "")
    if not match:
        return None
    lane = int(match.group(1))
    return lane or None


# -- A file's sequencing identity ---------------------------------------------
#
# One rule for every consumer that asks which unit a file came from: the typed
# column migration 119 added, then the legacy ``lane:``/``read:`` tag files
# written before it still carry, then the filename. Read groups and the sheet
# builder's mate pairing both read it here, and each keeps its own policy about
# WHICH files it looks at (a read-group view shows index reads; a sheet never
# feeds them to a pipeline).

# A unit about which nothing is known: a pre-merged FASTQ from a CRO, or a path
# a caller named directly. Every fact is absent.
UNKNOWN_UNIT: tuple[str, int, str] = ("", 0, "")


def _typed(f, column: str, kind: type):
    """A typed sequencing-identity column, or None when it says nothing.

    The isinstance check is load-bearing rather than defensive. Much of the test
    suite builds files out of MagicMock, which auto-vivifies any attribute into a
    truthy object; reading one of those as a lane would scatter a sample's files
    across as many units as it has files. The same guard covers a real row whose
    column is NULL.
    """
    value = getattr(f, column, None)
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def _tags(f) -> list:
    tags = getattr(f, "tags_json", None) or []
    return tags if isinstance(tags, (list, tuple)) else []


def read_type(f) -> str | None:
    """R1, R2, I1 or I2: the typed column, then the legacy ``read:`` tag, then the filename."""
    typed = _typed(f, "read_type", str)
    if typed in READ_TYPES:
        return typed
    for tag in _tags(f):
        if isinstance(tag, str) and tag.startswith("read:"):
            return tag.split(":", 1)[1]
    return read_type_from_filename(getattr(f, "filename", "") or "")


def lane(f) -> int | None:
    """The physical lane, or None when it is not known.

    None rather than a sentinel. ``"000"`` used to stand in for "I do not know",
    and it is a fabricated value that would be emitted as a real lane the moment
    a pipeline's own ``lane`` column gets filled. A lane is 1-based, so a parsed
    zero is not a lane either, and a typed zero does not fall through to the tag.
    """
    typed = _typed(f, "lane", int)
    if typed is not None:
        return typed if typed > 0 else None
    for tag in _tags(f):
        if isinstance(tag, str) and tag.startswith("lane:"):
            # Both spellings the two legacy writers produced ("1" and "001") are
            # one lane. Read as strings they were two dict keys, which is how one
            # physical lane became two units and a sample's mates stopped
            # pairing.
            digits = tag.split(":", 1)[1].strip()
            return int(digits) if digits.isdigit() and int(digits) > 0 else None
    return lane_from_filename(getattr(f, "filename", "") or "")


def flowcell_id(f) -> str | None:
    """The flow cell the file was read off, when bioAF holds it."""
    return _typed(f, "flowcell_id", str) or None


def run_accession(f) -> str | None:
    """The archive run accession a fetched file came from, when bioAF holds it."""
    return _typed(f, "source_run_accession", str) or None


def sequencing_unit(f) -> tuple[str, int, str]:
    """What tells two sequencing units of one sample apart, as a sort key.

    The read-group axis is (flow cell, lane): ``L001`` on two flow cells is two
    different lanes, so a lane number alone collides. A fetched FASTQ has neither
    and carries its archive run accession instead, which distinguishes sibling
    runs of one sample without pretending to be a lane.

    Everything unknown collapses to ``UNKNOWN_UNIT``, which is the case that must
    stay untouched: a CRO's pre-merged FASTQs have no lane at all.
    """
    return (flowcell_id(f) or "", lane(f) or 0, run_accession(f) or "")
