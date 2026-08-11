"""Schema-driven samplesheet generation, against real nf-core schemas.

The fallback generator used to emit a fixed sample,fastq_1,fastq_2 header for
every pipeline without a hand-written generator. It now builds the header from
the pipeline's own schema and fills each column from the Sample the run was
launched against.

Two rules carry the weight, both inherited from the generic QC engine:
  - Fill only what the schema corroborates. A wrong value is worse than none.
  - Never fill a column that defines experimental design. A guessed co-assembly
    group or differential contrast produces a scientifically wrong result that
    still runs green.

These assert the CSV a pipeline would receive, not how it was assembled.
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
    """A Sample with no metadata unless the test gives it some."""
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "library_layout"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = []
    return sample


def _rows(csv_text: str) -> list[list[str]]:
    return [line.split(",") for line in csv_text.strip().splitlines()]


def _header(csv_text: str) -> list[str]:
    return _rows(csv_text)[0]


def _col(csv_text: str, column: str, row: int = 1) -> str:
    rows = _rows(csv_text)
    return rows[row][rows[0].index(column)]


# -- Class A1: identity and provenance columns are filled from Sample fields --


def test_sarek_sheet_carries_patient_from_donor_source():
    """The headline unblock. sarek requires `patient` (meta: [patient]); bioAF
    holds exactly that as Sample.donor_source. Today's generic sheet omits the
    column entirely and sarek aborts on it."""
    samples = [_make_sample(1, "TUMOR_A", donor_source="DONOR_7")]
    parameters = {"input_paths": {"1": ["/data/A_R1.fastq.gz", "/data/A_R2.fastq.gz"]}}

    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, parameters)

    assert "patient" in _header(csv_text)
    assert _col(csv_text, "patient") == "DONOR_7"
    assert _col(csv_text, "sample") == "TUMOR_A"
    assert _col(csv_text, "fastq_1") == "/data/A_R1.fastq.gz"
    assert _col(csv_text, "fastq_2") == "/data/A_R2.fastq.gz"


def test_bacass_sheet_fills_its_ID_column_from_external_id():
    """bacass spells the sample column `ID` (meta: [sample]). The same concept
    under a third spelling must still resolve."""
    samples = [_make_sample(1, "ISOLATE_3")]
    csv_text = SampleSheetService.generate_from_contract(_contract("bacass"), samples, {})

    assert _col(csv_text, "ID") == "ISOLATE_3"


def test_genomeqc_sheet_fills_species_from_organism():
    samples = [_make_sample(1, "S1", organism="Homo sapiens")]
    csv_text = SampleSheetService.generate_from_contract(_contract("genomeqc"), samples, {})

    assert _col(csv_text, "species") == "Homo sapiens"


# -- Enum discipline --


def test_enum_constrained_column_accepts_a_legal_value():
    samples = [_make_sample(1, "S1")]
    parameters = {"instrument_platform": "ILLUMINA"}

    csv_text = SampleSheetService.generate_from_contract(_contract("taxprofiler"), samples, parameters)

    assert _col(csv_text, "instrument_platform") == "ILLUMINA"


def test_enum_constrained_column_rejects_an_illegal_value():
    """A value outside the enum is not written. Emitting it would produce a sheet
    that passes bioAF's own check and dies in Nextflow, which is the exact
    failure this change exists to remove."""
    samples = [_make_sample(1, "S1")]
    parameters = {"instrument_platform": "MY_SEQUENCER_9000"}

    csv_text = SampleSheetService.generate_from_contract(_contract("taxprofiler"), samples, parameters)

    assert _col(csv_text, "instrument_platform") == ""


def test_bioafs_auto_strandedness_default_is_not_written_into_a_schema_that_forbids_it():
    """bioAF defaults strandedness to 'auto', legal for nf-core/rnaseq and absent
    from rnasplice's enum (forward/reverse/unstranded). Reusing the default
    blindly is how a 'valid' sheet still fails."""
    samples = [_make_sample(1, "S1")]

    csv_text = SampleSheetService.generate_from_contract(_contract("rnasplice"), samples, {"strandedness": "auto"})

    assert _col(csv_text, "strandedness") == ""


def test_a_legal_strandedness_is_written():
    samples = [_make_sample(1, "S1")]
    csv_text = SampleSheetService.generate_from_contract(_contract("rnasplice"), samples, {"strandedness": "reverse"})

    assert _col(csv_text, "strandedness") == "reverse"


# -- Class A2: experimental-design columns are NEVER guessed --


def test_mag_group_is_not_filled_from_treatment_condition():
    """mag's `group` controls CO-ASSEMBLY: samples sharing a group are assembled
    together. Filling it from treatment_condition silently decides the assembly
    design and still runs green."""
    samples = [_make_sample(1, "S1", treatment_condition="drug_treated")]

    csv_text = SampleSheetService.generate_from_contract(_contract("mag"), samples, {})

    assert _col(csv_text, "group") == ""


def test_rnastructurome_condition_is_not_filled_from_treatment_condition():
    """Sharpest case: rnastructurome's `condition` is an enum of
    treated/untreated/denatured, an rf-norm chemistry concept, not a general
    treatment condition. The names collide; the meanings do not."""
    samples = [_make_sample(1, "S1", treatment_condition="treated")]

    csv_text = SampleSheetService.generate_from_contract(_contract("rnastructurome"), samples, {})

    assert _col(csv_text, "condition") == ""


def test_an_unmapped_column_is_never_filled_from_a_same_named_sample_attribute():
    """No attribute-name reflection. A column is filled only via the explicit
    alias table, so a Sample field that happens to share a name is not enough."""
    sample = _make_sample(1, "S1")
    sample.status = "SHOULD_NOT_APPEAR"

    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [sample], {})

    assert _col(csv_text, "status") == ""


# -- Structural behavior --


def test_header_is_the_schemas_own_columns():
    csv_text = SampleSheetService.generate_from_contract(_contract("demo"), [_make_sample(1, "S1")], {})

    assert _header(csv_text) == ["sample", "fastq_1", "fastq_2"]


def test_optional_column_with_no_source_is_emitted_empty_not_omitted():
    """Dropping the column would change the header shape between runs of the
    same pipeline; nf-schema reads by header name, so an empty cell is correct."""
    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [_make_sample(1, "S1")], {})

    assert "sex" in _header(csv_text)
    assert _col(csv_text, "sex") == ""


def test_numeric_external_id_is_still_prefixed():
    """Preserves _safe_sample_name: nf-schema infers a CSV column's type from its
    values, so a purely numeric name is typed integer and rejected."""
    csv_text = SampleSheetService.generate_from_contract(_contract("demo"), [_make_sample(1, "12345")], {})

    assert _col(csv_text, "sample") == "sample_12345"


def test_multi_lane_samples_emit_one_row_per_lane():
    """Preserves _extract_fastq_lane_pairs through the new path: lanes are
    separate rows, and index reads are excluded."""
    sample = _make_sample(1, "S1")
    sample._input_files = [
        _file("S1_L001_R1_001.fastq.gz", "gs://b/S1_L001_R1.fastq.gz"),
        _file("S1_L001_R2_001.fastq.gz", "gs://b/S1_L001_R2.fastq.gz"),
        _file("S1_L002_R1_001.fastq.gz", "gs://b/S1_L002_R1.fastq.gz"),
        _file("S1_L002_R2_001.fastq.gz", "gs://b/S1_L002_R2.fastq.gz"),
        _file("S1_L001_I1_001.fastq.gz", "gs://b/S1_L001_I1.fastq.gz"),
    ]

    csv_text = SampleSheetService.generate_from_contract(_contract("demo"), [sample], {})

    rows = _rows(csv_text)[1:]
    assert len(rows) == 2
    assert [r[1] for r in rows] == ["gs://b/S1_L001_R1.fastq.gz", "gs://b/S1_L002_R1.fastq.gz"]
    assert all("I1" not in cell for row in rows for cell in row)


def _file(filename: str, storage_uri: str):
    f = MagicMock()
    f.filename = filename
    f.storage_uri = storage_uri
    f.tags_json = []
    return f


def test_read_columns_are_filled_under_the_schemas_own_names():
    """mag calls them short_reads_1/short_reads_2, so the FASTQ paths must land
    there, not in fastq_1/fastq_2 columns mag does not define."""
    sample = _make_sample(1, "S1")
    parameters = {"input_paths": {"1": ["/data/A_R1.fastq.gz", "/data/A_R2.fastq.gz"]}}

    csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], parameters)

    assert "fastq_1" not in _header(csv_text)
    assert _col(csv_text, "short_reads_1") == "/data/A_R1.fastq.gz"
    assert _col(csv_text, "short_reads_2") == "/data/A_R2.fastq.gz"


def test_several_samples_each_get_a_row():
    samples = [
        _make_sample(1, "A", donor_source="D1"),
        _make_sample(2, "B", donor_source="D2"),
    ]
    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})

    assert [_col(csv_text, "patient", row=r) for r in (1, 2)] == ["D1", "D2"]
