"""Sample-name string-safety in the samplesheet generators.

nf-core/nf-schema infers a CSV column's type from its values, so a purely numeric
sample name (e.g. external_id '1') is typed as integer and rejected against the
schema's string 'sample' field. The generators must emit string-safe names.
"""

from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService, _safe_sample_name


def _sample(sample_id, external_id):
    s = MagicMock()
    s.id = sample_id
    s.external_id = external_id
    return s


def test_numeric_names_are_prefixed():
    assert _safe_sample_name(_sample(7, "1")) == "sample_1"
    assert _safe_sample_name(_sample(7, "2")) == "sample_2"
    assert _safe_sample_name(_sample(7, "007")) == "sample_007"
    assert _safe_sample_name(_sample(7, "1.5")) == "sample_1.5"


def test_string_names_pass_through():
    assert _safe_sample_name(_sample(7, "SAMPLE_A")) == "SAMPLE_A"
    assert _safe_sample_name(_sample(7, "T1-rep2")) == "T1-rep2"


def test_empty_external_id_falls_back_to_sample_id():
    assert _safe_sample_name(_sample(42, "")) == "sample_42"
    assert _safe_sample_name(_sample(42, None)) == "sample_42"
    assert _safe_sample_name(_sample(42, "   ")) == "sample_42"


def test_generic_sheet_stringifies_numeric_sample_column():
    samples = [_sample(1, "1"), _sample(2, "2")]
    params = {"input_paths": {"1": ["/r1.fq", "/r2.fq"], "2": ["/a.fq", "/b.fq"]}}
    out = SampleSheetService.generate_generic_sheet(samples, params)
    lines = out.strip().splitlines()
    assert lines[0] == "sample,fastq_1,fastq_2"
    # The sample column must be the stringified name, never a bare number.
    assert lines[1].split(",")[0] == "sample_1"
    assert lines[2].split(",")[0] == "sample_2"
