"""Cluster Autoscaler manifests (Stage 6e increment 3, EKS).

Pure builders for the in-cluster Kubernetes Cluster Autoscaler that EKS needs and
GKE does not (GKE's control plane autoscales node pools natively; EKS managed
node groups sit at their desired size until an in-cluster autoscaler moves them).
The terraform compute module provisions the AWS-side prerequisites -- the CA's
IRSA role and the ASG discovery/scale-from-zero tags -- and the app installs the
workload below at compute-deploy through the cluster connection.

This module is intentionally SDK-free (it returns plain manifest dicts) so it
stays a pure, directly-unit-testable leaf and the kubernetes client touch lives
only in the compute provider that applies these. It lives under ``adapters/`` so
the BAL guard's "no cloud/k8s SDK outside adapters" rule is satisfied by the
applier without exempting a service module.
"""

from __future__ import annotations

# kube-system is where cluster-critical addons live; the CA must run there to
# carry the system-cluster-critical priority class and survive node pressure.
NAMESPACE = "kube-system"
SERVICE_ACCOUNT = "cluster-autoscaler"

# Pin the CA image to the cluster's Kubernetes minor (EKS default 1.31 -> CA
# v1.31.x). The CA project ships one release train per k8s minor; mismatches are
# unsupported. Override via the cluster_autoscaler_image platform_config key if
# the EKS version is bumped (keep this in lockstep with the module's
# kubernetes_version default in terraform/aws/modules/compute/variables.tf).
DEFAULT_IMAGE = "registry.k8s.io/autoscaling/cluster-autoscaler:v1.31.1"

_LABELS = {"app": "cluster-autoscaler", "bioaf.io/managed": "true"}


def _cluster_role_rules() -> list[dict]:
    """The canonical Cluster Autoscaler ClusterRole rules (upstream parity)."""
    return [
        {"apiGroups": [""], "resources": ["events", "endpoints"], "verbs": ["create", "patch"]},
        {"apiGroups": [""], "resources": ["pods/eviction"], "verbs": ["create"]},
        {"apiGroups": [""], "resources": ["pods/status"], "verbs": ["update"]},
        {
            "apiGroups": [""],
            "resources": ["endpoints"],
            "resourceNames": ["cluster-autoscaler"],
            "verbs": ["get", "update"],
        },
        {"apiGroups": [""], "resources": ["nodes"], "verbs": ["watch", "list", "get", "update"]},
        {
            "apiGroups": [""],
            "resources": [
                "namespaces",
                "pods",
                "services",
                "replicationcontrollers",
                "persistentvolumeclaims",
                "persistentvolumes",
            ],
            "verbs": ["watch", "list", "get"],
        },
        {"apiGroups": ["extensions"], "resources": ["replicasets", "daemonsets"], "verbs": ["watch", "list", "get"]},
        {"apiGroups": ["policy"], "resources": ["poddisruptionbudgets"], "verbs": ["watch", "list"]},
        {
            "apiGroups": ["apps"],
            "resources": ["statefulsets", "replicasets", "daemonsets"],
            "verbs": ["watch", "list", "get"],
        },
        {
            "apiGroups": ["storage.k8s.io"],
            "resources": ["storageclasses", "csinodes", "csidrivers", "csistoragecapacities"],
            "verbs": ["watch", "list", "get"],
        },
        {"apiGroups": ["batch", "extensions"], "resources": ["jobs"], "verbs": ["get", "list", "watch", "patch"]},
        {"apiGroups": ["coordination.k8s.io"], "resources": ["leases"], "verbs": ["create"]},
        {
            "apiGroups": ["coordination.k8s.io"],
            "resourceNames": ["cluster-autoscaler"],
            "resources": ["leases"],
            "verbs": ["get", "update"],
        },
    ]


def _role_rules() -> list[dict]:
    """kube-system Role rules for the CA's status configmap (upstream parity)."""
    return [
        {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["create", "list", "watch"]},
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["cluster-autoscaler-status"],
            "verbs": ["delete", "get", "update", "watch"],
        },
    ]


