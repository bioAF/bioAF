"""The notebook runtime contract (plan 03.5B): what every notebook image must provide and prove.

Both build paths use it: the built-in scientific image (``notebook_image_service``) and notebook
EnvironmentVersions (``environment_build_service``). Each image

- starts from one pinned base (Ubuntu 22.04 with the ``jovyan`` notebook user, uid 1000 / gid 100);
- installs RStudio Server Open Source from a pinned package whose SHA-256 is checked before install;
- ends by running ``bioaf-notebook-check`` as the notebook user, which fails the build unless Jupyter
  executes a Python cell as that user and, when the environment has R, R evaluates an expression and
  RStudio Server serves its sign-in page.

A successful build therefore proves the capabilities its definition declares; the result is recorded
on the EnvironmentVersion with this contract's version. Images built before this contract, and user
Dockerfiles that do not include it, are "unverified", never assumed compatible.

The RStudio PAM exception: rserver itself runs as root (it authenticates against PAM and starts each
R session as the signed-in user); R analysis runs as that user. Jupyter never runs as root.
"""

from __future__ import annotations

import re

CONTRACT_VERSION = 1

# Shared with plan 05A's image-input pinning: one notebook base for both build paths.
NOTEBOOK_BASE_IMAGE = "jupyter/scipy-notebook@sha256:fca4bcc9cbd49d9a15e0e4df6c666adf17776c950da9fa94a4f0a045d5c4ad33"
NOTEBOOK_UID = 1000
NOTEBOOK_GID = 100

# RStudio Server Open Source for Ubuntu 22.04 (jammy), amd64. SHA-256 computed from the package
# downloaded over HTTPS from download2.rstudio.org on 2026-11-25.
RSTUDIO_DEB = "rstudio-server-2024.04.2-764-amd64.deb"
RSTUDIO_URL = f"https://download2.rstudio.org/server/jammy/amd64/{RSTUDIO_DEB}"
RSTUDIO_SHA256 = "d8ffb8e5c2dadb1c74df037c291a5585e595d978b3aec48a4a94a970e04a947d"

CHECK_PATH = "/usr/local/bin/bioaf-notebook-check"
CAPABILITY_FILE = "/etc/bioaf/notebook-capability.json"

# Run at the end of the build as the notebook user. Exits nonzero (failing the build) when a capability
# the image must have is missing. No network access is needed.
CHECK_SCRIPT = r"""#!/usr/bin/env bash
set -uo pipefail
expected_uid="${BIOAF_EXPECTED_UID:-1000}"
require_rstudio="${BIOAF_REQUIRE_RSTUDIO:-0}"
capability_file="${BIOAF_CAPABILITY_FILE:-/etc/bioaf/notebook-capability.json}"
fail() { echo "bioAF notebook check failed: $*"; exit 1; }

[ "$(id -u)" = "$expected_uid" ] || fail "the notebook runs as uid $(id -u), expected $expected_uid"
[ "$(id -u)" != 0 ] || fail "the notebook would run as root"
command -v jupyter >/dev/null 2>&1 || fail "Jupyter is not installed"
jupyter server --version >/dev/null 2>&1 || jupyter lab --version >/dev/null 2>&1 || fail "Jupyter Server does not start"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cat > "$work/check.ipynb" <<'NB'
{"cells": [{"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [],
  "source": ["import os\n", "assert os.getuid() != 0\n", "print(6 * 7)"]}],
 "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
 "nbformat": 4, "nbformat_minor": 5}
NB
jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=120 \
    --output executed.ipynb "$work/check.ipynb" >/dev/null 2>&1 || fail "Jupyter could not execute a Python cell"
grep -q '"42' "$work/executed.ipynb" || fail "the Python cell did not produce its output"

rstudio=unsupported
if [ "$require_rstudio" = 1 ]; then
    command -v R >/dev/null 2>&1 || fail "R is not installed"
    [ "$(R --slave -e 'cat(1 + 1)' 2>/dev/null)" = 2 ] || fail "R could not evaluate an expression"
    [ -x /usr/lib/rstudio-server/bin/rserver ] || fail "RStudio Server is not installed"
    [ -f /etc/bioaf/rstudio-serves ] || fail "RStudio Server did not serve its sign-in page during the build"
    rstudio=verified
fi
printf '{"contract": 1, "jupyter": "verified", "rstudio": "%s"}\n' "$rstudio" > "$capability_file" \
    || fail "could not record the result"
echo "bioAF notebook check passed (jupyter verified, rstudio $rstudio)"
"""


