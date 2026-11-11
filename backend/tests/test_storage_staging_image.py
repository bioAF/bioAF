"""Unit tests for the CopyStager staging_image() seam (Stage 4d.1).

DB-free: the container image a backend's stage-in/stage-out init containers run
(the CLI lives in that image). GCS uses google/cloud-sdk:slim (gsutil/gcloud);
S3 (Stage 6e) uses amazon/aws-cli; a mounted NFS backend needs only coreutils.
"""

import pytest

from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.exceptions import ValidationError


def test_gcs_staging_image_is_cloud_sdk():
    assert GcsStorageProvider().staging_image() == "google/cloud-sdk:slim"


def test_nfs_staging_image_is_minimal_coreutils():
    # A mounted filesystem stages by plain `cp`, so it needs only a tiny
    # coreutils image, not a cloud CLI.
    assert NfsStorageProvider().staging_image() == "busybox:stable"


def test_gcs_input_mount_spec_is_readonly_gcsfuse_csi():
    volume, volume_mount, pod_annotations = GcsStorageProvider().input_mount_spec(
        name="data-0", bucket="work-bucket", mount_path="/data/pipeline-outputs/1"
    )
    assert volume_mount == {"name": "data-0", "mountPath": "/data/pipeline-outputs/1", "readOnly": True}
    assert volume["name"] == "data-0"
    assert volume["csi"]["driver"] == "gcsfuse.csi.storage.gke.io"
    assert volume["csi"]["readOnly"] is True
    assert volume["csi"]["volumeAttributes"]["bucketName"] == "work-bucket"
    # The pod annotation that triggers GKE gcsfuse CSI sidecar injection.
    assert pod_annotations == {"gke-gcsfuse/volumes": "true"}


def test_gcs_nextflow_scratch_directives_enable_wave_and_fusion():
    directives = GcsStorageProvider().nextflow_scratch_directives("gs://work-bucket/nextflow-work")
    assert "workDir = 'gs://work-bucket/nextflow-work'" in directives
    assert "wave.enabled = true" in directives
    assert "fusion.enabled = true" in directives
    assert "fusion.exportStorageCredentials = true" in directives


def test_nfs_nextflow_scratch_directives_are_plain_workdir():
    # A mounted POSIX filesystem is the workDir directly; no Wave/Fusion overlay.
    directives = NfsStorageProvider().nextflow_scratch_directives("/mnt/scratch/work")
    assert directives == ["workDir = '/mnt/scratch/work'"]


def test_gcs_sync_commands_are_gsutil_rsync():
    gcs = GcsStorageProvider()
    sync_in = gcs.sync_in_command("gs://work/notebooks/42/", "/home/jovyan")
    assert sync_in == ["/bin/sh", "-c", "gsutil -m rsync -r gs://work/notebooks/42/ /home/jovyan || true"]
    sync_out = gcs.sync_out_command("/home/jovyan", "gs://work/notebooks/42/")
    assert sync_out == ["/bin/sh", "-c", "gsutil -m rsync -r /home/jovyan gs://work/notebooks/42/"]


def test_nfs_sync_commands_are_plain_cp():
    """file:// operands are mapped through the confined mount path (plan 17, F13); the root is
    checked so an unmounted store fails instead of looking like an empty prefix."""
    nfs = NfsStorageProvider(root="/srv")
    sync_in = nfs.sync_in_command("file://working/in/", "/local")[2]
    assert "if [ -d /srv/working/in ]; then cp -r /srv/working/in/. /local; fi" in sync_in
    assert "is not mounted" in sync_in
    sync_out = nfs.sync_out_command("/local", "file://working/out/")[2]
    assert sync_out.endswith("mkdir -p /srv/working/out && cp -r /local/. /srv/working/out")
    with pytest.raises(ValidationError):
        nfs.sync_in_command("/srv/in", "/local")
