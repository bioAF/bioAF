"""Tests for the Terraform compute module (Phase 19).

1. test_compute_module_files_exist - Verify main.tf, variables.tf, outputs.tf exist.
2. test_compute_module_creates_cluster_and_pools - Parse HCL and verify expected resources.
"""

import re
from pathlib import Path


COMPUTE_MODULE_DIR = Path(__file__).resolve().parents[1] / "terraform" / "modules" / "compute"


def test_compute_module_files_exist():
    """Verify compute module contains main.tf, variables.tf, and outputs.tf."""
    for filename in ("main.tf", "variables.tf", "outputs.tf"):
        filepath = COMPUTE_MODULE_DIR / filename
        assert filepath.exists(), f"{filename} should exist in terraform/modules/compute/"
        assert filepath.stat().st_size > 0, f"{filename} should not be empty"


def test_compute_module_creates_cluster_and_pools():
    """Parse HCL files and verify they define the expected GKE resources and outputs."""
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()
    variables_tf = (COMPUTE_MODULE_DIR / "variables.tf").read_text()
    outputs_tf = (COMPUTE_MODULE_DIR / "outputs.tf").read_text()

    # main.tf should define a GKE cluster and three node pools
    assert 'resource "google_container_cluster"' in main_tf, "Should define a GKE cluster resource"
    assert 'resource "google_container_node_pool"' in main_tf, "Should define node pool resources"
    assert "bioaf-pipelines" in main_tf, "Should have a pipelines node pool"
    assert "bioaf-interactive" in main_tf, "Should have an interactive node pool"
    assert "bioaf-system" in main_tf, "Should have a system node pool for GKE addons"

    # Workload Identity should be configured
    assert "workload_identity_config" in main_tf, "Should configure Workload Identity"

    # Network policy should be enabled
    assert "network_policy" in main_tf, "Should enable network policy"

    # variables.tf should define expected input variables
    for var_name in (
        "project_id",
        "region",
        "zone",
        "org_slug",
        "k8s_pipeline_machine_type",
        "k8s_pipeline_max_nodes",
        "k8s_pipeline_use_spot",
        "k8s_interactive_machine_type",
        "k8s_interactive_max_nodes",
        "k8s_system_machine_type",
        "k8s_system_max_nodes",
    ):
        assert f'"{var_name}"' in variables_tf, f"variables.tf should define var {var_name}"

    # outputs.tf should define expected outputs
    for output_name in ("cluster_name", "cluster_endpoint", "cluster_ca_cert"):
        assert f'"{output_name}"' in outputs_tf, f"outputs.tf should define output {output_name}"


def test_system_pool_is_always_on_and_uses_pd_standard():
    """The bioaf-system pool must stay scaled up so GKE addons (calico-typha,
    fluentbit, gmp-operator, etc.) always have a home, and must not consume
    SSD_TOTAL_GB quota -- that quota is already pressured by pipeline pool
    boot disks.

    Uses total_min_node_count / total_max_node_count (global counts) rather
    than min_node_count / max_node_count (per-zone). With ANY location
    policy and a regional cluster, this lets the autoscaler place the
    single floor node in whichever zone has e2-standard-2 capacity at
    deploy time, rather than forcing one node per active zone.
    """
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    # Locate the bioaf-system node pool block
    system_pool_marker = 'name           = "bioaf-system"'
    assert system_pool_marker in main_tf, "bioaf-system pool resource must exist"

    start = main_tf.index(system_pool_marker)
    # Take a generous window covering the resource body
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    system_block = main_tf[start:end]

    # Always-on, single-node global floor (not per-zone).
    assert "total_min_node_count = 1" in system_block, (
        "bioaf-system pool must use total_min_node_count = 1 (global, not per-zone)"
    )
    # The per-zone min_node_count knob must NOT be present -- it conflicts
    # with total_min_node_count and would re-introduce per-zone semantics.
    # Match it at line-start (with indent) so it doesn't false-positive on
    # the "total_min_node_count" substring.
    assert re.search(r"^\s+min_node_count\s*=", system_block, re.MULTILINE) is None, (
        "bioaf-system pool must not set per-zone min_node_count; use total_min_node_count instead"
    )
    assert re.search(r"^\s+max_node_count\s*=", system_block, re.MULTILINE) is None, (
        "bioaf-system pool must not set per-zone max_node_count; use total_max_node_count instead"
    )

    # Capacity-based zone selection. terraform fmt aligns `=` columns
    # within a block, so the gap between `location_policy` and `=` is
    # variable; match with regex.
    assert re.search(r'location_policy\s*=\s*"ANY"', system_block), (
        "bioaf-system pool must use location_policy=ANY so the autoscaler picks the zone with capacity"
    )

    # Disk type: pd-standard so we don't burn SSD_TOTAL_GB quota.
    assert 'disk_type    = "pd-standard"' in system_block or 'disk_type = "pd-standard"' in system_block, (
        "bioaf-system pool must use pd-standard disks"
    )

    # No spot: system addons cannot be evicted at random.
    # spot defaults to false, so just assert it is not set to true.
    assert "spot         = true" not in system_block and "spot = true" not in system_block, (
        "bioaf-system pool must not use spot instances"
    )

    # Pool label so node selectors can target it explicitly when needed.
    assert '"bioaf.io/pool" = "system"' in system_block, "bioaf-system pool must carry the bioaf.io/pool=system label"


