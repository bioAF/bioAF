"""What a launch asks the compute backend to run.

Two variants, because the two launch paths want different things from a job:

- ``NextflowJobSpec``: a catalog pipeline. The service decides the pipeline, version, parameters and
  sample sheet; the backend turns that into a Nextflow run (image, command, executor config, durable
  report/trace/output locations), because those depend on the cluster and the storage backend.
- ``CustomCommandJobSpec``: a custom pipeline. The service has already composed the container,
  command, staging, init steps, environment and resources; the backend runs exactly that.

Both are frozen, so nothing downstream can change what the service decided. ``job_spec_from_dict``
reads the legacy dict form, which the Kubernetes adapter still accepts.
"""

import copy
import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class VolumeMount:
    name: str
    mount_path: str
    read_only: bool = False


@dataclass(frozen=True)
class EnvVar:
    name: str
    value: str


@dataclass(frozen=True)
class InitStep:
    """A container that runs to completion before the job's main container."""

    name: str
    image: str
    command: tuple[str, ...]
    mounts: tuple[VolumeMount, ...] = ()


@dataclass(frozen=True)
class NextflowJobSpec:
    run_id: int
    pipeline_key: str
    pipeline_source: str | None
    pipeline_version: str | None
    parameters: Mapping[str, Any]
    sample_sheet: str
    experiment_id: int | None
    work_dir: str | None = None
    resume_from_run_id: int | None = None
    # Sample identifiers, for the record; the sample sheet is what the pipeline reads.
    input_files: tuple[str, ...] = ()
    namespace: str = "bioaf-pipelines"
    # Run this instead of the Nextflow command the backend would build, in this image.
    command: tuple[str, ...] | None = None
    container_image: str | None = None

    def __post_init__(self) -> None:
        # A read-only copy: the caller's dict stays the caller's.
        object.__setattr__(self, "parameters", MappingProxyType(copy.deepcopy(dict(self.parameters))))

    def __deepcopy__(self, memo: dict[int, Any]) -> "NextflowJobSpec":
        # A mappingproxy cannot be deep-copied; rebuilding copies the parameters instead.
        return dataclasses.replace(self)


@dataclass(frozen=True)
class CustomCommandJobSpec:
    run_id: int
    pipeline_name: str
    container_image: str
    command: tuple[str, ...]
    namespace: str = "bioaf-pipelines"
    stage_commands: tuple[str, ...] = ()
    extra_init_containers: tuple[InitStep, ...] = ()
    has_outputs_dir: bool = False
    has_code_dir: bool = False
    ssh_private_key: str | None = None
    cpu_request: str | None = None
    memory_request: str | None = None
    extra_env: tuple[EnvVar, ...] = field(default=())
    working_dir: str | None = None
    experiment_id: int | None = None
    project_id: int | None = None


JobSpec = NextflowJobSpec | CustomCommandJobSpec


_INIT_STEP_KEYS = {"name", "image", "command", "volumeMounts"}


def _init_step_from_dict(raw: Mapping[str, Any]) -> InitStep:
    unexpected = set(raw) - _INIT_STEP_KEYS
    if unexpected:
        raise ValueError(f"Init container fields a job spec cannot carry: {', '.join(sorted(unexpected))}")
    return InitStep(
        name=raw["name"],
        image=raw["image"],
        command=tuple(raw.get("command") or ()),
        mounts=tuple(
            VolumeMount(name=m["name"], mount_path=m["mountPath"], read_only=bool(m.get("readOnly", False)))
            for m in raw.get("volumeMounts") or ()
        ),
    )


def job_spec_from_dict(raw: Mapping[str, Any]) -> JobSpec:
    """Read the legacy dict job spec. A ``pipeline_source`` makes it a Nextflow job."""
    command = tuple(raw.get("command") or ())
    if raw.get("pipeline_source"):
        return NextflowJobSpec(
            run_id=raw.get("run_id", 0),
            pipeline_key=raw.get("pipeline_name", "unknown"),
            pipeline_source=raw["pipeline_source"],
            pipeline_version=raw.get("pipeline_version"),
            parameters=raw.get("parameters") or {},
            sample_sheet=raw.get("sample_sheet") or "",
            experiment_id=raw.get("experiment_id"),
            work_dir=raw.get("work_dir"),
            resume_from_run_id=raw.get("resume_from_run_id"),
            input_files=tuple(raw.get("input_files") or ()),
            namespace=raw.get("namespace", "bioaf-pipelines"),
            command=command or None,
            container_image=raw.get("container_image"),
        )
    return CustomCommandJobSpec(
        run_id=raw.get("run_id", 0),
        pipeline_name=raw.get("pipeline_name", "unknown"),
        container_image=raw.get("container_image", "alpine:3.19"),
        command=command,
        namespace=raw.get("namespace", "bioaf-pipelines"),
        stage_commands=tuple(raw.get("stage_commands") or ()),
        extra_init_containers=tuple(_init_step_from_dict(ic) for ic in raw.get("extra_init_containers") or ()),
        has_outputs_dir=bool(raw.get("has_outputs_dir")),
        has_code_dir=bool(raw.get("has_code_dir")),
        ssh_private_key=raw.get("ssh_private_key"),
        cpu_request=raw.get("cpu_request"),
        memory_request=raw.get("memory_request"),
        extra_env=tuple(EnvVar(name=e["name"], value=e["value"]) for e in raw.get("extra_env") or ()),
        working_dir=raw.get("working_dir"),
        experiment_id=raw.get("experiment_id"),
        project_id=raw.get("project_id"),
    )
