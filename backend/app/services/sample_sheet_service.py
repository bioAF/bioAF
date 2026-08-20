import csv
import io
import logging
import re
from functools import lru_cache

from app.services import sequencing_identity
from app.exceptions import (
    PipelineNotSampleLaunchableError,
    SamplesMissingRequiredFieldsError,
)

logger = logging.getLogger("bioaf.sample_sheet")


def _join(items) -> str:
    """Join names for a message read by a scientist, not a parser."""
    items = list(items)
    if not items:
        return "a different input"
    if len(items) == 1:
        return f"'{items[0]}'"
    return ", ".join(f"'{i}'" for i in items[:-1]) + f" and '{items[-1]}'"


# ChIP-seq control/input detection (lit_validation Phase 4). A fetched sample carries its ENA/GEO
# title + library_strategy in prep_notes (captured by FetchngsIngestService); we read that plus the
# external_id to tell a control/input sample from an IP sample. Markers are matched case-insensitively
# on word-ish boundaries so "input" doesn't fire on "reinput" etc.
_CONTROL_MARKERS = ("input", "igg", "mock", "control", "wce", "whole cell extract", "no antibody", "no-antibody")
# A histone-mark antibody token (H3K4me3, H3K27ac, H3K9me2, ...); a clean, no-space antibody label.
_HISTONE_MARK_RE = re.compile(r"\bH[1-4](?:K\d+)(?:me[1-3]|ac|ub)?\b", re.IGNORECASE)

# A sample name that is purely numeric (int or float). nf-core/nf-schema infers a
# CSV column's type from its values, so such a name is typed as integer/number and
# rejected against the schema's string 'sample' field ("Value is [integer] but
# should be [string]"). We prefix these so the value is unambiguously a string.
_NUMERIC_NAME_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _safe_sample_name(sample) -> str:
    """Resolve a sample's 'sample' column value, guaranteed to be string-typed.

    Uses the sample's ``external_id`` (falling back to ``sample_<id>`` when it is
    empty), then prefixes any purely numeric name so nf-schema does not coerce it
    to an integer and reject it. Non-numeric names and the fallback pass through
    unchanged, so this only changes names that nf-core would reject anyway.
    """
    name = (getattr(sample, "external_id", None) or "").strip()
    if not name:
        return f"sample_{sample.id}"
    if _NUMERIC_NAME_RE.fullmatch(name):
        return f"sample_{name}"
    return name


_READ_TYPES = ("R1", "R2", "I1", "I2")


