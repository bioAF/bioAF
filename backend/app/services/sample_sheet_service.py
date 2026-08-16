import csv
import io
import logging
import re
from functools import lru_cache

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


_ILLUMINA_READ_RE = re.compile(r"_(R[12]|I[12])_")

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


def _get_read_type(f) -> str | None:
    """Return read type (R1, R2, I1, I2) from tags_json or filename pattern."""
    tags = getattr(f, "tags_json", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("read:"):
            return tag.split(":", 1)[1]
    # Fallback to filename pattern
    filename = getattr(f, "filename", "") or ""
    m = _ILLUMINA_READ_RE.search(filename)
    if m:
        return m.group(1)
    return None


def _get_lane(f) -> str:
    """Return lane identifier from tags_json or filename, default '000'."""
    tags = getattr(f, "tags_json", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("lane:"):
            return tag.split(":", 1)[1]
    filename = getattr(f, "filename", "") or ""
    m = re.search(r"_L(\d{3})_", filename)
    if m:
        return m.group(1)
    return "000"


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
    """One dict of read values per lane for a sample.

    Shared by generation and the satisfiability check so the check reports what
    generation will actually produce, rather than assuming reads resolve.
    """
    if not read_columns:
        return [{}]
    paths = parameters.get("input_paths", {}).get(str(sample.id), [])
    if paths:
        pairs = [(paths[0] if len(paths) > 0 else "", paths[1] if len(paths) > 1 else "")]
    else:
        accepted, _ = _eligible_reads(contract, sample, read_columns)
        pairs = _extract_fastq_lane_pairs(sample, files=accepted)
    return [dict(zip(read_columns, pair)) for pair in pairs]


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
    """Extract (fastq_1, fastq_2) pairs grouped by lane.

    Excludes index reads (I1, I2). Uses tags_json read type when available,
    falls back to Illumina filename convention (_R1_/_R2_).
    Returns one tuple per lane, sorted by lane number.

    ``files`` lets the schema-driven path pass only the files that satisfy the
    read column's own pattern. Without it the unclassified fallback below will
    place ANY attached file into fastq_1, so a sample carrying only an alignment
    would hand a BAM to a read column.
    """
    fastq_files = _input_files(sample) if files is None else [f for f in files if getattr(f, "storage_uri", None)]
    if not fastq_files:
        return [("", "")]

    # Classify each file by read type
    lanes: dict[str, dict[str, str]] = {}
    unclassified = []
    for f in fastq_files:
        read_type = _get_read_type(f)
        if read_type and read_type.startswith("I"):
            continue  # Skip index reads
        if read_type in ("R1", "R2"):
            lane = _get_lane(f)
            lanes.setdefault(lane, {})
            lanes[lane][read_type] = f.storage_uri
        else:
            unclassified.append(f)

    if lanes:
        result = []
        for lane_key in sorted(lanes):
            r1 = lanes[lane_key].get("R1", "")
            r2 = lanes[lane_key].get("R2", "")
            result.append((r1, r2))
        return result

    # Fallback for files without read type info: sort by filename
    unclassified.sort(key=lambda f: getattr(f, "filename", "") or getattr(f, "storage_uri", ""))
    fastq_1 = unclassified[0].storage_uri if len(unclassified) > 0 else ""
    fastq_2 = unclassified[1].storage_uri if len(unclassified) > 1 else ""
    return [(fastq_1, fastq_2)]


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
    reads_used = any(value for rows in rows_by_sample.values() for row in rows for value in row.values())
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
    """
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


def _cell(contract, column: str, sample, parameters: dict, reads: dict[str, str], supplied=None) -> str:
    """The value for one column of one row, or empty when bioAF cannot source it.

    Order matters. A value the SCIENTIST stated wins over everything, because
    correcting a wrongly-resolved cell in place is the only backstop bioAF has
    against a file that matches a column's pattern without being the right file:
    a reference genome satisfies funcscan's ``fasta`` pattern exactly as well as
    an assembly does. After that come reads (the schema named them), then the
    explicit sample mapping, then launch parameters.

    An enum-constrained column only accepts a value the schema lists, and that
    applies to a stated value too. Emitting one the pipeline rejects would
    produce a sheet that passes bioAF's own checks and dies inside Nextflow,
    which is the failure this path exists to remove. Dropping it instead leaves
    the column empty, so ``check_contract_satisfiable`` blocks and names it.
    """
    value = (supplied or {}).get(column, "")

    if not value:
        if column in reads:
            return reads[column]

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
            if any(reads.values()) and column not in contract.required:
                return ""
            matches = _files_for_column(contract, column, sample)
            return matches[0].storage_uri if len(matches) == 1 else ""

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
        return ""

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
        if contract.is_empty or not contract.is_sample_launchable:
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

        if not contract.is_sample_launchable:
            wants = contract.required_non_fastq_inputs
            raise PipelineNotSampleLaunchableError(
                "This pipeline cannot be launched from samples, because it does not take a "
                f"per-sample file. It expects {_join(wants)} instead.",
                details={"required_inputs": wants},
            )

        missing = SampleSheetService.column_gaps(contract, samples, parameters, sample_values)

        if missing:
            raise SamplesMissingRequiredFieldsError(
                f"This pipeline requires {_join(sorted(missing))}, which is missing for some samples.",
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
        read_columns = _ordered_read_columns(contract)

        # Resolve each sample's rows once: the header depends on what the rows
        # turn out to contain, and re-deriving them per column would rescan
        # every sample's files for every column.
        rows_by_sample: dict[int, list[dict[str, str]]] = {
            sample.id: _read_rows(contract, sample, parameters, read_columns) for sample in samples
        }

        columns = _emitted_columns(contract, samples, parameters, rows_by_sample, sample_values)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)

        for sample in samples:
            supplied = _supplied(sample_values, sample)
            for reads in rows_by_sample[sample.id]:
                writer.writerow([_cell(contract, column, sample, parameters, reads, supplied) for column in columns])

        return output.getvalue()

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
    def generate_sheet(pipeline_key: str, samples: list, parameters: dict, contract=None) -> str:
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
            return SampleSheetService.generate_from_contract(contract, samples, parameters)
        else:
            return SampleSheetService.generate_generic_sheet(samples, parameters)
