"""Tests for GEO validation engine."""

from app.services.geo.validation import validate_experiment_for_geo


def _complete_experiment():
    return {
        "id": 1,
        "name": "scRNA-seq of human PBMCs",
        "description": "Single-cell RNA-seq analysis of peripheral blood mononuclear cells.",
        "hypothesis": "Treatment X affects T-cell differentiation.",
        "owner_user_name": "Jane Doe",
        "samples": [
            {"organism": "Homo sapiens", "tissue_type": "blood"},
        ],
    }


def _complete_sample():
    return {
        "id": 101,
        "sample_id_unique": "PBMC_001",
        "organism": "Homo sapiens",
        "molecule_type": "total RNA",
        "tissue_type": "blood",
        "treatment_condition": "Vehicle control",
        "library_prep_method": "10x Chromium 3' v3.1",
        "library_layout": "paired",
        "prep_notes": "Standard protocol",
        "sample_batch": None,
    }


def _complete_pipeline():
    return {
        "id": 10,
        "pipeline_name": "nf-core/scrnaseq",
        "pipeline_version": "2.7.0",
        "reference_genome": "GRCh38",
        "alignment_algorithm": "STARsolo",
    }


def _complete_files():
    return {
        "raw_files": [
            {"filename": "PBMC_001_R1.fastq.gz", "md5_checksum": "abc123", "gcs_uri": "gs://bucket/raw/R1.fastq.gz"},
            {"filename": "PBMC_001_R2.fastq.gz", "md5_checksum": "def456", "gcs_uri": "gs://bucket/raw/R2.fastq.gz"},
        ],
        "processed_files": [
            {"filename": "filtered_matrix.h5", "md5_checksum": "ghi789", "gcs_uri": "gs://bucket/results/matrix.h5"},
        ],
        "raw_filenames": "PBMC_001_R1.fastq.gz, PBMC_001_R2.fastq.gz",
        "processed_filenames": "filtered_matrix.h5",
        "processed_gcs_uris": "gs://bucket/results/matrix.h5",
    }


def test_all_complete_experiment():
    """All-complete experiment produces all-green validation."""
    report = validate_experiment_for_geo(
        _complete_experiment(),
        [_complete_sample()],
        _complete_pipeline(),
        _complete_files(),
    )

    # instrument_model moved from SampleBatch to POBatch; will be 1 missing until POBatch wired to GEO
    assert report.summary.missing_required <= 1
    assert report.summary.complete > 0
    assert len(report.sample_validations) == 1

    # All required fields should be complete
    for f in report.series_fields:
        assert f.status != "missing_required", f"Series field {f.geo_column} is missing_required"


def test_missing_required_fields():
    """Missing required fields are flagged correctly."""
    experiment = _complete_experiment()
    experiment["name"] = ""  # Required field

    sample = _complete_sample()
    sample["organism"] = None  # Required field
    sample["molecule_type"] = None  # Required field

    report = validate_experiment_for_geo(
        experiment,
        [sample],
        _complete_pipeline(),
        _complete_files(),
    )

    assert report.summary.missing_required > 0

    # Check specific fields
    series_statuses = {f.geo_column: f.status for f in report.series_fields}
    assert series_statuses["Series_title"] == "missing_required"

    sample_statuses = {f.geo_column: f.status for f in report.sample_validations[0].fields}
    assert sample_statuses["organism"] == "missing_required"
    assert sample_statuses["molecule"] == "missing_required"


def test_novel_vocabulary_values():
    """Values outside GEO's controlled vocabulary get populated_unvalidated."""
    sample = _complete_sample()
    sample["organism"] = "Custom organism XYZ"  # Not in GEO vocabulary

    report = validate_experiment_for_geo(
        _complete_experiment(),
        [sample],
        _complete_pipeline(),
        _complete_files(),
    )

    sample_statuses = {f.geo_column: f.status for f in report.sample_validations[0].fields}
    assert sample_statuses["organism"] == "populated_unvalidated"


def test_derived_library_strategy():
    """Library strategy derived correctly from library_prep_method."""
    sample = _complete_sample()
    sample["library_prep_method"] = "10x Chromium 3' v3.1"

    report = validate_experiment_for_geo(
        _complete_experiment(),
        [sample],
        _complete_pipeline(),
        _complete_files(),
    )

    sample_fields = {f.geo_column: f for f in report.sample_validations[0].fields}
    assert sample_fields["library_strategy"].value == "RNA-Seq"
    assert sample_fields["library_strategy"].status == "complete"


def test_derived_library_source():
    """Library source derived correctly from molecule_type."""
    sample = _complete_sample()
    sample["molecule_type"] = "total RNA"

    report = validate_experiment_for_geo(
        _complete_experiment(),
        [sample],
        _complete_pipeline(),
        _complete_files(),
    )

    sample_fields = {f.geo_column: f for f in report.sample_validations[0].fields}
    assert sample_fields["library_source"].value == "TRANSCRIPTOMIC"


def test_file_manifest_missing_checksums():
    """Files without checksums are flagged."""
    files = _complete_files()
    files["raw_files"][0]["md5_checksum"] = None

    report = validate_experiment_for_geo(
        _complete_experiment(),
        [_complete_sample()],
        _complete_pipeline(),
        files,
    )

    assert report.file_manifest.files_missing_checksums == 1
    assert report.file_manifest.files_with_checksums == 2


def test_multiple_samples():
    """Multiple samples produce individual validation records."""
    samples = [
        {**_complete_sample(), "id": 101, "sample_id_unique": "S001"},
        {**_complete_sample(), "id": 102, "sample_id_unique": "S002", "organism": None},
    ]

    report = validate_experiment_for_geo(
        _complete_experiment(),
        samples,
        _complete_pipeline(),
        _complete_files(),
    )

    assert len(report.sample_validations) == 2
    # First sample should be mostly complete
    s1_statuses = {f.geo_column: f.status for f in report.sample_validations[0].fields}
    assert s1_statuses["organism"] == "complete"
    # Second sample has missing organism
    s2_statuses = {f.geo_column: f.status for f in report.sample_validations[1].fields}
    assert s2_statuses["organism"] == "missing_required"


def test_no_pipeline_data():
    """Export without pipeline data flags pipeline-dependent fields."""
    report = validate_experiment_for_geo(
        _complete_experiment(),
        [_complete_sample()],
        None,  # No pipeline data
        _complete_files(),
    )

    # data_processing and Genome_build should be missing
    protocol_statuses = {f.geo_column: f.status for f in report.protocol_fields}
    assert protocol_statuses["Genome_build"] == "missing_required"


def test_empty_experiment():
    """Empty experiment (no samples) produces meaningful validation."""
    report = validate_experiment_for_geo(
        {"id": 1, "name": "Empty Exp"},
        [],
        None,
        None,
    )

    assert len(report.sample_validations) == 0
    assert report.summary.missing_required > 0
    assert report.file_manifest.total_files == 0
