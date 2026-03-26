"""Add pipeline_run_input_files junction table for input file lineage.

Replaces JSONB-only tracking of pipeline input files with a proper
relational junction table. Existing input_files_json rows are
backfilled on a best-effort basis.

Revision ID: 046
Revises: 045
Create Date: 2026-03-25
"""

import json
import logging

from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045"

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.create_table(
        "pipeline_run_input_files",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "pipeline_run_id",
            sa.Integer,
            sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            sa.Integer,
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(30), nullable=False, server_default="primary_input"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("pipeline_run_id", "file_id", name="uq_pipeline_run_input_file"),
    )
    op.create_index("ix_prif_pipeline_run_id", "pipeline_run_input_files", ["pipeline_run_id"])
    op.create_index("ix_prif_file_id", "pipeline_run_input_files", ["file_id"])

    # Best-effort backfill from existing input_files_json
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, input_files_json FROM pipeline_runs WHERE input_files_json IS NOT NULL"))
    inserted = 0
    skipped = 0
    for run_id, raw_json in rows:
        try:
            data = raw_json if isinstance(raw_json, list) else json.loads(raw_json)
            if not isinstance(data, list):
                skipped += 1
                continue

            file_ids: list[int] = []
            for item in data:
                if isinstance(item, int):
                    file_ids.append(item)
                elif isinstance(item, dict) and "file_id" in item:
                    fid = item["file_id"]
                    if isinstance(fid, int):
                        file_ids.append(fid)
                    elif isinstance(fid, str) and fid.isdigit():
                        file_ids.append(int(fid))

            for fid in file_ids:
                try:
                    conn.execute(
                        sa.text(
                            "INSERT INTO pipeline_run_input_files (pipeline_run_id, file_id) "
                            "VALUES (:rid, :fid) ON CONFLICT DO NOTHING"
                        ),
                        {"rid": run_id, "fid": fid},
                    )
                    inserted += 1
                except Exception:
                    skipped += 1

        except Exception:
            skipped += 1

    if inserted or skipped:
        logger.info(
            "Backfilled %d junction rows from input_files_json (%d skipped)",
            inserted,
            skipped,
        )


def downgrade() -> None:
    op.drop_index("ix_prif_file_id", table_name="pipeline_run_input_files")
    op.drop_index("ix_prif_pipeline_run_id", table_name="pipeline_run_input_files")
    op.drop_table("pipeline_run_input_files")
