"""ValidationStudy data_run_id + analysis_run_id (lit_validation A2 back-half linkage).

Revision ID: 110
Revises: 109
Create Date: 2026-07-03

Additive only. Adds the two pipeline-run links the orchestration driver sets as it advances a study
through the execution back-half: the nf-core/fetchngs data-acquisition run and the
nf-core/rnaseq|scrnaseq analysis run. Plain nullable integers (no FK), mirroring
app/models/validation_study.py, so the spine stays decoupled from pipeline_runs ordering.
"""

import sqlalchemy as sa
from alembic import op

revision = "110"
down_revision = "109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("validation_studies", sa.Column("data_run_id", sa.Integer(), nullable=True))
    op.add_column("validation_studies", sa.Column("analysis_run_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("validation_studies", "analysis_run_id")
    op.drop_column("validation_studies", "data_run_id")
