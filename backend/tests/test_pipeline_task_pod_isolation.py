"""Nextflow task pods must pin themselves to the bioaf-pipelines pool.

The pipelines pool is tainted NoSchedule (terraform, keyed bioaf.io/pool=pipelines) so GKE-managed
system addons can never land on and pin an expensive n2-highmem-16 node (a cost leak that blocks
scale-to-zero). For pipeline work to still run there, Nextflow's task pods -- created by the k8s
executor, configured via k8s.pod in the generated nextflow.config -- must carry the matching
toleration and a nodeSelector for the pool. (Nextflow 25.10 supports the `toleration` pod option; an
older assumption that it did not is why the pool was left untainted.)
"""

from app.adapters.compute.kubernetes import KubernetesComputeProvider


def _config():
    return KubernetesComputeProvider._build_nextflow_k8s_config(
        namespace="bioaf-pipelines",
        has_gcs_secret=True,
        gcs_work_dir="gs://bioaf-raw-x/nextflow-work",
    )


def test_task_pods_select_the_pipelines_pool():
    assert "[nodeSelector: 'bioaf.io/pool=pipelines']" in _config()


def test_task_pods_tolerate_the_pipelines_pool_taint():
    config = _config()
    assert "[toleration: [key: 'bioaf.io/pool', operator: 'Equal', value: 'pipelines', effect: 'NoSchedule']]" in config


def test_task_pod_directives_stay_in_the_k8s_pod_list():
    # The new directives must be part of the k8s.pod list, alongside the safe-to-evict annotation,
    # not stray config lines.
    config = _config()
    pod_line = next(line for line in config.splitlines() if line.startswith("k8s.pod = ["))
    assert "nodeSelector: 'bioaf.io/pool=pipelines'" in pod_line
    assert "toleration:" in pod_line
    assert "safe-to-evict" in pod_line  # existing directive preserved