def runtime_layer(*, r_home: str | None) -> str:
    """Dockerfile lines (run as root) installing RStudio Server and the check script.

    ``r_home`` is the R installation RStudio uses (a conda environment's prefix); None means the
    system R on PATH. The build starts rserver once, as root, to prove it serves its sign-in page.
    """
    which_r = f"{r_home}/bin/R" if r_home else "$(command -v R)"
    lines = [
        "",
        "# ---- bioAF notebook runtime contract (plan 03.5B) ----",
        "USER root",
        "RUN apt-get update && apt-get install -y --no-install-recommends gdebi-core wget curl \\",
        f"    && wget -q {RSTUDIO_URL} \\",
        f"    && echo '{RSTUDIO_SHA256}  {RSTUDIO_DEB}' | sha256sum -c - \\",
        f"    && gdebi -n {RSTUDIO_DEB} \\",
        f"    && rm {RSTUDIO_DEB} \\",
        "    && rm -rf /var/lib/apt/lists/*",
        "RUN mkdir -p /etc/bioaf /etc/rstudio \\",
        f'    && if [ -x "{which_r}" ]; then echo "rsession-which-r={which_r}" > /etc/rstudio/rserver.conf; fi \\',
        "    && if command -v R >/dev/null 2>&1 || [ -s /etc/rstudio/rserver.conf ]; then \\",
        "         (/usr/lib/rstudio-server/bin/rserver --server-daemonize=0 --www-port=8787 &) \\",
        "         && for i in $(seq 1 30); do curl -sf http://127.0.0.1:8787/auth-sign-in >/dev/null && break; sleep 1; done \\",
        "         && curl -sf http://127.0.0.1:8787/auth-sign-in >/dev/null && touch /etc/bioaf/rstudio-serves; \\",
        "         pkill -f rserver || true; \\",
        "       fi",
        f"COPY --chmod=0755 bioaf-notebook-check {CHECK_PATH}",
        f"RUN chown {NOTEBOOK_UID}:{NOTEBOOK_GID} /etc/bioaf",
    ]
    return "\n".join(lines) + "\n"


def check_step(*, require_rstudio: bool) -> str:
    """The final lines: switch to the notebook user and prove the image."""
    flag = "1" if require_rstudio else "0"
    return (
        f"\nUSER {NOTEBOOK_UID}\nWORKDIR /home/jovyan\n"
        f"RUN BIOAF_EXPECTED_UID={NOTEBOOK_UID} BIOAF_REQUIRE_RSTUDIO={flag} {CHECK_PATH}\n"
    )


def conda_has_r(definition_content: str) -> bool:
    import yaml

    try:
        data = yaml.safe_load(definition_content) or {}
    except yaml.YAMLError:
        return False
    deps = data.get("dependencies") or [] if isinstance(data, dict) else []
    names = [re.split(r"[=<> ]", d.strip(), maxsplit=1)[0] for d in deps if isinstance(d, str)]
    return "r-base" in names or "r" in names


def capability_after_build(definition_format: str, definition_content: str, *, image_uri: str, build_id: str) -> dict:
    """What a successful build of this definition proved (the check ran and passed), or "unverified".

    A conda notebook definition always gets the check. A Dockerfile definition is proven only when it
    ends with the contract's own check step (the system scientific template); any other Dockerfile is
    the user's own and stays unverified.
    """
    if definition_format == "conda":
        jupyter = "verified"
        rstudio = "verified" if conda_has_r(definition_content) else "unsupported"
    elif definition_content.rstrip().endswith(check_step(require_rstudio=True).rstrip()):
        jupyter = rstudio = "verified"
    elif definition_content.rstrip().endswith(check_step(require_rstudio=False).rstrip()):
        jupyter, rstudio = "verified", "unsupported"
    else:
        jupyter = rstudio = "unverified"
    return {
        "contract": CONTRACT_VERSION,
        "jupyter": jupyter,
        "rstudio": rstudio,
        "image_uri": image_uri,
        "build_id": build_id,
    }


def add_check_script(tar) -> None:
    """Put the check script in a build context (tarfile open for writing) as ``bioaf-notebook-check``."""
    import io
    import tarfile
    import time

    data = CHECK_SCRIPT.encode()
    info = tarfile.TarInfo(name="bioaf-notebook-check")
    info.size = len(data)
    info.mode = 0o755
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))
