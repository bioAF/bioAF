"""EKS realization of the ClusterAuth seam (Stage 6e).

Mints an ``aws eks get-token``-style bearer token without shelling out: a
presigned STS ``GetCallerIdentity`` URL, with the ``x-k8s-aws-id`` header bound
to the cluster name as a signed header, base64url-encoded behind the
``k8s-aws-v1.`` prefix (the format aws-iam-authenticator / the EKS API expect).
Tokens are valid ~15 min; the connection rebuilds well under that.

The endpoint / CA come from the SAME platform_config keys the GKE path uses
(``gke_cluster_endpoint`` / ``gke_cluster_ca_cert``); the deploy writes the EKS
values into them, so the shared connection plumbing needs no per-cloud change.
boto3 lives behind this adapter boundary, exactly like S3StorageProvider, and
authenticates through the ambient EC2 instance profile (IMDS).
"""

from __future__ import annotations

import base64

from app.adapters.cluster_auth.base import ClusterAuthProvider

# Lifetime requested for the presigned URL. EKS accepts the resulting token for
# ~15 min regardless; we rebuild the client at token_ttl_seconds, well under it.
_PRESIGN_EXPIRES_IN = 60
_TOKEN_PREFIX = "k8s-aws-v1."


def _get_eks_token(cluster_name: str, region: str) -> str:
    """Mint an EKS bearer token (a presigned STS GetCallerIdentity URL).

    Reproduces the algorithm of ``aws eks get-token`` / aws-iam-authenticator:
    presign ``sts:GetCallerIdentity`` with ``x-k8s-aws-id: <cluster>`` as a signed
    header, then base64url-encode the URL behind the ``k8s-aws-v1.`` prefix. Uses
    the ambient credentials (the EC2 instance profile via IMDS), the same identity
    S3StorageProvider authenticates through.
    """
    import boto3

    region = region or None
    sts = boto3.Session(region_name=region).client("sts", region_name=region)

    def _add_cluster_header(request, **_kwargs):
        # Bound to the cluster so the token is only valid for this cluster. Added
        # in before-sign so botocore signs it (it must appear in
        # X-Amz-SignedHeaders for EKS to accept the token).
        request.headers["x-k8s-aws-id"] = cluster_name

    sts.meta.events.register("before-sign.sts.GetCallerIdentity", _add_cluster_header)

    url = sts.generate_presigned_url(
        "get_caller_identity",
        Params={},
        ExpiresIn=_PRESIGN_EXPIRES_IN,
        HttpMethod="GET",
    )
    return _TOKEN_PREFIX + base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8").rstrip("=")


class EksClusterAuthProvider(ClusterAuthProvider):
    """EKS control-plane auth: STS presigned-URL token + the cluster endpoint/CA."""

    token_ttl_seconds = 840  # 14 min; under the ~15-min EKS STS token life

    def cluster_endpoint(self, cluster_config: dict) -> str:
        # Reuses the gke_cluster_* keys; the deploy stores the EKS endpoint there.
        return cluster_config.get("gke_cluster_endpoint", "") or ""

    def cluster_ca_cert(self, cluster_config: dict) -> str:
        return cluster_config.get("gke_cluster_ca_cert", "") or ""

    def bearer_token(self, cluster_config: dict) -> str:
        cluster_name = cluster_config.get("gke_cluster_name", "") or ""
        region = cluster_config.get("aws_region", "") or ""
        if not cluster_name:
            from app.exceptions import ValidationError

            raise ValidationError("EKS cluster name not configured (gke_cluster_name missing).")
        return _get_eks_token(cluster_name, region)
