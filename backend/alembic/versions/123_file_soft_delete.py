"""files.deleted_at: deletion removes a file from view, not from the catalogue.

Revision ID: 123
Revises: 122
Create Date: 2026-08-19

Decision 5 of 2026-08-19: soft delete. The row and its UUID are never removed.

It is what data catalogues and LIMS do, and bioAF already leaned this way:
`storage_deleted` separates "the bytes are gone from storage" from "the record is
gone", so storage can be freed while the catalogue entry survives and an exported
dataset or a published provenance record never dangles.

It is also what makes migration 120 mean anything. A UUID that stops resolving
the moment somebody tidies up is not a catalogue number, and the framing for the
whole scheme was "It's an ISBN. I didn't write the book, but need to catalog it
for later recall."

Two nullable columns and no backfill: NULL means "not deleted", which is what
every existing row is. A partial index over the live rows keeps the filter that
now sits on every file listing off the deleted ones.
"""

import sqlalchemy as sa
from alembic import op

revision = "123"
down_revision = "122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("files", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("files", sa.Column("deleted_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_files_deleted_by_user", "files", "users", ["deleted_by_user_id"], ["id"])
    # Every listing now filters on this, and all but a handful of rows satisfy
    # it, so the index covers the live set rather than the whole table.
    op.create_index(
        "ix_files_live",
        "files",
        ["organization_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_files_live", table_name="files")
    op.drop_constraint("fk_files_deleted_by_user", "files", type_="foreignkey")
    op.drop_column("files", "deleted_by_user_id")
    op.drop_column("files", "deleted_at")
