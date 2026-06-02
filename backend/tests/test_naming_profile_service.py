"""Tests for NamingProfileService CRUD against the redesigned schema.

The redesign drops the closed-enum field names and the
project_code_mappings / experiment_code_mappings columns. Profiles now
carry an optional experiment_template_id and a list of SegmentDefinition
objects with the new shape (identifier + field_name + field_type +
padding | date_format).
"""

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text

from app.schemas.naming_profile import (
    NamingProfileCreate,
    NamingProfileUpdate,
    SegmentDefinition,
)
from app.services.naming_profile_service import NamingProfileService


def _seg(**kwargs) -> SegmentDefinition:
    """Helper: SegmentDefinition with sensible defaults for the common shape."""
    defaults = {
        "position": 0,
        "identifier": "SMP",
        "field_name": "SampleID",
        "field_type": "number",
        "padding": 2,
        "date_format": None,
        "is_system_chip": False,
    }
    defaults.update(kwargs)
    return SegmentDefinition(**defaults)


@pytest_asyncio.fixture
async def org_user_ids(client, admin_token, session):
    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()
    result = await session.execute(text("SELECT id FROM users LIMIT 1"))
    user = result.fetchone()
    return org.id, user.id


@pytest_asyncio.fixture
async def sample_profile(session, org_user_ids):
    org_id, user_id = org_user_ids
    data = NamingProfileCreate(
        name="Team A profile",
        description="A team's filename convention",
        segments=[
            _seg(position=0, identifier="SMP", field_name="SampleID"),
            _seg(
                position=1,
                identifier=None,
                field_name="RunDate",
                field_type="date",
                padding=None,
                date_format="YYYYMMDD",
            ),
        ],
    )
    profile = await NamingProfileService.create_profile(session, org_id, user_id, data)
    await session.commit()
    return profile


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_profile_persists_with_new_schema(client, admin_token, session, org_user_ids):
    org_id, user_id = org_user_ids
    data = NamingProfileCreate(
        name="Test Profile",
        segments=[_seg()],
    )
    profile = await NamingProfileService.create_profile(session, org_id, user_id, data)
    await session.commit()
    assert profile.id is not None
    assert profile.status == "active"
    assert profile.experiment_template_id is None

    audit = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'naming_profile' AND action = 'create'")
    )
    assert audit.fetchone() is not None


@pytest.mark.asyncio
async def test_get_profile(client, admin_token, session, sample_profile):
    profile = await NamingProfileService.get_profile(session, sample_profile.id)
    assert profile is not None
    assert profile.name == "Team A profile"


@pytest.mark.asyncio
async def test_list_profiles(client, admin_token, session, org_user_ids, sample_profile):
    org_id, _ = org_user_ids
    profiles = await NamingProfileService.list_profiles(session, org_id)
    assert any(p.id == sample_profile.id for p in profiles)


@pytest.mark.asyncio
async def test_update_profile_name_and_delimiter(client, admin_token, session, org_user_ids, sample_profile):
    _, user_id = org_user_ids
    updated = await NamingProfileService.update_profile(
        session,
        sample_profile.id,
        user_id,
        NamingProfileUpdate(name="Renamed", delimiter="-"),
    )
    await session.commit()
    assert updated.name == "Renamed"
    assert updated.delimiter == "-"


@pytest.mark.asyncio
async def test_deactivate_profile_writes_audit(client, admin_token, session, org_user_ids, sample_profile):
    _, user_id = org_user_ids
    deactivated = await NamingProfileService.deactivate_profile(session, sample_profile.id, user_id)
    await session.commit()
    assert deactivated.status == "inactive"

    audit = await session.execute(
        text("SELECT * FROM audit_log WHERE entity_type = 'naming_profile' AND action = 'deactivate'")
    )
    assert audit.fetchone() is not None


# ---------------------------------------------------------------------------
# Schema validation. These run at the Pydantic layer and are independent of
# the DB. They guard the rules the parser relies on: unique identifiers,
# ASCII-only letters, padding range, segment-shape consistency.
# ---------------------------------------------------------------------------


def test_save_rejects_duplicate_identifier():
    with pytest.raises(ValidationError):
        NamingProfileCreate(
            name="Dup",
            segments=[
                _seg(position=0, identifier="SMP", field_name="SampleID"),
                _seg(position=1, identifier="SMP", field_name="Other"),
            ],
        )


def test_save_rejects_duplicate_identifier_case_insensitively():
    with pytest.raises(ValidationError):
        NamingProfileCreate(
            name="Dup",
            segments=[
                _seg(position=0, identifier="SMP", field_name="SampleID"),
                _seg(position=1, identifier="smp", field_name="Other"),
            ],
        )


def test_save_rejects_identifier_length_out_of_range():
    with pytest.raises(ValidationError):
        SegmentDefinition(
            position=0,
            identifier="TOOLONG",
            field_name="X",
            field_type="number",
            padding=2,
        )


def test_save_rejects_empty_identifier_on_non_date_segment():
    with pytest.raises(ValidationError):
        SegmentDefinition(
            position=0,
            identifier=None,
            field_name="X",
            field_type="number",
            padding=2,
        )


def test_save_rejects_padding_out_of_range():
    with pytest.raises(ValidationError):
        SegmentDefinition(
            position=0,
            identifier="SMP",
            field_name="SampleID",
            field_type="number",
            padding=99,
        )


def test_save_allows_zero_padding():
    """padding=0 means 'no minimum width' (lenient)."""
    SegmentDefinition(
        position=0,
        identifier="R",
        field_name="Read",
        field_type="number",
        padding=0,
    )


def test_save_rejects_non_ascii_identifier_letters():
    with pytest.raises(ValidationError):
        SegmentDefinition(
            position=0,
            identifier="SMP1",
            field_name="X",
            field_type="number",
            padding=2,
        )


def test_save_rejects_date_segment_with_identifier():
    with pytest.raises(ValidationError):
        SegmentDefinition(
            position=0,
            identifier="DT",
            field_name="RunDate",
            field_type="date",
            date_format="YYYYMMDD",
        )


def test_save_rejects_more_than_one_date_segment():
    with pytest.raises(ValidationError):
        NamingProfileCreate(
            name="Two dates",
            segments=[
                SegmentDefinition(
                    position=0,
                    identifier=None,
                    field_name="A",
                    field_type="date",
                    date_format="YYYYMMDD",
                ),
                SegmentDefinition(
                    position=1,
                    identifier=None,
                    field_name="B",
                    field_type="date",
                    date_format="YYMMDD",
                ),
            ],
        )


def test_save_allows_profile_with_no_template():
    """experiment_template_id is optional per the redesign plan."""
    data = NamingProfileCreate(name="No template", segments=[_seg()])
    assert data.experiment_template_id is None
