"""Tests that pipeline report reads the Nextflow HTML report from GCS.

The report endpoint uses get_job_report (which reads from GCS) rather than
get_job_logs (which reads container stdout/stderr).
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
    async def test_report_uses_get_job_report(self, mock_session):
        """get_run_report should call get_job_report, not get_job_logs."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "bioaf-pipeline-11"
        mock_session.execute.return_value = mock_result

        mock_adapter = MagicMock()
        mock_adapter.get_job_report = AsyncMock(return_value="<html>report</html>")

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=mock_adapter,
        ):
            report = await PipelineMonitorService.get_run_report(mock_session, 11)

        mock_adapter.get_job_report.assert_called_once_with("bioaf-pipeline-11")
        assert report == "<html>report</html>"

    @pytest.mark.asyncio
    async def test_report_returns_empty_when_no_k8s_job(self, mock_session):
        """get_run_report returns empty string when no k8s_job_name."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        report = await PipelineMonitorService.get_run_report(mock_session, 99)
        assert report == ""
