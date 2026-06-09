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


class StorageBucketInfo(BaseModel):
    name: str
    purpose: str
    is_ingest: bool
    size_gb: float
    object_count: int


class StorageBucketsResponse(BaseModel):
    org_slug: str
    buckets: list[StorageBucketInfo]
