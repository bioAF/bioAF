"""File external IDs and durable pipeline-output source identity.

Revision ID: 134
Revises: 133
Create Date: 2026-11-15

- files.external_id: nullable caller-supplied identifier, no backfill. Unique per organization when
  set, soft-deleted rows included so a retired identifier is never reassigned.
- files.source_storage_uri: where registration first saw a pipeline output. Unlike storage_uri it
  never changes when the canonical bytes move. Unique per (organization, source pipeline run) when
  set, tombstones included, so an ordinary rescan cannot register the same output twice.

The backfill sets a source identity only from unambiguous evidence: a pipeline output whose current
URI is unique in its (organization, run) scope and that was never moved by file organization. Every
member of a duplicate group, and every moved row, stays NULL and is reported by File ID; none is
chosen as a winner, merged, or deleted. The unique index covers only non-NULL identities, so those
legacy groups do not stop the upgrade.
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "134"
down_revision = "133"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# Audit actions that physically move a file's object (app/services/file_organization.py).
_MOVE_ACTIONS = ("assigned_to_experiment", "reassigned_to_experiment", "unlinked_from_experiment")


def upgrade() -> None:
    op.add_column("files", sa.Column("external_id", sa.String(255), nullable=True))
    op.add_column("files", sa.Column("source_storage_uri", sa.String(1000), nullable=True))

    conn = op.get_bind()
    candidates = conn.execute(
        sa.text(
            """
            SELECT f.id, f.organization_id, f.source_pipeline_run_id,
                   COALESCE(f.storage_uri, f.gcs_uri) AS uri,
                   EXISTS (
                       SELECT 1 FROM audit_log a
                       WHERE a.entity_type = 'file' AND a.entity_id = f.id AND a.action = ANY(:moves)
                   ) AS moved
            FROM files f
            WHERE f.source_type = 'pipeline_output'
              AND f.source_pipeline_run_id IS NOT NULL
              AND COALESCE(f.storage_uri, f.gcs_uri) IS NOT NULL
            ORDER BY f.id
            """
        ),
        {"moves": list(_MOVE_ACTIONS)},
    ).all()

    groups: dict[tuple, list] = {}
    for row in candidates:
        groups.setdefault((row.organization_id, row.source_pipeline_run_id, row.uri), []).append(row)

    duplicates: list[int] = []
    moved: list[int] = []
    for (_, _, uri), rows in groups.items():
        if len(rows) > 1:
            duplicates.extend(r.id for r in rows)
            continue
        row = rows[0]
        if row.moved:
            moved.append(row.id)
            continue
        conn.execute(sa.text("UPDATE files SET source_storage_uri = :u WHERE id = :i"), {"u": uri, "i": row.id})

    if duplicates:
        logger.warning(
            "134: pipeline outputs sharing one source URI in a run keep source_storage_uri NULL until "
            "reconciled (File IDs: %s)",
            ", ".join(str(i) for i in sorted(duplicates)),
        )
    if moved:
        logger.warning(
            "134: pipeline outputs moved after registration have no provable origin and keep "
            "source_storage_uri NULL (File IDs: %s)",
            ", ".join(str(i) for i in sorted(moved)),
        )

    op.create_index(
        "uq_files_org_external_id",
        "files",
        ["organization_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_files_pipeline_source_identity",
        "files",
        ["organization_id", "source_pipeline_run_id", "source_storage_uri"],
        unique=True,
        postgresql_where=sa.text("source_storage_uri IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_files_pipeline_source_identity", table_name="files")
    op.drop_index("uq_files_org_external_id", table_name="files")
    op.drop_column("files", "source_storage_uri")
    op.drop_column("files", "external_id")
