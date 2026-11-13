"""Golden characterization of the Kubernetes Job the compute adapter submits.

Each scenario runs a legacy dict job spec through ``_k8s_submit_job`` with the clock, credentials,
cluster config and storage backend pinned, and compares the full manifest, plus every Secret created
along the way, with a stored golden copy. The goldens were recorded from the adapter as it stood
before the typed job specs and the pure manifest builder were introduced, so a refactor that keeps
these green produces the same Jobs for the same inputs.

Regenerate (only for an intended manifest change): BIOAF_REGEN_K8S_GOLDENS=1 pytest this file.
"""

import copy
import json
import os
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.settings_contract import ComputeMode

GOLDENS = Path(__file__).parent / "fixtures" / "k8s_manifests"
FROZEN_EPOCH = 1_790_000_000

GCP_CONFIG = {
    "gcp_project_id": "bioaf-test",
    "raw_bucket_name": "bioaf-raw-test",
    "results_bucket_name": "bioaf-results-test",
    "k8s_pipeline_machine_type": "n2-highmem-16",
    "k8s_pipeline_disk_size_gb": "500",
}
AWS_CONFIG = {
    "pipeline_runner_role_arn": "arn:aws:iam::123456789012:role/bioaf-pipeline-runner",
    "raw_bucket_name": "bioaf-raw-test",
    "results_bucket_name": "bioaf-results-test",
    "k8s_pipeline_machine_type": "m6i.4xlarge",
}
SA_KEY = '{"type": "service_account", "project_id": "bioaf-test"}'

NEXTFLOW = {
    "run_id": 101,
    "pipeline_name": "nf-core/scrnaseq",
    "pipeline_source": "https://github.com/nf-core/scrnaseq",
    "pipeline_version": "2.7.1",
    "parameters": {"aligner": "star", "genome": "GRCh38", "protocol": "10XV3", "accessions": ["x"]},
    "sample_sheet": "sample,fastq_1,fastq_2\r\nS1,gs://b/S1_R1.fq.gz,gs://b/S1_R2.fq.gz\r\n",
    "experiment_id": 9,
    "work_dir": "/data/working/nextflow/run-101",
    "resume_from_run_id": None,
    "input_files": ["S1"],
}

CUSTOM_BASE: dict[str, Any] = {
    "run_id": 202,
    "pipeline_name": "In-house QC",
    "container_image": "registry.test/bioaf/qc:v3",
    "command": ["/bin/sh", "-c", "#!/bin/sh\nset -e\ntrap 'true' EXIT\nrun-qc"],
    "stage_commands": [
        'if [ -f "/secrets/gcp/key.json" ]; then gcloud auth activate-service-account --key-file=/secrets/gcp/key.json; fi',
        "mkdir -p /data/Proj/Exp && gsutil cp gs://b/a.tsv /data/Proj/Exp/a.tsv",
    ],
    "namespace": "bioaf-pipelines",
    "has_outputs_dir": True,
    "extra_init_containers": [
        {
            "name": "write-manifest",
            "image": "alpine:3.19",
            "command": ["/bin/sh", "-c", "printf '%s' '{}' > /data/manifest.json"],
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        },
        {
            "name": "write-params",
            "image": "alpine:3.19",
            "command": ["/bin/sh", "-c", "printf '%s' '{}' > /data/params.json"],
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        },
    ],
    "cpu_request": "2",
    "memory_request": "4Gi",
    "extra_env": [{"name": "PARAM_DEPTH", "value": "25"}],
    "experiment_id": 9,
    "project_id": 4,
}

