"""Tests for cluster node-pool quota preflight.

A pipeline pool configured onto pd-balanced at 500 GB in bioaf-495400 produced
ZERO schedulable nodes: pd-balanced draws on SSD_TOTAL_GB, whose regional limit
is 500 GB, so a single node exhausted it. Terraform reported success, the pool
reported RUNNING, and a study sat in `running` for 35 minutes with 11 pods that
could never be placed.

These tests pin the arithmetic that decides whether a proposed pool config can
be built, and the severity split between "cannot build one node" (block) and
"cannot build every node" (warn).

Numbers throughout are the real quotas observed in bioaf-495400 / us-central1
on 2026-08-26, so a regression reads as the incident it prevents.
"""

import pytest

from app.services.cluster_quota import (
    PoolPlan,
    QuotaMetric,
    evaluate_pool_quota,
    machine_vcpus,
)


def _observed_quotas(**overrides) -> dict[str, QuotaMetric]:
    """The quotas bioaf-495400/us-central1 actually reported during the incident."""
    quotas = {
        "SSD_TOTAL_GB": QuotaMetric(metric="SSD_TOTAL_GB", usage=30.0, limit=500.0),
        "DISKS_TOTAL_GB": QuotaMetric(metric="DISKS_TOTAL_GB", usage=90.0, limit=4096.0),
        "CPUS": QuotaMetric(metric="CPUS", usage=7.0, limit=200.0),
        "N2_CPUS": QuotaMetric(metric="N2_CPUS", usage=0.0, limit=200.0),
        "PREEMPTIBLE_CPUS": QuotaMetric(metric="PREEMPTIBLE_CPUS", usage=0.0, limit=0.0),
    }
    quotas.update(overrides)
    return quotas


def _plan(**overrides) -> PoolPlan:
    base = {
        "machine_type": "n2-highmem-16",
        "max_nodes": 20,
        "disk_size_gb": 500,
        "disk_type": "pd-standard",
        "use_spot": True,
    }
    base.update(overrides)
    return PoolPlan(**base)


# -- vCPU derivation --------------------------------------------------------


def test_machine_vcpus_reads_the_total_not_the_allocatable():
    """Quota charges TOTAL vCPU. n2-highmem-16 is 16, not the 14 k8s can allocate."""
    assert machine_vcpus("n2-highmem-16") == 16
    assert machine_vcpus("e2-standard-8") == 8
    assert machine_vcpus("n2-standard-32") == 32


# -- disk type maps to the right quota bucket -------------------------------


@pytest.mark.parametrize(
    "disk_type,expected_metric",
    [
        ("pd-balanced", "SSD_TOTAL_GB"),
        ("pd-ssd", "SSD_TOTAL_GB"),
        ("pd-standard", "DISKS_TOTAL_GB"),
    ],
)
def test_disk_type_selects_its_quota_bucket(disk_type, expected_metric):
    """The whole incident: pd-balanced and pd-standard bill to different quotas.

    Sized so the chosen bucket is the binding one, which makes the verdict name it.
    """
    verdict = evaluate_pool_quota(
        _plan(disk_type=disk_type, disk_size_gb=500, max_nodes=20),
        _observed_quotas(),
    )
    assert verdict.binding_metric == expected_metric


# -- the incident itself ----------------------------------------------------


def test_a_single_node_exceeding_disk_quota_blocks():
    """pd-balanced at 500 GB: one node needs 500, only 470 is free. Zero nodes."""
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-balanced", disk_size_gb=500),
        _observed_quotas(),
    )
    assert verdict.status == "block"
    assert verdict.achievable_nodes == 0
    assert verdict.binding_metric == "SSD_TOTAL_GB"


def test_block_message_names_the_quota_and_what_would_fit():
    """An operator must learn which quota, and what value is actually applyable."""
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-balanced", disk_size_gb=500),
        _observed_quotas(),
    )
    assert "SSD_TOTAL_GB" in verdict.message
    # 500 limit - 30 used = 470 GB is the largest single disk that fits.
    assert "470" in verdict.message


