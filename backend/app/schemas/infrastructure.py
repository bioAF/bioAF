from pydantic import BaseModel


class BucketMetrics(BaseModel):
    name: str
    size_gb: float
    object_count: int
    storage_class: str
    cost_monthly_usd: float


class InfraStorageMetricsResponse(BaseModel):
    buckets: list[BucketMetrics]
    total_size_gb: float
    total_cost_monthly_usd: float


class ComputeStackResponse(BaseModel):
    compute_stack: str


class StackOption(BaseModel):
    """One selectable compute+storage stack, with provider-appropriate labels.

    The backend is the source of truth for which combos are valid/available on a
    given cloud (stage 8); the frontend renders these labels rather than
    hardcoding GCP-specific names.
    """

    compute_stack: str  # "kubernetes" | "slurm"
    storage_backend: str  # "gcs" | "s3" | "nfs"
    label: str  # combined, e.g. "Kubernetes + GCS"
    compute_label: str  # e.g. "Kubernetes (GKE)" / "Kubernetes (EKS)"
    storage_label: str  # e.g. "GCS" / "S3" / "NFS"
    available: bool  # selectable today (SLURM is not yet)
    recommended: bool


class StackOptionsResponse(BaseModel):
    cloud_provider: str
    options: list[StackOption]


class StorageBucketInfo(BaseModel):
    name: str
    purpose: str
    is_ingest: bool
    size_gb: float
    object_count: int


class StorageBucketsResponse(BaseModel):
    org_slug: str
    buckets: list[StorageBucketInfo]
