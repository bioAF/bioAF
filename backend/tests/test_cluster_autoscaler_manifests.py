"""Unit tests for the EKS Cluster Autoscaler manifest builder (Stage 6e incr 3).

DB-free, SDK-free: asserts the kube-system CA manifests are wired correctly --
the IRSA SA annotation, the auto-discovery flag tying the CA to this cluster's
ASG tags, the AWS region env, the system-node placement, and image pinning.
"""

from app.adapters.compute.cluster_autoscaler import (
    DEFAULT_IMAGE,
    NAMESPACE,
    SERVICE_ACCOUNT,
    build_cluster_autoscaler_manifests,
)

ROLE_ARN = "arn:aws:iam::043671579834:role/bioaf-bioaf-8ec3ba-cluster-autoscaler"
SA_ANNOTATIONS = {"eks.amazonaws.com/role-arn": ROLE_ARN}


def _manifests(**overrides):
    kwargs = {
        "role_arn": ROLE_ARN,
        "cluster_name": "bioaf-bioaf-8ec3ba",
        "region": "us-west-1",
        "sa_annotations": SA_ANNOTATIONS,
    }
    kwargs.update(overrides)
    return build_cluster_autoscaler_manifests(**kwargs)


def test_returns_all_six_objects():
    m = _manifests()
    assert set(m) == {
        "service_account",
        "cluster_role",
        "cluster_role_binding",
        "role",
        "role_binding",
        "deployment",
    }


def test_service_account_carries_irsa_annotation_in_kube_system():
    sa = _manifests()["service_account"]
    assert sa["kind"] == "ServiceAccount"
    assert sa["metadata"]["name"] == SERVICE_ACCOUNT == "cluster-autoscaler"
    assert sa["metadata"]["namespace"] == NAMESPACE == "kube-system"
    assert sa["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == ROLE_ARN


def test_service_account_annotations_omitted_when_no_binding():
    sa = build_cluster_autoscaler_manifests(
        role_arn="",
        cluster_name="c",
        region="us-west-1",
        sa_annotations={},
    )["service_account"]
    # No empty {} annotations block (kept None so the create is clean).
    assert sa["metadata"]["annotations"] is None


def test_deployment_args_are_aws_and_scoped_to_this_cluster():
    dep = _manifests()["deployment"]
    container = dep["spec"]["template"]["spec"]["containers"][0]
    cmd = container["command"]
    assert "--cloud-provider=aws" in cmd
    assert "--balance-similar-node-groups" in cmd
    assert "--expander=least-waste" in cmd
    # The auto-discovery flag must reference THIS cluster's owned tag so the CA
    # only manages this cluster's ASGs (matches the terraform ASG tags).
    discovery = next(a for a in cmd if a.startswith("--node-group-auto-discovery="))
    assert "k8s.io/cluster-autoscaler/enabled" in discovery
    assert "k8s.io/cluster-autoscaler/bioaf-bioaf-8ec3ba" in discovery


def test_deployment_region_env_and_placement():
    dep = _manifests()["deployment"]
    spec = dep["spec"]["template"]["spec"]
    container = spec["containers"][0]
    assert {"name": "AWS_REGION", "value": "us-west-1"} in container["env"]
    assert spec["serviceAccountName"] == "cluster-autoscaler"
    # Runs on the always-on system node (cannot schedule itself up otherwise).
    assert spec["nodeSelector"] == {"bioaf.io/pool": "system"}
    assert spec["priorityClassName"] == "system-cluster-critical"


def test_deployment_image_default_and_override():
    assert _manifests()["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] == DEFAULT_IMAGE
    custom = "registry.k8s.io/autoscaling/cluster-autoscaler:v1.32.0"
    dep = _manifests(image=custom)["deployment"]
    assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == custom


def test_rbac_bindings_target_the_kube_system_sa():
    m = _manifests()
    crb = m["cluster_role_binding"]
    assert crb["roleRef"]["kind"] == "ClusterRole"
    assert crb["roleRef"]["name"] == "cluster-autoscaler"
    assert crb["subjects"] == [{"kind": "ServiceAccount", "name": "cluster-autoscaler", "namespace": "kube-system"}]
    rb = m["role_binding"]
    assert rb["roleRef"]["kind"] == "Role"
    assert rb["metadata"]["namespace"] == "kube-system"