def test_system_pool_default_machine_is_e2_standard_2():
    """Default machine type for the system pool must be e2-standard-2.

    Both e2-small and e2-medium are shared-core burstable machine types:
    they report 2 vCPU max but baseline-share only 0.5 / 1.0 vCPU
    respectively. Kubernetes treats the burstable max as `allocatable`
    and shows ~940m for both, so the autoscaler packs the same number
    of pods per node at either size. We confirmed this empirically on
    fresh deploys -- e2-medium pinned at 4 nodes (2 per zone, max=2)
    just like e2-small did, with CPU at 75-94% per node.

    e2-standard-2 has 2 *dedicated* vCPU and 8 GiB RAM (~1.9 CPU /
    ~7 GiB allocatable), enough that one node per zone absorbs the full
    addon DaemonSet set. Cost is roughly equal to 4 x e2-medium.
    """
    variables_tf = (COMPUTE_MODULE_DIR / "variables.tf").read_text()

    # Find the k8s_system_machine_type variable block
    marker = 'variable "k8s_system_machine_type"'
    assert marker in variables_tf, "k8s_system_machine_type variable must exist"
    start = variables_tf.index(marker)
    end = variables_tf.find("\nvariable ", start + 1)
    if end == -1:
        end = len(variables_tf)
    block = variables_tf[start:end]

    assert 'default     = "e2-standard-2"' in block or 'default = "e2-standard-2"' in block, (
        "k8s_system_machine_type default must be e2-standard-2"
    )


def test_interactive_pool_default_machine_is_e2_standard_8():
    """Default machine type for the interactive node pool must be e2-standard-8.

    n2-standard-8 is in the deprioritized Intel Cascade Lake (n2) family and has
    repeatedly stocked out in us-central1-a for fresh interactive pool scale-ups
    (see local/gke-capacity/gke-capacity-issue.md). e2-standard-8 has the same
    8 vCPU / 32 GB shape but is allocated against any compatible host
    generation, so it almost never stocks out -- which is the right trade-off
    for interactive notebook workloads where "can I launch" beats peak CPU.

    The prior default was n2-standard-4 (4 vCPU / 16 GB), which only supported
    Small notebooks. Bumping the default to e2-standard-8 also unlocks Medium
    notebooks out of the box without changing the cluster config.
    """
    variables_tf = (COMPUTE_MODULE_DIR / "variables.tf").read_text()

    marker = 'variable "k8s_interactive_machine_type"'
    assert marker in variables_tf, "k8s_interactive_machine_type variable must exist"
    start = variables_tf.index(marker)
    end = variables_tf.find("\nvariable ", start + 1)
    if end == -1:
        end = len(variables_tf)
    block = variables_tf[start:end]

    assert 'default     = "e2-standard-8"' in block or 'default = "e2-standard-8"' in block, (
        "k8s_interactive_machine_type default must be e2-standard-8 to avoid n2 capacity stockouts"
    )


