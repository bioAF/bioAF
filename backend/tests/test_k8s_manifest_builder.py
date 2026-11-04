"""Contract tests for the pure Kubernetes Job manifest builder.

The builder turns a typed job spec plus the values the adapter already resolved (job name, whether
the GCS key Secret exists, the staging image, and for Nextflow the command, image and executor
config) into a Job manifest. It reads no database, calls no provider or registry, reads no clock,
and changes nothing it is given. Full-manifest equivalence with the previous adapter is held by
test_k8s_manifest_characterization.py; these pin the contract itself.
"""

import copy
from unittest.mock import patch

import pytest

from app.adapters import registry as adapter_registry
from app.adapters.compute.k8s_manifest import ManifestInputs, NextflowLaunch, build_job_manifest
from app.adapters.job_specs import CustomCommandJobSpec, EnvVar, InitStep, NextflowJobSpec, VolumeMount

NEXTFLOW = NextflowJobSpec(
    run_id=11,
    pipeline_key="nf-core/rnaseq",
    pipeline_source="https://github.com/nf-core/rnaseq",
    pipeline_version="3.14.0",
    parameters={"genome": "GRCh38", "outdir": "gs://results/experiments/2/pipeline-runs/11"},
    sample_sheet="sample,fastq_1\r\nS1,a.fq\r\n",
    experiment_id=2,
)
LAUNCH = NextflowLaunch(
    image="nextflow/nextflow:25.10.4",
    command=("/bin/sh", "-c", "nextflow run x --outdir gs://results/experiments/2/pipeline-runs/11"),
    config="process { executor = 'k8s' }",
)
CLONE = InitStep(
    name="clone-repo",
    image="alpine/git:latest",
    command=("/bin/sh", "-c", "git clone x /code/r"),
    mounts=(VolumeMount("code", "/code"), VolumeMount("ssh-key", "/secrets/ssh", read_only=True)),
)
CUSTOM = CustomCommandJobSpec(
    run_id=22,
    pipeline_name="In-house QC",
    container_image="registry/qc:1",
    command=("/bin/sh", "-c", "run"),
    stage_commands=("cp a /data/a", "cp b /data/b"),
    extra_init_containers=(CLONE,),
    has_outputs_dir=True,
    has_code_dir=True,
    ssh_private_key="KEY",
    cpu_request="2",
    memory_request="4Gi",
    extra_env=(EnvVar("PARAM_DEPTH", "25"),),
    working_dir="/code/r",
)


def _inputs(*, gcs: bool = False, nextflow: NextflowLaunch | None = None) -> ManifestInputs:
    return ManifestInputs(
        job_name="bioaf-pipeline-x-1", has_gcs_secret=gcs, staging_image="gcr.io/stager:1", nextflow=nextflow
    )


def _pod(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]


def _init(manifest: dict, name: str) -> dict:
    return next(ic for ic in _pod(manifest).get("initContainers", []) if ic["name"] == name)


# --- Purity ---


def test_builds_without_database_providers_registry_or_clock():
    adapter_registry.reset_registry()
    with (
        patch("app.adapters.registry.get_storage_adapter", side_effect=AssertionError("registry used")),
        patch("time.time", side_effect=AssertionError("clock read")),
    ):
        nextflow = build_job_manifest(NEXTFLOW, _inputs(gcs=True, nextflow=LAUNCH))
        custom = build_job_manifest(CUSTOM, _inputs(gcs=True))
    assert nextflow["metadata"]["name"] == custom["metadata"]["name"] == "bioaf-pipeline-x-1"


def test_leaves_its_inputs_unchanged_and_shares_nothing_with_them():
    before = copy.deepcopy((CUSTOM, NEXTFLOW))
    first = build_job_manifest(CUSTOM, _inputs(gcs=True))
    _init(first, "clone-repo")["volumeMounts"].append({"name": "tampered", "mountPath": "/x"})
    second = build_job_manifest(CUSTOM, _inputs(gcs=True))
    build_job_manifest(NEXTFLOW, _inputs(gcs=True, nextflow=LAUNCH))

    assert (CUSTOM, NEXTFLOW) == before
    assert {"name": "tampered", "mountPath": "/x"} not in _init(second, "clone-repo")["volumeMounts"]


def test_a_nextflow_spec_needs_its_resolved_launch_and_a_command_job_must_not_have_one():
    with pytest.raises(ValueError):
        build_job_manifest(NEXTFLOW, _inputs())
    with pytest.raises(ValueError):
        build_job_manifest(CUSTOM, _inputs(nextflow=LAUNCH))


# --- Catalog Nextflow ---


def test_a_nextflow_job_runs_the_resolved_command_with_its_sheet_and_config():
    m = build_job_manifest(NEXTFLOW, _inputs(nextflow=LAUNCH))
    main = _pod(m)["containers"][0]
    assert main["image"] == LAUNCH.image
    assert main["command"] == list(LAUNCH.command)
    assert [ic["name"] for ic in _pod(m)["initContainers"]] == ["write-samplesheet", "write-nf-config"]
    sheet = _init(m, "write-samplesheet")["command"][2]
    assert "\r" not in sheet and "S1,a.fq" in sheet
    assert LAUNCH.config in _init(m, "write-nf-config")["command"][2]
    assert m["metadata"]["labels"]["bioaf.io/pipeline"] == "nf-core-rnaseq"


