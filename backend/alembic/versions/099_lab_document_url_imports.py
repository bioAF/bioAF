"""Lab Knowledge: server-side document URL imports (ADR-061).

Revision ID: 099
Revises: 098
Create Date: 2026-06-05

Adds ``lab_document_url_imports`` so an "import from URL" request can persist the
user-supplied URL and run the fetch in a background task that reads it back from
this row. Decoupling the request from the outbound fetch matches the Reference
Data importer and the glossary scan job, and keeps the user URL from flowing
directly into an outbound request in the request handler.
"""

import sqlalchemy as sa
from alembic import op

revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_document_url_imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("initiated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tag_ids", sa.ARRAY(sa.Integer()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("lab_documents.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'failed')",
            name="ck_lab_document_url_imports_status",
        ),
    )
    op.create_index("idx_lab_document_url_imports_org", "lab_document_url_imports", ["organization_id"])


def downgrade() -> None:
    op.drop_index("idx_lab_document_url_imports_org", table_name="lab_document_url_imports")
    op.drop_table("lab_document_url_imports")
