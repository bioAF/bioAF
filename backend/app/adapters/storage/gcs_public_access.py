"""GCS bucket public-access prevention, for buckets Terraform does not manage.

The foundation bucket guard (``app.services.foundation_bucket_guard``) calls these to enforce the
one setting on the Terraform state and backup buckets that bootstrap created outside Terraform's
state. Credentials are the VM's application default credentials (the bioaf-app service account).
"""

from __future__ import annotations

ENFORCED = "enforced"


def storage_client(project_id: str):
    from google.cloud import storage

    return storage.Client(project=project_id)


def enforce_public_access_prevention(client, name: str) -> str:
    """Set ``public_access_prevention`` to enforced on one bucket and read it back.

    Returns ``already_enforced``, ``enforced``, or ``failed: read back ...``. The patch carries the
    metageneration it read, so a bucket changed in between is not overwritten.
    """
    bucket = client.get_bucket(name)
    if bucket.iam_configuration.public_access_prevention == ENFORCED:
        return "already_enforced"
    bucket.iam_configuration.public_access_prevention = ENFORCED
    bucket.patch(if_metageneration_match=bucket.metageneration)
    bucket.reload()
    if bucket.iam_configuration.public_access_prevention != ENFORCED:
        return f"failed: read back {bucket.iam_configuration.public_access_prevention!r}"
    return ENFORCED
