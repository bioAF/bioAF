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


@pytest.mark.parametrize(
    "definition, expected",
    [
        (DEFAULT_NOTEBOOK_CONDA_YML, {"jupyter": "verified", "rstudio": "verified"}),
        (NOTEBOOK_YML_WITHOUT_R, {"jupyter": "verified", "rstudio": "unsupported"}),
    ],
)
def test_a_successful_build_records_what_its_check_proved(definition, expected):
    capability = notebook_runtime.capability_after_build("conda", definition, image_uri="reg/img:v1.1", build_id="b-1")
    assert capability["contract"] == notebook_runtime.CONTRACT_VERSION
    assert {k: capability[k] for k in ("jupyter", "rstudio")} == expected
    assert capability["image_uri"] == "reg/img:v1.1" and capability["build_id"] == "b-1"


def test_a_definition_without_the_check_is_never_claimed_compatible():
    # A raw Dockerfile definition is the user's own; the runtime check is not part of it.
    capability = notebook_runtime.capability_after_build(
        "dockerfile", "FROM python:3.11\n", image_uri="x", build_id="b"
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
        "PATH": os.path.dirname(JUPYTER) + ":" + os.environ["PATH"],
        "BIOAF_EXPECTED_UID": str(os.getuid()),
        "BIOAF_REQUIRE_RSTUDIO": "0",
        "BIOAF_CAPABILITY_FILE": str(tmp_path / "capability.json"),
        "HOME": str(tmp_path),
    }
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / "capability.json").read_text()) == {
        "contract": 1,
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
        "PATH": os.path.dirname(JUPYTER) + ":" + os.environ["PATH"],
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
