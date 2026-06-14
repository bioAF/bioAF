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
