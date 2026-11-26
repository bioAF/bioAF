"""The notebook runtime contract (plan 03.5B): what every notebook image must provide and prove.

Both build paths use it: the built-in scientific image (``notebook_image_service``) and notebook
EnvironmentVersions (``environment_build_service``). Each image

- starts from one pinned base (Ubuntu 22.04 with the ``jovyan`` notebook user, uid 1000 / gid 100);
- installs RStudio Server Open Source from a pinned package whose SHA-256 is checked before install;
- ends by running ``bioaf-notebook-check`` as the notebook user, which fails the build unless Jupyter
  executes a Python cell as that user and, when the environment has R, R evaluates an expression and
  RStudio Server serves its sign-in page.

The build provider reads the check's result back out of the image it built and returns it with the
pushed digest; only that evidence is recorded as "verified" on the EnvironmentVersion, and sessions
launch by that digest. Images built before this contract, and user
Dockerfiles that do not include it, are "unverified", never assumed compatible.

The RStudio PAM exception: rserver itself runs as root (it authenticates against PAM and starts each
R session as the signed-in user); R analysis runs as that user. Jupyter never runs as root.
"""

from __future__ import annotations

import re

from app.adapters.image_build import base as image_build_base
from app.adapters.image_build.base import BuildResult

# 2: RStudio must start an R session during the build (1 only proved its sign-in page served).
CONTRACT_VERSION = 2

# Shared with plan 05A's image-input pinning: one notebook base for both build paths.
NOTEBOOK_BASE_IMAGE = "jupyter/scipy-notebook@sha256:fca4bcc9cbd49d9a15e0e4df6c666adf17776c950da9fa94a4f0a045d5c4ad33"
NOTEBOOK_UID = 1000
NOTEBOOK_GID = 100

# RStudio Server Open Source for Ubuntu 22.04 (jammy), amd64. SHA-256 computed from the package
# downloaded over HTTPS from download2.rstudio.org on 2026-11-26. It must support the R conda installs
# (4.5 today): 2024.04 could not start an R 4.5 session.
RSTUDIO_DEB = "rstudio-server-2026.11.1-204-amd64.deb"
RSTUDIO_URL = f"https://download2.rstudio.org/server/jammy/amd64/{RSTUDIO_DEB}"
RSTUDIO_SHA256 = "9119d8a833dc3ffb12c27f66d9f42e35159fdf57da18c640608c82dfa3e8c1d2"

CHECK_PATH = "/usr/local/bin/bioaf-notebook-check"
# Read back out of the built image by the build provider as the build's evidence.
CAPABILITY_FILE = image_build_base.CAPABILITY_FILE

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
    --output executed.ipynb "$work/check.ipynb" >"$work/nbconvert.log" 2>&1 \
    || fail "Jupyter could not execute a Python cell: $(tail -c 600 "$work/nbconvert.log")"
grep -q '"42' "$work/executed.ipynb" || fail "the Python cell did not produce its output"

rstudio=unsupported
if [ "$require_rstudio" = 1 ]; then
    command -v R >/dev/null 2>&1 || fail "R is not installed"
    [ "$(R --slave -e 'cat(1 + 1)' 2>/dev/null)" = 2 ] || fail "R could not evaluate an expression"
    [ -x /usr/lib/rstudio-server/bin/rserver ] || fail "RStudio Server is not installed"
    [ -f /etc/bioaf/rstudio-serves ] || fail "RStudio Server did not serve its sign-in page during the build"
    # RStudio must start an R session itself, with the R and libraries rserver gives it: running R on
    # the command line does not prove that (an rsession can fail to load the R a conda env installs).
    conf=/etc/rstudio/rserver.conf
    which_r="$(sed -n 's/^rsession-which-r=//p' "$conf" 2>/dev/null)"
    which_r="${which_r:-$(command -v R)}"
    r_home="$("$which_r" RHOME 2>/dev/null)"
    # The directories rserver exports for rsession, as R itself reports them (system R keeps share,
    # include and doc outside R_HOME; a conda R keeps them inside).
    r_share="$("$which_r" --vanilla --slave -e 'cat(R.home("share"))' 2>/dev/null)"
    r_include="$("$which_r" --vanilla --slave -e 'cat(R.home("include"))' 2>/dev/null)"
    r_doc="$("$which_r" --vanilla --slave -e 'cat(R.home("doc"))' 2>/dev/null)"
    ld_extra="$(sed -n 's/^rsession-ld-library-path=//p' "$conf" 2>/dev/null)"
    R_HOME="$r_home" R_SHARE_DIR="$r_share" R_INCLUDE_DIR="$r_include" R_DOC_DIR="$r_doc" \
        LD_LIBRARY_PATH="$r_home/lib${ld_extra:+:$ld_extra}" \
        timeout 120 /usr/lib/rstudio-server/bin/rsession --verify-installation 1 >"$work/rsession.log" 2>&1 \
        || fail "RStudio could not start an R session: $(tail -c 300 "$work/rsession.log")"
    rstudio=verified
