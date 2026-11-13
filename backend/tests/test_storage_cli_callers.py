"""Hostile filenames stay data through every command a caller composes around the storage CLI.

The adapter contract alone is not enough: callers wrap the copy commands in ``mkdir -p``, guards,
loops, EXIT traps, heredocs and build templates. Each test here builds the caller's real final
command, maps its fixed container directories (``/data``, ``/outputs``, ``/home/jovyan``) onto a
temporary directory, runs it under ``/bin/sh`` with recording cloud CLIs, and checks the argv each
CLI received, the bytes written, and that nothing else happened.
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.settings_contract import ComputeMode
from tests.support.mode_adapters import build_mode_adapter
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError
from tests.test_storage_cli_commands import Shell, assert_gcs_fetched, gcs_escape

HOSTILE = [
    "a$(touch PWNED).fq",
    "b`touch PWNED`.fq",
    "c;touch PWNED;.fq",
    "d e.fq",
    "it's.fq",
    "INVENTORY_EOF",
    "x\nINVENTORY_EOF\ntouch PWNED",
    "-rf.fq",
    "g[1].fq",
]


@pytest.fixture
def sh(tmp_path):
    return Shell(tmp_path)


def _mapped(script: str, tmp_path, *dirs: str) -> str:
    """Point the container's fixed directories at temporary ones (plain paths, no quoting needed).

    Only whole path occurrences: not inside a URI (`.../sessions/42/outputs/`) or a longer name
    (`/dataset.h5ad`).
    """
    for d in dirs:
        target = str(tmp_path / d.strip("/").replace("/", "_"))
        script = re.sub(rf"(?<![\w.-]){re.escape(d)}(?=[/\s'\");]|$)", lambda _m: target, script)
    return script


# --- notebook input staging and inventory ---------------------------------------------------


def _notebook_adapter():
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    provider = build_mode_adapter(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    return provider


def _spec(input_files):
    return {
        "session_type": "jupyter",
        "session_id": 42,
        "user_id": 7,
        "resource_profile": "small",
        "cpu_cores": 2,
        "memory_gb": 4,
        "working_bucket": "w",
        "input_files": input_files,
    }


@pytest.mark.parametrize("storage", [GcsStorageProvider(), S3StorageProvider()], ids=["gcs", "s3"])
@pytest.mark.parametrize("has_key", [False, True])
def test_notebook_staging_copies_each_input_literally_and_writes_the_inventory(sh, tmp_path, storage, has_key):
    scheme = storage.build_uri("raw", "")
    files = [{"relative_path": f"proj/uploads/{n}", "gcs_uri": f"{scheme}uploads/{n}"} for n in HOSTILE]
    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        manifest = _notebook_adapter()._build_pod_manifest(_spec(files), has_gcs_secret=has_key)
    staging = next(c for c in manifest["spec"]["initContainers"] if c["name"] == "gcs-data-sync")
    for f in files:
        if isinstance(storage, GcsStorageProvider):
            sh.put(f["gcs_uri"], f["gcs_uri"].encode())
    sh.run(_mapped(staging["command"][2], tmp_path, "/data"))

    data = tmp_path / "data"
    if isinstance(storage, GcsStorageProvider):
        assert_gcs_fetched(sh, [(f["gcs_uri"], f"{data}/{f['relative_path']}") for f in files])
    else:
        copies = [c for c in sh.calls() if c[:2] == ["s3", "cp"]]
        assert [c[-2:] for c in copies] == [[f["gcs_uri"], f"{data}/{f['relative_path']}"] for f in files]
    inventory = "\n".join(
        ["# File Inventory", "", "Files mounted at session start:", ""]
        + [f"- /data/{f['relative_path']} (source: {f['gcs_uri']})" for f in files]
    )
    # The harness mapped /data everywhere, the inventory's own text included.
    assert (data / "FILE_INVENTORY.md").read_text() == _mapped(inventory, tmp_path, "/data") + "\n"
    if has_key and isinstance(storage, GcsStorageProvider):
        assert sh.calls()[0][:2] == ["auth", "activate-service-account"]


@pytest.mark.parametrize("rel", ["../escape.fq", "a/../../etc/passwd", "/abs.fq", "a//b.fq", "."])
def test_notebook_staging_refuses_a_path_that_leaves_data(rel):
    files = [{"relative_path": rel, "gcs_uri": "gs://raw/x"}]
    with patch("app.adapters.registry.get_storage_adapter", return_value=GcsStorageProvider()):
        with pytest.raises(ValidationError):
            _notebook_adapter()._build_pod_manifest(_spec(files), has_gcs_secret=False)


def test_notebook_staging_has_no_gsutil_bypass():
    files = [{"relative_path": "a.fq", "gcs_uri": "gs://raw/a.fq"}]
    with patch("app.adapters.registry.get_storage_adapter", return_value=GcsStorageProvider()):
        manifest = _notebook_adapter()._build_pod_manifest(_spec(files), has_gcs_secret=False)
    staging = next(c for c in manifest["spec"]["initContainers"] if c["name"] == "gcs-data-sync")
    assert "gsutil cp" not in staging["command"][2]
    assert "<<" not in staging["command"][2]  # no heredoc to terminate early


# --- notebook stop path: outputs sync and script capture ------------------------------------


SCRIPT_NAMES = ["plain.py", "sp ace.ipynb", "$(touch PWNED).R", "it's.Rmd", "nl\nx.py", "br[1].py", "-rf.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", [GcsStorageProvider(), S3StorageProvider()], ids=["gcs", "s3"])
async def test_notebook_stop_captures_every_script_once_as_data(sh, tmp_path, storage):
    from app.adapters.notebooks.kubernetes import HOME_DIR

    home = tmp_path / HOME_DIR.strip("/").replace("/", "_")
    (home / "nb").mkdir(parents=True)
    for name in SCRIPT_NAMES:
        (home / "nb" / name).write_text("x")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "o$(touch PWNED).csv").write_text("1")

    adapter = _notebook_adapter()
    adapter._get_k8s_core_client = MagicMock(return_value=MagicMock())
    storage.list_objects = AsyncMock(return_value=[])
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=storage),
        patch("kubernetes.stream.stream", return_value="") as stream,
    ):
        await adapter._k8s_terminate_session(
            session_id=42, pod_name="p", working_bucket="w", gcs_home_prefix=storage.build_uri("w", "notebooks/7/")
        )
    sidecar = [c.kwargs["command"][2] for c in stream.call_args_list if c.kwargs.get("container") == "gcs-sync"]
    outputs_shell, scripts_shell = sidecar[0], sidecar[1]
    sh.run(_mapped(outputs_shell, tmp_path, "/outputs"))
    sh.run(_mapped(scripts_shell, tmp_path, HOME_DIR))

    calls = [c for c in sh.calls() if c[0] in ("storage", "s3", "-m")]
    sync, copies = calls[0], calls[1:]
    assert sync[-2:] == [str(outputs), storage.build_uri("w", "sessions/42/outputs/")]
    scripts = storage.build_uri("w", "sessions/42/scripts/")
    escape = gcs_escape if isinstance(storage, GcsStorageProvider) else (lambda p: p)
    assert sorted(c[-2:] for c in copies) == sorted([escape(str(home / "nb" / n)), scripts] for n in SCRIPT_NAMES)


@pytest.mark.asyncio
async def test_notebook_stop_has_no_gsutil_copy_bypass():
    adapter = _notebook_adapter()
    adapter._get_k8s_core_client = MagicMock(return_value=MagicMock())
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=GcsStorageProvider()),
        patch("kubernetes.stream.stream", return_value="") as stream,
    ):
        await adapter._k8s_terminate_session(session_id=42, pod_name="p", working_bucket="w")
    assert "gsutil cp" not in str(stream.call_args_list)


# --- cellxgene ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("has_key", [False, True])
async def test_cellxgene_downloads_the_dataset_literally(sh, tmp_path, has_key):
    from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider

    provider = KubernetesCellxgeneProvider()
    provider._cluster_config = {}
    provider._namespace_ready = True
    apps = MagicMock()
    uri = "gs://results/pub/d$(touch PWNED) 'x'[1].h5ad"
    with (
        patch.object(provider, "_get_api_client_async", new_callable=AsyncMock),
        patch.object(provider, "_resolve_image", new_callable=AsyncMock, return_value="img"),
        patch.object(provider, "_ensure_gcp_secret", new_callable=AsyncMock, return_value=has_key),
        patch.object(provider, "_get_k8s_apps_client", return_value=apps),
        patch.object(provider, "_get_k8s_core_client", return_value=MagicMock()),
        patch("app.adapters.registry.get_storage_adapter", return_value=GcsStorageProvider()),
        patch("asyncio.create_task"),
    ):
        await provider.deploy(1, uri, "d")
    deployment = apps.create_namespaced_deployment.call_args.kwargs["body"]
    command = deployment.spec.template.spec.init_containers[0].command[2]
    (tmp_path / "data").mkdir()
    sh.put(uri, b"h5ad bytes")
    sh.run(_mapped(command, tmp_path, "/data", "/gcp"))
    # `[1]` in the key: the CLI would read it as a pattern, so the verified JSON API fetch is used.
    assert_gcs_fetched(sh, [(uri, f"{tmp_path}/data/dataset.h5ad")])


# --- custom pipeline staging and EXIT trap --------------------------------------------------


@pytest.mark.parametrize("storage", [GcsStorageProvider(), S3StorageProvider()], ids=["gcs", "s3"])
def test_custom_pipeline_stage_commands_are_literal(sh, tmp_path, storage):
    from app.services.custom_pipeline_service import CustomPipelineService

    specs = [{"relative_path": f"P/E/{n}", "gcs_uri": storage.build_uri("raw", f"u/{n}")} for n in HOSTILE]
    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        commands = CustomPipelineService._build_stage_commands(specs)
    for spec in specs:
        if isinstance(storage, GcsStorageProvider):
            sh.put(spec["gcs_uri"], spec["gcs_uri"].encode())
    sh.run(_mapped(" && ".join(commands), tmp_path, "/data", "/secrets"))
    if isinstance(storage, GcsStorageProvider):
        assert_gcs_fetched(sh, [(s["gcs_uri"], f"{tmp_path}/data/{s['relative_path']}") for s in specs])
    else:
        copies = [c for c in sh.calls() if c[:2] == ["s3", "cp"]]
        assert [c[-2:] for c in copies] == [[s["gcs_uri"], f"{tmp_path}/data/{s['relative_path']}"] for s in specs]


def test_custom_pipeline_stage_refuses_an_escaping_relative_path():
    from app.services.custom_pipeline_service import CustomPipelineService

    with patch("app.adapters.registry.get_storage_adapter", return_value=GcsStorageProvider()):
        with pytest.raises(ValidationError):
            CustomPipelineService._build_stage_commands([{"relative_path": "../x", "gcs_uri": "gs://raw/x"}])


@pytest.mark.parametrize("storage", [GcsStorageProvider(), S3StorageProvider()], ids=["gcs", "s3"])
@pytest.mark.parametrize("exit_code", [0, 3])
def test_custom_pipeline_exit_trap_copies_outputs_and_keeps_the_exit_status(sh, tmp_path, storage, exit_code):
    from app.services.custom_pipeline_service import CustomPipelineService

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    for n in ["r.csv", "a b.csv", "$(touch PWNED).csv", "-rf", "g[1].csv"]:
        (outputs / n).write_text(n)
    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        wrapper = CustomPipelineService._build_entrypoint_wrapper(f"exit {exit_code}", "res", "projects/4", 9)
    result = sh.run(_mapped(wrapper, tmp_path, "/outputs", "/secrets"), check=False)
    assert result.returncode == exit_code
    target = storage.build_uri("res", "projects/4/pipeline-runs/9/")
    (call,) = [c for c in sh.calls() if c[:2] in (["storage", "rsync"], ["s3", "cp"])]
    if isinstance(storage, GcsStorageProvider):
        assert call == [
            "storage",
            "rsync",
            "--recursive",
            "--exclude=(?s).*[\\r\\n].*|.*#[0-9]+$",
            str(outputs),
            target,
        ]
    else:
        assert call == ["s3", "cp", "--recursive", str(outputs), target]


@pytest.mark.parametrize("storage", [GcsStorageProvider(), S3StorageProvider()], ids=["gcs", "s3"])
@pytest.mark.parametrize("exit_code,expected", [(0, 1), (3, 3)])
def test_a_failed_output_copy_fails_the_run(sh, tmp_path, storage, exit_code, expected):
    """Outputs that never reached storage are not a completed run. A pipeline that already failed
    keeps its own status; one that succeeded becomes a failure, with the reason on stderr."""
    from app.services.custom_pipeline_service import CustomPipelineService

    (tmp_path / "outputs").mkdir()
    for tool in ("gcloud", "aws"):
        (sh.bin / tool).write_text("#!/bin/sh\necho 'storage unreachable' >&2\nexit 1\n")
    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        wrapper = CustomPipelineService._build_entrypoint_wrapper(f"exit {exit_code}", "res", "projects/4", 9)
    result = sh.run(_mapped(wrapper, tmp_path, "/outputs", "/secrets"), check=False)
    assert result.returncode == expected
    assert b"could not be copied" in result.stderr


# --- environment build template -------------------------------------------------------------


def _hcl_inline_commands(template: str) -> list[str]:
    """The shell lines of every provisioner `inline` list, with HCL string escapes undone."""
    lines = []
    for block in re.findall(r"inline = \[(.*?)\]", template, re.S):
        for literal in re.findall(r'^\s*("(?:[^"\\]|\\.)*"),?\s*$', block, re.M):
            lines.append(json.loads(literal).replace("$${", "${").replace("%%{", "%{"))
    return lines


def test_environment_build_downloads_its_yml_literally(sh, tmp_path):
    from app.services.environment_build_service import _render_packer_vm_template

    uri = 'gs://work/builds/my "env" $(touch PWNED) ${x}/v1/environment.yml'
    template = _render_packer_vm_template(GcsStorageProvider(), uri)
    (download,) = [line for line in _hcl_inline_commands(template) if "environment.yml" in line and "cp" in line]
    sh.run(_mapped(download, tmp_path, "/tmp/environment.yml"))
    assert sh.calls() == [["storage", "cp", gcs_escape(uri), f"{tmp_path}/tmp_environment.yml"]]


# --- work-node VM startup scripts -----------------------------------------------------------


@pytest.mark.parametrize(
    "module,builder,tool", [("gce", "_build_startup_script", "gsutil"), ("ec2", "_build_ec2_startup_script", "aws")]
)
def test_work_node_startup_stages_inputs_literally(sh, tmp_path, module, builder, tool):
    import importlib

    build = getattr(importlib.import_module(f"app.adapters.work_nodes.{module}"), builder)
    scheme = "gs" if module == "gce" else "s3"
    files = [{"relative_path": f"P/{n}", "gcs_uri": f"{scheme}://raw/u/{n}"} for n in HOSTILE]
    script = build({"input_files": files, "username": "u", "session_id": 1})
    staging = script[script.index("mkdir -p /data") : script.index("# 5. Create output")]
    if module == "gce":
        for f in files:
            sh.put(f["gcs_uri"], f["gcs_uri"].encode())
    sh.run(_mapped(staging, tmp_path, "/data"))
    pairs = [(f["gcs_uri"], f"{tmp_path}/data/{f['relative_path']}") for f in files]
    if module == "gce":
        # Plain names keep `gsutil cp`; names gsutil cannot represent use the adapter's verified
        # JSON API fetch (shared with notebook and custom-pipeline staging).
        assert_gcs_fetched(sh, pairs, cp=("cp",))
    else:
        copies = [c for c in sh.calls() if "cp" in c]
        assert [c[-2:] for c in copies] == [[uri, dest] for uri, dest in pairs]
