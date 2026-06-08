"""Normalized BAL return models (Phase 2 keystone).

These typed models are the contract a second backend (SLURM/NFS) is built to
satisfy, instead of reverse-engineering the K8s adapter's dict shapes. Each
model carries backend-neutral normalized fields plus an opaque
``provider_details`` dict for backend-specific extras (pod names, md5 hashes,
GKE phases) that detail views may show but core logic must never depend on.

This file tests the model definitions only. Routing the adapters/callers through
them (with parity tests) happens per category in later commits.
"""

from __future__ import annotations

from app.adapters.models import (
    CellxgeneInstance,
    ClusterMetrics,
    ClusterStatus,
    CostEstimate,
    JobProgress,
    JobState,
    JobStatus,
    JobSubmitResult,
    ProcessInfo,
    ServiceState,
    SessionInfo,
    SessionStatus,
    StorageMetrics,
    VmInfo,
    VmStatus,
)

# --- Status enums ------------------------------------------------------------


def test_job_state_has_the_five_canonical_values():
    assert {s.value for s in JobState} == {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_service_state_values():
    # Sessions, work-node VMs, and cellxgene instances share a lifecycle.
    assert {s.value for s in ServiceState} == {
        "starting",
        "running",
        "stopping",
        "stopped",
        "error",
        "unknown",
    }


def test_job_state_is_a_string_enum():
    # Stays JSON/DB friendly and compares equal to the legacy string.
    assert JobState.RUNNING == "running"


# --- provider_details discipline --------------------------------------------


def test_provider_details_defaults_to_empty_dict_and_is_optional():
    status = JobStatus(job_id="j1", status=JobState.RUNNING)
    assert status.provider_details == {}


def test_provider_details_carries_backend_extras():
    status = JobStatus(
        job_id="j1",
        status=JobState.FAILED,
        provider_details={"pod_name": "p-1", "node_name": "n-1"},
    )
    assert status.provider_details["pod_name"] == "p-1"


# --- Compute models ----------------------------------------------------------


def test_job_submit_result_defaults_to_queued():
    r = JobSubmitResult(job_id="j1")
    assert r.status == JobState.QUEUED
    assert r.estimated_cost is None


def test_job_submit_result_carries_cost_estimate():
    r = JobSubmitResult(job_id="j1", estimated_cost=CostEstimate(estimated_cost_usd=1.5))
    assert r.estimated_cost.estimated_cost_usd == 1.5
    assert r.estimated_cost.currency == "USD"


def test_job_status_optional_fields_default_none():
    s = JobStatus(job_id="j1", status=JobState.COMPLETED)
    assert s.started_at is None
    assert s.completed_at is None
    assert s.exit_code is None
    assert s.termination_reasons == []


def test_job_progress_holds_process_info():
    p = JobProgress(
        percent_complete=50.0,
        processes=[ProcessInfo(name="FASTQC", status="running", cpu=12.0, memory_gb=1.5, duration_s=30)],
    )
    assert p.percent_complete == 50.0
    assert p.processes[0].name == "FASTQC"
    assert p.processes[0].memory_gb == 1.5


def test_cost_estimate_currency_defaults_usd():
    assert CostEstimate(estimated_cost_usd=2.0).currency == "USD"


def test_cluster_status_holds_node_pools():
    cs = ClusterStatus(
        total_nodes=3,
        active_nodes=2,
        health="healthy",
        node_pools=[{"name": "pipelines", "current_nodes": 2, "spot": True}],  # type: ignore[list-item]
    )
    assert cs.node_pools[0].name == "pipelines"
    assert cs.node_pools[0].spot is True


def test_cluster_metrics_holds_pool_cost_rates():
    cm = ClusterMetrics(
        cost_burn_rate_hourly=0.42,
        node_pools=[{"name": "pipelines", "cost_rate_hourly": 0.21}],  # type: ignore[list-item]
    )
    assert cm.node_pools[0].cost_rate_hourly == 0.21


# --- Storage model -----------------------------------------------------------


def test_storage_metrics_holds_buckets():
    sm = StorageMetrics(
        total_size_gb=10.0,
        total_cost_monthly_usd=1.0,
        buckets=[{"name": "raw", "size_gb": 5.0, "object_count": 3}],  # type: ignore[list-item]
    )
    assert sm.buckets[0].name == "raw"
    assert sm.total_cost_monthly_usd == 1.0


# --- Session models ----------------------------------------------------------


def test_session_info_and_status():
    info = SessionInfo(session_id="s1", status=ServiceState.STARTING)
    assert info.status == ServiceState.STARTING
    st = SessionStatus(session_id="s1", status=ServiceState.RUNNING)
    assert st.status == "running"


# --- Work-node models --------------------------------------------------------


def test_vm_info_and_status():
    info = VmInfo(instance_name="bioaf-worknode-1", status=ServiceState.STARTING, zone="us-central1-a")
    assert info.zone == "us-central1-a"
    st = VmStatus(instance_name="bioaf-worknode-1", status=ServiceState.RUNNING, external_ip="1.2.3.4")
    assert st.external_ip == "1.2.3.4"


# --- Cellxgene model ---------------------------------------------------------


def test_cellxgene_instance():
    inst = CellxgeneInstance(publication_id=7, status=ServiceState.RUNNING, access_url="http://x")
    assert inst.publication_id == 7
    assert inst.access_url == "http://x"
