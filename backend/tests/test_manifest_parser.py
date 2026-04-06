"""Tests for Chunk 3: Manifest parsing service."""

import pytest

from app.services.manifest_parser import ManifestParseResult, parse_manifest


class TestMd5sumFormat:
    def test_parse_with_batch_header(self):
        content = (
            "# batch: SEQ-2026-0042\n"
            "d41d8cd98f00b204e9800998ecf8427e  EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz\n"
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6  EXP015_SAMPLE0003_S3_L001_R2_001.fastq.gz\n"
        )
        result = parse_manifest(content, "md5sum")

        assert isinstance(result, ManifestParseResult)
        assert result.batch_number == "SEQ-2026-0042"
        assert len(result.entries) == 2
        assert result.entries[0].filename == "EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz"
        assert result.entries[0].md5 == "d41d8cd98f00b204e9800998ecf8427e"
        assert result.entries[1].filename == "EXP015_SAMPLE0003_S3_L001_R2_001.fastq.gz"
        assert result.entries[1].md5 == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"

    def test_parse_without_batch_header(self):
        content = "d41d8cd98f00b204e9800998ecf8427e  file1.fastq.gz\na1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6  file2.fastq.gz\n"
        result = parse_manifest(content, "md5sum")

        assert result.batch_number is None
        assert len(result.entries) == 2
        assert result.entries[0].filename == "file1.fastq.gz"
        assert result.entries[1].filename == "file2.fastq.gz"

    def test_parse_skips_empty_lines(self):
        content = (
            "# batch: SEQ-001\n"
            "\n"
            "d41d8cd98f00b204e9800998ecf8427e  file1.fastq.gz\n"
            "\n"
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6  file2.fastq.gz\n"
            "\n"
        )
        result = parse_manifest(content, "md5sum")
        assert len(result.entries) == 2

    def test_parse_skips_comment_lines(self):
        content = "# batch: SEQ-001\n# This is a comment\nd41d8cd98f00b204e9800998ecf8427e  file1.fastq.gz\n"
        result = parse_manifest(content, "md5sum")
        assert result.batch_number == "SEQ-001"
        assert len(result.entries) == 1


class TestCsvFormat:
    def test_parse_csv(self):
        content = (
            "batch_number,filename,md5\n"
            "SEQ-2026-0042,EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz,d41d8cd98f00b204e9800998ecf8427e\n"
            "SEQ-2026-0042,EXP015_SAMPLE0003_S3_L001_R2_001.fastq.gz,a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6\n"
        )
        result = parse_manifest(content, "csv")

        assert result.batch_number == "SEQ-2026-0042"
        assert len(result.entries) == 2
        assert result.entries[0].filename == "EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz"
        assert result.entries[0].md5 == "d41d8cd98f00b204e9800998ecf8427e"

    def test_parse_csv_skips_empty_rows(self):
        content = "batch_number,filename,md5\n\nSEQ-001,file1.fastq.gz,abc123\n\n"
        result = parse_manifest(content, "csv")
        assert len(result.entries) == 1


class TestMalformedInput:
    def test_empty_content(self):
        result = parse_manifest("", "md5sum")
        assert len(result.entries) == 0
        assert result.batch_number is None

    def test_only_comments(self):
        result = parse_manifest("# batch: SEQ-001\n# nothing else\n", "md5sum")
        assert result.batch_number == "SEQ-001"
        assert len(result.entries) == 0

    def test_malformed_md5sum_line_missing_hash(self):
        """Lines without the two-space separator are skipped."""
        content = "d41d8cd98f00b204e9800998ecf8427e  good_file.fastq.gz\nbad_line_no_hash\nanother bad line\n"
        result = parse_manifest(content, "md5sum")
        assert len(result.entries) == 1
        assert result.entries[0].filename == "good_file.fastq.gz"

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="Unsupported manifest format"):
            parse_manifest("data", "unknown_format")
