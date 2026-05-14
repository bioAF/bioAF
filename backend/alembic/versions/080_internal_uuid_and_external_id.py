"""Internal UUIDs, samples.external_id, and per-org code counters.

Revision ID: 080
Revises: 079
Create Date: 2026-05-14

Additive only:
- projects.uuid, experiments.uuid, samples.uuid (NOT NULL, gen_random_uuid())
- samples.external_id (copied from sample_id_external; sample_id_external is now dead)
- partial unique index on (samples.experiment_id, external_id)
- org_code_counters table: monotonic odometer per (org, kind) for new code format
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() is built-in on Postgres 13+, no extension needed.

    for table in ("projects", "experiments", "samples"):
        op.add_column(
            table,
            sa.Column(
                "uuid",
                postgresql.UUID(as_uuid=True),
                nullable=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
        )
        op.execute(f"UPDATE {table} SET uuid = gen_random_uuid() WHERE uuid IS NULL")
        op.alter_column(table, "uuid", nullable=False)
        op.create_index(f"uq_{table}_uuid", table, ["uuid"], unique=True)

    op.add_column("samples", sa.Column("external_id", sa.String(255), nullable=True))
    op.execute("UPDATE samples SET external_id = sample_id_external WHERE sample_id_external IS NOT NULL")
    op.create_index(
        "uq_samples_experiment_external_id",
        "samples",
        ["experiment_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "org_code_counters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "kind", name="uq_org_code_counters_org_kind"),
    )


def downgrade() -> None:
    op.drop_table("org_code_counters")
    op.drop_index("uq_samples_experiment_external_id", table_name="samples")
    op.drop_column("samples", "external_id")
    for table in ("samples", "experiments", "projects"):
        op.drop_index(f"uq_{table}_uuid", table_name=table)
        op.drop_column(table, "uuid")
