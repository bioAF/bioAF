"""gsutil sync command builders + output parsing for the K8s notebook adapter.

These are notebook-backend (Kubernetes) helpers: they build the ``gsutil rsync``
shell commands run in a session's init/exec containers and parse the ``gsutil
ls`` listing of a session's outputs. They live in the adapter package (not in
``app.services``) so the notebook adapter does not invert the BAL layering rule
by importing a service (BAL rework, Phase 5).
"""

from __future__ import annotations

import re

# Output files to skip when registering a session's results.
_EXCLUDED_FILENAMES = {
    ".bash_history",
    ".Rhistory",
    ".bash_logout",
    ".bashrc",
    ".profile",
    ".gitconfig",
    ".DS_Store",
}
_EXCLUDED_PREFIXES = (".git/", "__pycache__/", ".ipynb_checkpoints/", ".cache/", ".local/")


def generate_sync_in_command(gcs_prefix: str, local_dir: str) -> list[str]:
    """Return the shell command to sync from GCS to a local directory (init container)."""
    return [
        "/bin/sh",
        "-c",
        f"gsutil -m rsync -r {gcs_prefix} {local_dir} || true",
    ]


def generate_sync_out_command(local_dir: str, gcs_prefix: str) -> list[str]:
    """Return the shell command to sync from a local directory to GCS."""
    return [
        "/bin/sh",
        "-c",
        f"gsutil -m rsync -r {local_dir} {gcs_prefix}",
    ]


def parse_gsutil_ls_output(raw_output: str) -> list[dict]:
    """Parse ``gsutil ls -l -r`` output into a list of {gcs_uri, size_bytes, filename}.

    Each line looks like:
       1234567  2026-04-04T12:00:00Z  gs://bucket/path/to/file.txt
    The final summary line starts with TOTAL: and is skipped.
    """
    files: list[dict] = []
    for line in raw_output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("TOTAL:"):
            continue
        match = re.match(r"^\s*(\d+)\s+\S+\s+(gs://.+)$", line)
        if match:
            size_bytes = int(match.group(1))
            gcs_uri = match.group(2)
            filename = gcs_uri.rsplit("/", 1)[-1] if "/" in gcs_uri else gcs_uri
            if not filename or filename in _EXCLUDED_FILENAMES:
                continue
            if any(filename.startswith(p.rstrip("/")) for p in _EXCLUDED_PREFIXES):
                continue
            files.append({"gcs_uri": gcs_uri, "size_bytes": size_bytes, "filename": filename})
    return files
