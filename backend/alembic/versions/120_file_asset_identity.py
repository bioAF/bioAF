"""files.uuid: a catalogue identity for every asset bioAF tracks.

Revision ID: 120
Revises: 119
Create Date: 2026-08-19

Projects, experiments and samples have carried a `uuid` since migration 080.
`File` did not, and it is the one bioAF most needs: a file is what gets exported,
published, and pointed at from a pipeline run, and the only identity it had was a
row id and a storage path.

A path cannot be an identity. Reassigning a file between experiments physically
moves the object and rewrites the row, so the integer id survives while
`storage_uri` changes. Anything keyed on the path was keyed on something mutable.

Added ALONGSIDE the integer key, never replacing it, which is the split the other
three tables already use: the integer is a storage detail that never leaves the
system, and the UUID is the contract with the outside world and is never used for
joins.

v4 today. v7 from the PostgreSQL 18 upgrade onward, which needs no migration
because both are 128-bit values in the same column and nothing in the schema
distinguishes them; the change is a one-line default flip, old rows keep v4, and
no identifier is ever reissued. The discipline that keeps that door open: never
treat UUID ordering as creation order, because a mixed v4/v7 set breaks it
silently. Use created_at.

Follows migration 080's recipe exactly. `gen_random_uuid()` is VOLATILE, so
ADD COLUMN with it as a default rewrites the table under an exclusive lock: at
the demo's ~3,400 rows that is milliseconds. At millions the recipe becomes add
nullable with no default, backfill in batches, CREATE UNIQUE INDEX CONCURRENTLY,
then set NOT NULL.

Nothing emits this identifier into a samplesheet yet. That also requires the
layer mapping every identifier back to PROJECT | EXPERIMENT | SAMPLE | FILE on
every human-readable surface, and emitting without it would push opaque
identifiers at scientists, which design 05 section 3 forbids.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    # Belt and braces: the server default covers the rewrite, and this covers any
    # row an older path inserted between the two statements.
    op.execute("UPDATE files SET uuid = gen_random_uuid() WHERE uuid IS NULL")
    op.alter_column("files", "uuid", nullable=False)
    op.create_index("uq_files_uuid", "files", ["uuid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_files_uuid", table_name="files")
    op.drop_column("files", "uuid")
