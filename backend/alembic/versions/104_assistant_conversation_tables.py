"""Assistant conversation persistence tables (ai_pipeline_run Phase 1, spec-02).

Revision ID: 104
Revises: 103
Create Date: 2026-06-24

Additive only. Creates the four entities that back the conversational assistant:
assistant_conversations, assistant_messages, assistant_tool_invocations,
assistant_action_plans. Mirrors app/models/assistant.py (the source of truth the test
suite builds via create_all).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "104"
down_revision = "103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid", name="uq_assistant_conversations_uuid"),
    )
    op.create_index(
        "ix_assistant_conversations_organization_id",
        "assistant_conversations",
        ["organization_id"],
    )

    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_invocation_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_messages_conversation_id",
        "assistant_messages",
        ["conversation_id"],
    )

    op.create_table(
        "assistant_tool_invocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("consequence_class", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="proposed"),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["assistant_messages.id"]),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_tool_invocations_conversation_id",
        "assistant_tool_invocations",
        ["conversation_id"],
    )

    op.create_table(
        "assistant_action_plans",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("steps_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversations.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_action_plans_conversation_id",
        "assistant_action_plans",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_action_plans_conversation_id", table_name="assistant_action_plans")
    op.drop_table("assistant_action_plans")
    op.drop_index("ix_assistant_tool_invocations_conversation_id", table_name="assistant_tool_invocations")
    op.drop_table("assistant_tool_invocations")
    op.drop_index("ix_assistant_messages_conversation_id", table_name="assistant_messages")
    op.drop_table("assistant_messages")
    op.drop_index("ix_assistant_conversations_organization_id", table_name="assistant_conversations")
    op.drop_table("assistant_conversations")
