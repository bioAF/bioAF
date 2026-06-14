"""Unit tests for the CopyStager staging_image() seam (Stage 4d.1).

DB-free: the container image a backend's stage-in/stage-out init containers run
(the CLI lives in that image). GCS uses google/cloud-sdk:slim (gsutil/gcloud);
S3 (Stage 6e) uses amazon/aws-cli; a mounted NFS backend needs only coreutils.
"""

from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider


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