def test_notebook_runner_workload_identity_depends_on_system_pool():
    """The notebook_runner_workload_identity binding's depends_on must include
    the system pool, mirroring the existing pattern for the other two pools.
    Without this, Terraform may schedule the binding before the WI pool is
    fully registered and produce 'Identity Pool does not exist' errors.
    """
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    binding_marker = 'resource "google_service_account_iam_member" "notebook_runner_workload_identity"'
    assert binding_marker in main_tf, "notebook_runner_workload_identity binding must exist"

    start = main_tf.index(binding_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    binding_block = main_tf[start:end]

    assert "google_container_node_pool.system" in binding_block, (
        "notebook_runner_workload_identity must depend_on the system pool"
    )


def test_pipeline_runner_service_account_exists():
    """A dedicated bioaf-pipeline-runner GSA is required for Workload Identity:
    the bioaf-pipelines node pool enforces GKE_METADATA, so Nextflow pods get
    no GCP identity unless their KSA is bound to a GSA that can read/write the
    bioaf-* buckets. Without this resource, Nextflow GCS access fails with
    'storage.objects.get denied'.
    """
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    resource_marker = 'resource "google_service_account" "pipeline_runner"'
    assert resource_marker in main_tf, "pipeline_runner GSA resource must exist"

    start = main_tf.index(resource_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    block = main_tf[start:end]

    assert 'account_id   = "bioaf-pipeline-runner"' in block or 'account_id = "bioaf-pipeline-runner"' in block, (
        "pipeline_runner GSA account_id must be bioaf-pipeline-runner"
    )


def test_pipeline_runner_has_storage_admin():
    """pipeline_runner needs bucket-level access (not just object-level) on
    bioaf-* buckets. Fusion mounts buckets as a local filesystem inside task
    pods, which requires storage.buckets.get -- present in roles/storage.admin
    but NOT in roles/storage.objectAdmin. Without it, task pods fail with
    'does not have storage.buckets.get access' and exit 126 before the
    pipeline command runs. Matches how bioaf-app is scoped in install-gcp.sh.
    """
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    binding_marker = 'resource "google_project_iam_member" "pipeline_runner_storage"'
    assert binding_marker in main_tf, "pipeline_runner_storage binding must exist"

    start = main_tf.index(binding_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    block = main_tf[start:end]

    assert re.search(r'role\s*=\s*"roles/storage\.admin"', block), (
        "pipeline_runner must have roles/storage.admin (objectAdmin lacks storage.buckets.get needed by Fusion)"
    )
    # Scope to bioaf-* buckets so this SA can't read unrelated project data.
    assert 'resource.name.startsWith(\\"projects/_/buckets/bioaf-\\")' in block, (
        "pipeline_runner storage binding must be conditioned on bioaf-* buckets"
    )


def test_pipeline_runner_workload_identity_binding_exists():
    """The Workload Identity binding maps the bioaf-pipelines/bioaf-pipeline-runner
    KSA to the GSA. Without this binding, the KSA's iam.gke.io/gcp-service-account
    annotation is inert."""
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    binding_marker = 'resource "google_service_account_iam_member" "pipeline_runner_workload_identity"'
    assert binding_marker in main_tf, "pipeline_runner_workload_identity binding must exist"

    start = main_tf.index(binding_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    block = main_tf[start:end]

    assert (
        'role               = "roles/iam.workloadIdentityUser"' in block
        or 'role = "roles/iam.workloadIdentityUser"' in block
    ), "pipeline_runner WI binding must grant roles/iam.workloadIdentityUser"
    assert "bioaf-pipelines/bioaf-pipeline-runner" in block, (
        "pipeline_runner WI binding must reference bioaf-pipelines/bioaf-pipeline-runner KSA"
    )


def test_pipeline_head_node_pool_exists_and_is_on_demand():
    """Nextflow head pods are killed by Spot preemption mid-pipeline. A dedicated
    on-demand pool isolates the orchestrator from preemption while task pods
    stay on the cheaper Spot pipelines pool. Confirmed empirically: a run on
    2026-05-11 had its head pod killed at ~11 min by Spot reclamation despite
    cluster-autoscaler.kubernetes.io/safe-to-evict=false (which only blocks
    voluntary autoscaler scale-down, not Spot)."""
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    resource_marker = 'resource "google_container_node_pool" "pipeline_head"'
    assert resource_marker in main_tf, "bioaf-pipeline-head node pool must exist"

    start = main_tf.index(resource_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    block = main_tf[start:end]

    assert '"bioaf-pipeline-head"' in block, "pool name must be bioaf-pipeline-head"
    # Must NOT be spot -- the whole point is to survive preemption.
    assert "spot         = true" not in block and "spot = true" not in block, (
        "pipeline-head pool must not use Spot instances"
    )
    # Label so the head Job's nodeSelector can target this pool.
    assert '"bioaf.io/pool" = "pipeline-head"' in block, "pool must carry bioaf.io/pool=pipeline-head label"


def test_pipeline_head_pool_is_tainted_for_strict_isolation():
    """The head pool carries a NoSchedule taint so Nextflow's task pods
    (which don't carry custom tolerations) can't accidentally land on it
    and consume capacity reserved for orchestrators."""
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    resource_marker = 'resource "google_container_node_pool" "pipeline_head"'
    start = main_tf.index(resource_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    block = main_tf[start:end]

    assert "taint" in block, "pipeline-head pool must declare a taint block"
    assert 'key    = "bioaf.io/pool"' in block or 'key = "bioaf.io/pool"' in block
    assert 'value  = "pipeline-head"' in block or 'value = "pipeline-head"' in block
    assert 'effect = "NO_SCHEDULE"' in block


def test_pipeline_head_pool_variables_defined():
    """variables.tf must declare machine_type and max_nodes for the head pool."""
    variables_tf = (COMPUTE_MODULE_DIR / "variables.tf").read_text()
    for var_name in ("k8s_pipeline_head_machine_type", "k8s_pipeline_head_max_nodes"):
        assert f'"{var_name}"' in variables_tf, f"variables.tf should define {var_name}"


def test_pipeline_runner_workload_identity_depends_on_node_pools():
    """The WI binding must depend_on the cluster and all three node pools, so
    Terraform waits for the Workload Identity pool to register before applying
    the binding (otherwise 'Identity Pool does not exist' on first apply)."""
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    binding_marker = 'resource "google_service_account_iam_member" "pipeline_runner_workload_identity"'
    assert binding_marker in main_tf

    start = main_tf.index(binding_marker)
    end = main_tf.find('resource "', start + 1)
    if end == -1:
        end = len(main_tf)
    block = main_tf[start:end]

    for dep in (
        "google_container_cluster.bioaf",
        "google_container_node_pool.pipelines",
        "google_container_node_pool.interactive",
        "google_container_node_pool.system",
    ):
        assert dep in block, f"pipeline_runner_workload_identity must depend_on {dep}"


def test_gke_default_pool_zone_variable_exists():
    """The compute module must accept gke_default_pool_zone.

    This variable holds the zone the pre-flight capacity probe selected.
    The cluster's top-level node_locations is set from it so the
    throwaway default pool is only provisioned in that one zone. The
    real node pools (system / pipelines / interactive / pipeline_head)
    set their own node_locations and are unaffected, so the cluster
    remains regional and they retain multi-zone fallback.

    Default is "" (empty list): backward-compatible with existing
    deploys that have not run the probe yet, behaves like today (GKE
    picks all zones in the region).
    """
    variables_tf = (COMPUTE_MODULE_DIR / "variables.tf").read_text()

    marker = 'variable "gke_default_pool_zone"'
    assert marker in variables_tf, "gke_default_pool_zone variable must exist"

    start = variables_tf.index(marker)
    end = variables_tf.find("\nvariable ", start + 1)
    if end == -1:
        end = len(variables_tf)
    block = variables_tf[start:end]

    assert "type        = string" in block or "type = string" in block, (
        "gke_default_pool_zone must be typed as a string"
    )
    assert 'default     = ""' in block or 'default = ""' in block, (
        'gke_default_pool_zone must default to "" so unset behaves like today'
    )


def test_cluster_pins_default_pool_to_probed_zone():
    """The cluster's node_locations must be set from gke_default_pool_zone
    when non-empty.

    The throwaway default pool (initial_node_count = 1, no autoscaling,
    no location_policy) is the single point of failure for cluster
    bootstrap. Constraining it to one probed-healthy zone replaces
    P(any of 3 zones stocked out) with P(this one probed zone stocks
    out between probe and create). The real node pools set their own
    node_locations and are unaffected.
    """
    main_tf = (COMPUTE_MODULE_DIR / "main.tf").read_text()

    cluster_marker = 'resource "google_container_cluster" "bioaf"'
    assert cluster_marker in main_tf, "bioaf cluster resource must exist"
    start = main_tf.index(cluster_marker)
    end = main_tf.find('\nresource "', start + 1)
    if end == -1:
        end = len(main_tf)
    cluster_block = main_tf[start:end]

    assert "node_locations" in cluster_block, (
        "cluster must set node_locations to constrain the default pool's bootstrap"
    )
    assert "var.gke_default_pool_zone" in cluster_block, (
        "cluster's node_locations must read from var.gke_default_pool_zone"
    )
