"""The typed job specifications a launch hands to the compute backend.

Two variants, because the two launch paths want different things from a job: a catalog launch is a
Nextflow pipeline the backend runs, and a custom launch is a container command the service has
already composed. Specs are immutable, so nothing downstream can change what the service decided.
The legacy dict form is still accepted at the adapter and read into the same types.
"""

import dataclasses
from typing import Any

import pytest

from app.adapters.job_specs import (
    CustomCommandJobSpec,
    EnvVar,
    InitStep,
    NextflowJobSpec,
    VolumeMount,
    job_spec_from_dict,
)


def _nextflow(**overrides) -> NextflowJobSpec:
    fields = dict(
        run_id=1,
        pipeline_key="nf-core/rnaseq",
        pipeline_source="https://github.com/nf-core/rnaseq",
        pipeline_version="3.14.0",
        parameters={"genome": "GRCh38"},
        sample_sheet="sample\nS1\n",
        experiment_id=2,
    )
    return NextflowJobSpec(**{**fields, **overrides})


def test_specs_are_immutable():
    spec = _nextflow()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.run_id = 2  # type: ignore[misc]
    step = InitStep(name="write", image="alpine:3.19", command=("true",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.name = "other"  # type: ignore[misc]


def test_a_dict_with_a_pipeline_source_reads_as_a_nextflow_spec():
    spec = job_spec_from_dict(
        {
            "run_id": 7,
            "pipeline_name": "nf-core/scrnaseq",
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"aligner": "star"},
            "sample_sheet": "s\n",
            "experiment_id": 3,
            "work_dir": "/data/working/nextflow/run-7",
            "resume_from_run_id": 5,
            "input_files": ["S1"],
        }
    )
    assert spec == NextflowJobSpec(
        run_id=7,
        pipeline_key="nf-core/scrnaseq",
        pipeline_source="https://github.com/nf-core/scrnaseq",
        pipeline_version="2.7.1",
        parameters={"aligner": "star"},
        sample_sheet="s\n",
        experiment_id=3,
        work_dir="/data/working/nextflow/run-7",
        resume_from_run_id=5,
        input_files=("S1",),
    )


def test_an_explicit_command_on_a_nextflow_dict_is_kept_as_an_override():
    spec = job_spec_from_dict(
        {"run_id": 1, "pipeline_source": "https://x", "command": ["echo", "hi"], "container_image": "img:1"}
    )
    assert isinstance(spec, NextflowJobSpec)
    assert spec.command == ("echo", "hi")
    assert spec.container_image == "img:1"


def test_a_dict_without_a_pipeline_source_reads_as_a_command_spec():
    spec = job_spec_from_dict(
        {
            "run_id": 9,
            "pipeline_name": "QC",
            "container_image": "registry/qc:1",
            "command": ["/bin/sh", "-c", "run"],
            "stage_commands": ["cp a b"],
            "has_outputs_dir": True,
            "has_code_dir": True,
            "extra_init_containers": [
                {
                    "name": "clone-repo",
                    "image": "alpine/git",
                    "command": ["git", "clone"],
                    "volumeMounts": [{"name": "ssh-key", "mountPath": "/secrets/ssh", "readOnly": True}],
                }
            ],
            "ssh_private_key": "KEY",
            "cpu_request": "2",
            "memory_request": "4Gi",
            "extra_env": [{"name": "PARAM_X", "value": "1"}],
            "working_dir": "/code",
            "experiment_id": 3,
            "project_id": 4,
        }
    )
    assert spec == CustomCommandJobSpec(
        run_id=9,
        pipeline_name="QC",
        container_image="registry/qc:1",
        command=("/bin/sh", "-c", "run"),
        stage_commands=("cp a b",),
        has_outputs_dir=True,
        has_code_dir=True,
        extra_init_containers=(
            InitStep(
                name="clone-repo",
                image="alpine/git",
                command=("git", "clone"),
                mounts=(VolumeMount(name="ssh-key", mount_path="/secrets/ssh", read_only=True),),
            ),
        ),
        ssh_private_key="KEY",
        cpu_request="2",
        memory_request="4Gi",
        extra_env=(EnvVar(name="PARAM_X", value="1"),),
        working_dir="/code",
        experiment_id=3,
        project_id=4,
    )


def test_a_bare_dict_keeps_the_adapters_old_defaults():
    spec = job_spec_from_dict({})
    assert spec == CustomCommandJobSpec(run_id=0, pipeline_name="unknown", container_image="alpine:3.19", command=())


def test_an_init_container_with_fields_the_spec_cannot_carry_is_refused():
    with pytest.raises(ValueError, match="securityContext"):
        job_spec_from_dict(
            {"extra_init_containers": [{"name": "x", "image": "y", "command": [], "securityContext": {}}]}
        )


def test_reading_a_dict_does_not_keep_references_to_its_parts():
    raw: dict[str, Any] = {"pipeline_source": "https://x", "parameters": {"a": "1"}}
    spec = job_spec_from_dict(raw)
    raw["parameters"]["a"] = "changed"
    assert spec.parameters == {"a": "1"}
