"""Session persistence service for GCS-backed home directory sync.

Handles syncing notebook session home directories to/from GCS using
gsutil rsync. Used by the K8s notebook adapter for init containers
(sync-in) and session termination (sync-out).
"""

import logging

logger = logging.getLogger("bioaf.session_persistence")


def generate_sync_in_command(gcs_prefix: str, local_dir: str) -> list[str]:
    """Return shell command to sync from GCS to local directory (init container)."""
    return [
        "/bin/sh",
        "-c",
        f"gsutil -m rsync -r {gcs_prefix} {local_dir} || true",
    ]


def generate_sync_out_command(local_dir: str, gcs_prefix: str) -> list[str]:
    """Return shell command to sync from local directory to GCS."""
    return [
        "/bin/sh",
        "-c",
        f"gsutil -m rsync -r {local_dir} {gcs_prefix}",
    ]


def generate_outputs_sync_command(local_dir: str, gcs_prefix: str) -> list[str]:
    """Return shell command to sync /outputs/ to GCS (working bucket)."""
    return [
        "/bin/sh",
        "-c",
        f'if [ -d {local_dir} ] && [ "$(ls -A {local_dir})" ]; then gsutil -m rsync -r {local_dir} {gcs_prefix}; fi',
    ]


def generate_script_capture_command(home_dir: str, gcs_prefix: str) -> list[str]:
    """Return shell command to find and copy notebook/script files to GCS."""
    return [
        "/bin/sh",
        "-c",
        f"find {home_dir} -maxdepth 3 "
        r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
        "-type f "
        f'| while read f; do gsutil cp "$f" {gcs_prefix}"$(basename "$f")"; done',
    ]


def generate_list_outputs_command(gcs_prefix: str) -> list[str]:
    """Return shell command to list all files at a GCS prefix with sizes."""
    return [
        "/bin/sh",
        "-c",
        f"gsutil ls -l -r {gcs_prefix}** 2>/dev/null || true",
    ]


def _get_k8s_core_client():
    """Get a Kubernetes CoreV1Api client. Tests mock this function."""
    from kubernetes import client, config

    config.load_incluster_config()
    return client.CoreV1Api()


async def sync_session_to_gcs(
    pod_name: str,
    namespace: str,
    gcs_prefix: str,
    local_dir: str = "/home/jovyan",
) -> None:
    """Execute the sync-out command inside a running pod via kubectl exec."""
    from kubernetes.stream import stream

    core_client = _get_k8s_core_client()
    sync_cmd = ["/bin/sh", "-c", f"gsutil -m rsync -r {local_dir} {gcs_prefix}"]

    logger.info("Syncing session data to GCS from pod %s", pod_name)
    stream(
        core_client.connect_get_namespaced_pod_exec,
        name=pod_name,
        namespace=namespace,
        command=sync_cmd,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    logger.info("GCS sync complete for pod %s", pod_name)