def test_a_nextflow_command_override_runs_as_given_without_nextflow_setup():
    spec = NextflowJobSpec(**{**_fields(NEXTFLOW), "command": ("echo", "hi"), "container_image": "img:2"})
    m = build_job_manifest(spec, _inputs())
    main = _pod(m)["containers"][0]
    assert (main["image"], main["command"]) == ("img:2", ["echo", "hi"])
    assert "initContainers" not in _pod(m)


def _fields(spec) -> dict:
    import dataclasses

    return {f.name: getattr(spec, f.name) for f in dataclasses.fields(spec)}


# --- Custom commands ---


def test_a_custom_job_stages_inputs_first_then_its_own_init_steps():
    m = build_job_manifest(CUSTOM, _inputs())
    inits = _pod(m)["initContainers"]
    assert [ic["name"] for ic in inits] == ["stage-inputs", "clone-repo"]
    assert inits[0]["image"] == "gcr.io/stager:1"
    assert inits[0]["command"] == ["/bin/sh", "-c", "cp a /data/a && cp b /data/b"]


def test_a_custom_job_keeps_its_image_command_resources_env_dirs_and_working_dir():
    main = _pod(build_job_manifest(CUSTOM, _inputs()))["containers"][0]
    assert (main["image"], main["command"]) == ("registry/qc:1", ["/bin/sh", "-c", "run"])
    assert main["resources"] == {"requests": {"cpu": "2", "memory": "4Gi"}, "limits": {"cpu": "2", "memory": "4Gi"}}
    assert main["env"] == [{"name": "PARAM_DEPTH", "value": "25"}]
    assert main["workingDir"] == "/code/r"
    assert [v["mountPath"] for v in main["volumeMounts"]] == ["/data", "/outputs", "/code"]


def test_an_ssh_key_mounts_its_secret_on_the_custom_init_steps_only():
    m = build_job_manifest(CUSTOM, _inputs())
    assert {"name": "ssh-key", "mountPath": "/root/.ssh", "readOnly": True} in _init(m, "clone-repo")["volumeMounts"]
    assert all(v["name"] != "ssh-key" for v in _init(m, "stage-inputs")["volumeMounts"])
    ssh_volume = next(v for v in _pod(m)["volumes"] if v["name"] == "ssh-key")
    assert ssh_volume["secret"] == {"secretName": "bioaf-ssh-key-22", "defaultMode": 0o400}


def test_no_ssh_key_means_no_ssh_volume():
    import dataclasses

    m = build_job_manifest(dataclasses.replace(CUSTOM, ssh_private_key=None), _inputs())
    assert all(v["name"] != "ssh-key" for v in _pod(m)["volumes"])


# --- Credentials ---


@pytest.mark.parametrize("spec,nextflow", [(CUSTOM, None), (NEXTFLOW, LAUNCH)], ids=["custom", "nextflow"])
def test_a_gcs_key_secret_is_mounted_everywhere_and_absent_without_one(spec, nextflow):
    with_key = build_job_manifest(spec, _inputs(gcs=True, nextflow=nextflow))
    without = build_job_manifest(spec, _inputs(gcs=False, nextflow=nextflow))
    gcs_mount = {"name": "gcp-sa-key", "mountPath": "/secrets/gcp", "readOnly": True}
    gcs_env = {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/secrets/gcp/key.json"}
    for container in _pod(with_key)["initContainers"] + _pod(with_key)["containers"]:
        assert gcs_mount in container["volumeMounts"]
        assert gcs_env in container["env"]
    assert any(v["name"] == "gcp-sa-key" for v in _pod(with_key)["volumes"])
    for container in _pod(without)["initContainers"] + _pod(without)["containers"]:
        assert gcs_mount not in container["volumeMounts"]
        assert gcs_env not in container.get("env", [])
    assert all(v["name"] != "gcp-sa-key" for v in _pod(without)["volumes"])


# --- Scheduling ---


@pytest.mark.parametrize("spec,nextflow", [(CUSTOM, None), (NEXTFLOW, LAUNCH)], ids=["custom", "nextflow"])
def test_every_job_runs_on_the_pipeline_head_pool_and_is_never_evicted(spec, nextflow):
    m = build_job_manifest(spec, _inputs(nextflow=nextflow))
    pod = _pod(m)
    assert pod["nodeSelector"] == {"bioaf.io/pool": "pipeline-head"}
    assert pod["tolerations"] == [{"key": "bioaf.io/pool", "value": "pipeline-head", "effect": "NoSchedule"}]
    assert pod["serviceAccountName"] == "bioaf-pipeline-runner"
    assert m["spec"]["template"]["metadata"]["annotations"] == {
        "cluster-autoscaler.kubernetes.io/safe-to-evict": "false"
    }
    assert (m["spec"]["backoffLimit"], m["spec"]["ttlSecondsAfterFinished"]) == (0, 3600)
    assert m["metadata"]["labels"]["bioaf.io/pipeline-run"] == str(spec.run_id)
