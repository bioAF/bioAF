"""Internal UUIDs, samples.external_id, and per-org code counters.

Revision ID: 080
Revises: 079
Create Date: 2026-05-14

Additive only:
- projects.uuid, experiments.uuid, samples.uuid (NOT NULL, gen_random_uuid())
- samples.external_id (copied from sample_id_external, which is retained as the source label)
- partial unique index on (samples.experiment_id, external_id)
- org_code_counters table: monotonic odometer per (org, kind) for new code format

Compatibility correction (plan 9, owner policy 2026-11-07). Below 080 a source label had no
uniqueness guarantee, so an install holding the same label twice in one experiment failed here and
could not upgrade. Such duplicates now get operational IDs before the index is built, and the
transition is recorded in sample_external_id_transitions. Installs without duplicates (every install
already past 080, and every fresh install) get exactly the same result as before.

The generation rule is frozen; never change it, because a retried or later upgrade must assign the
same IDs:

- Labels unique within their experiment are copied unchanged. NULL stays NULL.
- Every member of a duplicate group (same experiment, same label) gets ``<label>~<sample id>``; none
  keeps the bare label, so a lookup by it cannot silently pick one sample.
- The label is shortened so the result fits 255 characters; the ``~<sample id>`` suffix is kept whole.
- If that value already exists in the experiment (an original label or one assigned earlier in this
  pass, samples taken in (experiment, id) order), ``~2``, ``~3``, ... is appended until it does not.

Runs inside the migration transaction: an interruption rolls the whole step back, and the retry
starts again from 079's data. Reads every labelled sample once; one UPDATE per relabelled sample.
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
    create_transitions_table()
    _relabel_duplicate_source_labels(op.get_bind())
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


EXTERNAL_ID_LENGTH = 255


def create_transitions_table() -> None:
    """The visible record of relabelled samples. Also run (IF NOT EXISTS) by 132 for installs past 080."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sample_external_id_transitions (
            id SERIAL PRIMARY KEY,
            sample_id INTEGER NOT NULL UNIQUE REFERENCES samples (id) ON DELETE CASCADE,
            experiment_id INTEGER NOT NULL REFERENCES experiments (id),
            source_label VARCHAR(255) NOT NULL,
            external_id VARCHAR(255) NOT NULL,
            revision VARCHAR(32) NOT NULL DEFAULT '080',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sample_external_id_transitions_label "
        "ON sample_external_id_transitions (experiment_id, source_label)"
    )


def assign_external_ids(rows: list[tuple[int, int, str]]) -> dict[int, str]:
    """Frozen rule (module docstring): sample id -> assigned ID, for duplicate-group members only.

    ``rows`` are ``(sample id, experiment id, label)`` for every labelled sample.
    """
    counts: dict[tuple[int, str], int] = {}
    taken: dict[int, set[str]] = {}
    for _sid, exp, label in rows:
        counts[(exp, label)] = counts.get((exp, label), 0) + 1
        taken.setdefault(exp, set()).add(label)
    assigned: dict[int, str] = {}
    for sid, exp, label in sorted(rows, key=lambda r: (r[1], r[0])):
        if counts[(exp, label)] < 2:
            continue
        attempt = 1
        while True:
            suffix = f"~{sid}" if attempt == 1 else f"~{sid}~{attempt}"
            candidate = label[: EXTERNAL_ID_LENGTH - len(suffix)] + suffix
            if candidate not in taken[exp]:
                break
            attempt += 1
        taken[exp].add(candidate)
        assigned[sid] = candidate
    return assigned


def _relabel_duplicate_source_labels(bind) -> None:
    rows = bind.execute(
        sa.text("SELECT id, experiment_id, sample_id_external FROM samples WHERE sample_id_external IS NOT NULL")
    ).fetchall()
    assigned = assign_external_ids([(r[0], r[1], r[2]) for r in rows])
    labels = {r[0]: (r[1], r[2]) for r in rows}
    for sid, external_id in sorted(assigned.items()):
        experiment_id, label = labels[sid]
        bind.execute(sa.text("UPDATE samples SET external_id = :x WHERE id = :id"), {"x": external_id, "id": sid})
        bind.execute(
            sa.text(
                "INSERT INTO sample_external_id_transitions (sample_id, experiment_id, source_label, external_id) "
                "VALUES (:sid, :exp, :label, :x)"
            ),
            {"sid": sid, "exp": experiment_id, "label": label, "x": external_id},
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sample_external_id_transitions")
    op.drop_table("org_code_counters")
    op.drop_index("uq_samples_experiment_external_id", table_name="samples")
    op.drop_column("samples", "external_id")
    for table in ("samples", "experiments", "projects"):
        op.drop_index(f"uq_{table}_uuid", table_name=table)
        op.drop_column(table, "uuid")