def test_partial_fit_warns_and_reports_the_achievable_count():
    """pd-standard 500 GB x 20: disk allows 8 nodes, CPU allows 12. Disk binds."""
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-standard", disk_size_gb=500, max_nodes=20),
        _observed_quotas(),
    )
    assert verdict.status == "warn"
    assert verdict.achievable_nodes == 8
    assert verdict.binding_metric == "DISKS_TOTAL_GB"
    assert "8" in verdict.message and "20" in verdict.message


def test_a_fully_fitting_config_is_ok():
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-standard", disk_size_gb=100, max_nodes=4),
        _observed_quotas(),
    )
    assert verdict.status == "ok"
    assert verdict.achievable_nodes >= 4


# -- CPU is a real ceiling too ----------------------------------------------


def test_cpu_binds_when_it_runs_out_before_disk():
    """Small disks make CPU the binding quota: 200-7 = 193 vCPU / 16 = 12 nodes."""
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-standard", disk_size_gb=50, max_nodes=20),
        _observed_quotas(),
    )
    assert verdict.status == "warn"
    assert verdict.achievable_nodes == 12
    assert verdict.binding_metric == "CPUS"


def test_machine_family_quota_binds_when_lower_than_aggregate():
    """n2-* charges N2_CPUS as well as CPUS; the tighter of the two wins."""
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-standard", disk_size_gb=50, max_nodes=20),
        _observed_quotas(N2_CPUS=QuotaMetric(metric="N2_CPUS", usage=0.0, limit=32.0)),
    )
    assert verdict.achievable_nodes == 2
    assert verdict.binding_metric == "N2_CPUS"


def test_spot_pool_charges_cpus_when_preemptible_quota_is_zero():
    """bioaf-495400 has PREEMPTIBLE_CPUS=0, so spot nodes bill to CPUS.

    Pinned because trusting the other reading would make every spot pool look
    unlimited.
    """
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-standard", disk_size_gb=50, max_nodes=20, use_spot=True),
        _observed_quotas(),
    )
    assert verdict.binding_metric == "CPUS"
    assert verdict.achievable_nodes == 12


# -- headroom is net of what this pool already holds -------------------------


def test_headroom_nets_out_the_pools_own_current_usage():
    """Re-applying an unchanged config on a running pool must not block on itself.

    The pool already holds 8 nodes x 500 GB = 4000 GB of the 4090 in use. Without
    netting that out, its own disks would make its own config unapplyable.
    """
    quotas = _observed_quotas(
        DISKS_TOTAL_GB=QuotaMetric(metric="DISKS_TOTAL_GB", usage=4090.0, limit=4096.0),
        # Coherent with the 8 running nodes: 7 vCPU of baseline plus 8 x 16.
        CPUS=QuotaMetric(metric="CPUS", usage=135.0, limit=200.0),
        N2_CPUS=QuotaMetric(metric="N2_CPUS", usage=128.0, limit=200.0),
    )
    verdict = evaluate_pool_quota(
        _plan(disk_type="pd-standard", disk_size_gb=500, max_nodes=8),
        quotas,
        pool_current_usage={"DISKS_TOTAL_GB": 4000.0, "CPUS": 128.0, "N2_CPUS": 128.0},
    )
    assert verdict.status == "ok"


# -- unknown quota fails open ------------------------------------------------


def test_unknown_quota_is_unverified_and_never_blocks():
    """No IAM permission, a cloud API error, or a provider with no reader."""
    verdict = evaluate_pool_quota(_plan(disk_type="pd-balanced", disk_size_gb=500), None)
    assert verdict.status == "unverified"
    assert verdict.achievable_nodes is None


def test_a_missing_metric_does_not_block_on_that_metric():
    """A quota response lacking SSD_TOTAL_GB must not be read as zero headroom."""
    quotas = _observed_quotas()
    del quotas["SSD_TOTAL_GB"]
    verdict = evaluate_pool_quota(_plan(disk_type="pd-balanced", disk_size_gb=500, max_nodes=1), quotas)
    assert verdict.status != "block"