fi
printf '{"contract": 2, "jupyter": "verified", "rstudio": "%s"}\n' "$rstudio" > "$capability_file" \
    || fail "could not record the result"
echo "bioAF notebook check passed (jupyter verified, rstudio $rstudio)"
"""


def runtime_layer(*, r_home: str | None) -> str:
    """Dockerfile lines (run as root) installing RStudio Server and the check script.

    ``r_home`` is the R installation RStudio uses (a conda environment's prefix); None means the
    system R on PATH. The build starts rserver once, as root, to prove it serves its sign-in page.
    """
    which_r = f"{r_home}/bin/R" if r_home else "$(command -v R)"
    ld_line = f' && echo "rsession-ld-library-path={r_home}/lib" >> /etc/rstudio/rserver.conf' if r_home else ""
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
        # A conda R also needs its environment's own libraries (libstdc++) loaded into the R session.
        f'    && if [ -x "{which_r}" ]; then echo "rsession-which-r={which_r}" > /etc/rstudio/rserver.conf{ld_line}; fi \\',
        "    && if command -v R >/dev/null 2>&1 || [ -s /etc/rstudio/rserver.conf ]; then \\",
        "         (/usr/lib/rstudio-server/bin/rserver --server-daemonize=0 --www-port=8787 &) \\",
        "         && for i in $(seq 1 30); do curl -sf http://127.0.0.1:8787/auth-sign-in >/dev/null && break; sleep 1; done \\",
        "         && curl -sf http://127.0.0.1:8787/auth-sign-in >/dev/null && touch /etc/bioaf/rstudio-serves; \\",
        "         pkill -f rserver || true; \\",
        "       fi",
        # Plain COPY + chmod: build services use the classic builder, which refuses COPY --chmod.
        f"COPY bioaf-notebook-check {CHECK_PATH}",
        f"RUN chmod 0755 {CHECK_PATH}",
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


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _runs_the_check(definition_format: str, definition_content: str) -> bool:
    """Whether bioAF's own check is the definition's last step, so its result cannot be overwritten.

    A conda notebook definition always gets the check. A Dockerfile counts only when it ends with the
    contract's check step (the system scientific template); a user's own Dockerfile could write the
    capability file itself, so its evidence proves nothing.
    """
    if definition_format == "conda":
        return True
    content = definition_content.rstrip()
    return any(content.endswith(check_step(require_rstudio=r).rstrip()) for r in (True, False))


def capability_from_build(
    definition_format: str, definition_content: str, result: BuildResult, *, image_uri: str, build_id: str
) -> dict:
    """What a successful build proved: the check's own result read out of the built image, bound to
    the pushed digest. Anything less (no evidence, no digest, another contract, a definition whose
    last step is not the check) records "unverified", never an inferred claim.
    """
    evidence = result.evidence or {}
    digest = result.image_digest if result.image_digest and _DIGEST.fullmatch(result.image_digest) else None
    proven = (
        digest is not None
        and evidence.get("contract") == CONTRACT_VERSION
        and _runs_the_check(definition_format, definition_content)
    )
    jupyter = rstudio = "unverified"
    if proven:
        jupyter = "verified" if evidence.get("jupyter") == "verified" else "unverified"
        rstudio = evidence.get("rstudio") if evidence.get("rstudio") in ("verified", "unsupported") else "unverified"
    return {
        "contract": CONTRACT_VERSION,
        "jupyter": jupyter,
        "rstudio": rstudio,
        "image_uri": image_uri,
        "image_digest": digest,
        "build_id": build_id,
    }


def launch_image(image_uri: str, capability: dict | None) -> str:
    """The image a session launches: pinned to the recorded digest when there is one, so a moved tag
    never changes what a verified version runs."""
    digest = (capability or {}).get("image_digest")
    if not digest or not _DIGEST.fullmatch(digest):
        return image_uri
    name = image_uri.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name = name[: len(name) - len(last)] + last.split(":", 1)[0]
    return f"{name}@{digest}"


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
