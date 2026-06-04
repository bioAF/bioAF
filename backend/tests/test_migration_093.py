"""Tests for migration 093 - default interactive pool machine type to e2-standard-8.

Migration 055 set the precedent: when the recommended default for a cluster
config key shifts, a migration updates *only* rows whose current value is the
prior default. Users who explicitly chose a different machine type keep their
selection.

Migration 093 does the same swap for k8s_interactive_machine_type:
n2-standard-4 -> e2-standard-8. The n2 family has repeatedly stocked out in
us-central1-a for interactive pool scale-ups (see
local/gke-capacity/gke-capacity-issue.md). e2-standard-8 spills onto any
available host generation, so it almost never stocks out.
"""

from pathlib import Path

import pytest
from sqlalchemy import text


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_FILE = MIGRATIONS_DIR / "093_default_interactive_machine_to_e2_standard_8.py"


def test_migration_file_exists():
    assert MIGRATION_FILE.exists(), (
        f"Expected migration file at {MIGRATION_FILE}. Run TDD: add the migration that "
        "updates k8s_interactive_machine_type from n2-standard-4 to e2-standard-8."
    )


def test_migration_chains_to_092():
    content = MIGRATION_FILE.read_text()
    assert 'revision = "093"' in content
    assert 'down_revision = "092"' in content, "migration 093 must chain to 092 so alembic upgrade head picks it up"


def test_migration_only_updates_rows_that_still_hold_the_prior_default():
    """Mirror migration 055's pattern: UPDATE ... WHERE value = '<prior default>'.

    Users who already picked something other than n2-standard-4 must keep
    their selection; this is a default-bump migration, not a forced rewrite.
    """
    content = MIGRATION_FILE.read_text()
    assert "k8s_interactive_machine_type" in content
    assert "e2-standard-8" in content
    assert "n2-standard-4" in content, "upgrade() must scope its UPDATE to rows whose value is still n2-standard-4"
    # The WHERE clause must include both the key and the prior-default guard;
    # a bare UPDATE on the key would overwrite user-customized values.
    assert "WHERE key = 'k8s_interactive_machine_type' AND value = 'n2-standard-4'" in content, (
        "upgrade() must guard on both the key and the prior default value"
    )


def test_downgrade_restores_n2_standard_4_only_when_value_is_e2_standard_8():
    content = MIGRATION_FILE.read_text()
    assert "WHERE key = 'k8s_interactive_machine_type' AND value = 'e2-standard-8'" in content, (
        "downgrade() must scope its UPDATE so users who set their own value stay put"
    )


@pytest.mark.asyncio
async def test_migration_sql_flips_only_default_rows(session):
    """Executing the migration's UPDATE statement directly against the test
    DB must flip rows currently set to n2-standard-4 and leave others alone."""
    # Seed three rows: one at the prior default, one user-customized, one
    # unrelated key. Then run the same SQL the migration runs.
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('k8s_interactive_machine_type', 'n2-standard-4'), "
            "('k8s_interactive_max_nodes', '5') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    await session.execute(
        text(
            "UPDATE platform_config "
            "SET value = 'e2-standard-8', updated_at = now() "
            "WHERE key = 'k8s_interactive_machine_type' AND value = 'n2-standard-4'"
        )
    )
    await session.commit()

    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'k8s_interactive_machine_type'"))
    ).fetchone()
    assert row is not None
    assert row[0] == "e2-standard-8"

    # Unrelated row must be untouched.
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'k8s_interactive_max_nodes'"))
    ).fetchone()
    assert row is not None
    assert row[0] == "5"


@pytest.mark.asyncio
async def test_migration_sql_leaves_user_customized_rows_alone(session):
    """A user who picked n2-highmem-16 keeps n2-highmem-16 after the migration."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('k8s_interactive_machine_type', 'n2-highmem-16') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    await session.execute(
        text(
            "UPDATE platform_config "
            "SET value = 'e2-standard-8', updated_at = now() "
            "WHERE key = 'k8s_interactive_machine_type' AND value = 'n2-standard-4'"
        )
    )
    await session.commit()

    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'k8s_interactive_machine_type'"))
    ).fetchone()
    assert row is not None
    assert row[0] == "n2-highmem-16"
