"""Lab Knowledge: notes (comments) on lab documents (ADR-059).

Revision ID: 098
Revises: 097
Create Date: 2026-06-05

Adds the ``lab_document_notes`` table so users can annotate a lab document, the
same way comments work on literature papers. Org-scoped and soft-deletable. No
new RBAC resource: reads/writes are gated on the existing ``lab_documents``
view/manage permissions at the API layer.
"""

import sqlalchemy as sa
from alembic import op

revision = "098"
down_revision = "097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_document_notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("lab_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("idx_lab_document_notes_doc", "lab_document_notes", ["document_id"])
    op.create_index("idx_lab_document_notes_org", "lab_document_notes", ["organization_id"])


def downgrade() -> None:
    op.drop_index("idx_lab_document_notes_org", table_name="lab_document_notes")
    op.drop_index("idx_lab_document_notes_doc", table_name="lab_document_notes")
    op.drop_table("lab_document_notes")
