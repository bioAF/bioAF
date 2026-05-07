"""Tests for the bootstrap_metadata service.

The installer writes bioaf_bootstrap_sa_email to VM instance metadata.
On first startup the backend reads it and persists to platform_config.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.services.bootstrap_metadata import persist_bootstrap_sa_from_metadata


@pytest.mark.asyncio
async def test_persist_skipped_when_metadata_unreachable(session):
    """No platform_config row written when the metadata server is unreachable."""
    with patch(
        "app.services.bootstrap_metadata._read_metadata_attribute",
        return_value=None,
    ):
        ok = await persist_bootstrap_sa_from_metadata(session)
    assert ok is False
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='gcp_bootstrap_sa_email'"))).scalar()
    assert row is None


@pytest.mark.asyncio
async def test_persist_writes_value_from_metadata(session):
    """Reads metadata and upserts platform_config when the attribute is set."""
    with patch(
        "app.services.bootstrap_metadata._read_metadata_attribute",
        return_value="bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    ):
        ok = await persist_bootstrap_sa_from_metadata(session)
    assert ok is True
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='gcp_bootstrap_sa_email'"))).scalar()
    assert row == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_persist_does_not_overwrite_existing_value(session):
    """If the row already exists, leave it untouched (admin override safe)."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('gcp_bootstrap_sa_email', 'manual-override@my-project.iam.gserviceaccount.com')"
        )
    )
    await session.commit()

    with patch(
        "app.services.bootstrap_metadata._read_metadata_attribute",
        return_value="bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    ) as mock_reader:
        ok = await persist_bootstrap_sa_from_metadata(session)

    # Reader is not invoked because the row already exists.
    mock_reader.assert_not_called()
    assert ok is True
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='gcp_bootstrap_sa_email'"))).scalar()
    assert row == "manual-override@my-project.iam.gserviceaccount.com"
