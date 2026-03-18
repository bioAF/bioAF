"""Tests that pipeline report and logs use the real k8s_job_name.

The report endpoint was looking for a job called "report-{run_id}" which
does not exist. For K8s runs, both the report and logs tab should read
from the actual k8s_job_name stored on the pipeline_run record.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.pipeline_monitor_service import PipelineMonitorService


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


class TestRunReport:
    @pytest.mark.asyncio
    async def test_report_uses_k8s_job_name_not_report_prefix(self, mock_session):
        """get_run_report should use k8s_job_name, not 'report-{run_id}'."""
        # Mock the DB query to return k8s_job_name
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "bioaf-pipeline-11"

        # get_run_report does two queries: first work_dir, then we need k8s_job_name
        # Let's check what it actually queries
        mock_session.execute.return_value = mock_result

        mock_adapter = MagicMock()
        mock_adapter.get_job_logs = AsyncMock(return_value="Container 'pipeline': exit_code=1")

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=mock_adapter,
        ):
            await PipelineMonitorService.get_run_report(mock_session, 11)

        # Should NOT have called get_job_logs with "report-11"
        if mock_adapter.get_job_logs.called:
            call_arg = mock_adapter.get_job_logs.call_args[0][0]
            assert call_arg != "report-11", f"Report should use k8s_job_name, not 'report-11'. Got: {call_arg}"

    @pytest.mark.asyncio
    async def test_report_returns_logs_for_k8s_run(self, mock_session):
        """For a K8s run, the report should return the job's container logs."""
        # First query: work_dir (not used for K8s, but checked for existence)
        # Second query: k8s_job_name
        mock_result_1 = MagicMock()
        mock_result_1.scalar_one_or_none.return_value = "/data/work"

        mock_result_2 = MagicMock()
        mock_result_2.scalar_one_or_none.return_value = "bioaf-pipeline-11"

        mock_session.execute.side_effect = [mock_result_1, mock_result_2]

        mock_adapter = MagicMock()
        mock_adapter.get_job_logs = AsyncMock(
            return_value="Pod bioaf-pipeline-11-abc - phase: Failed\nContainer 'pipeline': exit_code=1, reason=Error"
        )

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=mock_adapter,
        ):
            report = await PipelineMonitorService.get_run_report(mock_session, 11)

        assert "exit_code=1" in report
