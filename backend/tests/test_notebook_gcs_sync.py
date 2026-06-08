"""Tests for the K8s notebook adapter's gsutil sync helpers + pod-exec sync.

BAL rework, Phase 5: the gsutil command builders and gsutil-ls parser moved out
of app.services into the notebook adapter package (draining the adapter->service
inversion), and the pod-exec home-dir sync became a NotebookProvider method
(draining the kubernetes SDK import from app.services).
"""

import pytest
from unittest.mock import MagicMock, patch

from app.adapters.notebooks.gcs_sync import (
    generate_sync_in_command,
    generate_sync_out_command,
    parse_gsutil_ls_output,
)


def test_generate_sync_in_command():
    cmd = generate_sync_in_command(
        gcs_prefix="gs://bioaf-working/notebooks/42/",
        local_dir="/home/jovyan",
    )
    assert isinstance(cmd, list)
    joined = " ".join(cmd)
    assert "gsutil" in joined and "rsync" in joined
    assert "gs://bioaf-working/notebooks/42/" in joined
    assert "/home/jovyan" in joined


def test_generate_sync_out_command():
    cmd = generate_sync_out_command(
        local_dir="/home/jovyan",
        gcs_prefix="gs://bioaf-working/notebooks/42/",
    )
    joined = " ".join(cmd)
    assert "gsutil" in joined and "rsync" in joined
    assert "/home/jovyan" in joined
    assert "gs://bioaf-working/notebooks/42/" in joined


def test_parse_gsutil_ls_output():
    raw = (
        "  1234  2026-04-04T12:00:00Z  gs://bucket/out/result.h5ad\n"
        "  10    2026-04-04T12:00:00Z  gs://bucket/out/.bash_history\n"
        "TOTAL: 2 objects, 1244 bytes\n"
    )
    files = parse_gsutil_ls_output(raw)
    assert files == [{"gcs_uri": "gs://bucket/out/result.h5ad", "size_bytes": 1234, "filename": "result.h5ad"}]


@pytest.mark.asyncio
async def test_sync_session_storage_execs_in_pod():
    """The pod-exec sync runs gsutil rsync inside the named pod via the adapter."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    adapter._mode = "k8s"
    mock_core = MagicMock()
    adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
    mock_stream = MagicMock(return_value="sync complete")

    with patch("kubernetes.stream.stream", mock_stream):
        await adapter.sync_session_storage(
            session_id=42,
            pod_name="bioaf-notebook-99",
            namespace="bioaf-notebooks",
            gcs_prefix="gs://bioaf-working/notebooks/42/",
        )

    mock_stream.assert_called_once()
    call_str = str(mock_stream.call_args)
    assert "bioaf-notebook-99" in call_str
    assert "bioaf-notebooks" in call_str


@pytest.mark.asyncio
async def test_sync_session_storage_local_mode_is_noop():
    """In local mode there is no pod to exec into; the sync is skipped."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    adapter._mode = "local"
    adapter._get_k8s_core_client = MagicMock(side_effect=AssertionError("should not touch K8s"))

    await adapter.sync_session_storage(session_id=1, pod_name="p", namespace="n", gcs_prefix="gs://x/")


@pytest.mark.asyncio
async def test_base_sync_session_storage_is_noop():
    """A backend without live exec inherits the no-op default (no raise)."""
    from app.adapters.compute.slurm import SlurmComputeProvider  # noqa: F401
    from app.adapters.notebooks.slurm import SlurmNotebookProvider

    provider = SlurmNotebookProvider()
    assert await provider.sync_session_storage("123", pod_name="p", gcs_prefix="gs://x/") is None  # type: ignore[func-returns-value]
