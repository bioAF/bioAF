"""Add tfvars_json column to terraform_runs.

Stores the exact variable inputs used for each Terraform run so
deploys are reproducible and auditable.

Revision ID: 064
Revises: 063
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "064"
down_revision = "063"


def upgrade() -> None:
    op.add_column("terraform_runs", sa.Column("tfvars_json", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("terraform_runs", "tfvars_json")
