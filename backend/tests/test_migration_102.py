"""Tests for migration 102 - rename gcs_uri -> storage_uri (BAL Phase 4)."""

from pathlib import Path

MIGRATION_FILE = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "102_rename_gcs_uri_to_storage_uri.py"
)


def test_migration_file_exists():
    assert MIGRATION_FILE.exists()


def test_migration_chains_to_101():
    content = MIGRATION_FILE.read_text()
    assert 'revision = "102"' in content
    assert 'down_revision = "101"' in content


def test_renames_on_every_table_with_the_column():
    content = MIGRATION_FILE.read_text()
    for table in ("files", "lab_documents", "lab_document_versions", "reference_dataset_files"):
        assert table in content
    # upgrade renames gcs_uri -> storage_uri; downgrade is the inverse (reversible).
    assert 'new_column_name="storage_uri"' in content
    assert 'new_column_name="gcs_uri"' in content
