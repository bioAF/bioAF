"""Add session_credentials table and last_login to users.

Revision ID: 035
Revises: 034
Create Date: 2026-03-22
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "session_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
    )
    op.create_index("ix_session_credentials_user_id", "session_credentials", ["user_id"], unique=True)
    op.create_index(
        "ix_session_credentials_org_username",
        "session_credentials",
        ["organization_id", "username"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_session_credentials_org_username", table_name="session_credentials")
    op.drop_index("ix_session_credentials_user_id", table_name="session_credentials")
    op.drop_table("session_credentials")
    op.drop_column("users", "last_login")
