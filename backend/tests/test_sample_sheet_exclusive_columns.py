"""Emitting only the columns a pipeline actually wants.

The first version of the generator emitted EVERY column a schema declared,
empty when bioAF could not source it, on the reasoning that nf-schema reads by
header name so a stable header is harmless.

Driving the real UI showed that is wrong. nf-core/ampliseq accepts two mutually
exclusive input styles and its schema forbids mixing them:

    "When using legacy format (sampleID/forwardReads), do not use standardized
     fields (sample/fastq_1/fastq_2)"

Emitting every column emits both styles at once, which that rule rejects. So the
generator now emits required columns, the chosen read columns, and columns it
actually fills, and it honors a schema's exclusive branches when choosing.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


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


def _paths(sample_id: int = 1):
    return {"input_paths": {str(sample_id): ["/data/A_R1.fastq.gz", "/data/A_R2.fastq.gz"]}}


# -- The ampliseq case: two exclusive input styles --


def test_ampliseq_emits_only_one_input_style():
    """The defect this file exists for: both styles in one sheet is invalid."""
    csv_text = SampleSheetService.generate_from_contract(_contract("ampliseq"), [_make_sample(1, "S1")], _paths())
    header = _header(csv_text)

    assert "sample" in header
    assert "fastq_1" in header
    for legacy in ("sampleID", "forwardReads", "reverseReads"):
        assert legacy not in header, f"{legacy} belongs to the legacy style and must not appear beside fastq_1"


def test_ampliseq_picks_the_style_bioaf_can_actually_fill():
    """Both branches are satisfiable in principle; bioAF fills the standardized
    one, so that is the branch to commit to."""
    csv_text = SampleSheetService.generate_from_contract(_contract("ampliseq"), [_make_sample(1, "S1")], _paths())
    header = _header(csv_text)
    row = csv_text.strip().splitlines()[1].split(",")

    assert row[header.index("sample")] == "S1"
    assert row[header.index("fastq_1")] == "/data/A_R1.fastq.gz"


# -- Unfilled optional columns are dropped --


def test_unfilled_optional_columns_are_not_emitted():
    """sarek declares bam, cram, vcf and spring as alternative inputs. bioAF
    fills none of them, and seventeen mostly-empty columns is not a samplesheet
    anyone can read."""
    csv_text = SampleSheetService.generate_from_contract(
        _contract("sarek"), [_make_sample(1, "S1", donor_source="D1")], _paths()
    )
    header = _header(csv_text)

    for unfilled in ("bam", "cram", "vcf", "crai", "bai", "spring_1", "spring_2", "table", "contamination"):
        assert unfilled not in header


def test_required_and_filled_columns_survive():
    csv_text = SampleSheetService.generate_from_contract(
        _contract("sarek"), [_make_sample(1, "S1", donor_source="D1", sex="XX")], _paths()
    )
    header = _header(csv_text)

    assert "patient" in header  # required
    assert "sample" in header  # required
    assert "fastq_1" in header  # read column
    assert "sex" in header  # filled from Sample.sex


def test_a_sample_value_outside_the_schemas_enum_is_not_emitted():
    """sarek's sex enum is XX/XY/NA. A free-text Sample.sex of "female" is not a
    legal value there, so it is dropped rather than written into a sheet the
    pipeline would reject. The column then carries nothing and disappears with
    the other unfilled optionals."""
    csv_text = SampleSheetService.generate_from_contract(
        _contract("sarek"), [_make_sample(1, "S1", donor_source="D1", sex="female")], _paths()
    )

    assert "sex" not in _header(csv_text)


def test_a_required_column_is_emitted_even_when_it_cannot_be_sourced():
    """The sheet stays honest: a required column is never silently dropped just
    because bioAF has no value. The launch is blocked separately."""
    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [_make_sample(1, "S1")], _paths())

    assert "patient" in _header(csv_text)


def test_a_column_filled_for_only_some_samples_is_still_emitted():
    """The header is one shape for the whole sheet, so a column any row needs
    must appear for every row."""
    samples = [
        _make_sample(1, "A", donor_source="D1", sex="XX"),
        _make_sample(2, "B", donor_source="D2"),
    ]
    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})
    header = _header(csv_text)
    rows = [r.split(",") for r in csv_text.strip().splitlines()[1:]]

    assert "sex" in header
    assert rows[0][header.index("sex")] == "XX"
    assert rows[1][header.index("sex")] == ""


# -- Schemas with no exclusivity are unaffected --


def test_a_schema_without_branches_is_unaffected():
    csv_text = SampleSheetService.generate_from_contract(_contract("demo"), [_make_sample(1, "S1")], _paths())

    assert _header(csv_text) == ["sample", "fastq_1", "fastq_2"]


def test_mag_still_emits_its_own_read_column_names():
    csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [_make_sample(1, "S1")], _paths())
    header = _header(csv_text)

    assert "short_reads_1" in header
    assert "fastq_1" not in header
