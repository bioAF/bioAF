"""TDD: ReferenceDataService._create_import_job / _delete_import_job auth path
and Job spec.

The bug history:
- Reference URL import is supposed to submit a GKE Job to bioaf-cluster.
- _create_import_job called kubernetes.config.load_incluster_config() (fails:
  backend runs on the VM, not in a Pod) then load_kube_config() (fails: no
  ~/.kube/config in the backend container), and crashed before submission.
- The Job spec also referenced an importer image and KSA that were never
  built.

These tests pin the corrected contract:
- _create_import_job authenticates to GKE via the same path the compute /
  notebooks / cellxgene adapters use (out-of-cluster client built from
  platform_config), surfaced through the shared compute adapter.
- The Job spec uses the bioAF backend image and runs
  `python -m app.workers.reference_importer` as its command.
- The Job spec sets all env vars the worker's config_from_env reads.
- _delete_import_job uses the same auth path.
- A missing GKE endpoint raises a ValueError whose message triggers a 503
  from the API layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.reference_data_service import ReferenceDataService


def _make_batch_client_mock() -> MagicMock:
    batch = MagicMock(name="BatchV1Api")
    batch.create_namespaced_job = MagicMock()
    batch.delete_namespaced_job = MagicMock()
    return batch


@pytest.fixture
def fake_compute_adapter():
    adapter = MagicMock(name="ComputeAdapter")
    adapter._get_k8s_batch_client = MagicMock(return_value=_make_batch_client_mock())
    return adapter


def _kwargs(**overrides):
    base = dict(
        reference_id=42,
        source_url="https://ftp.example.org/data/gencode.v45.gtf.gz",
        source_md5_url=None,
        gcs_prefix="annotation/gencode/v45/",
        bucket_name="bioaf-references-prod",
        extract="gzip",
        auth_header=None,
        callback_url="http://bioaf.example.com/api/internal/references/42/import-progress",
        internal_token="t0p-secret",
    )
    base.update(overrides)
    return base


def test_create_import_job_uses_compute_adapter_not_load_kube_config(fake_compute_adapter):
    """_create_import_job must NOT touch kubernetes.config.load_kube_config
    (no kubeconfig exists in the backend container). It must build its
    BatchV1Api via the shared compute adapter, which uses out-of-cluster
    auth from platform_config."""
    with (
        patch("app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter),
        patch("kubernetes.config.load_incluster_config") as load_in,
        patch("kubernetes.config.load_kube_config") as load_file,
    ):
        ReferenceDataService._create_import_job(**_kwargs())

    fake_compute_adapter._get_k8s_batch_client.assert_called_once()
    fake_compute_adapter._get_k8s_batch_client.return_value.create_namespaced_job.assert_called_once()
    load_in.assert_not_called()
    load_file.assert_not_called()


def test_create_import_job_builds_job_with_backend_image_and_worker_command(fake_compute_adapter):
    """The Job runs the bioAF backend image with `python -m
    app.workers.reference_importer` so the importer module can run inside
    the Pod without needing a separate image build."""
    with patch(
        "app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter
    ):
        ReferenceDataService._create_import_job(**_kwargs())

    batch = fake_compute_adapter._get_k8s_batch_client.return_value
    call = batch.create_namespaced_job.call_args
    body = call.kwargs.get("body") or call.args[1]
    container = body.spec.template.spec.containers[0]

    assert "bioaf-backend" in container.image, container.image
    assert container.command == ["python", "-m", "app.workers.reference_importer"]


def test_create_import_job_sets_env_vars_matching_worker_contract(fake_compute_adapter):
    """The env vars the Job writes must match the keys
    app.workers.reference_importer.config_from_env reads."""
    with patch(
        "app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter
    ):
        ReferenceDataService._create_import_job(
            **_kwargs(source_md5_url="https://ftp.example.org/data/gencode.v45.gtf.gz.md5", auth_header="Bearer x")
        )

    batch = fake_compute_adapter._get_k8s_batch_client.return_value
    body = batch.create_namespaced_job.call_args.kwargs.get("body") or batch.create_namespaced_job.call_args.args[1]
    env = {e.name: e.value for e in body.spec.template.spec.containers[0].env}

    assert env["REFERENCE_ID"] == "42"
    assert env["SOURCE_URL"] == "https://ftp.example.org/data/gencode.v45.gtf.gz"
    assert env["GCS_BUCKET"] == "bioaf-references-prod"
    assert env["GCS_PREFIX"] == "annotation/gencode/v45/"
    assert env["EXTRACT_MODE"] == "gzip"
    assert env["SOURCE_MD5_URL"] == "https://ftp.example.org/data/gencode.v45.gtf.gz.md5"
    assert env["SOURCE_AUTH_HEADER"] == "Bearer x"
    assert env["CALLBACK_URL"] == "http://bioaf.example.com/api/internal/references/42/import-progress"
    assert env["INTERNAL_TOKEN"] == "t0p-secret"


def test_create_import_job_uses_pipeline_runner_ksa(fake_compute_adapter):
    """The Pod runs as bioaf-pipeline-runner so it inherits the existing
    Workload Identity binding to the nextflow GSA (project-wide
    storage.objectAdmin). Reusing this KSA avoids needing a dedicated
    reference-importer KSA + GSA on day 1; least-privilege split can come
    later."""
    with patch(
        "app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter
    ):
        ReferenceDataService._create_import_job(**_kwargs())

    batch = fake_compute_adapter._get_k8s_batch_client.return_value
    body = batch.create_namespaced_job.call_args.kwargs.get("body") or batch.create_namespaced_job.call_args.args[1]
    pod_spec = body.spec.template.spec
    assert pod_spec.service_account_name == "bioaf-pipeline-runner"


def test_create_import_job_raises_value_error_when_no_gke_endpoint(fake_compute_adapter):
    """The compute adapter raises RuntimeError when platform_config has no
    gke_cluster_endpoint. _create_import_job must translate that into a
    ValueError whose message contains 'not configured', so the existing
    API-layer ValueError -> 503 mapping kicks in."""
    fake_compute_adapter._get_k8s_batch_client.side_effect = RuntimeError(
        "No GKE cluster endpoint in platform_config. Deploy the compute stack first."
    )
    with patch(
        "app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter
    ):
        with pytest.raises(ValueError) as excinfo:
            ReferenceDataService._create_import_job(**_kwargs())
        assert "not configured" in str(excinfo.value).lower()


def test_create_import_job_translates_k8s_not_found_to_value_error(fake_compute_adapter):
    """If the bioaf-pipelines namespace or pipeline-runner KSA doesn't exist
    (compute stack not deployed yet), k8s returns 404 on Job creation.
    Translate that into the same 'Compute stack not configured' ValueError
    so the API surfaces a 503 with a clear remediation message instead of
    a 500."""
    from kubernetes.client.rest import ApiException

    batch = fake_compute_adapter._get_k8s_batch_client.return_value
    batch.create_namespaced_job.side_effect = ApiException(
        status=404, reason="Not Found"
    )
    with patch(
        "app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter
    ):
        with pytest.raises(ValueError) as excinfo:
            ReferenceDataService._create_import_job(**_kwargs())
        assert "not configured" in str(excinfo.value).lower()


def test_delete_import_job_uses_compute_adapter_batch_client(fake_compute_adapter):
    """The cancel path needs the same out-of-cluster auth; today it also
    uses load_kube_config and crashes."""
    with (
        patch("app.services.reference_data_service.get_compute_adapter", return_value=fake_compute_adapter),
        patch("kubernetes.config.load_incluster_config") as load_in,
        patch("kubernetes.config.load_kube_config") as load_file,
    ):
        ReferenceDataService._delete_import_job("refimport-42-stub")

    fake_compute_adapter._get_k8s_batch_client.return_value.delete_namespaced_job.assert_called_once()
    load_in.assert_not_called()
    load_file.assert_not_called()
