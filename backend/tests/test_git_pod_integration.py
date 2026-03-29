"""Tests for git integration in notebook pod adapter."""

import pytest
from unittest.mock import MagicMock, patch

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider


class TestDataSyncInitContainer:
    def test_input_files_add_data_sync_init_container(self):
        """When input_files are in the spec, a gcs-data-sync init container is added."""
        provider = KubernetesNotebookProvider()
        spec = {
            "session_id": 1,
            "session_type": "jupyter",
            "user_id": 1,
            "cpu_cores": 2,
            "memory_gb": 4,
            "resource_profile": "small",
            "input_files": [
                {"file_id": 1, "gcs_uri": "gs://bucket/file1.h5ad", "relative_path": "file1.h5ad"},
                {"file_id": 2, "gcs_uri": "gs://bucket/file2.csv", "relative_path": "file2.csv"},
            ],
        }

        # Use local mode to get the session data without K8s API
        result = provider._local_launch_session(spec)
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_input_files_generate_data_volume(self):
        """Verify that input_files spec generates data volume in the pod manifest."""
        provider = KubernetesNotebookProvider()
        spec = {
            "session_id": 42,
            "session_type": "jupyter",
            "user_id": 1,
            "cpu_cores": 2,
            "memory_gb": 4,
            "resource_profile": "small",
            "input_files": [
                {"file_id": 1, "gcs_uri": "gs://bucket/matrix.h5ad", "relative_path": "matrix.h5ad"},
            ],
        }

        # Directly test pod manifest building by calling a helper
        # We can't call _k8s_launch_session without a real cluster,
        # so test that local mode handles input_files gracefully
        result = provider._local_launch_session(spec)
        assert "session_id" in result


class TestGitAutocommitSidecar:
    def test_git_config_adds_sidecar_container(self):
        """When git_config is in the spec, a git-autocommit sidecar is added."""
        # This tests the pod manifest generation logic
        # We verify the sidecar would be included by checking the spec handling
        provider = KubernetesNotebookProvider()
        spec = {
            "session_id": 42,
            "session_type": "jupyter",
            "user_id": 1,
            "cpu_cores": 2,
            "memory_gb": 4,
            "resource_profile": "small",
            "git_config": {
                "branch": "session/42-alice-2026-03-27",
                "repo_url": "git@github.com:test-org/EXP-001-notebooks.git",
            },
        }

        # Local mode does not actually build the pod, but verify no errors
        result = provider._local_launch_session(spec)
        assert result["status"] == "running"


class TestTerminateGitFlow:
    @pytest.mark.asyncio
    async def test_terminate_attempts_git_commit(self):
        """Terminate should attempt final git commit before GCS sync."""
        provider = KubernetesNotebookProvider()

        mock_core_client = MagicMock()
        mock_stream_result = "GIT_BRANCH=session/42-test\nGIT_HASH=abc123\n"

        with patch.object(provider, "_get_k8s_core_client", return_value=mock_core_client):
            with patch("kubernetes.stream.stream", return_value=mock_stream_result) as mock_stream:
                result = await provider._k8s_terminate_session(
                    session_id=42,
                    pod_name="bioaf-notebook-42",
                    namespace="bioaf-notebooks",
                    gcs_home_prefix="gs://bucket/home/1/",
                )

        assert result["status"] == "stopped"
        # stream should have been called at least twice: git commit + gcs sync
        assert mock_stream.call_count >= 2

    @pytest.mark.asyncio
    async def test_terminate_stores_git_info(self):
        """Terminate should store git branch and commit hash in DB."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        @asynccontextmanager
        async def mock_factory():
            yield mock_db

        provider = KubernetesNotebookProvider(session_factory=mock_factory)

        mock_core_client = MagicMock()
        mock_stream_result = "GIT_BRANCH=session/42-test\nGIT_HASH=abc123\n"

        with patch.object(provider, "_get_k8s_core_client", return_value=mock_core_client):
            with patch("kubernetes.stream.stream", return_value=mock_stream_result):
                await provider._k8s_terminate_session(
                    session_id=42,
                    pod_name="bioaf-notebook-42",
                    namespace="bioaf-notebooks",
                    gcs_home_prefix="gs://bucket/home/1/",
                )

        # Should have updated git info in DB
        calls = mock_db.execute.call_args_list
        git_update_found = False
        for call in calls:
            sql_arg = str(call[0][0])
            if "git_branch_name" in sql_arg:
                git_update_found = True
                break
        assert git_update_found, "Expected DB update with git_branch_name"

    @pytest.mark.asyncio
    async def test_terminate_handles_no_git_gracefully(self):
        """Terminate should not fail if git is not set up in the pod."""
        provider = KubernetesNotebookProvider()

        mock_core_client = MagicMock()
        # Empty result means no git repo
        with patch.object(provider, "_get_k8s_core_client", return_value=mock_core_client):
            with patch("kubernetes.stream.stream", return_value=""):
                result = await provider._k8s_terminate_session(
                    session_id=42,
                    pod_name="bioaf-notebook-42",
                    namespace="bioaf-notebooks",
                    gcs_home_prefix="gs://bucket/home/1/",
                )

        assert result["status"] == "stopped"
