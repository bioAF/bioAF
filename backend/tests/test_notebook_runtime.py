"""Plan 03.5B: one notebook runtime contract for both image build paths.

Both the built-in scientific image and notebook EnvironmentVersions build on the same pinned base, run
as the unprivileged notebook user, carry a checksum-verified RStudio Server Open Source, and end with
the same capability check, which fails the build unless Jupyter runs a Python cell as that user (and,
for environments with R, R evaluates and RStudio serves its sign-in page). The check script itself is
run for real here against a local Jupyter when one is available.
"""

import json
import os
import re
import subprocess

import pytest

from app.adapters.image_build.base import BuildResult
from app.services import notebook_runtime
from app.services.environment_build_service import _build_conda_dockerfile
from app.services.environment_service import DEFAULT_NOTEBOOK_CONDA_YML
from app.services.notebook_image_service import _render_dockerfile

NOTEBOOK_YML_WITHOUT_R = "name: py-only\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.11\n  - jupyterlab\n"


def _final_user(dockerfile: str) -> str:
    return re.findall(r"^USER\s+(\S+)", dockerfile, re.M)[-1]


def _scientific():
    from unittest.mock import MagicMock, patch

    adapter = MagicMock()
    adapter.image_storage_pip_packages.return_value = "google-cloud-storage==2.18.2"
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        return _render_dockerfile()


RENDERERS = {
    "scientific": _scientific,
    "default-notebook": lambda: _build_conda_dockerfile(
        DEFAULT_NOTEBOOK_CONDA_YML, "Default Notebook", environment_type="notebook"
    )[0],
    "custom-without-r": lambda: _build_conda_dockerfile(NOTEBOOK_YML_WITHOUT_R, "Py", environment_type="notebook")[0],
}


@pytest.mark.parametrize("which", list(RENDERERS))
def test_the_dockerfile_builds_without_buildkit(which):
    # Cloud Build's docker step (and CodeBuild's) use the classic builder, which refuses BuildKit-only
    # syntax: a live Cloud Build failed on `COPY --chmod` (2026-11-26, blackbird).
    dockerfile = RENDERERS[which]()
    for line in dockerfile.splitlines():
        instruction = line.strip().split(" ", 1)[0].upper()
        if instruction in ("COPY", "ADD"):
            assert not re.search(r"--(chmod|link|parents|exclude)\b", line), line
        if instruction == "RUN":
            assert "--mount" not in line and "<<" not in line, line
    assert re.search(r"^RUN chmod 0755 /usr/local/bin/bioaf-notebook-check$", dockerfile, re.M)


@pytest.mark.parametrize("which", list(RENDERERS))
def test_both_build_paths_share_one_pinned_unprivileged_runtime(which):
    dockerfile = RENDERERS[which]()
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert from_lines == [f"FROM {notebook_runtime.NOTEBOOK_BASE_IMAGE}"]
    assert re.search(r"@sha256:[0-9a-f]{64}$", notebook_runtime.NOTEBOOK_BASE_IMAGE)
    assert _final_user(dockerfile) == "1000"
    # The capability check runs as the notebook user at the end of every build.
    last = dockerfile.rstrip().splitlines()[-1]
    assert last.startswith("RUN ") and last.endswith(notebook_runtime.CHECK_PATH)
    assert "BIOAF_EXPECTED_UID=1000" in last
    assert notebook_runtime.RSTUDIO_SHA256 in dockerfile
    assert "sha256sum -c" in dockerfile


def test_rstudio_is_pinned_and_verified_before_install():
    layer = notebook_runtime.runtime_layer(r_home=None)
    assert notebook_runtime.RSTUDIO_DEB in layer
    assert re.fullmatch(r"[0-9a-f]{64}", notebook_runtime.RSTUDIO_SHA256)
    verify = layer.index("sha256sum -c")
    install = layer.index("gdebi -n")
    assert verify < install


def test_rstudio_supports_the_r_conda_installs():
    # Conda installs current R (4.5.3 on 2026-11-26); RStudio 2024.04 could not start an R session
    # with it (undefined symbol Rf_countContexts, seen live on GKE).
    assert notebook_runtime.RSTUDIO_DEB == "rstudio-server-2026.11.1-204-amd64.deb"
    assert notebook_runtime.RSTUDIO_SHA256 == "9119d8a833dc3ffb12c27f66d9f42e35159fdf57da18c640608c82dfa3e8c1d2"


def test_a_conda_r_session_loads_the_environments_own_libraries():
    # Conda's R needs conda's libstdc++ (CXXABI_1.3.15), which rsession does not load by default.
    dockerfile, _ = _build_conda_dockerfile(DEFAULT_NOTEBOOK_CONDA_YML, "Default Notebook", environment_type="notebook")
    assert "rsession-ld-library-path=/opt/conda/envs/bioaf-notebook/lib" in dockerfile


def test_the_check_proves_rstudio_can_start_an_r_session():
    # Serving the sign-in page and running R on the command line are not enough: the build must fail
    # unless RStudio itself starts an R session with the configured R and libraries.
    script = notebook_runtime.CHECK_SCRIPT
    assert "--verify-installation 1" in script
    assert "rsession-ld-library-path" in script
    assert "RStudio could not start an R session" in script


