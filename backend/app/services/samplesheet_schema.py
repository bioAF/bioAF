"""Parse an nf-core ``assets/schema_input.json`` into a samplesheet contract.

Samplesheet generation used to be keyed on a substring of the pipeline name
(``"rnaseq" in pipeline_key``) with a fixed ``sample,fastq_1,fastq_2`` fallback
for everything else. Measured across the catalog, that produced a sheet missing
at least one required column for 52% of active nf-core pipelines, which fails
inside Nextflow minutes after launch rather than in bioAF.

Every nf-core pipeline publishes its exact samplesheet contract as a JSON Schema
alongside the workflow. Reading it is the same move the generic QC engine made:
key on the tool's own machine-readable contract, not on a hand-written per-
pipeline guess.

This module only READS a schema. Deciding what to put in each column, and when
to refuse, lives in ``sample_sheet_service`` and ``pipeline_run_service``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("bioaf.samplesheet_schema")

# How a read column is recognized, in priority order.
#
# 1. THE COLUMN'S OWN PATTERN. nf-core schemas declare the accepted file
#    extension, e.g. mag's short_reads_1 carries ``^\S+\.f(ast)?q\.gz$``. This is
#    the pipeline stating the fact machine-readably, so it is authoritative and
#    name-independent. Measured across the catalog it proves 73 of 124 pipelines
#    sample-launchable on its own, including every pipeline that names its read
#    columns something other than fastq_N (mag, ampliseq, genomeassembler,
#    detaxizer).
#
#    The pattern is a REGEX, so it must be stripped of metacharacters before
#    being searched for an extension: ``^\S+\.f(ast)?q\.gz$`` contains no literal
#    "fastq" until ``(``, ``)`` and ``?`` are removed.
_REGEX_META = re.compile(r"[()\[\]?\\*+|{}]")
_FASTQ_EXTENSION = re.compile(r"\.(fastq|fq)")

# 2. NAME FALLBACK, for the 3 schemas that declare no pattern at all. Kept
#    deliberately tiny and explicit rather than heuristic: a column called
#    "reads" (isoseq, PacBio) or "fastq_dir" (sopa, a directory) is NOT known to
#    hold per-sample FASTQ, so those stay unlaunchable rather than being guessed
#    into a sheet that cannot work.
FASTQ_COLUMNS: frozenset[str] = frozenset({"fastq_1", "fastq_2", "fastq", "R1", "R2"})

# Stored in place of a schema for a pipeline that ships none, so the lazy
# launch-time fetch does not re-request a known 404 on every launch. Distinct
# from NULL, which means "not fetched yet", and from a transient fetch failure,
# which records nothing so the next launch retries.
SCHEMA_ABSENT: dict = {"__bioaf_schema_input__": "absent"}


def is_absent_marker(schema: object) -> bool:
    """Whether ``schema`` records that the pipeline publishes no contract."""
    return isinstance(schema, dict) and schema.get("__bioaf_schema_input__") == "absent"


# The sample's own name, spelled four ways across the catalog. Excluded from the
# "what does this pipeline actually want" refusal message, because naming the
# sample id back at the user explains nothing about why the launch was refused.
_IDENTITY_COLUMNS: frozenset[str] = frozenset({"sample", "sample_id", "id", "ID"})


def _declares_fastq(spec: object) -> bool:
    """Whether a column's own schema says it holds a FASTQ file."""
    if not isinstance(spec, dict):
        return False
    pattern = spec.get("pattern")
    if not isinstance(pattern, str):
        return False
    return bool(_FASTQ_EXTENSION.search(_REGEX_META.sub("", pattern).lower()))


