"""How an asset's identity is spelled for a pipeline, and found again afterwards.

bioAF decides which sample a pipeline output belongs to by matching text in the
output's path. That is sound only while two strings agree: the name bioAF wrote
into the samplesheet and the name it later searches for. Nothing enforces it, and
a scientist accepting a recommended spelling breaks it.

An identifier removes the disagreement, but it cannot be a raw UUID. Measured
against the captured catalog: nf-core/ampliseq requires
``^[a-zA-Z][a-zA-Z0-9_]+$``, which a UUID fails twice, on its hyphens and on a
leading digit. So the samplesheet SPELLING is a letter prefix plus the hex with
hyphens stripped, and the stored identity stays an ordinary UUID; only its
rendering for pipelines is constrained.

Checked against every identity column in the captured catalog (ampliseq, bacass,
demo, funcscan, mag, raredisease, rnasplice, rnastructurome, sarek, taxprofiler):
all accept it, at 33 characters.

**Nothing emits this yet.** Emitting it also requires the layer that maps an
identifier back to PROJECT | EXPERIMENT | SAMPLE | FILE on every human-readable
surface, downloads and exports included, because once the sheet carries a UID the
pipeline names its outputs after it and writes it into its own reports. This
module is the half that can be built and proven on its own: the spelling, and
finding it again in a path.
"""

import re
import uuid as uuid_pkg

# A letter, so the value can never start with a digit.
UID_PREFIX = "s"

# Bounded on both sides so a UID is recognised wherever a pipeline puts it. The
# position VARIES and both forms below are real, observed on the demo:
#
#   nf-core/demo        fastqc/SAMPLE-101/SAMPLE-101_1_fastqc.html   (segment AND filename)
#   nf-core/bamtofastq  samtools/SRX30659361.flagstat                (filename only)
#
# A rule written for one of those passes on that pipeline and fails silently on
# the other, which is the unmatched-per-sample-file failure this project exists to
# remove. A 33-character identifier is distinctive enough that a bounded token
# match anywhere in the path is safe, and it covers any third layout too.
_UID_RE = re.compile(rf"(?<![A-Za-z0-9]){UID_PREFIX}([0-9a-f]{{32}})(?![0-9a-fA-F])")


def sheet_spelling(value: uuid_pkg.UUID | str) -> str:
    """The samplesheet rendering of a UUID: a letter prefix plus bare hex."""
    if not isinstance(value, uuid_pkg.UUID):
        value = uuid_pkg.UUID(str(value))
    return f"{UID_PREFIX}{value.hex}"


def parse_sheet_spelling(text: str) -> uuid_pkg.UUID | None:
    """The UUID a spelling denotes, or None when the text is not one."""
    match = _UID_RE.fullmatch((text or "").strip())
    return uuid_pkg.UUID(match.group(1)) if match else None


def uids_in(text: str) -> set[uuid_pkg.UUID]:
    """Every asset identity named anywhere in a path or filename.

    A set rather than the first hit: a pipeline may name two assets in one path
    (an output derived from a pair), and silently taking the first would attach
    the file to one of them and not the other.
    """
    return {uuid_pkg.UUID(m.group(1)) for m in _UID_RE.finditer(text or "")}