def test_a_conda_environment_with_r_wires_rstudio_to_that_r():
    dockerfile, _ = _build_conda_dockerfile(DEFAULT_NOTEBOOK_CONDA_YML, "Default Notebook", environment_type="notebook")
    assert "rsession-which-r=/opt/conda/envs/bioaf-notebook/bin/R" in dockerfile
    assert "BIOAF_REQUIRE_RSTUDIO=1" in dockerfile


def test_a_conda_environment_without_r_does_not_claim_rstudio():
    dockerfile, _ = _build_conda_dockerfile(NOTEBOOK_YML_WITHOUT_R, "Py", environment_type="notebook")
    assert "BIOAF_REQUIRE_RSTUDIO=0" in dockerfile


def test_pipeline_conda_images_keep_their_own_builder():
    dockerfile, _ = _build_conda_dockerfile(NOTEBOOK_YML_WITHOUT_R, "Pipe", environment_type="pipeline")
    assert notebook_runtime.NOTEBOOK_BASE_IMAGE not in dockerfile
    assert "bioaf-notebook-check" not in dockerfile


DIGEST = "sha256:" + "b" * 64


@pytest.mark.parametrize("rstudio", ["verified", "unsupported"])
def test_a_successful_build_records_what_its_check_reported(rstudio):
    # The check's own result, read out of the built image and bound to the pushed digest.
    result = BuildResult("SUCCESS", DIGEST, {"contract": 2, "jupyter": "verified", "rstudio": rstudio})
    capability = notebook_runtime.capability_from_build(
        "conda", DEFAULT_NOTEBOOK_CONDA_YML, result, image_uri="reg/img:v1.1", build_id="b-1"
    )
    assert capability["contract"] == notebook_runtime.CONTRACT_VERSION
    assert (capability["jupyter"], capability["rstudio"]) == ("verified", rstudio)
    assert capability["image_uri"] == "reg/img:v1.1" and capability["build_id"] == "b-1"


def test_a_definition_without_the_check_is_never_claimed_compatible():
    # A raw Dockerfile definition is the user's own; the runtime check is not part of it.
    result = BuildResult("SUCCESS", DIGEST, {"contract": 2, "jupyter": "verified", "rstudio": "verified"})
    capability = notebook_runtime.capability_from_build(
        "dockerfile", "FROM python:3.11\n", result, image_uri="x", build_id="b"
    )
    assert capability["jupyter"] == "unverified" and capability["rstudio"] == "unverified"


# -- The check script, for real ------------------------------------------------------------------

JUPYTER = os.environ.get("BIOAF_TEST_JUPYTER_BIN")


@pytest.mark.skipif(not JUPYTER, reason="BIOAF_TEST_JUPYTER_BIN not set")
def test_the_check_passes_when_jupyter_runs_a_python_cell(tmp_path):
    script = tmp_path / "check"
    script.write_text(notebook_runtime.CHECK_SCRIPT)
    script.chmod(0o755)
    env = {
        **os.environ,
        "PATH": os.path.dirname(str(JUPYTER)) + ":" + os.environ["PATH"],
        "BIOAF_EXPECTED_UID": str(os.getuid()),
        "BIOAF_REQUIRE_RSTUDIO": "0",
        "BIOAF_CAPABILITY_FILE": str(tmp_path / "capability.json"),
        "HOME": str(tmp_path),
    }
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / "capability.json").read_text()) == {
        "contract": 2,
        "jupyter": "verified",
        "rstudio": "unsupported",
    }


@pytest.mark.skipif(not JUPYTER, reason="BIOAF_TEST_JUPYTER_BIN not set")
def test_the_check_fails_as_the_wrong_user(tmp_path):
    script = tmp_path / "check"
    script.write_text(notebook_runtime.CHECK_SCRIPT)
    script.chmod(0o755)
    env = {
        **os.environ,
        "PATH": os.path.dirname(str(JUPYTER)) + ":" + os.environ["PATH"],
        "BIOAF_EXPECTED_UID": "4242",
        "HOME": str(tmp_path),
    }
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "runs as uid" in result.stdout + result.stderr


def test_the_check_fails_without_jupyter(tmp_path):
    script = tmp_path / "check"
    script.write_text(notebook_runtime.CHECK_SCRIPT)
    script.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "BIOAF_EXPECTED_UID": str(os.getuid()), "HOME": str(tmp_path)}
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "Jupyter" in result.stdout + result.stderr


def test_the_check_records_the_current_contract():
    assert notebook_runtime.CONTRACT_VERSION == 2
    assert f'"contract": {notebook_runtime.CONTRACT_VERSION},' in notebook_runtime.CHECK_SCRIPT


def test_rstudio_proven_under_an_older_contract_is_no_longer_claimed():
    # Contract 1 never proved RStudio could start an R session; Jupyter's check is unchanged.
    from app.schemas.environment import notebook_capability

    shown = notebook_capability({"contract": 1, "jupyter": "verified", "rstudio": "verified"})
    assert (shown.jupyter, shown.rstudio) == ("verified", "unverified")
    current = notebook_capability({"contract": 2, "jupyter": "verified", "rstudio": "verified"})
    assert current.rstudio == "verified"
    unsupported = notebook_capability({"contract": 1, "jupyter": "verified", "rstudio": "unsupported"})
    assert unsupported.rstudio == "unsupported"
