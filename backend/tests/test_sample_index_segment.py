"""Tests for the sample_index naming profile segment type."""

from app.services.naming_profile_parser import parse_filename


def _make_profile(
    segments_json,
    delimiter="_",
    strip_extension=True,
):
    """Create a mock NamingProfile-like object for testing."""

    class MockProfile:
        def __init__(self):
            self.id = 1
            self.name = "TestProfile"
            self.delimiter = delimiter
            self.strip_extension = strip_extension
            self.segments_json = segments_json
            self.project_code_mappings = {}
            self.experiment_code_mappings = {}

    return MockProfile()


# Illumina-style profile: ignore_ignore_ignore_sampleIndex_ignore_ignore_ignore
ILLUMINA_SEGMENTS = [
    {"position": 0, "field": "ignore", "required": True},
    {"position": 1, "field": "ignore", "required": True},
    {"position": 2, "field": "ignore", "required": True},
    {"position": 3, "field": "sample_index", "required": True},
    {"position": 4, "field": "ignore", "required": True},
    {"position": 5, "field": "ignore", "required": True},
    {"position": 6, "field": "ignore", "required": True},
]


def test_parse_s_number_segment():
    """S3 parses to segments['sample_index'] == '3'."""
    profile = _make_profile(ILLUMINA_SEGMENTS)
    result = parse_filename("pbmc_1k_v3_S3_L001_R2_001.fastq.gz", profile)
    assert result.success, f"Parse failed: {result.error}"
    assert result.segments["sample_index"] == "3"


def test_parse_lowercase_s_number():
    """s1 parses to segments['sample_index'] == '1'."""
    profile = _make_profile(ILLUMINA_SEGMENTS)
    result = parse_filename("pbmc_1k_v3_s1_L001_R1_001.fastq.gz", profile)
    assert result.success
    assert result.segments["sample_index"] == "1"


def test_parse_bare_number():
    """Bare '42' parses to segments['sample_index'] == '42'."""
    profile = _make_profile(ILLUMINA_SEGMENTS)
    result = parse_filename("pbmc_1k_v3_42_L001_R1_001.fastq.gz", profile)
    assert result.success
    assert result.segments["sample_index"] == "42"


def test_parse_invalid_sample_index():
    """Non-numeric value fails when sample_index is required."""
    profile = _make_profile(ILLUMINA_SEGMENTS)
    result = parse_filename("pbmc_1k_v3_NOTANUM_L001_R1_001.fastq.gz", profile)
    assert not result.success


def test_sample_id_still_resolves_by_external_id():
    """Profile with sample_id segment still resolves via sample_id_unique lookup."""
    segments = [
        {"position": 0, "field": "sample_id", "required": True},
        {"position": 1, "field": "ignore", "required": True},
    ]
    profile = _make_profile(segments)
    result = parse_filename("PBMC-001_data.txt", profile)
    assert result.success
    assert result.segments["sample_id"] == "PBMC-001"
    assert "sample_index" not in result.segments


def test_schema_accepts_sample_index():
    """NamingProfileCreate schema accepts sample_index as a valid field."""
    from app.schemas.naming_profile import NamingProfileCreate

    from app.schemas.naming_profile import SegmentDefinition

    profile = NamingProfileCreate(
        name="Illumina S-number",
        segments=[
            SegmentDefinition(position=0, field="ignore", required=True),
            SegmentDefinition(position=1, field="sample_index", required=True),
        ],
    )
    assert profile.segments[1].field == "sample_index"
