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

import pytest
from sqlalchemy import text


# The file-exists, revision-string and WHERE-clause-substring tests that used to
# sit here were removed. The first two are covered by
# test_migrations_apply.py, which runs the whole chain instead of reading it,
# and the last two asserted the SQL text of the same UPDATE that the two
# database-backed tests below already execute and check the results of. Greping
# a migration for its own WHERE clause restates the implementation and passes
# even if the statement never runs.


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
