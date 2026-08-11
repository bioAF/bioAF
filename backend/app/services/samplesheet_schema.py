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
    # The schema's own declared property order. This is the order the pipeline's
    # documentation and example sheets use, so a generated sheet someone opens to
    # debug a run looks like the one they are comparing it against. nf-schema
    # reads by header name, so this is legibility, not correctness.
    column_order: tuple[str, ...] = ()
    is_empty: bool = False

    @property
    def is_sample_launchable(self) -> bool:
        """Whether this pipeline consumes per-sample FASTQ reads.

        False for pipelines whose primary input is an assembly, a variant set, an
        alignment, an image bundle or spectra. Those cannot be launched from
        bioAF samples at all, so the honest response is to refuse with an
        explanation rather than emit a FASTQ sheet Nextflow will reject.
        """
        return bool(self.read_columns)

    @property
    def required_without_default(self) -> set[str]:
        """Required columns nf-schema will NOT fill in for us.

        A required column carrying a ``default`` is supplied by nf-schema, so its
        absence from bioAF's output is not a launch blocker.
        """
        return self.required - self.defaulted

    @property
    def required_non_fastq_inputs(self) -> list[str]:
        """The non-read inputs this pipeline requires, for the refusal message.

        Only meaningful when the pipeline is not sample-launchable; it is what
        tells the user what the pipeline actually wants.
        """
        if self.is_sample_launchable:
            return []
        return sorted(self.required - _IDENTITY_COLUMNS)

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
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        col = str(name)
        if "default" in spec:
            defaulted.add(col)
        allowed = spec.get("enum")
        if isinstance(allowed, list) and allowed:
            enums[col] = [v for v in allowed if isinstance(v, str)]
        if _declares_fastq(spec) or col in FASTQ_COLUMNS:
            read_columns.add(col)

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
        column_order=tuple(declared + undeclared),
        is_empty=False,
    )


__all__ = [
    "FASTQ_COLUMNS",
    "SCHEMA_ABSENT",
    "SamplesheetContract",
    "is_absent_marker",
    "parse_contract",
]
