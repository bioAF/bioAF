"""Tests for git-related database columns on compute_sessions."""

import pytest
from sqlalchemy import text


class TestGitDatabaseColumns:
    @pytest.mark.asyncio
    async def test_compute_sessions_has_git_branch_name(self, session, admin_user):
        """ComputeSession should have git_branch_name column."""
        from app.models.notebook_session import ComputeSession

        cs = ComputeSession(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=4,
            git_branch_name="session/42-alice-2026-03-27",
        )
        session.add(cs)
        await session.flush()

        result = await session.execute(
            text("SELECT git_branch_name FROM compute_sessions WHERE id = :id"),
            {"id": cs.id},
        )
        assert result.scalar() == "session/42-alice-2026-03-27"

    @pytest.mark.asyncio
    async def test_compute_sessions_has_git_commit_hash(self, session, admin_user):
        """ComputeSession should have git_commit_hash column."""
        from app.models.notebook_session import ComputeSession

        cs = ComputeSession(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=4,
            git_commit_hash="abc123def456",
        )
        session.add(cs)
        await session.flush()

        result = await session.execute(
            text("SELECT git_commit_hash FROM compute_sessions WHERE id = :id"),
            {"id": cs.id},
        )
        assert result.scalar() == "abc123def456"
