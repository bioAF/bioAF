"""Tests that launch_run auto-populates FASTQ paths from sample_files.

When the user does not provide input_paths in parameters, the sample
sheet generator should look up files linked to each sample via the
sample_files junction table and use their GCS URIs as fastq_1/fastq_2.
"""

from app.services.sample_sheet_service import SampleSheetService

from unittest.mock import MagicMock


def _make_sample(sample_id: int, external_id: str, files: list[dict] | None = None):
    """Create a mock sample with optional linked files."""
    sample = MagicMock()
    sample.id = sample_id
    sample.sample_id_unique = external_id
    sample.files = files or []
    return sample


class TestAutoPopulateFiles:
    def test_uses_linked_files_when_no_input_paths(self):
        """When input_paths is empty, use sample.files GCS URIs."""
        file1 = MagicMock()
        file1.gcs_uri = "gs://bucket/sample1_R1.fastq.gz"
        file1.filename = "sample1_R1.fastq.gz"

        file2 = MagicMock()
        file2.gcs_uri = "gs://bucket/sample1_R2.fastq.gz"
        file2.filename = "sample1_R2.fastq.gz"

        sample = _make_sample(1, "PBMC_donor1", files=[file1, file2])

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {"expected_cells": 10000},
        )

        assert "gs://bucket/sample1_R1.fastq.gz" in csv_output
        assert "gs://bucket/sample1_R2.fastq.gz" in csv_output

    def test_input_paths_takes_precedence_over_linked_files(self):
        """Explicit input_paths should override linked files."""
        file1 = MagicMock()
        file1.gcs_uri = "gs://bucket/linked_R1.fastq.gz"
        file1.filename = "linked_R1.fastq.gz"

        sample = _make_sample(1, "PBMC_donor1", files=[file1])

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {
                "input_paths": {"1": ["gs://bucket/explicit_R1.fastq.gz"]},
                "expected_cells": 10000,
            },
        )

        assert "gs://bucket/explicit_R1.fastq.gz" in csv_output
        assert "gs://bucket/linked_R1.fastq.gz" not in csv_output

    def test_empty_files_produces_empty_paths(self):
        """When no files and no input_paths, fastq columns are empty."""
        sample = _make_sample(1, "PBMC_donor1", files=[])

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {},
        )

        lines = csv_output.strip().split("\n")
        # Header + 1 data row
        assert len(lines) == 2
        # Data row should have empty fastq fields
        assert "PBMC_donor1" in lines[1]

    def test_sorts_files_r1_before_r2(self):
        """FASTQ files should be sorted so R1 comes before R2."""
        file_r2 = MagicMock()
        file_r2.gcs_uri = "gs://bucket/sample1_R2_001.fastq.gz"
        file_r2.filename = "sample1_R2_001.fastq.gz"

        file_r1 = MagicMock()
        file_r1.gcs_uri = "gs://bucket/sample1_R1_001.fastq.gz"
        file_r1.filename = "sample1_R1_001.fastq.gz"

        # Pass R2 first to verify sorting
        sample = _make_sample(1, "PBMC_donor1", files=[file_r2, file_r1])

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {},
        )

        lines = csv_output.strip().split("\n")
        data_row = lines[1]
        r1_pos = data_row.index("R1")
        r2_pos = data_row.index("R2")
        assert r1_pos < r2_pos, "R1 should appear before R2 in the CSV row"

    def test_excludes_index_reads_from_fastq_paths(self):
        """I1 (index) files must be excluded; only R1 and R2 used."""
        file_i1 = MagicMock()
        file_i1.gcs_uri = "gs://bucket/PBMC_S1_L001_I1_001.fastq.gz"
        file_i1.filename = "PBMC_S1_L001_I1_001.fastq.gz"
        file_i1.tags_json = ["read:I1", "lane:001", "sample:PBMC"]

        file_r1 = MagicMock()
        file_r1.gcs_uri = "gs://bucket/PBMC_S1_L001_R1_001.fastq.gz"
        file_r1.filename = "PBMC_S1_L001_R1_001.fastq.gz"
        file_r1.tags_json = ["read:R1", "lane:001", "sample:PBMC"]

        file_r2 = MagicMock()
        file_r2.gcs_uri = "gs://bucket/PBMC_S1_L001_R2_001.fastq.gz"
        file_r2.filename = "PBMC_S1_L001_R2_001.fastq.gz"
        file_r2.tags_json = ["read:R2", "lane:001", "sample:PBMC"]

        sample = _make_sample(1, "PBMC_donor1", files=[file_i1, file_r1, file_r2])

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {"expected_cells": 10000},
        )

        assert "I1" not in csv_output, "Index read I1 should not appear in samplesheet"
        assert "gs://bucket/PBMC_S1_L001_R1_001.fastq.gz" in csv_output
        assert "gs://bucket/PBMC_S1_L001_R2_001.fastq.gz" in csv_output

    def test_multi_lane_produces_multiple_rows(self):
        """Multi-lane data (L001+L002) should produce one row per lane."""
        files = []
        for lane in ["L001", "L002"]:
            for read in ["I1", "R1", "R2"]:
                f = MagicMock()
                f.filename = f"PBMC_S1_{lane}_{read}_001.fastq.gz"
                f.gcs_uri = f"gs://bucket/PBMC_S1_{lane}_{read}_001.fastq.gz"
                f.tags_json = [f"read:{read}", f"lane:{lane[-3:]}", "sample:PBMC"]
                files.append(f)

        sample = _make_sample(1, "PBMC_donor1", files=files)

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {"expected_cells": 10000},
        )

        lines = csv_output.strip().split("\n")
        # Header + 2 data rows (one per lane)
        assert len(lines) == 3, f"Expected 3 lines (header + 2 lanes), got {len(lines)}"
        assert "L001_R1" in lines[1]
        assert "L001_R2" in lines[1]
        assert "L002_R1" in lines[2]
        assert "L002_R2" in lines[2]
        # No I1 in any row
        for line in lines:
            assert "I1" not in line, f"Index read I1 found in: {line}"

    def test_read_type_from_tags_json(self):
        """Uses tags_json read type when available, not filename sort."""
        file_r1 = MagicMock()
        file_r1.gcs_uri = "gs://bucket/reads_barcode.fastq.gz"
        file_r1.filename = "reads_barcode.fastq.gz"
        file_r1.tags_json = ["read:R1"]

        file_r2 = MagicMock()
        file_r2.gcs_uri = "gs://bucket/reads_cdna.fastq.gz"
        file_r2.filename = "reads_cdna.fastq.gz"
        file_r2.tags_json = ["read:R2"]

        # Pass R2 first to verify tag-based assignment beats sort order
        sample = _make_sample(1, "PBMC_donor1", files=[file_r2, file_r1])

        csv_output = SampleSheetService.generate_scrnaseq_sheet(
            [sample],
            {"expected_cells": 10000},
        )

        lines = csv_output.strip().split("\n")
        data_row = lines[1].split(",")
        # fastq_1 column (index 1) should be the R1 file
        assert data_row[1] == "gs://bucket/reads_barcode.fastq.gz"
        # fastq_2 column (index 2) should be the R2 file
        assert data_row[2] == "gs://bucket/reads_cdna.fastq.gz"
