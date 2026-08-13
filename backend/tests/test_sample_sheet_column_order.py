"""Header order does not depend on how the schema was stored.

The generator originally emitted columns in the schema's own declared order, on
the reasoning that a sheet someone opens to debug a run should look like the
pipeline's documented example.

That reasoning does not survive storage. Catalog schemas live in a JSONB column,
and PostgreSQL jsonb normalises object key order (shortest key first, then
bytewise). Sarek's real stored schema comes back
``bai, bam, sex, vcf, crai, cram, lane, table, sample, ...`` against a file order
of ``patient, sample, sex, status, lane, ...``. So the declared order held in
tests reading the fixture file and never held in production.

The order is now chosen rather than inherited: identity, then reads, then the
rest alphabetically. nf-schema reads by header name, so this is legibility, not
correctness; the point is that it is the SAME everywhere.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _jsonb_shuffled(name: str):
    """The same schema as PostgreSQL jsonb hands it back: keys reordered by
    length then bytewise. This is what a real installed pipeline looks like."""
    doc = json.loads((FIXTURES / f"{name}.json").read_text())
    props = doc["items"]["properties"]
    doc["items"]["properties"] = {k: props[k] for k in sorted(props, key=lambda k: (len(k), k))}
    return parse_contract(doc)


def _make_sample(sample_id: int, external_id: str, **fields):
    s = MagicMock()
    s.id = sample_id
    s.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(s, attr, fields.get(attr))
    s._input_files = []
    return s


def _header(csv_text: str) -> list[str]:
    return csv_text.strip().splitlines()[0].split(",")


def _paths():
    return {"input_paths": {"1": ["/data/A_R1.fastq.gz", "/data/A_R2.fastq.gz"]}}


def test_order_is_identical_however_the_schema_was_stored():
    """The property that actually matters: a pipeline's sheet does not change
    shape depending on whether the schema came from a file or from JSONB."""
    sample = _make_sample(1, "S1", donor_source="D1")

    from_file = SampleSheetService.generate_from_contract(_contract("sarek"), [sample], _paths())
    from_storage = SampleSheetService.generate_from_contract(_jsonb_shuffled("sarek"), [sample], _paths())

    assert _header(from_file) == _header(from_storage)


def test_identity_leads_then_reads_then_the_rest():
    csv_text = SampleSheetService.generate_from_contract(
        _jsonb_shuffled("sarek"), [_make_sample(1, "S1", donor_source="D1")], _paths()
    )

    assert _header(csv_text) == ["sample", "fastq_1", "fastq_2", "patient"]


def test_a_pipeline_with_its_own_read_column_names_orders_the_same_way():
    csv_text = SampleSheetService.generate_from_contract(
        _jsonb_shuffled("mag"), [_make_sample(1, "S1", treatment_condition="t")], _paths()
    )
    header = _header(csv_text)

    assert header[0] == "sample"
    assert header[1:3] == ["short_reads_1", "short_reads_2"]


def test_the_simple_case_is_unchanged():
    csv_text = SampleSheetService.generate_from_contract(_jsonb_shuffled("demo"), [_make_sample(1, "S1")], _paths())

    assert _header(csv_text) == ["sample", "fastq_1", "fastq_2"]


def test_remaining_columns_are_alphabetical_so_the_order_is_predictable():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sample", "fastq_1", "zebra", "alpha", "middle"],
            "properties": {
                "zebra": {"type": "string"},
                "sample": {"type": "string"},
                "alpha": {"type": "string"},
                "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"},
                "middle": {"type": "string"},
            },
        },
    }
    csv_text = SampleSheetService.generate_from_contract(parse_contract(schema), [_make_sample(1, "S1")], _paths())

    assert _header(csv_text) == ["sample", "fastq_1", "alpha", "middle", "zebra"]