def _enum_text(value: object) -> str | None:
    """One legal value, in the form a samplesheet row will carry it.

    A CSV cell is text, and every value bioAF compares against an enum is already
    a string, so a schema's ``0`` has to become ``"0"`` or it can never match.
    Filtering to strings instead is what made raredisease's ``phenotype``
    (``[0, 1, 2]``) and sarek's ``status`` (``[0, 1]``) look unconstrained.

    ``null`` has no cell representation and is dropped; booleans take the JSON
    spelling rather than Python's, since that is what a pipeline reads back.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _enum_values(spec: dict) -> list[str]:
    """Every value a column accepts, including vocabularies split across branches.

    A column may declare its values directly, or as one ``oneOf``/``anyOf`` branch
    per type: raredisease's ``sex`` is integer ``0/1/2`` OR string ``other``, and
    reading only the top level saw no constraint at all.

    A branch that names no values means anything goes for that branch, so the
    whole column is unconstrained. Building a fence out of the branches that
    happen to list values would reject inputs the pipeline accepts, and an empty
    enum has always meant "anything goes" here rather than "nothing is allowed".
    """
    declared = spec.get("enum")
    if isinstance(declared, list) and declared:
        return [text for text in (_enum_text(v) for v in declared) if text is not None]

    for keyword in ("oneOf", "anyOf"):
        branches = spec.get(keyword)
        if not isinstance(branches, list) or not branches:
            continue
        collected: list[str] = []
        for branch in branches:
            values = branch.get("enum") if isinstance(branch, dict) else None
            if not isinstance(values, list) or not values:
                collected = []
                break
            collected.extend(text for text in (_enum_text(v) for v in values) if text is not None)
        if collected:
            return collected
    return []


def _declares_file(spec: object) -> bool:
    """Whether a column holds a per-sample FILE of any kind, not just a read.

    ``format: "file-path"`` is the nf-core convention and is what every non-read
    file column in the catalog declares (funcscan's ``fasta``, sarek's
    ``bam``/``cram``/``vcf``, genomeqc's ``gff``). It is the pipeline stating the
    fact machine-readably, so it is preferred over inspecting the pattern.

    The FASTQ pattern check is kept as a second route because a handful of
    schemas (rnasplice) declare a read column with a pattern and no ``format``.

    A column with neither is NOT treated as a file. That is the safe direction:
    it keeps today's behavior rather than inventing a file bioAF cannot identify.
    """
    if not isinstance(spec, dict):
        return False
    return spec.get("format") == "file-path" or _declares_fastq(spec)


@dataclass(frozen=True)
class ExclusiveBranch:
    """One of a schema's mutually exclusive input styles.

    nf-core/ampliseq accepts either the legacy ``sampleID``/``forwardReads``
    columns or the standardized ``sample``/``fastq_1`` ones, and its schema
    forbids mixing them. Emitting every declared column emits both at once,
    which that rule rejects.

    ``forbidden`` comes from the branch's ``not.anyOf[].required``: the columns
    that must be ABSENT for this branch to hold.
    """

    required: frozenset[str] = frozenset()
    forbidden: frozenset[str] = frozenset()


def _parse_branches(items: dict) -> tuple[ExclusiveBranch, ...]:
    """Read mutually exclusive input styles out of a row schema.

    Only branches carrying a ``not`` are collected: a branch that forbids
    nothing cannot make a sheet invalid, so it needs no choice. Measured across
    the catalog, exactly one schema (ampliseq) does this, but the shape is read
    from the schema rather than special-cased by pipeline name.
    """
    branches: list[ExclusiveBranch] = []
    for keyword in ("oneOf", "anyOf"):
        for raw in items.get(keyword) or []:
            if not isinstance(raw, dict) or "not" not in raw:
                continue
            required = {str(c) for c in (raw.get("required") or []) if isinstance(c, str)}
            forbidden: set[str] = set()
            negated = raw.get("not")
            if isinstance(negated, dict):
                for clause in negated.get("anyOf") or []:
                    if isinstance(clause, dict):
                        forbidden |= {str(c) for c in (clause.get("required") or []) if isinstance(c, str)}
                forbidden |= {str(c) for c in (negated.get("required") or []) if isinstance(c, str)}
            if required or forbidden:
                branches.append(ExclusiveBranch(frozenset(required), frozenset(forbidden)))
    return tuple(branches)


@dataclass(frozen=True)
class SamplesheetContract:
    """What a pipeline's samplesheet must look like.

    ``is_empty`` means the schema was absent or in a layout we do not recognize.
    That is deliberately distinct from "the pipeline requires nothing": callers
    must fall back to prior behavior rather than conclude anything from it.
    """

    required: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)
    defaulted: set[str] = field(default_factory=set)
    enums: dict[str, list[str]] = field(default_factory=dict)
    read_columns: set[str] = field(default_factory=set)
    # Every column holding a per-sample file, reads included. A pipeline whose
    # input is an assembly, an alignment or a variant set declares these and no
    # read column; bioAF stores arbitrary files per sample, so such a pipeline is
    # launchable whenever the samples carry what it asks for.
    file_columns: set[str] = field(default_factory=set)
    # What the pipeline says a column is FOR, in its own words. Most nf-core
    # schemas describe nothing (0 of the 10 captured fixtures describe a column
    # like mag's `group`), and an absent description stays absent: bioAF writing
    # its own explanation would be the hand-written per-pipeline knowledge the
    # schema-driven work removed, and it would go stale without anyone noticing.
    descriptions: dict[str, str] = field(default_factory=dict)
    # The schema's own ``errorMessage``, which is a FORMAT hint rather than a
    # meaning ("Group needs to be string or integer with no spaces!"). Worth
    # surfacing before a value is typed, since it is otherwise only seen as a
    # Nextflow failure after the launch.
    error_messages: dict[str, str] = field(default_factory=dict)
    # Each column's declared regex, used to decide which of a sample's files
    # belongs in it. Matching on the schema's own pattern is what nf-schema will
    # do, so it needs no extension list of bioAF's own to drift out of date.
    patterns: dict[str, str] = field(default_factory=dict)
    # The property order as parsed. NOT a reliable stand-in for the order the
    # pipeline documents: a schema read back from the catalog's JSONB column has
    # been normalised by PostgreSQL (shortest key first, then bytewise), so this
    # matches the published file only when the schema came straight from one.
    # The emitted header order is chosen explicitly in sample_sheet_service
    # rather than inherited from here.
    column_order: tuple[str, ...] = ()
    # Mutually exclusive input styles, when the schema declares any.
    branches: tuple[ExclusiveBranch, ...] = ()
    # Columns a row must carry ONCE ANOTHER COLUMN IS FILLED, keyed on the column
    # that triggers the requirement. mag declares
    # ``{"short_reads_1": ["short_reads_platform"]}``: a row with short reads must
    # say which platform produced them. These columns are absent from
    # ``required``, so nothing else in this module sees them, and a sheet missing
    # one is rejected by nf-schema after the launch rather than by bioAF before
    # it. The rule is per ROW: a sample whose trigger column is empty owes
    # nothing.
    dependent_required: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Columns whose value must be unique, keyed on the column, holding the OTHER
    # columns it is unique WITHIN. mag declares ``run: {unique: ["sample"]}``: two
    # sequencing runs of one sample are told apart by the run, so the run and
    # sample pair may not repeat. An empty tuple means the column alone must be
    # unique across the sheet.
    #
    # bioAF emits one row per read pair, so a sample sequenced over two lanes
    # produces two rows that are identical in exactly these columns, and
    # nf-schema rejects that sheet after the node has scaled up.
    unique_with: dict[str, tuple[str, ...]] = field(default_factory=dict)
    is_empty: bool = False

    def select_branch(self, sourceable: set[str]) -> ExclusiveBranch | None:
        """The exclusive input style bioAF should commit to, if any.

        Picks the first branch whose required columns bioAF can all supply, so
        the choice follows what the platform actually holds rather than schema
        order alone. None when the schema declares no branches, or when none is
        satisfiable (in which case the caller emits what it can and the
        satisfiability check reports the gap).
        """
        for branch in self.branches:
            if branch.required <= sourceable:
                return branch
        return None

    @property
    def is_sample_launchable(self) -> bool:
        """Whether this pipeline consumes a per-sample FILE of any kind.

        This used to mean "declares a FASTQ column", which refused every pipeline
        whose input is an assembly, an alignment or a variant set. bioAF holds
        arbitrary files per sample, so that refused runs the platform could
        actually feed: funcscan wants an assembly, and a sample carrying one can
        launch it.

        False only when the pipeline asks for no per-sample file at all (a bare
        taxid, an accession). No amount of attaching files changes that, so the
        honest response is still to refuse.

        Whether THESE samples can satisfy the columns is a separate question,
        answered per sample in ``check_contract_satisfiable`` so the user is told
        which column and which samples, rather than being turned away.
        """
        return bool(self.read_columns or self.file_columns)

    @property
    def required_without_default(self) -> set[str]:
        """Required columns nf-schema will NOT fill in for us.

        A required column carrying a ``default`` is supplied by nf-schema, so its
        absence from bioAF's output is not a launch blocker.
        """
        return self.required - self.defaulted

    @property
    def required_non_fastq_inputs(self) -> list[str]:
        """The inputs this pipeline requires, for the refusal message.

        Only meaningful when the pipeline is not sample-launchable; it is what
        tells the user what the pipeline actually wants.
        """
        if self.is_sample_launchable:
            return []
        return sorted(self.required - _IDENTITY_COLUMNS)

    @property
    def non_read_file_columns(self) -> set[str]:
        """File columns resolved from the sample's own files rather than reads.

        Reads are paired and lane-grouped by the read path, so they are excluded
        here even though they are file columns too.
        """
        return self.file_columns - self.read_columns

    def enum_for(self, column: str) -> list[str]:
        """Legal values for ``column``, or empty when it is unconstrained.

        Empty means "anything goes", never "nothing is allowed". Callers must
        not treat an empty list as a rejection.
        """
        return self.enums.get(column, [])


_EMPTY = SamplesheetContract(is_empty=True)


def parse_contract(schema: object) -> SamplesheetContract:
    """Read a parsed ``schema_input.json`` into a contract.

    Never raises. A schema that is absent, malformed, or in an unrecognized
    layout yields an empty contract, because a transient GitHub failure or a
    novel schema layout must not block a launch that works today.
    """
    if not isinstance(schema, dict):
        return _EMPTY

    items = schema.get("items")
    if not isinstance(items, dict):
        return _EMPTY

    properties = items.get("properties")
    if not isinstance(properties, dict) or not properties:
        return _EMPTY

    required_raw = items.get("required")
    required = {str(c) for c in required_raw} if isinstance(required_raw, list) else set()

    defaulted: set[str] = set()
    enums: dict[str, list[str]] = {}
    read_columns: set[str] = set()
    file_columns: set[str] = set()
    patterns: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    error_messages: dict[str, str] = {}
    unique_with: dict[str, tuple[str, ...]] = {}
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        col = str(name)
        if "default" in spec:
            defaulted.add(col)
        described = spec.get("description")
        if isinstance(described, str) and described.strip():
            descriptions[col] = described.strip()
        hint = spec.get("errorMessage")
        if isinstance(hint, str) and hint.strip():
            error_messages[col] = hint.strip()
        allowed = _enum_values(spec)
        if allowed:
            enums[col] = allowed
        pattern = spec.get("pattern")
        if isinstance(pattern, str) and pattern:
            patterns[col] = pattern
        # ``unique: false`` states that a column is NOT constrained, which bacass
        # writes on its ID. Reading it as a constraint would block every bacass
        # launch, so only ``true`` and a list of companion columns count.
        declared_unique = spec.get("unique")
        if declared_unique is True:
            unique_with[col] = ()
        elif isinstance(declared_unique, list):
            unique_with[col] = tuple(str(u) for u in declared_unique if isinstance(u, str))
        if _declares_fastq(spec) or col in FASTQ_COLUMNS:
            read_columns.add(col)
        if _declares_file(spec):
            file_columns.add(col)

    dependent_required: dict[str, tuple[str, ...]] = {}
    raw_dependent = items.get("dependentRequired")
    if isinstance(raw_dependent, dict):
        for trigger, dependents in raw_dependent.items():
            if not isinstance(dependents, list):
                continue
            names = tuple(str(d) for d in dependents if isinstance(d, str))
            if names:
                dependent_required[str(trigger)] = names

    declared = [str(c) for c in properties]
    columns = set(declared)
    # A schema that requires a column it never defines is self-inconsistent, but
    # it is the pipeline's own published contract, so honor the requirement. Such
    # a column has no declared position, so it goes last.
    undeclared = sorted(required - columns)
    return SamplesheetContract(
        required=required,
        columns=columns | required,
        defaulted=defaulted,
        enums=enums,
        read_columns=read_columns,
        file_columns=file_columns,
        descriptions=descriptions,
        error_messages=error_messages,
        patterns=patterns,
        column_order=tuple(declared + undeclared),
        branches=_parse_branches(items),
        dependent_required=dependent_required,
        unique_with=unique_with,
        is_empty=False,
    )


__all__ = [
    "FASTQ_COLUMNS",
    "SCHEMA_ABSENT",
    "SamplesheetContract",
    "is_absent_marker",
    "parse_contract",
]
