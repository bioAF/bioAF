"""llm_feature_model_override: let one feature name its own model.

Revision ID: 130
Revises: 129
Create Date: 2026-09-02

Literature validation and literature review ride on the org's single active provider today, so a lab
that picks a cheap model to keep review affordable silently gets a validation feature that cannot
bind a claim to a metric. The two are independent decisions now.

The override names a provider the org has already configured. No API key is stored here: it stays on
that provider's llm_provider_config row, so a key is rotated in one place and an override can never
hold a stale secret. Unique on (organization_id, feature), so a feature has at most one model.
"""

import sqlalchemy as sa
from alembic import op

revision = "130"
down_revision = "129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_feature_model_override",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_feature_model_override_organization_id", "llm_feature_model_override", ["organization_id"]
    )
    op.create_index(
        "uq_llm_feature_model_override_org_feature",
        "llm_feature_model_override",
        ["organization_id", "feature"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_llm_feature_model_override_org_feature", table_name="llm_feature_model_override")
    op.drop_index("ix_llm_feature_model_override_organization_id", table_name="llm_feature_model_override")
    op.drop_table("llm_feature_model_override")
