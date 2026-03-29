"""Tests for file type detection and artifact classification utilities."""

from app.services.file_type_utils import classify_artifact_type, detect_file_type


class TestDetectFileType:
    """Tests for detect_file_type() - maps filename extensions to file type categories."""

    def test_fastq_extensions(self) -> None:
        assert detect_file_type("sample_R1.fastq") == "fastq"
        assert detect_file_type("sample_R2.fq") == "fastq"

    def test_compressed_fastq(self) -> None:
        assert detect_file_type("sample_R1.fastq.gz") == "fastq"
        assert detect_file_type("sample_R2.fq.gz") == "fastq"
        assert detect_file_type("reads.fastq.bz2") == "fastq"

    def test_alignment_files(self) -> None:
        assert detect_file_type("aligned.bam") == "bam"
        assert detect_file_type("aligned.sam") == "bam"
        assert detect_file_type("aligned.cram") == "bam"

    def test_anndata(self) -> None:
        assert detect_file_type("filtered.h5ad") == "h5ad"

    def test_count_matrices(self) -> None:
        assert detect_file_type("counts.csv") == "count_matrix"
        assert detect_file_type("metrics.tsv") == "count_matrix"
        assert detect_file_type("matrix.mtx") == "count_matrix"

    def test_images(self) -> None:
        assert detect_file_type("plot.png") == "image"
        assert detect_file_type("figure.svg") == "image"
        assert detect_file_type("photo.jpg") == "image"
        assert detect_file_type("scan.tiff") == "image"

    def test_documents(self) -> None:
        assert detect_file_type("report.pdf") == "document"
        assert detect_file_type("notes.txt") == "document"
        assert detect_file_type("readme.md") == "document"

    def test_unknown_extension(self) -> None:
        assert detect_file_type("data.xyz") == "other"
        assert detect_file_type("noext") == "other"

    def test_case_insensitive(self) -> None:
        assert detect_file_type("DATA.CSV") == "count_matrix"
        assert detect_file_type("image.PNG") == "image"

    def test_html(self) -> None:
        assert detect_file_type("report.html") == "report"

    def test_h5(self) -> None:
        assert detect_file_type("filtered_feature_bc_matrix.h5") == "h5"


class TestClassifyArtifactType:
    """Tests for classify_artifact_type() - maps filenames to semantic artifact categories."""

    def test_alignment_artifacts(self) -> None:
        assert classify_artifact_type("aligned.bam") == "alignment"
        assert classify_artifact_type("aligned.cram") == "alignment"
        assert classify_artifact_type("aligned.sam") == "alignment"

    def test_anndata_artifact(self) -> None:
        assert classify_artifact_type("filtered.h5ad") == "anndata"

    def test_feature_matrix(self) -> None:
        assert classify_artifact_type("filtered_feature_bc_matrix.h5") == "feature_matrix"

    def test_count_matrix_artifacts(self) -> None:
        assert classify_artifact_type("counts.csv") == "count_matrix"
        assert classify_artifact_type("metrics.tsv") == "count_matrix"
        assert classify_artifact_type("matrix.mtx") == "count_matrix"

    def test_fastq_artifact(self) -> None:
        assert classify_artifact_type("reads.fastq") == "fastq"
        assert classify_artifact_type("reads.fastq.gz") == "fastq"

    def test_image_artifacts(self) -> None:
        assert classify_artifact_type("plot.png") == "image"
        assert classify_artifact_type("figure.svg") == "image"
        assert classify_artifact_type("chart.pdf") == "document"

    def test_report_artifact(self) -> None:
        assert classify_artifact_type("report.html") == "report"

    def test_trace_artifact(self) -> None:
        assert classify_artifact_type("trace.tsv") == "count_matrix"

    def test_unknown_artifact(self) -> None:
        assert classify_artifact_type("data.xyz") == "other"
