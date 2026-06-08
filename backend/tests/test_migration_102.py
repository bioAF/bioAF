"""Tests for migration 102 - add storage_uri (BAL Phase 4 expand phase)."""

from pathlib import Path

MIGRATION_FILE = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "102_add_storage_uri.py"


def test_migration_file_exists():
    assert MIGRATION_FILE.exists()


def test_migration_chains_to_101():
    content = MIGRATION_FILE.read_text()
    assert 'revision = "102"' in content
    assert 'down_revision = "101"' in content


def test_adds_and_backfills_storage_uri_on_every_table():
    content = MIGRATION_FILE.read_text()
    for table in ("files", "lab_documents", "lab_document_versions", "reference_dataset_files"):
        assert table in content
    # additive column + backfill from gcs_uri; downgrade drops it (reversible).
    assert "add_column" in content
    assert "SET storage_uri = gcs_uri" in content
    assert "drop_column" in content
