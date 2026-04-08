from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService


def _make_sample(sample_id: int, external_id: str):
    """Create a mock sample object."""
    sample = MagicMock()
    sample.id = sample_id
    sample.sample_id_unique = external_id
    return sample


def test_generate_scrnaseq_sheet():
    """Generate nf-core/scrnaseq sample sheet."""
    samples = [_make_sample(1, "SAMPLE_A"), _make_sample(2, "SAMPLE_B")]
    parameters = {
        "input_paths": {
            "1": ["/data/raw/SAMPLE_A_R1.fastq.gz", "/data/raw/SAMPLE_A_R2.fastq.gz"],
            "2": ["/data/raw/SAMPLE_B_R1.fastq.gz", "/data/raw/SAMPLE_B_R2.fastq.gz"],
        },
        "expected_cells": 5000,
    }
    result = SampleSheetService.generate_scrnaseq_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2,expected_cells"
    assert "SAMPLE_A" in lines[1]
    assert "/data/raw/SAMPLE_A_R1.fastq.gz" in lines[1]
    assert "5000" in lines[1]
    assert "SAMPLE_B" in lines[2]


def test_generate_rnaseq_sheet():
    """Generate nf-core/rnaseq sample sheet."""
    samples = [_make_sample(1, "RNA_1")]
    parameters = {
        "input_paths": {"1": ["/data/raw/RNA_1_R1.fastq.gz", "/data/raw/RNA_1_R2.fastq.gz"]},
        "strandedness": "reverse",
    }
    result = SampleSheetService.generate_rnaseq_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2,strandedness"
    assert "RNA_1" in lines[1]
    assert "reverse" in lines[1]


def test_generate_generic_sheet():
    """Generate generic sample sheet."""
    samples = [_make_sample(1, "GEN_1")]
    parameters = {"input_paths": {"1": ["/data/raw/GEN_1_R1.fastq.gz"]}}
    result = SampleSheetService.generate_generic_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2"
    assert "GEN_1" in lines[1]


def test_sheet_with_no_linked_files():
    """Handle samples with no linked files — empty paths."""
    samples = [_make_sample(1, "NO_FILES")]
    parameters = {}
    result = SampleSheetService.generate_scrnaseq_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert "NO_FILES" in lines[1]
    # Should have empty file paths
    parts = lines[1].split(",")
    assert parts[1] == ""  # fastq_1 empty
    assert parts[2] == ""  # fastq_2 empty


def test_sheet_with_manual_path_fallback():
    """Manual paths provided in parameters work as fallback."""
    samples = [_make_sample(42, "MANUAL")]
    parameters = {
        "input_paths": {"42": ["/manual/path/R1.fq.gz", "/manual/path/R2.fq.gz"]},
    }
    result = SampleSheetService.generate_scrnaseq_sheet(samples, parameters)
    assert "/manual/path/R1.fq.gz" in result


def test_generate_sheet_routes_correctly():
    """The generate_sheet method routes to correct generator."""
    samples = [_make_sample(1, "TEST")]
    parameters = {"input_paths": {"1": ["/data/R1.fq.gz"]}}

    scrnaseq_result = SampleSheetService.generate_sheet("nf-core/scrnaseq", samples, parameters)
    assert "expected_cells" in scrnaseq_result

    rnaseq_result = SampleSheetService.generate_sheet("nf-core/rnaseq", samples, parameters)
    assert "strandedness" in rnaseq_result

    generic_result = SampleSheetService.generate_sheet("custom-pipeline", samples, parameters)
    assert "sample,fastq_1,fastq_2" in generic_result


def test_sample_without_external_id():
    """Sample without external ID uses fallback naming."""
    sample = _make_sample(5, None)
    result = SampleSheetService.generate_scrnaseq_sheet([sample], {})
    assert "sample_5" in result
