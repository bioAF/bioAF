"""LLM section-builder: agent_review_prompts + new prompt-snapshot columns.

Revision ID: 082
Revises: 081
Create Date: 2026-05-17

Additive only:
- agent_review_prompts (org-wide named custom prompts).
- agent_review_jobs / agent_reviews gain prompt_text, prompt_sections,
  prompt_source, prompt_custom_id columns. All nullable so historical rows
  remain valid.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_review_prompts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_review_prompts_organization_id",
        "agent_review_prompts",
        ["organization_id"],
    )
    op.create_index(
        "uq_agent_review_prompts_org_name",
        "agent_review_prompts",
        ["organization_id", "name"],
        unique=True,
    )

    for table in ("agent_review_jobs", "agent_reviews"):
        op.add_column(table, sa.Column("prompt_text", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "prompt_sections",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
        op.add_column(table, sa.Column("prompt_source", sa.String(32), nullable=True))
        op.add_column(table, sa.Column("prompt_custom_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    for table in ("agent_reviews", "agent_review_jobs"):
        op.drop_column(table, "prompt_custom_id")
        op.drop_column(table, "prompt_source")
        op.drop_column(table, "prompt_sections")
        op.drop_column(table, "prompt_text")

    op.drop_index("uq_agent_review_prompts_org_name", table_name="agent_review_prompts")
    op.drop_index("ix_agent_review_prompts_organization_id", table_name="agent_review_prompts")
    op.drop_table("agent_review_prompts")
