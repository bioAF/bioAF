"""Add gcp_service_account_email to platform_config.

Revision ID: 029
Revises: 028
Create Date: 2026-03-12

Adds an optional service account email field so users can specify
which SA to impersonate when using VM default credentials.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platform_config (key, value) VALUES
            ('gcp_service_account_email', '')
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_config
        WHERE key = 'gcp_service_account_email'
        """
    )
