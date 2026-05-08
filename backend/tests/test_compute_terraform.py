"""Tests for the Terraform compute module (Phase 19).

1. test_compute_module_files_exist - Verify main.tf, variables.tf, outputs.tf exist.
2. test_compute_module_creates_cluster_and_pools - Parse HCL and verify expected resources.
"""

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

    # Always-on: min_node_count must be >= 1 so addons never lose their home.
    assert "min_node_count  = 1" in system_block or "min_node_count = 1" in system_block, (
        "bioaf-system pool must have min_node_count = 1 (always-on)"
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


def test_system_pool_default_machine_is_e2_medium():
    """Default machine type for the system pool must be e2-medium.

    e2-small (940m CPU, 1.4 GiB allocatable) was tried first but system
    DaemonSets (calico-node, fluentbit-gke, gke-metrics-agent, gmp-system
    collectors, gke-metadata-server, netd, ip-masq-agent, pdcsi-node,
    node-local-dns, kube-proxy) plus per-node container runtime overhead
    pushed CPU requests to ~99% on a single node, forcing the autoscaler
    to max=2 in every active zone. e2-medium (~1.9 vCPU, ~3.5 GiB) gives
    enough headroom that one node per zone covers the addon set, so the
    pool sits at its minimum (1 per zone) instead of its ceiling.
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

    assert 'default     = "e2-medium"' in block or 'default = "e2-medium"' in block, (
        "k8s_system_machine_type default must be e2-medium"
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
