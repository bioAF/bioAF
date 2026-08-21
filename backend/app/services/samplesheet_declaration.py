"""A samplesheet contract a SCIENTIST wrote, for a pipeline that publishes none.

Seventeen pipelines in the catalog ship no ``schema_input.json``. bioAF answered
that with a fixed ``sample,fastq_1,fastq_2`` header, and
``generate_generic_sheet(samples, parameters)`` took neither a mapping nor stated
values, so the same request with and without them produced a byte-identical
sheet. Nothing a scientist could say reached the file.

Decision 1 of 2026-08-19 closes that: they declare the columns in the same
``{"fields": [{name, type, required}]}`` shape the experiment field editor
already uses, and each column carries a BINDING saying where its value comes
from. The result is parsed into the same ``SamplesheetContract`` a published
schema produces, so the rest of the pipeline (rows, gaps, blocks, the entry grid,
the review preview) works on it unchanged rather than through a second path that
could disagree with the first.

**What a declaration is NOT.** It is not a claim about what the pipeline
requires. bioAF has no schema here and cannot check one, so ``required`` means
"this scientist says a row without it is wrong", and that is the only authority
the block appeals to.

**Declaring nothing keeps today's behaviour.** No declaration means the generic
sheet, unchanged, because no schema means "we do not know" and never a refusal.
"""

from app.services.samplesheet_schema import SamplesheetContract

# Where a declared column's value may come from.
#
#   read          the row's mate-1 or mate-2 FASTQ, grouped by sequencing unit
#   sample_field  a field on the Sample
#   file_type     one of the sample's files, chosen by File.file_type
#   custom_field  one of the sample's custom fields, by name
#   literal       a constant, the same in every row
#
# A column with NO binding is asked per sample in the entry grid. That is not an
# omission in the vocabulary: a co-assembly grouping or a differential contrast
# is a design decision no binding can derive, and inventing one is precisely what
# this project exists to stop.
BINDING_SOURCES = ("read", "sample_field", "file_type", "custom_field", "literal")

# Sample fields a binding may read. An allowlist rather than reflection onto any
# attribute, for the same reason ``_COLUMN_TO_SAMPLE_FIELD`` is one: a name
# collision is not evidence of a shared meaning, and open reflection would let a
# samplesheet column read an internal id or an organisation key.
BINDABLE_SAMPLE_FIELDS = (
    "external_id",
    "donor_source",
    "organism",
    "tissue_type",
    "treatment_condition",
    "chemistry_version",
    "sample_batch_code",
    "sequencing_batch_code",
    "molecule_type",
    "library_prep_method",
    "library_layout",
    "assay",
    "sex",
)

# What the editor opens with, and what a pipeline with no declaration still gets:
# today's generic sheet, expressed in this vocabulary. A scientist who opens the
# editor and saves it untouched must get the file they got before, or the editor
# is a way to break a working launch.
DEFAULT_DECLARATION = {
    "fields": [
        {
            "name": "sample",
            "type": "string",
            "required": True,
            "binding": {"source": "sample_field", "key": "external_id"},
        },
        {"name": "fastq_1", "type": "file", "required": False, "binding": {"source": "read", "key": "1"}},
        {"name": "fastq_2", "type": "file", "required": False, "binding": {"source": "read", "key": "2"}},
    ]
}


def _binding(field: dict, column: str) -> dict | None:
    """One column's binding, refused rather than ignored when it is not one.

    A source bioAF does not implement must not degrade into "ask the scientist":
    the column would look answerable in the grid and never resolve, and the
    launch would block on a question with no answer.
    """
    raw = field.get("binding")
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "").strip()
    if not source:
        return None
    if source not in BINDING_SOURCES:
        raise ValueError(f"Column {column!r} declares an unknown binding source {source!r}")

    key = str(raw.get("key") or "").strip()
    if source == "sample_field" and key not in BINDABLE_SAMPLE_FIELDS:
        raise ValueError(f"Column {column!r} cannot be bound to sample field {key!r}")
    if source == "read" and key not in ("1", "2"):
        raise ValueError(f"Column {column!r} declares read mate {key!r}, which is neither 1 nor 2")
    if source in ("file_type", "custom_field", "literal") and not key:
        raise ValueError(f"Column {column!r} binds to {source} without saying which")
    return {"source": source, "key": key}


def parse_declaration(declaration: object) -> SamplesheetContract:
    """The contract a scientist's column declaration amounts to.

    Empty when there is nothing declared, which every caller already treats as
    "we do not know" and falls back on. A malformed declaration RAISES rather
    than parsing to empty: silently discarding it would launch the old fixed
    sheet while the scientist believed their columns were in use.
    """
    fields = (declaration or {}).get("fields") if isinstance(declaration, dict) else None
    if not isinstance(fields, list) or not fields:
        return SamplesheetContract(is_empty=True)

    order: list[str] = []
    required: set[str] = set()
    read_columns: set[str] = set()
    file_columns: set[str] = set()
    bindings: dict[str, dict] = {}

    for field in fields:
        if not isinstance(field, dict):
            continue
        column = str(field.get("name") or "").strip()
        if not column:
            continue
        if column in bindings or column in order:
            raise ValueError(f"Column {column!r} is declared twice")
        order.append(column)

        binding = _binding(field, column)
        if binding:
            bindings[column] = binding
            if binding["source"] == "read":
                read_columns.add(column)
                file_columns.add(column)
            elif binding["source"] == "file_type":
                file_columns.add(column)
        if field.get("required"):
            required.add(column)

    if not order:
        return SamplesheetContract(is_empty=True)

    return SamplesheetContract(
        required=required,
        columns=set(order),
        read_columns=read_columns,
        file_columns=file_columns,
        column_order=tuple(order),
        bindings=bindings,
        is_declared=True,
    )
