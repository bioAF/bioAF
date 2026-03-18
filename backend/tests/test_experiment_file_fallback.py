"""Tests that launch_run falls back to experiment-level files.

When sample_files junction table is empty (no per-sample file links),
the pipeline launch should query FASTQ files by experiment_id and
attach them to samples before generating the sample sheet.
"""

from unittest.mock import MagicMock

from app.services.pipeline_run_service import PipelineRunService


def _mock_file(file_id, filename, gcs_uri, experiment_id=1):
    f = MagicMock()
    f.id = file_id
    f.filename = filename
    f.gcs_uri = gcs_uri
    f.experiment_id = experiment_id
    f.file_type = "fastq"
    return f


class TestExperimentFileFallback:
    def test_attach_experiment_files_populates_empty_sample_files(self):
        """When sample.files is empty, _attach_experiment_files should populate it."""
        sample = MagicMock()
        sample.id = 1
        sample.experiment_id = 1
        sample.files = []

        files = [
            _mock_file(1, "sample_R1_001.fastq.gz", "gs://bucket/R1.fastq.gz"),
            _mock_file(2, "sample_R2_001.fastq.gz", "gs://bucket/R2.fastq.gz"),
        ]

        PipelineRunService._attach_experiment_files([sample], files)

        assert len(sample.files) == 2
        uris = [f.gcs_uri for f in sample.files]
        assert "gs://bucket/R1.fastq.gz" in uris
        assert "gs://bucket/R2.fastq.gz" in uris

    def test_attach_skips_samples_with_existing_files(self):
        """Samples that already have linked files should not be modified."""
        existing_file = _mock_file(99, "linked.fastq.gz", "gs://bucket/linked.fastq.gz")

        sample = MagicMock()
        sample.id = 1
        sample.experiment_id = 1
        sample.files = [existing_file]

        experiment_files = [
            _mock_file(1, "exp_R1.fastq.gz", "gs://bucket/exp_R1.fastq.gz"),
        ]

        PipelineRunService._attach_experiment_files([sample], experiment_files)

        assert len(sample.files) == 1
        assert sample.files[0].gcs_uri == "gs://bucket/linked.fastq.gz"

    def test_attach_filters_non_fastq_files(self):
        """Only FASTQ files should be attached, not PDFs or other types."""
        sample = MagicMock()
        sample.id = 1
        sample.experiment_id = 1
        sample.files = []

        fastq = _mock_file(1, "sample_R1.fastq.gz", "gs://bucket/R1.fastq.gz")
        pdf = _mock_file(2, "report.pdf", "gs://bucket/report.pdf")
        pdf.file_type = "pdf"
        pdf.filename = "report.pdf"

        PipelineRunService._attach_experiment_files([sample], [fastq, pdf])

        assert len(sample.files) == 1
        assert sample.files[0].filename == "sample_R1.fastq.gz"
