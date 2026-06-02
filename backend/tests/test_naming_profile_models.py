import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_naming_profile_creation(client, admin_token, session):
    """Test NamingProfile model can be instantiated and persisted."""
    from app.models.naming_profile import NamingProfile

    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()
    result = await session.execute(text("SELECT id FROM users LIMIT 1"))
    user = result.fetchone()

    profile = NamingProfile(
        organization_id=org.id,
        name="Test profile",
        description="A test naming profile",
        delimiter="_",
        strip_extension=True,
        segments_json=[
            {
                "position": 0,
                "identifier": "SMP",
                "field_name": "SampleID",
                "field_type": "number",
                "padding": 2,
                "date_format": None,
                "is_system_chip": True,
            },
        ],
        experiment_template_id=None,
        status="active",
        created_by=user.id,
    )
    session.add(profile)
    await session.flush()
    assert profile.id is not None
    assert profile.status == "active"
    assert profile.delimiter == "_"
    assert profile.strip_extension is True


@pytest.mark.asyncio
async def test_file_parse_result_creation(client, admin_token, session):
    """Test FileParseResult model can be instantiated."""
    from app.models.file_parse_result import FileParseResult
    from app.models.file import File

    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()

    file = File(
        organization_id=org.id,
        gcs_uri="gs://bucket/test.fastq",
        filename="test.fastq",
        file_type="fastq",
    )
    session.add(file)
    await session.flush()

    parse_result = FileParseResult(
        file_id=file.id,
        naming_profile_id=None,
        parsed_segments_json={"date": "2026-03-10", "project_code": "PRJX"},
        match_status="matched",
        auto_linked=True,
    )
    session.add(parse_result)
    await session.flush()
    assert parse_result.id is not None
    assert parse_result.match_status == "matched"
    assert parse_result.auto_linked is True


@pytest.mark.asyncio
async def test_ingest_event_creation(client, admin_token, session):
    """Test IngestEvent model can be instantiated."""
    from app.models.ingest_event import IngestEvent

    event = IngestEvent(
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/test.fastq",
        ingest_status="cataloged",
        parsed_project_code="PRJX",
        parsed_experiment_code="EXP001",
        auto_created_entities={"projects": [], "experiments": []},
    )
    session.add(event)
    await session.flush()
    assert event.id is not None
    assert event.ingest_status == "cataloged"


@pytest.mark.asyncio
async def test_project_is_unclaimed_defaults_false(client, admin_token, session):
    """Test that is_unclaimed defaults to false on Project."""
    from app.models.project import Project

    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()

    project = Project(organization_id=org.id, name="Test Project")
    session.add(project)
    await session.flush()
    assert project.is_unclaimed is False


@pytest.mark.asyncio
async def test_experiment_is_unclaimed_defaults_false(client, admin_token, session):
    """Test that is_unclaimed defaults to false on Experiment."""
    from app.models.experiment import Experiment

    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()

    experiment = Experiment(organization_id=org.id, name="Test Experiment", status="registered")
    session.add(experiment)
    await session.flush()
    assert experiment.is_unclaimed is False


@pytest.mark.asyncio
async def test_sample_is_unclaimed_defaults_false(client, admin_token, session):
    """Test that is_unclaimed defaults to false on Sample."""
    from app.models.experiment import Experiment

    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()

    experiment = Experiment(organization_id=org.id, name="Temp Exp", status="registered")
    session.add(experiment)
    await session.flush()

    from app.models.sample import Sample

    sample = Sample(experiment_id=experiment.id, status="registered")
    session.add(sample)
    await session.flush()
    assert sample.is_unclaimed is False


@pytest.mark.asyncio
async def test_file_ingest_source_defaults_manual(client, admin_token, session):
    """Test that ingest_source defaults to manual on File."""
    from app.models.file import File

    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()

    file = File(
        organization_id=org.id,
        gcs_uri="gs://bucket/test2.fastq",
        filename="test2.fastq",
        file_type="fastq",
    )
    session.add(file)
    await session.flush()
    # Check from DB to verify server default
    row = await session.execute(text(f"SELECT ingest_source FROM files WHERE id = {file.id}"))
    assert row.fetchone().ingest_source == "manual"