def _typed(f, column: str, kind: type):
    """A typed sequencing-identity column, or None when it says nothing.

    The isinstance check is load-bearing rather than defensive. Much of this
    suite builds files out of MagicMock, which auto-vivifies any attribute into a
    truthy object; reading one of those as a lane would scatter a sample's files
    across as many units as it has files. The same guard covers a real row whose
    column is NULL.
    """
    value = getattr(f, column, None)
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def _get_read_type(f) -> str | None:
    """Return read type (R1, R2, I1, I2).

    The typed column first, then the legacy ``read:`` tag, then the filename.
    The tag reader stays for one release so files written before the sequencing
    identity migration keep pairing exactly as they do today.
    """
    typed = _typed(f, "read_type", str)
    if typed in _READ_TYPES:
        return typed
    tags = getattr(f, "tags_json", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("read:"):
            return tag.split(":", 1)[1]
    return sequencing_identity.read_type_from_filename(getattr(f, "filename", "") or "")


def _get_lane(f) -> int | None:
    """Return the physical lane number, or None when it is not known.

    None rather than a sentinel. ``"000"`` used to stand in for "I do not know",
    and it is a fabricated value that would be emitted as a real lane the moment
    a pipeline's own ``lane`` column gets filled. A lane is 1-based, so a parsed
    zero is not a lane either.
    """
    typed = _typed(f, "lane", int)
    if typed is not None:
        return typed if typed > 0 else None
    tags = getattr(f, "tags_json", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("lane:"):
            # Both spellings the two legacy writers produced ("1" and "001") are
            # one lane. Read as strings they were two dict keys, which is how one
            # physical lane became two units and a sample's mates stopped
            # pairing.
            digits = tag.split(":", 1)[1].strip()
            return int(digits) if digits.isdigit() and int(digits) > 0 else None
    return sequencing_identity.lane_from_filename(getattr(f, "filename", "") or "")


def _sequencing_unit(f) -> tuple:
    """What tells two sequencing units of one sample apart, as a sort key.

    The read-group axis is (flow cell, lane): ``L001`` on two flow cells is two
    different lanes, so a lane number alone collides. A fetched FASTQ has neither
    and carries its archive run accession instead, which distinguishes sibling
    runs of one sample without pretending to be a lane.

    Everything unknown collapses to one implicit unit, which is the case that
    must stay untouched: a CRO's pre-merged FASTQs have no lane at all.
    """
    return (
        _typed(f, "flowcell_id", str) or "",
        _get_lane(f) or 0,
        _typed(f, "source_run_accession", str) or "",
    )


# The facts ``_sequencing_unit`` packs into its key, in that order, so a unit can
# be read back as the facts it is made of rather than by tuple position.
_UNIT_FIELDS = ("flowcell_id", "lane", "source_run_accession")

# A unit about which nothing is known: a pre-merged FASTQ from a CRO, or a path
# the caller named directly. Every fact is absent, so no column is filled from it.
_UNKNOWN_UNIT = ("", 0, "")

# Samplesheet columns bioAF fills from the sequencing unit the row came from,
# keyed on the column name the pipeline's own schema uses.
#
# The discipline is the one ``_COLUMN_TO_SAMPLE_FIELD`` states: exact, explicit
# matching, and only where the column's MEANING is the fact bioAF holds. A lane
# is a lane, so sarek's `lane` is reporting a measurement. mag's and ampliseq's
# `run` is a sequencing RUN, which a lane is NOT, and taxprofiler's
# `run_accession` names a real archive run rather than anything derived from a
# filename. Those keep asking, because a wrong mapping is worse than a missing
# one and this is the exact spot where a plausible-looking guess would enter the
# sheet as a scientific claim.
_COLUMN_TO_SEQUENCING_FACT: dict[str, str] = {
    "lane": "lane",
    "run_accession": "source_run_accession",
}


def _unit_facts(contract, unit: tuple) -> dict[str, str]:
    """What this sequencing unit answers about itself, keyed by column.

    Only columns the pipeline actually declares, and only facts the unit really
    carries: an absent key leaves the column to resolve as it did before, so a
    file with no accession still falls back to the sample's own name.
    """
    known = dict(zip(_UNIT_FIELDS, unit))
    facts: dict[str, str] = {}
    for column, field in _COLUMN_TO_SEQUENCING_FACT.items():
        if column not in getattr(contract, "columns", ()):
            continue
        value = known.get(field)
        if value:
            facts[column] = str(value)
    return facts


def _input_files(sample) -> list:
    """The sample's input-eligible files.

    Prefers the set the launcher resolves (raw inputs only, with prior
    pipeline/notebook outputs excluded by default). Falls back to all linked
    files for callers that don't set it. The isinstance guard stops a MagicMock
    auto-vivifying the attribute in unit tests.
    """
    files = getattr(sample, "_input_files", None)
    if not isinstance(files, list):
        files = getattr(sample, "files", None) or []
    return [f for f in files if getattr(f, "storage_uri", None)]


def _candidates(value: str) -> list[str]:
    """Progressively less literal spellings of one value, most faithful first.

    Scientists name samples the way their lab names samples, and a pipeline's
    schema often will not take that name: ampliseq requires
    ``^[a-zA-Z][a-zA-Z0-9_]+$``, so ``SAMPLE-101`` is rejected on the hyphen.

    These are only CANDIDATES for something to SUGGEST. Every one is checked
    against the column's own regex by ``_recommendation`` before it can be
    offered, so this list never has to be correct about which pipelines accept
    what, and nothing here is ever substituted for the scientist's own value.

    Ordered so the smallest change that works wins, and deterministic, so the
    same sample yields the same suggestion on every run.
    """
    trimmed = value.strip()
    spaced = re.sub(r"\s+", "_", trimmed)
    tidied = re.sub(r"[^A-Za-z0-9_]", "_", spaced)
    collapsed = re.sub(r"_+", "_", tidied).strip("_")
    return [trimmed, spaced, tidied, collapsed, f"s_{collapsed}"]


def _compiled_matches(pattern: str, value: str) -> bool:
    """Whether a value satisfies a column's declared regex.

    An unparseable pattern in a published schema is treated as no constraint,
    the same way it is everywhere else here: a pipeline's broken regex must not
    take down a launch bioAF could otherwise make.
    """
    regex = _compiled(pattern)
    return True if regex is None else bool(regex.match(value))


def _recommendation(value: str, pattern: str | None) -> str | None:
    """A spelling of ``value`` this column would accept, or None if there is none.

    Only ever a RECOMMENDATION. bioAF does not decide what a field says, so this
    is offered to the scientist rather than substituted for them.

    The verification is what makes it safe to offer. A transform that merely
    looks tidier is not evidence a pipeline will take it, so each candidate is
    matched against the schema's declared pattern and the first that actually
    satisfies it is the one shown.

    None when nothing works, which is the honest answer for a constraint that
    punctuation cannot repair: no rearrangement turns a mistyped
    ``^GC[AF]_[0-9]{9}\\.[0-9]+$`` accession into a correct one, and suggesting
    something that merely looks like an accession would name the wrong assembly.
    """
    if not value or not pattern:
        return None
    regex = _compiled(pattern)
    if regex is None:
        return None
    for candidate in _candidates(value):
        if candidate and candidate != value and regex.match(candidate):
            return candidate
    return None


def _supplied(sample_values, sample) -> dict[str, str]:
    """The values a scientist stated for one sample, keyed by column.

    **Matched on the sample's own ID, never on position.** A positional match
    misaligned by one row (a header included in a paste, a sample filtered out of
    the grid but not the spreadsheet, a different sort order) assigns every value
    to the wrong sample, and the run then completes green with the wrong
    co-assembly grouping or the wrong differential contrast. Keying on the id
    makes that class of error unrepresentable rather than merely unlikely.

    Values for samples this run does not include are simply absent from the
    result: a mapping carried over from an earlier run names samples that may not
    be selected now, and those values must not land on somebody else's row.

    A blank value is dropped here, so it reads as an unanswered question rather
    than as an instruction to emit nothing. The column then resolves as it would
    have without the grid, and a required column with no other source blocks the
    launch, which is what design section 5 asks for.
    """
    if not sample_values:
        return {}
    raw = sample_values.get(str(getattr(sample, "id", "")))
    if not isinstance(raw, dict):
        return {}
    return {
        str(column).strip(): str(value).strip()
        for column, value in raw.items()
        if value is not None and str(value).strip()
    }


@lru_cache(maxsize=512)
def _compiled(pattern: str):
    """A column's declared regex, or None when the schema's pattern is invalid.

    Never raises: a malformed pattern in a published schema must not take a
    launch down, and returning None simply means nothing matches that column.
    """
    try:
        return re.compile(pattern)
    except re.error:
        logger.info("Ignoring unparseable samplesheet column pattern %r", pattern)
        return None


def _file_matches(pattern: str, f) -> bool:
    """Whether a file satisfies a column's own declared pattern.

    Checked against both the bare filename and the full storage URI, because
    catalog patterns are written both ways: sarek anchors ``^\\S+\\.bam$`` (which
    a URI satisfies, since ``\\S`` covers the scheme and slashes) while others
    anchor an optional leading path explicitly.
    """
    regex = _compiled(pattern)
    if regex is None:
        return False
    for candidate in (getattr(f, "filename", "") or "", getattr(f, "storage_uri", "") or ""):
        if candidate and regex.search(candidate):
            return True
    return False


def _bound_files(binding: dict, sample) -> list:
    """The sample's files a ``file_type`` binding selects.

    Matched on ``File.file_type``, which is what the scientist chose from, and
    never on the filename: a declared sheet has no pattern to match against,
    because the pipeline published nothing to declare one.
    """
    wanted = (binding.get("key") or "").strip().lower()
    return [f for f in _input_files(sample) if (getattr(f, "file_type", "") or "").strip().lower() == wanted]


def _custom_field(sample, name: str) -> str:
    """One of the sample's custom fields, by the name the intake editor gave it."""
    for field in getattr(sample, "custom_fields", None) or []:
        if (getattr(field, "field_name", "") or "").strip() == name:
            return str(getattr(field, "field_value", "") or "")
    return ""


def _bound_value(contract, column: str, sample) -> str:
    """What a declared column's binding resolves to, or empty when it cannot.

    Empty is a real answer and never a guess: a binding that resolves to nothing
    leaves the column blank, and a REQUIRED column left blank is reported by
    ``column_gaps`` naming the column and the samples. File columns are resolved
    by ``_files_for_column``, which blocks on ambiguity rather than picking.
    """
    binding = (getattr(contract, "bindings", None) or {}).get(column)
    if not binding:
        return ""
    source = binding.get("source")
    if source == "literal":
        return str(binding.get("key") or "")
    if source == "sample_field":
        return str(getattr(sample, binding.get("key") or "", None) or "")
    if source == "custom_field":
        return _custom_field(sample, binding.get("key") or "")
    # `read` is resolved per row and `file_type` per file column, both before
    # this is reached.
    return ""


def _files_for_column(contract, column: str, sample) -> list:
    """The sample's files eligible for one file column.

    A column with no declared pattern yields nothing: bioAF cannot identify which
    file belongs there, and guessing is what this whole project exists to stop.

    Column patterns OVERLAP, so a file matching this column may belong to
    another. funcscan's ``protein`` accepts ``^\\S+\\.(faa|fasta)(\\.gz)?$``, which
    an assembly named ``.fasta`` satisfies just as well as the ``fasta`` column
    does; filling both hands a nucleotide assembly to a protein input. An
    OPTIONAL column therefore takes a file only when no other file column claims
    it, while a REQUIRED column takes its match regardless, because the pipeline
    has said it cannot run without one.

    Known limit: two REQUIRED columns whose patterns both match the same lone
    file would both take it. No catalog schema does this, and blocking on a case
    nothing exhibits would be untested complexity.
    """
    binding = (getattr(contract, "bindings", None) or {}).get(column)
    if binding and binding.get("source") == "file_type":
        return _bound_files(binding, sample)

    patterns = getattr(contract, "patterns", {})
    pattern = patterns.get(column)
    if not pattern:
        return []

    matches = [f for f in _input_files(sample) if _file_matches(pattern, f)]
    if not matches or column in contract.required:
        return matches

    rivals = [patterns[c] for c in contract.file_columns - {column} if patterns.get(c)]
    return [f for f in matches if not any(_file_matches(rival, f) for rival in rivals)]


def _read_pattern(contract, read_columns: list[str]) -> str | None:
    """The pattern a file must satisfy to BE a read for this pipeline.

    None when the schema declares none (bacass names its columns R1/R2 and
    describes nothing), in which case today's unfiltered behavior stands.
    """
    patterns = getattr(contract, "patterns", {})
    return next((patterns[c] for c in read_columns if patterns.get(c)), None)


def _eligible_reads(contract, sample, read_columns: list[str]) -> tuple[list, list]:
    """The sample's files split into (accepted as reads, present but rejected).

    The second half is what stops a silent drop. Filtering by the read pattern
    is correct (an uncompressed FASTQ is not something a `.gz`-only pipeline can
    read), but dropping the file without saying so turns a schema error that
    named the problem into an empty column that does not.
    """
    files = _input_files(sample)
    pattern = _read_pattern(contract, read_columns)
    if not pattern:
        return files, []
    accepted = [f for f in files if _file_matches(pattern, f)]
    return accepted, [f for f in files if f not in accepted]


def _read_rows(contract, sample, parameters: dict, read_columns: list[str]) -> list[dict[str, str]]:
    """One dict per sequencing unit of a sample: its reads, and what tells it apart.

    Shared by generation and the satisfiability check so the check reports what
    generation will actually produce, rather than assuming reads resolve.

    A row carries more than its reads. Two rows of one sample exist BECAUSE the
    sample was sequenced twice, and the fact that separates them is a property of
    the row rather than of the sample, so it can only be resolved here, where the
    unit is still known. See ``_unit_facts``.

    Paths named explicitly by the caller carry no unit, and get none: bioAF was
    handed a URI, not a file it holds facts about.
    """
    if not read_columns:
        return [{}]
    paths = parameters.get("input_paths", {}).get(str(sample.id), [])
    if paths:
        units = [(_UNKNOWN_UNIT, (paths[0] if len(paths) > 0 else "", paths[1] if len(paths) > 1 else ""))]
    else:
        accepted, _ = _eligible_reads(contract, sample, read_columns)
        units = _fastq_units(sample, files=accepted)
    return [{**dict(zip(read_columns, pair)), **_unit_facts(contract, unit)} for unit, pair in units]


def _unusable_reads_gap(
    contract, samples: list, parameters: dict, read_columns: list[str], sample_values=None
) -> dict | None:
    """Samples whose attached files cannot serve as this pipeline's reads.

    Deliberately narrow. It fires only when a sample HAS files, none of them
    qualify as reads, and no other file column is filled either, so the row would
    carry nothing the pipeline can act on. A sample with no files attached at all
    is a different situation, handled elsewhere in the launch path, and is left
    exactly as it behaves today.
    """
    pattern = _read_pattern(contract, read_columns)
    if not pattern:
        return None

    offenders = []
    for sample in samples:
        if parameters.get("input_paths", {}).get(str(sample.id)):
            continue
        # A read the scientist named is a read, whatever bioAF made of the files
        # it found. This is how an ambiguous or unrecognised read column gets
        # resolved from the review step.
        if any(_supplied(sample_values, sample).get(c) for c in read_columns):
            continue
        accepted, rejected = _eligible_reads(contract, sample, read_columns)
        if accepted or not rejected:
            continue
        # An alternative input (sarek's bam/cram) is a legitimate reason to have
        # no reads, so it is not a gap. It only counts when EXACTLY ONE file
        # matches, because that is the condition under which it will actually be
        # filled; an ambiguous alternative resolves to empty, and treating it as
        # satisfied would emit a row with no input in it at all.
        if any(len(_files_for_column(contract, c, sample)) == 1 for c in contract.non_read_file_columns):
            continue
        offenders.append(sample)

    if not offenders:
        return None
    return {
        "sample_field": None,
        "allowed_values": [],
        "reason": "no_matching_file",
        "pattern": pattern,
        "samples": [{"id": s.id, "external_id": s.external_id} for s in offenders],
    }


def _extract_fastq_lane_pairs(sample, files: list | None = None) -> list[tuple[str, str]]:
    """Extract (fastq_1, fastq_2) pairs grouped by sequencing unit.

    The pairs of ``_fastq_units``, for the callers that need only the reads.
    """
    return [pair for _unit, pair in _fastq_units(sample, files=files)]


def _fastq_units(sample, files: list | None = None) -> list[tuple[tuple, tuple[str, str]]]:
    """Each sequencing unit of a sample, with its (fastq_1, fastq_2).

    Excludes index reads (I1, I2). Uses the file's typed read type when
    available, falling back to its legacy tag and then to the Illumina filename
    convention (_R1_/_R2_). Returns one entry per sequencing unit, in a
    deterministic order; see ``_sequencing_unit`` for what separates two of them.

    The unit is returned alongside the reads rather than discarded, because it
    holds the only answer to what tells two rows of one sample apart. Discarding
    it here is why that question used to be unanswerable further up.

    ``files`` lets the schema-driven path pass only the files that satisfy the
    read column's own pattern. Without it the unclassified fallback below will
    place ANY attached file into fastq_1, so a sample carrying only an alignment
    would hand a BAM to a read column.
    """
    fastq_files = _input_files(sample) if files is None else [f for f in files if getattr(f, "storage_uri", None)]
    if not fastq_files:
        return [(_UNKNOWN_UNIT, ("", ""))]

    # Classify each file by read type
    units: dict[tuple, dict[str, str]] = {}
    unclassified = []
    for f in fastq_files:
        read_type = _get_read_type(f)
        if read_type and read_type.startswith("I"):
            continue  # Skip index reads
        if read_type in ("R1", "R2"):
            unit = _sequencing_unit(f)
            units.setdefault(unit, {})
            units[unit][read_type] = f.storage_uri
        else:
            unclassified.append(f)

    if units:
        result = []
        for unit_key in sorted(units):
            r1 = units[unit_key].get("R1", "")
            r2 = units[unit_key].get("R2", "")
            result.append((unit_key, (r1, r2)))
        return result

    # Fallback for files without read type info: sort by filename. Nothing is
    # known about the unit here, by definition: the files could not even be told
    # apart as mates, so their sequencing identity is not something to report.
    unclassified.sort(key=lambda f: getattr(f, "filename", "") or getattr(f, "storage_uri", ""))
    fastq_1 = unclassified[0].storage_uri if len(unclassified) > 0 else ""
    fastq_2 = unclassified[1].storage_uri if len(unclassified) > 1 else ""
    return [(_UNKNOWN_UNIT, (fastq_1, fastq_2))]


def _extract_fastq_paths(sample) -> tuple[str, str]:
    """Extract fastq_1 and fastq_2 GCS URIs from sample.files.

    Uses read type metadata to correctly identify R1/R2 and exclude index reads.
    For single-lane data returns one pair; for multi-lane see _extract_fastq_lane_pairs.
    """
    pairs = _extract_fastq_lane_pairs(sample)
    return pairs[0] if pairs else ("", "")


def _sample_text(sample) -> str:
    """Text used to classify a ChIP-seq sample: its external_id + prep_notes (which carry the ENA/GEO
    title + library_strategy for a fetched sample). Original case preserved for antibody extraction;
    control matching lowercases it itself."""
    parts = [getattr(sample, "external_id", None) or "", getattr(sample, "prep_notes", None) or ""]
    return " ".join(parts)


def _is_chip_control(sample) -> bool:
    """True if a sample looks like a ChIP-seq control/input (IgG, input, mock, WCE)."""
    text = _sample_text(sample).lower()
    return any(m in text for m in _CONTROL_MARKERS)


def _antibody_label(sample) -> str:
    """A no-space antibody token for a ChIP (IP) sample's ``antibody`` column.

    Prefers a recognized histone mark parsed from the title (H3K4me3, H3K27ac, ...); otherwise falls
    back to a sanitized sample name. The value only needs to be non-empty, space-free, and consistent
    across replicates of the same target (nf-core/chipseq groups consensus peaks by it)."""
    m = _HISTONE_MARK_RE.search(_sample_text(sample))
    if m:
        return m.group(0).replace(" ", "")
    return re.sub(r"\s+", "_", _safe_sample_name(sample))


def _ordered_columns(contract, read_columns: list[str]) -> list[str]:
    """A stable header order: identity, then reads, then the rest alphabetically.

    This used to follow the schema's own declared order, so a generated sheet
    would look like the pipeline's documented example. That does not survive
    storage: catalog schemas live in a JSONB column and PostgreSQL normalises
    object key order, so sarek's stored schema returns
    ``bai, bam, sex, vcf, crai, ...`` where its file declares
    ``patient, sample, sex, status, ...``. The declared order therefore held only
    in tests that read the fixture file.

    nf-schema reads by header name, so order is legibility rather than
    correctness. What matters is that it is the same wherever the schema came
    from, which an explicit order gives and an inherited one does not.
    """
    # A declared sheet is emitted in the order it was declared. There is no
    # published schema to fall back on, so the scientist's own order is the only
    # statement about this sheet's shape that exists.
    if getattr(contract, "is_declared", False):
        return list(contract.column_order)

    identity = [c for c in contract.column_order if _COLUMN_TO_SAMPLE_FIELD.get(c) == "external_id"]
    leading = identity + [c for c in read_columns if c not in identity]
    rest = sorted(c for c in contract.column_order if c not in leading)
    return leading + rest


def _emitted_columns(contract, samples: list, parameters: dict, rows_by_sample: dict, sample_values=None) -> list[str]:
    """The columns this sheet will actually carry.

    Emits required columns, the chosen read columns, and any column bioAF fills
    for at least one sample. Unfilled optional columns are dropped rather than
    emitted empty: nf-schema treats an absent optional as absent, and a sheet of
    mostly-empty columns is both unreadable and, for a schema with exclusive
    input styles, invalid.

    When the schema declares mutually exclusive styles (ampliseq's legacy vs
    standardized columns), the branch bioAF can satisfy is chosen and the other
    style's columns are excluded, because that schema forbids mixing them.
    """
    # Every declared column is emitted, filled or not. Dropping an empty
    # optional column is right for a published schema, where nf-schema treats an
    # absent optional as absent; here bioAF knows nothing about the pipeline, so
    # removing a header the scientist wrote would be bioAF deciding the shape of
    # a sheet it does not understand.
    if getattr(contract, "is_declared", False):
        return list(contract.column_order)

    read_columns = set(_ordered_read_columns(contract))

    filled: set[str] = set()
    for column in contract.column_order:
        if column in read_columns:
            continue
        for sample in samples:
            reads = rows_by_sample.get(sample.id, [{}])[0]
            if _cell(contract, column, sample, parameters, reads, _supplied(sample_values, sample)):
                filled.add(column)
                break

    keep = set(contract.required) | filled

    # Read columns are normally emitted whether or not they resolved, because a
    # read-driven pipeline expects them. The exception is a row fed by an
    # ALTERNATIVE input: a BAM-only sarek row has no reads by design, and
    # carrying `fastq_1,fastq_2` as empty columns states something false about
    # it. A required read column stays regardless, since it is in `required`.
    # Read columns only. A row also carries what tells it apart from its sibling
    # (sarek's `lane`), and reading a filled lane as a resolved read would keep
    # `fastq_1,fastq_2` on a BAM-only row, which is the false statement the
    # exception below exists to avoid.
    reads_used = any(row.get(column) for rows in rows_by_sample.values() for row in rows for column in read_columns)
    if reads_used or not (filled & contract.non_read_file_columns):
        keep |= read_columns

    branch = contract.select_branch(keep)
    if branch is not None:
        keep = (keep | branch.required) - branch.forbidden

    return [c for c in _ordered_columns(contract, _ordered_read_columns(contract)) if c in keep]


# A read column for a sequencing technology bioAF does not register samples for.
# mag and detaxizer accept both short and long reads in one sheet; the Illumina
# FASTQs bioAF holds belong in the short-read columns, and putting them in
# `long_reads` would hand Nanopore/PacBio tooling Illumina data.
_LONG_READ_MARKER = re.compile(r"long|ont|hifi|nanopore|pacbio", re.IGNORECASE)


def _ordered_read_columns(contract) -> list[str]:
    """The mate-1 and mate-2 columns, in that order.

    Lexical sorting is not enough: mag defines `long_reads`, `short_reads_1` and
    `short_reads_2`, and sorting puts `long_reads` first, which would place R1
    into the long-read column. Columns are paired on their trailing `_1`/`_2`
    instead, and a complete short-read pair wins over anything else.

    A DECLARED sheet needs none of that guesswork: its binding says which mate
    each column holds, so a pipeline whose columns are called `R1` and `R2`, or
    anything else, is read correctly instead of by a name heuristic.
    """
    if getattr(contract, "is_declared", False):
        bindings = getattr(contract, "bindings", None) or {}
        mates = {
            binding.get("key"): column
            for column, binding in bindings.items()
            if binding.get("source") == "read"
        }
        return [mates[key] for key in ("1", "2") if key in mates]

    columns = contract.read_columns
    if not columns:
        return []

    # Group by base name: short_reads_1 / short_reads_2 -> "short_reads".
    pairs: dict[str, dict[str, str]] = {}
    singles: list[str] = []
    for column in columns:
        match = re.fullmatch(r"(.*?)[_]?([12])", column)
        if match and match.group(1):
            pairs.setdefault(match.group(1), {})[match.group(2)] = column
        else:
            singles.append(column)

    def rank(base: str) -> tuple[int, int, str]:
        mates = pairs[base]
        # complete pairs first, then short-read over long-read, then stable
        return (0 if len(mates) == 2 else 1, 1 if _LONG_READ_MARKER.search(base) else 0, base)

    for base in sorted(pairs, key=rank):
        if _LONG_READ_MARKER.search(base) and len(pairs) > 1:
            continue
        mates = pairs[base]
        return [mates[m] for m in ("1", "2") if m in mates]

    # Single-end schemas: one unpaired read column (nf-core/demo's `fastq`,
    # genomeqc's `fastq`). R2, if any, is simply never placed.
    return sorted(c for c in singles if not _LONG_READ_MARKER.search(c))[:1]


def _file_column_gap(contract, column: str, samples: list, sample_values=None) -> dict | None:
    """Why a required file column cannot be filled, or None when it can.

    Two distinct gaps, because they need opposite instructions. No matching file
    means "attach one"; several matching files means "say which", and telling a
    user to attach an assembly when two are already attached would send them the
    wrong way.

    Ambiguity is reported ahead of absence: if some samples are ambiguous the
    user has to resolve that regardless, and a run cannot proceed on a column
    where bioAF would otherwise be picking a file at random.
    """
    zero: list = []
    ambiguous: list = []
    candidates: set[str] = set()
    for sample in samples:
        # A file the scientist named settles both gaps at once: it is the answer
        # to "attach one" and to "say which", which is exactly how design section
        # 7 has them resolve an ambiguous column from the review step.
        if _supplied(sample_values, sample).get(column):
            continue
        matches = _files_for_column(contract, column, sample)
        if not matches:
            zero.append(sample)
        elif len(matches) > 1:
            ambiguous.append(sample)
            candidates.update(getattr(f, "filename", "") or f.storage_uri for f in matches)

    if ambiguous:
        return {
            "sample_field": None,
            "allowed_values": contract.enum_for(column),
            "reason": "ambiguous",
            "candidates": sorted(candidates),
            "samples": [{"id": s.id, "external_id": s.external_id} for s in ambiguous],
        }
    if zero:
        return {
            "sample_field": None,
            "allowed_values": contract.enum_for(column),
            "reason": "missing",
            "samples": [{"id": s.id, "external_id": s.external_id} for s in zero],
        }
    return None


def _to_csv(columns: list[str], rows: list[list[str]]) -> str:
    """A samplesheet, written the one way bioAF writes them."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def _sheet_rows(
    contract, samples: list, parameters: dict, sample_values=None
) -> tuple[list[str], list[dict], list[dict]]:
    """The columns and rows of a schema-driven sheet, each row naming its sample.

    One pass produces both the CSV handed to Nextflow and the table the review
    step renders, so the two cannot disagree about what is about to run.

    A row carries its ``sample_id`` because a wrongly-resolved cell is corrected
    in place from that table. A row identified only by position is the same
    hazard as a positional paste: the correction lands on the wrong sample and
    the run completes green.

    Also returns what the sheet LEAVES OUT: a value the pipeline's own
    vocabulary cannot express is dropped, and a dropped value is invisible in
    the result. Collected here, in the pass that builds the rows, rather than
    while choosing the header, because the header pass probes each column until
    one sample fills it and would report a drop the final sheet did not make.
    """
    read_columns = _ordered_read_columns(contract)

    # Resolve each sample's rows once: the header depends on what the rows turn
    # out to contain, and re-deriving them per column would rescan every
    # sample's files for every column.
    rows_by_sample: dict[int, list[dict[str, str]]] = {
        sample.id: _read_rows(contract, sample, parameters, read_columns) for sample in samples
    }
    columns = _emitted_columns(contract, samples, parameters, rows_by_sample, sample_values)

    rows: list[dict] = []
    for sample in samples:
        supplied = _supplied(sample_values, sample)
        for reads in rows_by_sample[sample.id]:
            rows.append(
                {
                    "sample_id": sample.id,
                    "external_id": sample.external_id,
                    "values": [_cell(contract, column, sample, parameters, reads, supplied) for column in columns],
                }
            )

    # Probed separately from the rows, over every column the schema constrains
    # with a vocabulary, INCLUDING the ones the header left out. A column left
    # out is precisely the case worth reporting: nothing could fill it, and one
    # reason nothing could is that the only value a sample had was a value this
    # pipeline cannot express.
    omissions: dict = {}
    for sample in samples:
        supplied = _supplied(sample_values, sample)
        sample_rows = rows_by_sample.get(sample.id) or [{}]
        for column in getattr(contract, "enums", {}):
            _cell(contract, column, sample, parameters, sample_rows[0], supplied, omissions)

    return columns, rows, list(omissions.values())


def _parsed_rows(csv_text: str, samples: list) -> tuple[list[str], list[dict]]:
    """A sheet a tailored generator produced, read back into columns and rows.

    chipseq pairs each IP sample with a detected control and fetchngs emits an
    accession list, so those sheets are built by hand rather than from a schema.
    They still get reviewed, which means reading back what the generator wrote.

    Rows are matched to samples on the name the generator itself emitted, which
    is the same function that produced the value, so the join cannot drift. A row
    naming something else keeps a null sample id rather than being guessed at.
    """
    parsed = list(csv.reader(io.StringIO(csv_text)))
    if not parsed:
        return [], []

    columns, body = parsed[0], parsed[1:]
    by_name = {_safe_sample_name(s): s for s in samples}
    name_at = columns.index("sample") if "sample" in columns else None

    rows: list[dict] = []
    for values in body:
        sample = by_name.get(values[name_at]) if name_at is not None and name_at < len(values) else None
        rows.append(
            {
                "sample_id": getattr(sample, "id", None),
                "external_id": getattr(sample, "external_id", None),
                "values": values,
            }
        )
    return columns, rows


def _dependency_gaps(contract, samples: list, parameters: dict, sample_values=None) -> dict[str, dict]:
    """Columns a filled trigger column made required, and the samples missing them.

    ``dependentRequired`` is the pipeline saying "if a row carries this, it must
    also carry that": mag's ``short_reads_1 -> short_reads_platform``, funcscan's
    ``protein -> gbk``. Nothing else in this module reads the keyword, and these
    columns are absent from ``required``, so bioAF emitted mag sheets with reads
    and no platform column and let nf-schema reject them after the launch.

    Evaluated per SAMPLE, because the requirement is a property of the row: a
    single-end sample leaves ``short_reads_2`` empty and owes nothing for it.

    The detail names the trigger, because the dependent column is optional in the
    schema's own ``required`` list and "short_reads_platform is missing" is
    unanswerable without knowing what made it necessary.
    """
    if not contract.dependent_required:
        return {}

    read_columns = _ordered_read_columns(contract)
    gaps: dict[str, dict] = {}
    for sample in samples:
        supplied = _supplied(sample_values, sample)
        reads = _read_rows(contract, sample, parameters, read_columns)[0]

        def filled(column: str, sample=sample, supplied=supplied, reads=reads) -> bool:
            return bool(_cell(contract, column, sample, parameters, reads, supplied))

        for trigger, dependents in contract.dependent_required.items():
            if trigger not in contract.columns or not filled(trigger):
                continue
            for dependent in dependents:
                if filled(dependent):
                    continue
                gap = gaps.setdefault(
                    dependent,
                    {
                        "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(dependent),
                        "allowed_values": contract.enum_for(dependent),
                        "reason": "required_by",
                        "required_by": trigger,
                        "samples": [],
                    },
                )
                gap["samples"].append({"id": sample.id, "external_id": sample.external_id})
    return gaps


def _supplied_value_gaps(contract, samples: list, parameters: dict, sample_values=None) -> dict[str, dict]:
    """Values a scientist stated that the pipeline will not take.

    A value bioAF SOURCED and cannot use may be dropped quietly when the column
    is optional: nobody asked for it. A value a scientist TYPED is different.
    They believe they set it, and silently discarding it means the run proceeds
    on a design other than the one they specified, which is the failure this
    project exists to remove. So it is reported whether or not the column is
    required.

    Reported with the offending value and the constraint that rejected it,
    because "ncbi is missing" is not a usable answer to "I entered an ncbi
    accession and it has a typo in it".
    """
    gaps: dict[str, dict] = {}
    read_columns = set(_ordered_read_columns(contract))
    for sample in samples:
        supplied = _supplied(sample_values, sample)
        if not supplied:
            continue
        reads = _read_rows(contract, sample, parameters, _ordered_read_columns(contract))[0]
        for column, stated in supplied.items():
            if column not in contract.columns or column in read_columns:
                continue
            if _cell(contract, column, sample, parameters, reads, supplied):
                continue
            gap = gaps.setdefault(
                column,
                {
                    "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(column),
                    "allowed_values": contract.enum_for(column),
                    "pattern": getattr(contract, "patterns", {}).get(column),
                    "reason": "not_accepted",
                    "samples": [],
                },
            )
            gap["samples"].append({"id": sample.id, "external_id": sample.external_id, "value": stated})
    return gaps


# Gaps where the value is PRESENT and the pipeline refuses it, as opposed to
# absent. The distinction decides the wording, and it matters: the sentence is
# what a scientist reads first, and telling someone their value is missing when
# it is sitting right there sends them to look for the wrong problem.
_VALUE_REASONS: frozenset[str] = frozenset({"invalid_characters", "collision", "not_accepted"})


def _blocked_summary(missing: dict[str, dict]) -> str:
    """One sentence saying why this run cannot start.

    Three cases, because a sentence that asserts the wrong one is worse than a
    vaguer one. Found by driving the demo: the detail read "will not accept these
    values" while the sentence above it said "which is missing for some samples".
    """
    columns = _join(sorted(missing))
    reasons = {gap.get("reason") for gap in missing.values()}

    # Nothing is missing and nothing was refused: the sheet carries rows the
    # pipeline cannot distinguish. Saying "missing" would send the scientist
    # looking for a value to supply for every sample, when what is needed is one
    # that differs between the repeated rows.
    if reasons == {"not_unique"}:
        return f"This pipeline needs {columns} to tell some rows apart, and they would repeat."

    if reasons <= _VALUE_REASONS:
        return f"This pipeline will not accept {columns} as set for some samples."
    if not (reasons & _VALUE_REASONS):
        return f"This pipeline requires {columns}, which is missing for some samples."
    return f"This pipeline cannot start: {columns} need attention for some samples."


def _identity_columns(contract) -> list[str]:
    """The columns carrying the sample's own name."""
    return [c for c in contract.column_order if _COLUMN_TO_SAMPLE_FIELD.get(c) == "external_id"]


def _pattern_gaps(contract, samples: list, parameters: dict, sample_values=None) -> dict[str, dict]:
    """Values the pipeline's own regex will not accept, each with a way forward.

    Every file column declares a pattern, and so do many metadata columns:
    ampliseq's ``sample`` is ``^[a-zA-Z][a-zA-Z0-9_]+$``, so the real demo name
    ``SAMPLE-101`` is rejected on the hyphen. bioAF never checked, so the sheet
    was emitted and Nextflow rejected it minutes later on a rule the scientist
    could not see.

    Reported rather than repaired. bioAF does not decide what a field says, so
    the block names the offending value, states the constraint, and offers a
    spelling that would satisfy it. Accepting that is the ordinary step 2 path,
    since a stated value overrides everything.

    A FILE column is checked but never given a recommendation: a tidier-looking
    path names a different file, and one that does not exist.
    """
    patterns = getattr(contract, "patterns", {})
    if not patterns:
        return {}

    read_columns = _ordered_read_columns(contract)
    gaps: dict[str, dict] = {}
    for sample in samples:
        supplied = _supplied(sample_values, sample)
        reads = _read_rows(contract, sample, parameters, read_columns)[0]
        for column, pattern in patterns.items():
            if column not in contract.columns:
                continue
            value = _cell(contract, column, sample, parameters, reads, supplied)
            if not value or _compiled_matches(pattern, value):
                continue
            gap = gaps.setdefault(
                column,
                {
                    "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(column),
                    "allowed_values": contract.enum_for(column),
                    "pattern": pattern,
                    "reason": "invalid_characters",
                    "samples": [],
                },
            )
            gap["samples"].append(
                {
                    "id": sample.id,
                    "external_id": sample.external_id,
                    "value": value,
                    "suggestion": None if column in contract.file_columns else _recommendation(value, pattern),
                }
            )
    return gaps


def _collision_gaps(
    contract, samples: list, parameters: dict, pattern_gaps: dict, sample_values=None
) -> dict[str, dict]:
    """Distinct samples that would end up sharing one name.

    Two routes to the same hazard. A recommendation can map two different samples
    onto one spelling (``SAMPLE-1`` and ``SAMPLE_1`` both want ``SAMPLE_1``), and
    a scientist can type the same clash by hand. Either way a sheet carrying that
    name twice merges two samples' results, and every downstream artifact keyed
    on the name inherits the merge.

    So the recommendation is withheld and the clash reported instead. Grouped by
    SAMPLE ID rather than by row, because a multi-lane sample legitimately
    repeats its own name across rows and owes nothing for it.
    """
    gaps: dict[str, dict] = {}
    for column in _identity_columns(contract):
        offered = {
            entry["id"]: entry["suggestion"]
            for entry in (pattern_gaps.get(column, {}).get("samples") or [])
            if entry.get("suggestion")
        }

        by_name: dict[str, dict[int, object]] = {}
        for sample in samples:
            emitted = offered.get(sample.id) or _cell(
                contract, column, sample, parameters, {}, _supplied(sample_values, sample)
            )
            if emitted:
                by_name.setdefault(emitted, {})[sample.id] = sample

        clashing = [held for held in by_name.values() if len(held) > 1]
        if not clashing:
            continue
        offenders = [s for held in clashing for s in held.values()]
        gaps[column] = {
            "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(column),
            "allowed_values": [],
            "pattern": getattr(contract, "patterns", {}).get(column),
            "reason": "collision",
            "samples": [{"id": s.id, "external_id": s.external_id, "suggestion": None} for s in offenders],
        }
    return gaps


def _incomplete_row_gaps(contract, samples: list, parameters: dict, sample_values=None) -> dict[str, dict]:
    """Rows bioAF would emit with a required column left empty.

    Required-column checking is otherwise per SAMPLE, and the sheet is per ROW.
    A sample carrying only an R2 emits a row whose ``fastq_1`` is empty and whose
    ``fastq_2`` holds the only path it has, with ``fastq_1`` required, and
    nothing caught it: ``column_gaps`` skips
    required read columns, ``_unusable_reads_gap`` fires only when NO attached
    file qualifies as a read (this one does), and the launch path's own gate
    fires only when a sample has no files at all. The row fell between all three
    and nf-schema rejected it after the node had scaled up.

    Checked against the rows the generator actually produces rather than against
    the sample, so a sample sequenced over two lanes is judged one lane at a
    time: the pair that resolved is not evidence for the pair that did not.

    Deliberately narrow, and it cannot refuse a launch that works today. Only
    columns the schema requires AND does not default are considered, because
    nf-schema fills a defaulted column itself when the cell is empty; and only
    columns this sheet actually emits, because a column absent from the header is
    already somebody else's report. Single-end input stays legal, since
    ``fastq_2`` is not required.

    A sample with NO input files at all is skipped, on the same principle
    ``_unusable_reads_gap`` states: that is a different situation, owned by the
    launch path's own gate, which can also be told to drop those samples and
    proceed. Duplicating it here would refuse launches that work today and would
    report the same problem twice under a reason that does not fit it.
    """
    required = getattr(contract, "required_without_default", set()) or set()
    if not required:
        return {}

    columns, rows, _omissions = _sheet_rows(contract, samples, parameters, sample_values)
    index = {column: position for position, column in enumerate(columns)}
    checkable = sorted(required & set(index))
    if not checkable:
        return {}

    by_sample = {s.id: s for s in samples}
    fileless = {s.id for s in samples if not _input_files(s)}
    gaps: dict[str, dict] = {}
    for row in rows:
        if row["sample_id"] in fileless:
            continue
        values = row["values"]
        for column in checkable:
            position = index[column]
            if position < len(values) and values[position]:
                continue
            gap = gaps.setdefault(
                column,
                {
                    "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(column),
                    "allowed_values": contract.enum_for(column),
                    "pattern": getattr(contract, "patterns", {}).get(column),
                    "reason": "empty_in_row",
                    "samples": [],
                },
            )
            # By sample rather than by row: a sample with two incomplete lanes
            # owes one answer, and naming it twice reads as two problems.
            if any(entry["id"] == row["sample_id"] for entry in gap["samples"]):
                continue
            sample = by_sample.get(row["sample_id"])
            gap["samples"].append(
                {
                    "id": row["sample_id"],
                    "external_id": getattr(sample, "external_id", None) or row.get("external_id"),
                    "suggestion": None,
                }
            )
    return gaps


def _uniqueness_gaps(contract, samples: list, parameters: dict, sample_values=None) -> dict[str, dict]:
    """Rows the pipeline would not be able to tell apart.

    A schema can declare that a column, on its own or paired with others, may not
    repeat. mag writes ``run: {unique: ["sample"]}``: two sequencing runs of one
    sample are distinguished by the run, so the run and sample pair is what must
    not repeat.

    bioAF emits one row per read pair, so a sample sequenced over two lanes
    produces two rows identical in exactly those columns. nf-schema rejects that
    sheet, and it does so after the node has scaled up and the containers have
    pulled, which is the cost this check exists to avoid.

    **bioAF does not fill the distinguishing value in.** A lane is not a
    sequencing run; writing one in would be a guess carrying a scientific claim.
    The block names the column the pipeline uses to tell the rows apart, and the
    scientist states it.
    """
    declared = dict(getattr(contract, "unique_with", {}))

    # The same rule stated from the sheet's side, which is how most of the
    # catalog spells it. Keyed on the column the scientist can actually act on:
    # for mag's ("sample", "run") that is `run`, because a row's sample name is
    # not something they may change to break a tie. Where every column in the
    # group is the sample's own name, as in ampliseq's ("sample",), the report
    # lands there, since the repetition itself is what has to be answered.
    for group in getattr(contract, "unique_entries", ()) or ():
        # Report against the column the scientist can actually answer with, which
        # is the one bioAF has no source for: sarek's ("lane", "patient",
        # "sample") is told apart by the LANE, and reporting `patient` would name
        # a column that is already filled and cannot break the tie. Where every
        # column is one bioAF fills, the group's own first column is named, since
        # the repetition itself is then what has to be answered.
        unsourceable = [
            c
            for c in group
            if c not in _COLUMN_TO_SAMPLE_FIELD
            and c not in _COLUMN_TO_PARAMETER
            and c not in getattr(contract, "file_columns", frozenset())
        ]
        anchor = unsourceable[0] if unsourceable else group[0]
        declared.setdefault(anchor, tuple(c for c in group if c != anchor))

    if not declared and not getattr(contract, "unique_rows", False):
        return {}

    columns, rows, _omissions = _sheet_rows(contract, samples, parameters, sample_values)
    if len(rows) < 2:
        return {}

    by_sample = {s.id: s for s in samples}
    index = {column: position for position, column in enumerate(columns)}
    gaps: dict[str, dict] = {}

    # ``uniqueItems`` on the array: whole rows must differ, with no column named.
    if getattr(contract, "unique_rows", False) and columns:
        declared.setdefault(columns[0], tuple(columns[1:]))

    for column, companions in declared.items():
        # A group whose columns are ALL absent from this sheet is not a rule
        # about it. ampliseq declares uniqueEntries for both of its mutually
        # exclusive input styles, so the style bioAF did not emit contributes an
        # empty value to every row, and reading that as a repetition would block
        # every ampliseq launch. At least one column present makes the check
        # meaningful: mag's ("sample", "run") is still checked with `run` absent,
        # because that absence is exactly how two lanes of one sample collide.
        if column not in index and not any(name in index for name in companions):
            continue

        keyed: dict[tuple, list[int]] = {}
        for row in rows:
            values = row["values"]

            def _at(name: str) -> str:
                position = index.get(name)
                return values[position] if position is not None and position < len(values) else ""

            key = (_at(column), *(_at(name) for name in companions))
            keyed.setdefault(key, []).append(row["sample_id"])

        offenders: list[int] = []
        for sample_ids in keyed.values():
            if len(sample_ids) > 1:
                offenders.extend(sid for sid in sample_ids if sid is not None)
        if not offenders:
            continue

        seen: list[int] = []
        for sid in offenders:
            if sid not in seen:
                seen.append(sid)
        gaps[column] = {
            "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(column),
            "allowed_values": contract.enum_for(column),
            "pattern": getattr(contract, "patterns", {}).get(column),
            "reason": "not_unique",
            "unique_with": list(companions),
            "samples": [
                {
                    "id": sid,
                    "external_id": getattr(by_sample.get(sid), "external_id", None),
                    "suggestion": None,
                }
                for sid in seen
            ],
        }
    return gaps


def _cell(
    contract,
    column: str,
    sample,
    parameters: dict,
    row: dict[str, str],
    supplied=None,
    omissions: dict | None = None,
) -> str:
    """The value for one column of one row, or empty when bioAF cannot source it.

    Order matters. A value the SCIENTIST stated wins over everything, because
    correcting a wrongly-resolved cell in place is the only backstop bioAF has
    against a file that matches a column's pattern without being the right file:
    a reference genome satisfies funcscan's ``fasta`` pattern exactly as well as
    an assembly does. After that comes what the ROW already resolved, which is
    its reads (the schema named them) and the facts of the sequencing unit it
    came from, then the explicit sample mapping, then launch parameters.

    An enum-constrained column only accepts a value the schema lists, and that
    applies to a stated value too. Emitting one the pipeline rejects would
    produce a sheet that passes bioAF's own checks and dies inside Nextflow,
    which is the failure this path exists to remove. Dropping it instead leaves
    the column empty, so ``check_contract_satisfiable`` blocks and names it.
    """
    value = (supplied or {}).get(column, "")

    if not value:
        if column in row:
            return row[column]

        # A per-sample file column (funcscan's assembly, sarek's bam) is resolved
        # from the sample's own files by the column's declared pattern. Exactly
        # one match fills it; two is ambiguity and stays empty, so the
        # satisfiability check reports it rather than this silently choosing.
        if column in getattr(contract, "non_read_file_columns", frozenset()):
            # Reads and their alternatives are exclusive per row: sarek takes
            # FASTQs OR an alignment, never both, and its schema declares no
            # `oneOf` that would catch the combination. So once reads resolved,
            # an OPTIONAL file column is an alternative input and filling it
            # produces a row the pipeline cannot act on. A REQUIRED file column
            # is not an alternative (funcscan wants its assembly regardless) and
            # is still filled.
            # Read columns only: a row's own distinguishing value (sarek's
            # `lane`) is not a read, and reading one as a resolved read would
            # empty a BAM column that has no reads to be an alternative to.
            # It is an inference about a PUBLISHED schema, so a declared sheet
            # is exempt: a scientist who declared a read column and a file
            # column asked for both, and emptying one would overrule them.
            if (
                not getattr(contract, "is_declared", False)
                and any(row.get(c) for c in getattr(contract, "read_columns", ()))
                and column not in contract.required
            ):
                return ""
            matches = _files_for_column(contract, column, sample)
            return matches[0].storage_uri if len(matches) == 1 else ""

        # A DECLARED sheet resolves only through its own bindings. bioAF's
        # automatic maps describe what a published schema's column names mean,
        # and a scientist naming a column `sample` in a sheet bioAF knows
        # nothing about has not thereby said it holds the sample's name. An
        # unbound column is a question for the grid, and silently answering it
        # here is what would make it look answered.
        if getattr(contract, "is_declared", False):
            binding = (getattr(contract, "bindings", None) or {}).get(column) or {}
            if binding.get("source") == "sample_field" and binding.get("key") == "external_id":
                value = _safe_sample_name(sample)
            else:
                value = _bound_value(contract, column, sample)
        else:
            field = _COLUMN_TO_SAMPLE_FIELD.get(column)
            if field == "external_id":
                value = _safe_sample_name(sample)
            elif field:
                value = getattr(sample, field, None) or ""
            else:
                parameter = _COLUMN_TO_PARAMETER.get(column)
                if parameter:
                    raw = parameters.get(parameter)
                    value = "" if raw is None else str(raw)

        if not value:
            return ""

    # A value outside the schema's enum would produce a sheet that passes bioAF's
    # own checks and dies in Nextflow. bioAF's "auto" strandedness default is
    # exactly this: legal for nf-core/rnaseq, absent from rnasplice's enum.
    allowed = contract.enum_for(column)
    if allowed and value not in allowed:
        logger.info(
            "Dropping %s=%r: not in this pipeline's allowed values %s",
            column,
            value,
            allowed,
        )
        # Recorded, not just logged. A dropped value is invisible in the sheet
        # itself: the column is simply absent, which reads as "this pipeline does
        # not ask for sex" rather than "your sample's sex could not be
        # expressed". One entry per sample and column, however many rows a
        # sample has, because it is one fact about the sample.
        if omissions is not None:
            omissions.setdefault(
                (column, getattr(sample, "id", None)),
                {
                    "column": column,
                    "sample_id": getattr(sample, "id", None),
                    "external_id": getattr(sample, "external_id", None),
                    "value": value,
                    "reason": "not_in_enum",
                    "allowed_values": list(allowed),
                },
            )
        return ""

    # A value that violates the column's declared pattern is NOT rewritten here.
    # bioAF does not decide what a field says: the user does, and a value quietly
    # respelled is one the scientist did not choose, leaving the sheet and the
    # LIMS disagreeing about what the sample is called. The violation is reported
    # by ``_pattern_gaps`` with a recommendation instead, and the launch blocks
    # until the scientist settles it. Emitting the value unchanged is what lets
    # the preview show them exactly what the pipeline objects to.
    return value


# -- Schema-driven generation ------------------------------------------------
#
# What a samplesheet column may be filled from, keyed on the column name the
# pipeline's own schema uses. Two disciplines apply, both carried over from the
# generic MultiQC engine:
#
# 1. EXACT, EXPLICIT MATCHING. A column is filled only if it appears here. There
#    is no reflection onto same-named Sample attributes, because a name collision
#    is not evidence of a shared meaning.
#
# 2. IDENTITY AND PROVENANCE ONLY. Every entry below names something bioAF
#    already knows about the sample itself. Columns that define EXPERIMENTAL
#    DESIGN are deliberately absent and must never be added: mag's `group`
#    controls co-assembly, rnasplice's `condition` defines the differential
#    contrast, and rnastructurome's `condition` is an rf-norm chemistry enum
#    (treated/untreated/denatured) that merely shares a name with
#    Sample.treatment_condition. Guessing any of them yields a scientifically
#    wrong result that still runs green, which is worse than a refused launch.
_COLUMN_TO_SAMPLE_FIELD: dict[str, str] = {
    # the sample's own name, spelled four ways across the catalog
    "sample": "external_id",
    "sample_id": "external_id",
    "id": "external_id",
    "ID": "external_id",
    "sample_name": "external_id",
    # which individual the material came from (sarek/rnadnavar: meta [patient])
    "patient": "donor_source",
    "subject_id": "donor_source",
    "donor": "donor_source",
    # organism and anatomy
    "species": "organism",
    "organism": "organism",
    "tissue": "tissue_type",
    # optional on the sample; a pipeline requiring it blocks when it is empty
    "sex": "sex",
    # provenance: bioAF carries one row per sample, so the sample's own external
    # id is its run accession
    "run_accession": "external_id",
}

# Pipelines whose sheet is built by a tailored generator, matched as substrings
# of the pipeline key exactly as generate_sheet routes them.
_HANDWRITTEN_GENERATORS: tuple[str, ...] = ("scrnaseq", "rnaseq", "chipseq", "atacseq", "fetchngs")

# Columns supplied by a launch parameter rather than by the sample: values that
# are constant for a whole run, so one answer applies to every row.
#
# Membership is a judgement about the COLUMN'S SEMANTICS and is deliberately
# conservative, because a per-sample column collected as one run-level value
# would relabel every row identically and still run. Two that look like they
# belong here and do not:
#   - riboseq's `type` (riboseq/rnaseq/tiseq): a run PAIRS ribosome-profiling
#     samples with matched RNA-seq samples, so one value destroys the pairing.
#   - sammyseq's `fraction`: different chromatin fractions of the SAME sample.
# Both stay blocked until per-sample collection exists.
_COLUMN_TO_PARAMETER: dict[str, str] = {
    # library prep property, already a launch parameter for nf-core/rnaseq today
    "strandedness": "strandedness",
    # the sequencer the run came off
    "instrument_platform": "instrument_platform",
    # hlatyping: whether the library is DNA or RNA
    "seq_type": "seq_type",
    # fastquorum: the UMI read layout, fixed by the library prep
    "read_structure": "read_structure",
    "expected_cells": "expected_cells",
}


class SampleSheetService:
    @staticmethod
    def required_user_inputs(contract) -> list[dict]:
        """Run-level samplesheet columns the user must answer for this pipeline.

        These are the columns that would otherwise block the launch and whose
        value is constant across the run, so a single field collects them. The
        allowed values come from the pipeline's own schema, so the options
        offered cannot drift from what it accepts.

        Returns nothing for a pipeline that cannot be launched from samples at
        all: collecting answers would imply a launch that is still impossible.
        """
        if contract.is_empty or not contract.is_sample_launchable:
            return []

        read_columns = set(_ordered_read_columns(contract))
        specs: list[dict] = []
        for column in contract.column_order:
            if column not in contract.required_without_default or column in read_columns:
                continue
            # Sourced from the sample itself, so there is nothing to ask.
            if column in _COLUMN_TO_SAMPLE_FIELD:
                continue
            if column not in _COLUMN_TO_PARAMETER:
                continue
            specs.append(
                {
                    "name": column,
                    "parameter": _COLUMN_TO_PARAMETER[column],
                    "required": True,
                    "allowed_values": contract.enum_for(column),
                }
            )
        return specs

    @staticmethod
    def sample_field_updates(contract, samples: list, sample_values=None) -> dict[int, dict[str, str]]:
        """Which stated values are facts about the sample, keyed by sample id.

        Design section 1 writes back only where a column already maps to a
        ``Sample`` field, so ``_COLUMN_TO_SAMPLE_FIELD`` is the allowlist rather
        than a second list that could drift from it. Three refusals qualify it,
        and each exists because the alternative degrades the record:

        - **A column the pipeline constrains never writes back** (section 9).
          Choosing from XX/XY/NA is answering sarek, not describing the sample,
          and letting it through would have the narrowest vocabulary in the
          catalog overwrite real biology one run at a time.
        - **A field that already holds a value is never overwritten.** The run
          gets what it needs; the record keeps what is true.
        - **The identity column never writes back.** It maps to ``external_id``
          so bioAF can FILL it. Writing it back would let a samplesheet rename
          the sample it came from, and every output already produced under the
          old name would stop matching it.

        Only values the SCIENTIST stated are considered. Everything else was
        read off the sample to begin with.
        """
        updates: dict[int, dict[str, str]] = {}
        for sample in samples:
            stated = _supplied(sample_values, sample)
            if not stated:
                continue
            for column, raw in stated.items():
                field = _COLUMN_TO_SAMPLE_FIELD.get(column)
                if not field or field == "external_id":
                    continue
                if contract is not None and contract.enum_for(column):
                    continue
                value = str(raw or "").strip()
                if not value or getattr(sample, field, None):
                    continue
                updates.setdefault(sample.id, {})[field] = value
        return updates

    @staticmethod
    def column_gaps(contract, samples: list, parameters: dict, sample_values=None) -> dict[str, dict]:
        """Every column that stops this run producing a valid samplesheet.

        The single computation behind both a refusal and a form. ``launch_run``
        renders it as a block, the entry grid renders it as the set of questions
        to ask, and they cannot disagree about which columns are outstanding.
        Two computations would drift, and a grid that omits a blocking column
        strands the user on a Launch button that never enables.

        Empty when nothing is outstanding. Empty too when the contract is empty,
        because no schema means "we do not know", not "nothing is required".
        """
        # A DECLARED contract is launchable by construction: the scientist said
        # what the sheet is, and bioAF has no schema with which to contradict
        # them. ``is_sample_launchable`` answers a question about a PUBLISHED
        # schema (does this pipeline take a per-sample file at all), and asking
        # it here would refuse a sheet of pure metadata that the pipeline may
        # well want.
        if contract.is_empty or not (contract.is_sample_launchable or getattr(contract, "is_declared", False)):
            return {}

        ordered_reads = _ordered_read_columns(contract)
        read_columns = set(ordered_reads)
        file_columns = contract.non_read_file_columns
        missing: dict[str, dict] = {}

        # Reads are resolved rather than read off the row, so a sample whose
        # files exist but do not satisfy the read pattern is reported here
        # instead of silently producing an empty read column.
        if ordered_reads:
            unusable = _unusable_reads_gap(contract, samples, parameters, ordered_reads, sample_values)
            if unusable:
                missing[ordered_reads[0]] = unusable

        for column in sorted(contract.required_without_default):
            if column in read_columns:
                continue

            if column in file_columns:
                detail = _file_column_gap(contract, column, samples, sample_values)
                if detail:
                    missing[column] = detail
                continue

            offenders = [
                s for s in samples if not _cell(contract, column, s, parameters, {}, _supplied(sample_values, s))
            ]
            if not offenders:
                continue

            missing[column] = {
                "sample_field": _COLUMN_TO_SAMPLE_FIELD.get(column),
                "allowed_values": contract.enum_for(column),
                "reason": "missing",
                "samples": [{"id": s.id, "external_id": s.external_id} for s in offenders],
            }

        # A column the schema requires only once another is filled. Reported
        # alongside the outright-missing ones rather than instead of them: both
        # have to be answered before this sheet is valid.
        for column, gap in _dependency_gaps(contract, samples, parameters, sample_values).items():
            missing.setdefault(column, gap)

        # A value the scientist stated that the pipeline will not take. This
        # REPLACES any "missing" entry for the same column: the column is indeed
        # empty, but telling someone who just typed a value that it is missing
        # sends them to look for the wrong problem.
        for column, gap in _supplied_value_gaps(contract, samples, parameters, sample_values).items():
            missing[column] = gap

        # Characters the pipeline's own regex will not accept. Reported even
        # though the column is filled, because the value is present and wrong
        # rather than absent, and each entry carries a spelling that would work.
        pattern_gaps = _pattern_gaps(contract, samples, parameters, sample_values)
        for column, gap in pattern_gaps.items():
            missing[column] = gap

        # Two different samples that would end up sharing one name. Overrides the
        # pattern gap above, because recommending a spelling that merges two
        # samples' results would be worse than the problem it solves.
        for column, gap in _collision_gaps(contract, samples, parameters, pattern_gaps, sample_values).items():
            missing[column] = gap

        # Rows the pipeline could not tell apart, under a uniqueness rule the
        # schema declares. Added rather than substituted: the column is usually
        # absent from the sheet entirely, so nothing else reports it.
        for column, gap in _uniqueness_gaps(contract, samples, parameters, sample_values).items():
            missing.setdefault(column, gap)

        # A row about to be emitted with a required column empty. Last, and by
        # setdefault, because every check above says something more specific
        # about the same column: this one knows only that the cell came out
        # blank. Where a narrower gap already claimed the column, the samples
        # this one would have named are reported on the next attempt, once the
        # narrower problem is answered. The launch is blocked either way, and a
        # report that named samples under someone else's reason would be wrong
        # about all of them.
        for column, gap in _incomplete_row_gaps(contract, samples, parameters, sample_values).items():
            missing.setdefault(column, gap)

        return missing

    @staticmethod
    def per_sample_inputs(contract, samples: list, parameters: dict, sample_values=None) -> list[dict]:
        """The columns an entry grid must collect, and how to render each one.

        Derived from ``column_gaps``, so the grid asks for exactly what the
        launch check blocks on. Ordered as the samplesheet orders its columns,
        because the grid and the review table are read one after the other.

        Two rules from the design decide the rendering:

        **A pipeline's enum constrains a pipeline PARAMETER and never a field
        recorded on the sample.** rnastructurome's ``condition`` is an rf-norm
        chemistry value, so its three legal values are the whole truth and belong
        in a closed list. raredisease's ``sex`` is a PED code, which is what that
        pipeline ingests and not a vocabulary for sex: XXY, X0, XYY, XXX and
        mosaics are all real, so constraining the sample's own field to a
        pipeline's enum would write a false biological model into the LIMS. The
        allowed values still travel, as information rather than as a fence.

        **bioAF explains a column only in the pipeline's own words.** An absent
        description stays absent.
        """
        gaps = SampleSheetService.column_gaps(contract, samples, parameters, sample_values)
        if not gaps:
            return []

        ordered = _ordered_columns(contract, _ordered_read_columns(contract))
        ordered += [c for c in sorted(gaps) if c not in ordered]

        specs: list[dict] = []
        for column in ordered:
            gap = gaps.get(column)
            if gap is None:
                continue
            sample_field = _COLUMN_TO_SAMPLE_FIELD.get(column)
            allowed = contract.enum_for(column)
            specs.append(
                {
                    "name": column,
                    "required": True,
                    "is_file": column in contract.file_columns,
                    "sample_field": sample_field,
                    "allowed_values": allowed,
                    "constrained": bool(allowed) and sample_field is None,
                    "description": contract.descriptions.get(column),
                    "format_hint": contract.error_messages.get(column),
                    "required_by": gap.get("required_by"),
                    "reason": gap.get("reason"),
                    "samples": gap.get("samples", []),
                }
            )
        return specs

    @staticmethod
    def check_contract_satisfiable(contract, samples: list, parameters: dict, sample_values=None) -> None:
        """Raise if this run cannot produce a valid samplesheet.

        Called before the run row exists, so a refusal costs the user nothing.
        Silent when the contract is empty: no schema means "we do not know", and
        refusing on ignorance would regress every pipeline that works today.

        ``sample_values`` carries what the scientist stated per sample, keyed by
        sample id. It must be the same set generation will use, or this reports a
        gap the sheet does not have and refuses a launch that would work.
        """
        if contract.is_empty:
            return

        if not contract.is_sample_launchable and not getattr(contract, "is_declared", False):
            wants = contract.required_non_fastq_inputs
            raise PipelineNotSampleLaunchableError(
                "This pipeline cannot be launched from samples, because it does not take a "
                f"per-sample file. It expects {_join(wants)} instead.",
                details={"required_inputs": wants},
            )

        missing = SampleSheetService.column_gaps(contract, samples, parameters, sample_values)

        if missing:
            raise SamplesMissingRequiredFieldsError(
                _blocked_summary(missing),
                details={"missing_columns": missing},
            )

    @staticmethod
    def generate_from_contract(contract, samples: list, parameters: dict, sample_values=None) -> str:
        """Build a samplesheet from a pipeline's own ``schema_input.json``.

        The header carries the required columns, the read columns, and whatever
        else bioAF can actually fill. Unfilled optional columns are dropped, and
        where a schema declares mutually exclusive input styles only the chosen
        one is emitted.

        ``sample_values`` carries what the scientist stated per sample, keyed by
        sample id. A column the pipeline never declared is ignored rather than
        appended: a mapping carried over from another pipeline names columns this
        one does not have, and an undeclared column fails nf-schema's validation
        of the whole sheet.

        Whether the resulting sheet is actually launchable is decided upstream in
        ``PipelineRunService``: this builds the best sheet it can and reports
        nothing, so that the blocking decision lives in one place.
        """
        columns, rows, _omissions = _sheet_rows(contract, samples, parameters, sample_values)
        return _to_csv(columns, [row["values"] for row in rows])

    @staticmethod
    def preview(pipeline_key: str, samples: list, parameters: dict, contract=None, sample_values=None) -> dict:
        """The sheet this launch would submit, produced without launching it.

        Returns the columns, the rows (each naming the sample it belongs to) and
        the exact CSV, so the review step can render a table by default and show
        the raw file behind a button. Both come from the generator that feeds
        Nextflow rather than from a second code path, because a preview that can
        differ from the submitted sheet is worse than no preview.

        Shown on every launch, so it covers the pipelines a schema does not
        describe as well: a tailored generator's sheet is read back into rows,
        and fetchngs' accession list is carried as its own text, since rendering
        that as a table would invent a structure it does not have.
        """
        if "fetchngs" in pipeline_key:
            return {
                "columns": [],
                "rows": [],
                "csv": SampleSheetService.generate_fetchngs_ids(parameters),
                "omissions": [],
            }

        schema_driven = (
            not SampleSheetService.has_handwritten_generator(pipeline_key)
            and contract is not None
            and not contract.is_empty
        )
        if schema_driven:
            columns, rows, omissions = _sheet_rows(contract, samples, parameters, sample_values)
            return {
                "columns": columns,
                "rows": rows,
                "csv": _to_csv(columns, [r["values"] for r in rows]),
                "omissions": omissions,
            }

        csv_text = SampleSheetService.generate_sheet(
            pipeline_key, samples, parameters, contract=contract, sample_values=sample_values
        )
        columns, rows = _parsed_rows(csv_text, samples)
        # Nothing is claimed about a sheet bioAF did not build from a contract:
        # a tailored generator decides its own columns and bioAF cannot say what
        # it chose to leave out.
        return {"columns": columns, "rows": rows, "csv": csv_text, "omissions": []}

    @staticmethod
    def generate_scrnaseq_sheet(samples: list, parameters: dict) -> str:
        """Generate nf-core/scrnaseq sample sheet CSV.

        If samples don't have linked files, falls back to input_paths in parameters.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2", "expected_cells"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            expected_cells = parameters.get("expected_cells", 10000)
            if paths:
                fastq_1 = paths[0] if len(paths) > 0 else ""
                fastq_2 = paths[1] if len(paths) > 1 else ""
                writer.writerow([sample_name, fastq_1, fastq_2, expected_cells])
            else:
                for fastq_1, fastq_2 in _extract_fastq_lane_pairs(sample):
                    writer.writerow([sample_name, fastq_1, fastq_2, expected_cells])

        return output.getvalue()

    @staticmethod
    def generate_rnaseq_sheet(samples: list, parameters: dict) -> str:
        """Generate nf-core/rnaseq sample sheet CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2", "strandedness"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            strandedness = parameters.get("strandedness", "auto")
            if paths:
                fastq_1 = paths[0] if len(paths) > 0 else ""
                fastq_2 = paths[1] if len(paths) > 1 else ""
                writer.writerow([sample_name, fastq_1, fastq_2, strandedness])
            else:
                for fastq_1, fastq_2 in _extract_fastq_lane_pairs(sample):
                    writer.writerow([sample_name, fastq_1, fastq_2, strandedness])

        return output.getvalue()

    @staticmethod
    def generate_chipseq_sheet(samples: list, parameters: dict) -> str:
        """Generate an nf-core/chipseq sample sheet CSV (lit_validation Phase 4).

        Columns: sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate. Only sample +
        fastq_1 are mandatory; antibody requires control (schema dependency). Control/input samples
        carry empty antibody/control; IP samples get an antibody label and point ``control`` at the
        detected control sample (so MACS2 subtracts input and peaks/FRiP are computed).

        Control detection is best-effort from each sample's metadata (external_id + prep_notes, which
        carry the ENA/GEO title for a fetched sample). If no control can be identified, IP samples are
        emitted WITHOUT antibody/control -- still schema-valid and the run completes (alignment + QC),
        but those samples are not peak-called. This degrade is logged, never a launch failure.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2", "replicate", "antibody", "control", "control_replicate"])

        input_paths = parameters.get("input_paths", {})

        controls = [s for s in samples if _is_chip_control(s)]
        control_name = _safe_sample_name(controls[0]) if controls else ""
        if not controls:
            logger.info(
                "chipseq sheet: no control/input sample identified among %d samples; IP samples will be "
                "emitted without antibody/control (no peak calling for them)",
                len(samples),
            )

        def _rows_for(sample):
            paths = input_paths.get(str(sample.id), [])
            if paths:
                return [(paths[0] if len(paths) > 0 else "", paths[1] if len(paths) > 1 else "")]
            return _extract_fastq_lane_pairs(sample)

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            is_control = _is_chip_control(sample)
            # An IP sample gets an antibody only if there is a control to reference (schema: antibody
            # requires control). Control samples, and IP samples with no available control, go bare.
            if is_control or not control_name:
                antibody, control, control_replicate = "", "", ""
            else:
                antibody, control, control_replicate = _antibody_label(sample), control_name, "1"
            for fastq_1, fastq_2 in _rows_for(sample):
                writer.writerow([sample_name, fastq_1, fastq_2, "1", antibody, control, control_replicate])

        return output.getvalue()

    @staticmethod
    def generate_atacseq_sheet(samples: list, parameters: dict) -> str:
        """Generate an nf-core/atacseq sample sheet CSV (lit_validation Phase 4).

        Columns: sample,fastq_1,fastq_2,replicate. ATAC-seq has no antibody/immunoprecipitation, so
        (unlike chipseq) there is no antibody/control -- but ``replicate`` is required by the schema.
        One replicate per sample (=1); a downstream user can merge biological replicates by editing it.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2", "replicate"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            if paths:
                rows = [(paths[0] if len(paths) > 0 else "", paths[1] if len(paths) > 1 else "")]
            else:
                rows = _extract_fastq_lane_pairs(sample)
            for fastq_1, fastq_2 in rows:
                writer.writerow([sample_name, fastq_1, fastq_2, "1"])

        return output.getvalue()

    @staticmethod
    def generate_generic_sheet(samples: list, parameters: dict) -> str:
        """Generic fallback CSV sample sheet."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            if paths:
                fastq_1 = paths[0] if len(paths) > 0 else ""
                fastq_2 = paths[1] if len(paths) > 1 else ""
                writer.writerow([sample_name, fastq_1, fastq_2])
            else:
                for fastq_1, fastq_2 in _extract_fastq_lane_pairs(sample):
                    writer.writerow([sample_name, fastq_1, fastq_2])

        return output.getvalue()

    @staticmethod
    def generate_fetchngs_ids(parameters: dict) -> str:
        """Build nf-core/fetchngs's --input ids file: one database accession per line, no header.

        fetchngs pulls FASTQ + metadata from these accessions itself (no per-sample files). bioAF
        carries them in parameters["accessions"] (a list, or a comma/space/newline-separated string);
        the run path feeds this file in via --input.
        """
        raw = parameters.get("accessions") or []
        if isinstance(raw, str):
            raw = re.split(r"[,\s]+", raw)
        ids = [str(a).strip() for a in raw if str(a).strip()]
        return "\n".join(ids) + ("\n" if ids else "")

    @staticmethod
    def has_handwritten_generator(pipeline_key: str) -> bool:
        """Whether a tailored generator owns this pipeline.

        These build sheets a schema cannot describe: chipseq pairs each IP sample
        with a detected control and labels its antibody, and fetchngs emits an
        accession list rather than a samplesheet at all. They are exempt from
        schema-driven generation and from its blocking checks.
        """
        return any(k in pipeline_key for k in _HANDWRITTEN_GENERATORS)

    @staticmethod
    def generate_sheet(pipeline_key: str, samples: list, parameters: dict, contract=None, sample_values=None) -> str:
        """Route to the correct sheet generator based on pipeline type.

        The four tailored generators and fetchngs keep priority. Everything else
        is built from the pipeline's own schema when one is available, and falls
        back to the fixed generic sheet when it is not.
        """
        if "scrnaseq" in pipeline_key:
            return SampleSheetService.generate_scrnaseq_sheet(samples, parameters)
        elif "rnaseq" in pipeline_key:
            return SampleSheetService.generate_rnaseq_sheet(samples, parameters)
        elif "chipseq" in pipeline_key:
            return SampleSheetService.generate_chipseq_sheet(samples, parameters)
        elif "atacseq" in pipeline_key:
            return SampleSheetService.generate_atacseq_sheet(samples, parameters)
        elif "fetchngs" in pipeline_key:
            return SampleSheetService.generate_fetchngs_ids(parameters)
        elif contract is not None and not contract.is_empty:
            return SampleSheetService.generate_from_contract(contract, samples, parameters, sample_values)
        else:
            return SampleSheetService.generate_generic_sheet(samples, parameters)