def _autoscaler_args(cluster_name: str) -> list[str]:
    """The CA container command + flags.

    ``--node-group-auto-discovery`` matches the ASG tags the terraform module
    stamps on each managed node group (k8s.io/cluster-autoscaler/enabled +
    k8s.io/cluster-autoscaler/<cluster_name>), so the CA finds and scales exactly
    this cluster's groups. ``--balance-similar-node-groups`` spreads scale-up
    across the two AZs; ``least-waste`` picks the group that wastes the least
    CPU/RAM for a Pending pod.
    """
    discovery = f"asg:tag=k8s.io/cluster-autoscaler/enabled,k8s.io/cluster-autoscaler/{cluster_name}"
    return [
        "./cluster-autoscaler",
        "--v=4",
        "--stderrthreshold=info",
        "--cloud-provider=aws",
        "--skip-nodes-with-local-storage=false",
        "--expander=least-waste",
        f"--node-group-auto-discovery={discovery}",
        "--balance-similar-node-groups",
        # Scale workload pools back to 0 promptly once idle (cost control); the
        # head/interactive/pipeline pools are all scale-to-zero.
        "--scale-down-unneeded-time=5m",
        "--scale-down-delay-after-add=5m",
    ]


def build_cluster_autoscaler_manifests(
    *,
    role_arn: str,
    cluster_name: str,
    region: str,
    sa_annotations: dict[str, str] | None = None,
    image: str | None = None,
) -> dict[str, dict]:
    """Build the kube-system Cluster Autoscaler manifests as plain dicts.

    Returns a dict keyed by object kind: ``service_account``, ``cluster_role``,
    ``cluster_role_binding``, ``role``, ``role_binding``, ``deployment``. The
    ``sa_annotations`` (the IRSA ``eks.amazonaws.com/role-arn`` binding, resolved
    by the PodIdentity seam from ``role_arn``) annotate the service account so the
    CA pod assumes the autoscaler IAM role. ``image`` defaults to the pinned
    CA image matched to the cluster's Kubernetes minor.
    """
    annotations = dict(sa_annotations or {})
    img = image or DEFAULT_IMAGE

    service_account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": SERVICE_ACCOUNT,
            "namespace": NAMESPACE,
            "labels": dict(_LABELS),
            "annotations": annotations or None,
        },
    }

    cluster_role = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {"name": SERVICE_ACCOUNT, "labels": dict(_LABELS)},
        "rules": _cluster_role_rules(),
    }

    cluster_role_binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": SERVICE_ACCOUNT, "labels": dict(_LABELS)},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": SERVICE_ACCOUNT},
        "subjects": [{"kind": "ServiceAccount", "name": SERVICE_ACCOUNT, "namespace": NAMESPACE}],
    }

    role = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": SERVICE_ACCOUNT, "namespace": NAMESPACE, "labels": dict(_LABELS)},
        "rules": _role_rules(),
    }

    role_binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": SERVICE_ACCOUNT, "namespace": NAMESPACE, "labels": dict(_LABELS)},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": SERVICE_ACCOUNT},
        "subjects": [{"kind": "ServiceAccount", "name": SERVICE_ACCOUNT, "namespace": NAMESPACE}],
    }

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": SERVICE_ACCOUNT, "namespace": NAMESPACE, "labels": dict(_LABELS)},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "cluster-autoscaler"}},
            "template": {
                "metadata": {
                    "labels": {"app": "cluster-autoscaler"},
                    "annotations": {"prometheus.io/scrape": "true", "prometheus.io/port": "8085"},
                },
                "spec": {
                    "priorityClassName": "system-cluster-critical",
                    "serviceAccountName": SERVICE_ACCOUNT,
                    # Run on the always-on system node so the CA survives when the
                    # workload pools are scaled to zero (it cannot schedule itself
                    # up). The system group carries no taint, so no toleration.
                    "nodeSelector": {"bioaf.io/pool": "system"},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65534,
                        "fsGroup": 65534,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "cluster-autoscaler",
                            "image": img,
                            "command": _autoscaler_args(cluster_name),
                            "env": [{"name": "AWS_REGION", "value": region}],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "600Mi"},
                                "limits": {"cpu": "200m", "memory": "600Mi"},
                            },
                            "ports": [{"containerPort": 8085, "name": "metrics"}],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    }

    return {
        "service_account": service_account,
        "cluster_role": cluster_role,
        "cluster_role_binding": cluster_role_binding,
        "role": role,
        "role_binding": role_binding,
        "deployment": deployment,
    }
