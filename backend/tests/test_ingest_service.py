"""Tests for the ingest service core pipeline."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.schemas.naming_profile import NamingProfileCreate, SegmentDefinition
from app.services.auto_ingest_gate import AUTO_INGEST_DISABLED
from app.services.ingest_service import (
    detect_file_type,
    get_unclaimed_entities,
    process_ingest_event,
)
from app.services.naming_profile_service import NamingProfileService

# Auto-ingest is gated off during the Naming Profile redesign. These tests
# assert behaviors of the gated body and will be re-enabled by the follow-up
# rework when AUTO_INGEST_DISABLED flips. See
# local/Naming Profiles/spec-auto-ingest-neutralize.md.
pytestmark = pytest.mark.skipif(
    AUTO_INGEST_DISABLED,
    reason="auto-ingest gated off during Naming Profile redesign",
)


@pytest_asyncio.fixture
async def org_user(client, admin_token, session):
    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()
    result = await session.execute(text("SELECT id FROM users LIMIT 1"))
    user = result.fetchone()
    return org.id, user.id


@pytest_asyncio.fixture
async def cro_profile(session, org_user):
    """Placeholder profile used by the (currently skipped) auto-ingest tests.

    The auto-ingest body these tests exercise is gated behind
    AUTO_INGEST_DISABLED (see local/Naming Profiles/spec-auto-ingest-neutralize.md);
    the fixture exists only so mypy and pytest collection are happy. The
    follow-up rework will replace these tests in lockstep with picking
    profile selection at parse time.
    """
    org_id, user_id = org_user
    data = NamingProfileCreate(
        name="CRO Standard",
        segments=[
            SegmentDefinition(
                position=0,
                identifier=None,
                field_name="RunDate",
                field_type="date",
                padding=None,
                date_format="YYYY-MM-DD",
                is_system_chip=False,
            ),
            SegmentDefinition(
                position=1,
                identifier="PRJ",
                field_name="ProjectCode",
                field_type="number",
                padding=2,
                date_format=None,
                is_system_chip=True,
            ),
            SegmentDefinition(
                position=2,
                identifier="EXP",
                field_name="ExperimentCode",
                field_type="number",
                padding=2,
                date_format=None,
                is_system_chip=True,
            ),
        ],
    )
    profile = await NamingProfileService.create_profile(session, org_id, user_id, data)
    await session.commit()
    return profile


class TestDetectFileType:
    def test_fastq(self):
        assert detect_file_type("sample.fastq") == "fastq"

    def test_fastq_gz(self):
        assert detect_file_type("sample.fastq.gz") == "fastq"

    def test_bam(self):
        assert detect_file_type("aligned.bam") == "bam"

    def test_h5ad(self):
        assert detect_file_type("data.h5ad") == "h5ad"

    def test_csv(self):
        assert detect_file_type("counts.csv") == "count_matrix"

    def test_png(self):
        assert detect_file_type("plot.png") == "image"

    def test_pdf(self):
        assert detect_file_type("report.pdf") == "document"

    def test_unknown(self):
        assert detect_file_type("data.xyz") == "other"

    def test_fq_gz(self):
        assert detect_file_type("reads.fq.gz") == "fastq"


@pytest.mark.asyncio
async def test_full_ingest_matched(client, admin_token, session, org_user, cro_profile):
    """Test full ingest with a matched filename creates file and ingest event."""
    org_id, user_id = org_user
    event = await process_ingest_event(
        filename="2026-03-10_TestProject_EXP001.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/2026-03-10_TestProject_EXP001.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
    )
    await session.commit()

    assert event.ingest_status == "cataloged"
    assert event.file_id is not None
    assert event.naming_profile_id == cro_profile.id
    assert event.parsed_project_code == "TestProject"
    assert event.parsed_experiment_code == "EXP001"


@pytest.mark.asyncio
async def test_ingest_auto_creates_project(client, admin_token, session, org_user, cro_profile):
    """Test that an unmapped project code auto-creates with is_unclaimed=True."""
    org_id, user_id = org_user
    event = await process_ingest_event(
        filename="2026-03-10_NewStudy_EXP001.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/2026-03-10_NewStudy_EXP001.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
    )
    await session.commit()

    assert event.resolved_project_id is not None
    # Verify project is unclaimed
    result = await session.execute(
        text(f"SELECT is_unclaimed, name FROM projects WHERE id = {event.resolved_project_id}")
    )
    row = result.fetchone()
    assert row.is_unclaimed is True
    assert row.name == "NewStudy"


@pytest.mark.asyncio
async def test_ingest_auto_creates_experiment(client, admin_token, session, org_user, cro_profile):
    """Test that an unmapped experiment code auto-creates under the resolved project."""
    org_id, user_id = org_user
    event = await process_ingest_event(
        filename="2026-03-10_AutoPrj_AutoExp.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/file.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
    )
    await session.commit()

    assert event.resolved_experiment_id is not None
    result = await session.execute(
        text(f"SELECT is_unclaimed, name FROM experiments WHERE id = {event.resolved_experiment_id}")
    )
    row = result.fetchone()
    assert row.is_unclaimed is True
    assert row.name == "AutoExp"


@pytest.mark.asyncio
async def test_ingest_no_profile_match(client, admin_token, session, org_user, cro_profile):
    """Test ingest with no profile match produces unmatched status."""
    org_id, user_id = org_user
    event = await process_ingest_event(
        filename="totally_random_file.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/totally_random_file.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
    )
    await session.commit()

    assert event.ingest_status == "unmatched"
    assert event.naming_profile_id is None
    assert event.file_id is not None  # File still created


@pytest.mark.asyncio
async def test_ingest_duplicate_detection(client, admin_token, session, org_user, cro_profile):
    """Test that a second file with same MD5 produces duplicate status."""
    org_id, user_id = org_user

    # First ingest
    event1 = await process_ingest_event(
        filename="2026-03-10_Prj1_Exp1.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/file1.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
        content_md5="abc123hash",
    )
    await session.commit()
    assert event1.ingest_status == "cataloged"

    # Second ingest with same MD5
    event2 = await process_ingest_event(
        filename="2026-03-10_Prj1_Exp1.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/file1_copy.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
        content_md5="abc123hash",
    )
    await session.commit()
    assert event2.ingest_status == "duplicate"


@pytest.mark.asyncio
async def test_experiment_transitions_to_fastq_uploaded(client, admin_token, session, org_user, cro_profile):
    """Test that experiment status transitions to fastq_uploaded when first FASTQ is linked."""
    org_id, user_id = org_user
    event = await process_ingest_event(
        filename="2026-03-10_TransPrj_TransExp.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/file.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
    )
    await session.commit()

    result = await session.execute(text(f"SELECT status FROM experiments WHERE id = {event.resolved_experiment_id}"))
    row = result.fetchone()
    assert row.status == "fastq_uploaded"


@pytest.mark.asyncio
async def test_unclaimed_entities_list(client, admin_token, session, org_user, cro_profile):
    """Test that auto-created entities appear in the unclaimed entities list."""
    org_id, user_id = org_user
    await process_ingest_event(
        filename="2026-03-10_UnclaimedPrj_UnclaimedExp.fastq",
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/unclaimed.fastq",
        org_id=org_id,
        db=session,
        user_id=user_id,
    )
    await session.commit()

    unclaimed = await get_unclaimed_entities(org_id, session)
    project_names = [e["name"] for e in unclaimed if e["entity_type"] == "project"]
    experiment_names = [e["name"] for e in unclaimed if e["entity_type"] == "experiment"]
    assert "UnclaimedPrj" in project_names
    assert "UnclaimedExp" in experiment_names
