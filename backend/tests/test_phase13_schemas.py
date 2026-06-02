"""Pydantic schema sanity checks for the Phase 13 surfaces.

The Naming Profile schemas were rewritten in 2026-06 (see
local/Naming Profiles/redesign-plan.md). This file now exercises the new
shape; the closed-enum field-name tests it used to carry are gone with the
feature they tested.
"""

import pytest
from pydantic import ValidationError

from app.schemas.ingest import (
    BulkReassignRequest,
    IngestSimulateRequest,
)
from app.schemas.naming_profile import (
    NamingProfileCreate,
    NamingProfileTestRequest,
    SegmentDefinition,
)
from app.services.budget_service import BudgetCheckResult


def _number_segment(**overrides) -> SegmentDefinition:
    defaults = {
        "position": 0,
        "identifier": "SMP",
        "field_name": "SampleID",
        "field_type": "number",
        "padding": 2,
        "date_format": None,
        "is_system_chip": False,
    }
    defaults.update(overrides)
    return SegmentDefinition(**defaults)


class TestSegmentDefinition:
    def test_valid_segment(self):
        seg = _number_segment()
        assert seg.field_name == "SampleID"
        assert seg.identifier == "SMP"
        assert seg.field_type == "number"
        assert seg.position == 0

    def test_invalid_field_type_value(self):
        with pytest.raises(ValidationError):
            SegmentDefinition(
                position=0,
                identifier="SMP",
                field_name="SampleID",
                field_type="not_a_real_type",  # type: ignore[arg-type]
            )

    def test_negative_position(self):
        with pytest.raises(ValidationError):
            _number_segment(position=-1)


class TestNamingProfileCreate:
    def test_valid_profile(self):
        profile = NamingProfileCreate(
            name="Test Profile",
            segments=[_number_segment()],
        )
        assert profile.name == "Test Profile"
        assert profile.delimiter == "_"
        assert profile.strip_extension is True
        assert profile.experiment_template_id is None

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            NamingProfileCreate(name="   ", segments=[_number_segment()])

    def test_empty_segments_rejected(self):
        with pytest.raises(ValidationError):
            NamingProfileCreate(name="Test", segments=[])

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            NamingProfileCreate(name="Test")  # type: ignore[call-arg]


class TestNamingProfileTestRequest:
    def test_valid_request(self):
        req = NamingProfileTestRequest(
            filenames=["test.fastq", "sample.bam"],
            segments=[_number_segment()],
        )
        assert len(req.filenames) == 2
        assert len(req.segments) == 1

    def test_empty_filenames_rejected(self):
        with pytest.raises(ValidationError):
            NamingProfileTestRequest(filenames=[], segments=[_number_segment()])

    def test_empty_segments_rejected(self):
        with pytest.raises(ValidationError):
            NamingProfileTestRequest(filenames=["a.txt"], segments=[])


class TestIngestSimulateRequest:
    def test_valid_request(self):
        req = IngestSimulateRequest(filename="test.fastq", file_size_bytes=1024)
        assert req.filename == "test.fastq"

    def test_empty_filename_rejected(self):
        with pytest.raises(ValidationError):
            IngestSimulateRequest(filename="   ")


class TestBulkReassignRequest:
    def test_valid_request(self):
        req = BulkReassignRequest(file_ids=[1, 2, 3], target_project_id=1)
        assert len(req.file_ids) == 3

    def test_empty_file_ids_rejected(self):
        with pytest.raises(ValidationError):
            BulkReassignRequest(file_ids=[])


class TestBudgetCheckResult:
    def test_valid_result(self):
        result = BudgetCheckResult(
            estimated_cost=5.0,
            confidence_interval_pct=15.0,
            current_month_spend=100.0,
            queued_running_cost=10.0,
            projected_total=115.0,
            monthly_budget=500.0,
            decision="within_budget",
        )
        assert result.decision == "within_budget"
