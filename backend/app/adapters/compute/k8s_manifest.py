"""Build the Kubernetes Job for a pipeline run from values that are already decided.

Pure: ``build_job_manifest`` reads no database, calls no provider or adapter registry, reads no
clock, and changes nothing it is given. Everything that needs any of those (the job name's
timestamp, the GCS key Secret, the storage backend's URIs and staging image, the cluster's
Nextflow settings) is resolved by the adapter first and passed in through ``ManifestInputs``.
The adapter keeps the effects: namespace and autoscaler setup, credential reads, Secret creation,
and the submission itself.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.adapters.job_specs import CustomCommandJobSpec, InitStep, JobSpec, NextflowJobSpec, VolumeMount

_GCS_KEY_SECRET = "bioaf-gcs-sa-key"
_GCS_MOUNT = {"name": "gcp-sa-key", "mountPath": "/secrets/gcp", "readOnly": True}
_GCS_ENV = {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/secrets/gcp/key.json"}
_SSH_MOUNT = {"name": "ssh-key", "mountPath": "/root/.ssh", "readOnly": True}
_DATA_MOUNT = {"name": "data", "mountPath": "/data"}
_DEFAULT_IMAGE = "alpine:3.19"


@dataclass(frozen=True)
class NextflowLaunch:
    """The Nextflow run the adapter resolved for a catalog job."""

    image: str
    command: tuple[str, ...]
    config: str


@dataclass(frozen=True)
class ManifestInputs:
    job_name: str
    has_gcs_secret: bool
    staging_image: str
    # Set exactly when the spec is a Nextflow job the backend builds (no command override).
    nextflow: NextflowLaunch | None = None


def sanitize_label_value(value: str) -> str:
    """Sanitize a string for use as a K8s label value.

    Label values must match: (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?
    Replaces invalid characters with '-' and trims to 63 chars.
    """
    sanitized = re.sub(r"[^A-Za-z0-9\-_.]", "-", value)
    sanitized = sanitized.strip("-_.")
    return sanitized[:63]


def ssh_key_secret_name(run_id: int) -> str:
    return f"bioaf-ssh-key-{run_id}"


def builds_nextflow(spec: JobSpec) -> bool:
    """Whether the backend composes a Nextflow run for this spec (rather than running a command)."""
    return isinstance(spec, NextflowJobSpec) and bool(spec.pipeline_source) and not spec.command


def _mount(m: VolumeMount) -> dict[str, Any]:
    d: dict[str, Any] = {"name": m.name, "mountPath": m.mount_path}
    if m.read_only:
        d["readOnly"] = True
    return d


def _init_container(step: InitStep) -> dict[str, Any]:
    return {
        "name": step.name,
        "image": step.image,
        "command": list(step.command),
        "volumeMounts": [_mount(m) for m in step.mounts],
    }


def _write_file_init(name: str, command: str) -> dict[str, Any]:
    return {
        "name": name,
        "image": _DEFAULT_IMAGE,
        "command": ["/bin/sh", "-c", command],
        "volumeMounts": [dict(_DATA_MOUNT)],
    }


def build_job_manifest(spec: JobSpec, inputs: ManifestInputs) -> dict[str, Any]:
    """The Job manifest for ``spec``. See the module notes for what ``inputs`` must already hold."""
    if builds_nextflow(spec) != (inputs.nextflow is not None):
        raise ValueError("A resolved Nextflow launch is required for, and only for, a Nextflow job")

    init_containers: list[dict[str, Any]] = []
    custom_inits: list[dict[str, Any]] = []
    if isinstance(spec, CustomCommandJobSpec):
        label = spec.pipeline_name
        image = spec.container_image
        command = list(spec.command)
        if spec.stage_commands:
            init_containers.append(
                {
                    "name": "stage-inputs",
                    "image": inputs.staging_image,
                    "command": ["/bin/sh", "-c", " && ".join(spec.stage_commands)],
                    "volumeMounts": [dict(_DATA_MOUNT)],
                }
            )
        custom_inits = [_init_container(step) for step in spec.extra_init_containers]
        init_containers.extend(custom_inits)
    else:
        label = spec.pipeline_key
        if inputs.nextflow is not None:
            image, command = inputs.nextflow.image, list(inputs.nextflow.command)
            if spec.sample_sheet:
                # Strip carriage returns that browsers/forms sometimes inject
                sheet = spec.sample_sheet.replace("\r\n", "\n").replace("\r", "\n").replace("'", "'\\''")
                init_containers.append(
                    _write_file_init("write-samplesheet", f"printf '%s' '{sheet}' > /data/samplesheet.csv")
                )
            # Heredoc avoids shell escaping issues with single quotes in config values.
            init_containers.append(
                _write_file_init(
                    "write-nf-config", f"cat > /data/nextflow.config << 'NFEOF'\n{inputs.nextflow.config}\nNFEOF"
                )
            )
        else:
            image, command = spec.container_image or _DEFAULT_IMAGE, list(spec.command or ())

    if inputs.has_gcs_secret:
        for ic in init_containers:
            ic["volumeMounts"].append(dict(_GCS_MOUNT))
            ic.setdefault("env", []).append(dict(_GCS_ENV))

    ssh_key = isinstance(spec, CustomCommandJobSpec) and bool(spec.ssh_private_key)
    if ssh_key:
        for ic in custom_inits:
            ic["volumeMounts"].append(dict(_SSH_MOUNT))

    main: dict[str, Any] = {
        "name": "pipeline",
        "image": image,
        "volumeMounts": [dict(_DATA_MOUNT)],
        "terminationMessagePolicy": "FallbackToLogsOnError",
    }
    if inputs.has_gcs_secret:
        main["volumeMounts"].append(dict(_GCS_MOUNT))
        main["env"] = [dict(_GCS_ENV)]
    if command:
        main["command"] = command

    has_outputs_dir = has_code_dir = False
    if isinstance(spec, CustomCommandJobSpec):
        has_outputs_dir, has_code_dir = spec.has_outputs_dir, spec.has_code_dir
        if has_outputs_dir:
            main["volumeMounts"].append({"name": "outputs", "mountPath": "/outputs"})
        if has_code_dir:
            main["volumeMounts"].append({"name": "code", "mountPath": "/code"})
        # Guaranteed QoS: requests equal limits.
        if spec.cpu_request or spec.memory_request:
            requests: dict[str, str] = {}
            if spec.cpu_request:
                requests["cpu"] = str(spec.cpu_request)
            if spec.memory_request:
                requests["memory"] = str(spec.memory_request)
            main["resources"] = {"requests": requests, "limits": dict(requests)}
        if spec.extra_env:
            main.setdefault("env", []).extend({"name": e.name, "value": e.value} for e in spec.extra_env)
        if spec.working_dir:
            main["workingDir"] = spec.working_dir

    volumes: list[dict[str, Any]] = [{"name": "data", "emptyDir": {"sizeLimit": "50Gi"}}]
    if inputs.has_gcs_secret:
        volumes.append({"name": "gcp-sa-key", "secret": {"secretName": _GCS_KEY_SECRET}})
    if has_outputs_dir:
        volumes.append({"name": "outputs", "emptyDir": {"sizeLimit": "50Gi"}})
    if has_code_dir:
        volumes.append({"name": "code", "emptyDir": {"sizeLimit": "10Gi"}})
    if ssh_key:
        volumes.append(
            {"name": "ssh-key", "secret": {"secretName": ssh_key_secret_name(spec.run_id), "defaultMode": 0o400}}
        )

    pod_spec: dict[str, Any] = {
        # Head pod runs on the dedicated on-demand pipeline-head pool, NOT the Spot pipelines pool.
        # Spot preemption of the head pod kills the entire pipeline; on-demand keeps the
        # orchestrator alive while task pods (Spot, retried by Nextflow's errorStrategy on exit
        # 143/137/247) stay cheap. This head pool is tainted bioaf.io/pool=pipeline-head; task pods
        # tolerate only the pipelines taint, so they never land here.
        "nodeSelector": {"bioaf.io/pool": "pipeline-head"},
        "tolerations": [{"key": "bioaf.io/pool", "value": "pipeline-head", "effect": "NoSchedule"}],
        "serviceAccountName": "bioaf-pipeline-runner",
        "containers": [main],
        "volumes": volumes,
        "restartPolicy": "Never",
    }
    if init_containers:
        pod_spec["initContainers"] = init_containers

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": inputs.job_name,
            "namespace": spec.namespace,
            "labels": {
                "bioaf.io/pipeline-run": str(spec.run_id),
                "bioaf.io/pipeline": sanitize_label_value(label),
                "bioaf.io/pool": "pipelines",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {
                    # Pin the head pod to its node. Without this, the cluster autoscaler treats
                    # Nextflow's coordinator pod as "movable" (it requests no resources) and scales
                    # down its node mid-pipeline, killing the workflow and any task pods it's
                    # coordinating.
                    "annotations": {"cluster-autoscaler.kubernetes.io/safe-to-evict": "false"},
                },
                "spec": pod_spec,
            },
        },
    }