SCENARIOS = {
    "nextflow_gcp_service_account_key": dict(spec=NEXTFLOW, config=GCP_CONFIG, creds=("service_account_key", SA_KEY)),
    "nextflow_gcp_workload_identity": dict(spec=NEXTFLOW, config=GCP_CONFIG, creds=("vm_default", "")),
    "nextflow_aws_irsa": dict(spec=NEXTFLOW, config=AWS_CONFIG, creds=("vm_default", ""), storage="s3"),
    # A replayed spec may carry an outdir, but only the run's own location (plan 17, D3); any
    # other is refused, see test_nextflow_command_safety.
    "nextflow_explicit_outdir": dict(
        spec={
            **NEXTFLOW,
            "parameters": {
                **NEXTFLOW["parameters"],
                "outdir": "gs://bioaf-results-test/experiments/9/pipeline-runs/101/",
            },
        },
        config=GCP_CONFIG,
        creds=("vm_default", ""),
    ),
    "nextflow_command_override": dict(
        spec={**NEXTFLOW, "command": ["/bin/sh", "-c", "nextflow -version"], "container_image": "registry.test/nf:1"},
        config=GCP_CONFIG,
        creds=("service_account_key", SA_KEY),
    ),
    "custom_inline_with_key": dict(
        spec={**CUSTOM_BASE, "has_code_dir": False, "working_dir": "/data", "ssh_private_key": None},
        config=GCP_CONFIG,
        creds=("service_account_key", SA_KEY),
    ),
    "custom_code_blob_no_key": dict(
        spec={
            **CUSTOM_BASE,
            "has_code_dir": True,
            "working_dir": "/code",
            "extra_init_containers": CUSTOM_BASE["extra_init_containers"]
            + [
                {
                    "name": "write-code",
                    "image": "alpine:3.19",
                    "command": ["/bin/sh", "-c", "printf '%s' 'echo hi' > /code/script"],
                    "volumeMounts": [{"name": "code", "mountPath": "/code"}],
                }
            ],
        },
        config=GCP_CONFIG,
        creds=("vm_default", ""),
    ),
    "custom_git_with_key_and_ssh": dict(
        spec={
            **CUSTOM_BASE,
            "has_code_dir": True,
            "working_dir": "/code/myrepo",
            "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nKEY\n-----END OPENSSH PRIVATE KEY-----",
            "extra_init_containers": CUSTOM_BASE["extra_init_containers"]
            + [
                {
                    "name": "clone-repo",
                    "image": "alpine/git:latest",
                    "command": ["/bin/sh", "-c", "git clone 'git@github.com:x/myrepo.git' '/code/myrepo'"],
                    "volumeMounts": [
                        {"name": "code", "mountPath": "/code"},
                        {"name": "ssh-key", "mountPath": "/secrets/ssh", "readOnly": True},
                    ],
                }
            ],
        },
        config=GCP_CONFIG,
        creds=("service_account_key", SA_KEY),
    ),
    "custom_aws_no_resources": dict(
        spec={
            **CUSTOM_BASE,
            "stage_commands": [],
            "cpu_request": None,
            "memory_request": None,
            "extra_env": [],
            "has_code_dir": False,
            "working_dir": None,
        },
        config=AWS_CONFIG,
        creds=("vm_default", ""),
        storage="s3",
    ),
    "bare_command_job": dict(
        spec={"run_id": 303, "pipeline_name": "Smoke Test!", "command": ["echo", "hi"]},
        config=GCP_CONFIG,
        creds=("vm_default", ""),
    ),
}


async def submit(build, name: str, spec: dict, config: dict, creds: tuple[str, str], storage: str = "gcs") -> dict:
    """Run one scenario through the adapter; return everything it sent to Kubernetes.

    ``build`` is the mode_adapter_factory: the compute and storage adapters take their real branches."""
    provider = build(KubernetesComputeProvider, mode=ComputeMode.KUBERNETES)
    provider._namespace_ready = True
    provider._autoscaler_ready = True
    provider._cluster_config = dict(config)

    batch, core = MagicMock(), MagicMock()
    core.read_namespaced_secret.side_effect = ApiException(status=404)
    storage_adapter = build(S3StorageProvider if storage == "s3" else GcsStorageProvider, mode=ComputeMode.KUBERNETES)

    async def fresh():
        return None

    async def credentials():
        return creds

    with (
        patch.object(provider, "_ensure_cluster_config_fresh", fresh),
        patch.object(provider, "_read_gcp_credentials", credentials),
        patch.object(provider, "_get_k8s_batch_client", return_value=batch),
        patch.object(provider, "_get_k8s_core_client", return_value=core),
        patch("app.adapters.registry.get_storage_adapter", return_value=storage_adapter),
        patch("app.adapters.compute.kubernetes.time.time", return_value=FROZEN_EPOCH),
    ):
        result = await provider._k8s_submit_job(copy.deepcopy(spec))

    return {
        "result": {k: v for k, v in result.items() if k != "estimated_cost"},
        "secrets": [c.kwargs["body"] for c in core.create_namespaced_secret.call_args_list],
        "job": batch.create_namespaced_job.call_args.kwargs,
    }


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.asyncio
async def test_manifest_matches_golden(name, mode_adapter_factory):
    sent = json.loads(json.dumps(await submit(mode_adapter_factory, name, **SCENARIOS[name]), sort_keys=True))
    golden = GOLDENS / f"{name}.json"
    if os.environ.get("BIOAF_REGEN_K8S_GOLDENS"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(sent, indent=2, sort_keys=True) + "\n")
    assert sent == json.loads(golden.read_text())
