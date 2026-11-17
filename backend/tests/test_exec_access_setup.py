"""Terminal-instruction setup and command rendering for GKE and EKS clusters.

The setup guide, kubectl context, and exec command must agree with each other and with the
deployment's real cluster. Pure: no DB, no cluster.
"""

import shlex

import pytest

from app.adapters.kubernetes.exec_access import cluster_access, render_exec_command
from app.exceptions import ConnectionTargetInvalidError

GKE_REGIONAL = {"gke_cluster_name": "bioaf-lab-abc123", "gcp_project_id": "lab-proj-1", "gcp_region": "us-west1"}
EKS = {"gke_cluster_name": "bioaf-lab-xyz789", "aws_region": "eu-west-2"}


def _argv(command_line: str) -> list[str]:
    return shlex.split(command_line)


def _line_with(guide: str, needle: str) -> str:
    return next(line.strip() for line in guide.splitlines() if needle in line)


def test_gke_regional_cluster_uses_region_form_and_its_own_context():
    access = cluster_access("gke", GKE_REGIONAL, namespace="bioaf-pipelines")

    assert access.context == "gke_lab-proj-1_us-west1_bioaf-lab-abc123"
    creds = _argv(_line_with(access.setup_guide, "get-credentials"))
    assert creds == [
        "gcloud",
        "container",
        "clusters",
        "get-credentials",
        "bioaf-lab-abc123",
        "--region",
        "us-west1",
        "--project",
        "lab-proj-1",
    ]
    check = _argv(_line_with(access.setup_guide, "get pods"))
    assert check == ["kubectl", "--context", access.context, "-n", "bioaf-pipelines", "get", "pods"]
    assert "<" not in access.setup_guide  # no placeholders to fill in
    assert "aws " not in access.setup_guide


def test_gke_zonal_location_uses_zone_form():
    cfg = {**GKE_REGIONAL, "gcp_region": "us-central1-a"}
    access = cluster_access("gke", cfg, namespace="bioaf-notebooks")

    creds = _argv(_line_with(access.setup_guide, "get-credentials"))
    assert creds[5:7] == ["--zone", "us-central1-a"]
    assert access.context == "gke_lab-proj-1_us-central1-a_bioaf-lab-abc123"


def test_eks_uses_aws_setup_with_an_explicit_alias_shared_by_the_command():
    access = cluster_access("eks", EKS, namespace="bioaf-notebooks")

    assert "gcloud" not in access.setup_guide
    update = _argv(_line_with(access.setup_guide, "update-kubeconfig"))
    assert update[:3] == ["aws", "eks", "update-kubeconfig"]
    assert update[update.index("--name") + 1] == "bioaf-lab-xyz789"
    assert update[update.index("--region") + 1] == "eu-west-2"
    alias = update[update.index("--alias") + 1]
    assert alias == access.context
    assert "bioaf-lab-xyz789" in alias and "eu-west-2" in alias

    command = render_exec_command(
        pod="bioaf-notebook-7", namespace="bioaf-notebooks", container="notebook", context=access.context
    )
    argv = _argv(command)
    assert argv[argv.index("--context") + 1] == alias


def test_two_clusters_get_distinct_contexts():
    a = cluster_access("eks", EKS, namespace="ns")
    b = cluster_access("eks", {**EKS, "gke_cluster_name": "bioaf-other-000001"}, namespace="ns")
    assert a.context != b.context
    c = cluster_access("gke", GKE_REGIONAL, namespace="ns")
    d = cluster_access("gke", {**GKE_REGIONAL, "gcp_project_id": "lab-proj-2"}, namespace="ns")
    assert c.context != d.context


@pytest.mark.parametrize(
    "backend,cfg",
    [
        ("gke", {k: v for k, v in GKE_REGIONAL.items() if k != "gke_cluster_name"}),
        ("gke", {k: v for k, v in GKE_REGIONAL.items() if k != "gcp_project_id"}),
        ("gke", {k: v for k, v in GKE_REGIONAL.items() if k != "gcp_region"}),
        ("eks", {k: v for k, v in EKS.items() if k != "aws_region"}),
        ("eks", {k: v for k, v in EKS.items() if k != "gke_cluster_name"}),
        ("gke", {**GKE_REGIONAL, "gcp_project_id": "proj; rm -rf /"}),
        ("eks", {**EKS, "aws_region": "eu-west-2 --profile admin"}),
        ("gke", {**GKE_REGIONAL, "gke_cluster_name": "$(whoami)"}),
    ],
)
def test_missing_or_malformed_cluster_configuration_is_refused(backend, cfg):
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        cluster_access(backend, cfg, namespace="bioaf-pipelines")
    assert exc.value.status_code == 409
    assert exc.value.code == "connection_target_invalid"
    assert exc.value.details["reason"] == "cluster_setup_incomplete"
    assert "administrator" in str(exc.value)


def test_command_names_context_namespace_container_and_fixed_shell():
    command = render_exec_command(
        pod="bioaf-pipeline-42-1700000000-abcde",
        namespace="team-pipelines",
        container="pipeline",
        context="gke_p_us-west1_c",
    )
    assert _argv(command) == [
        "kubectl",
        "exec",
        "-it",
        "--context",
        "gke_p_us-west1_c",
        "-n",
        "team-pipelines",
        "bioaf-pipeline-42-1700000000-abcde",
        "--container",
        "pipeline",
        "--",
        "/bin/sh",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("pod", "pod; rm -rf ~"),
        ("pod", "$(curl evil)"),
        ("pod", "Upper-Case"),
        ("pod", "-n=kube-system"),
        ("pod", ""),
        ("namespace", "ns`id`"),
        ("namespace", "a" * 64),
        ("container", "main && sh"),
    ],
)
def test_hostile_or_invalid_identities_are_refused(field, value):
    kwargs = {"pod": "bioaf-notebook-1", "namespace": "bioaf-notebooks", "container": "notebook", "context": "ctx"}
    kwargs[field] = value
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        render_exec_command(**kwargs)
    assert exc.value.details["reason"] == "invalid_identity"


def test_context_with_shell_metacharacters_stays_one_literal_argument():
    context = "arn:aws:eks:eu-west-2:1:cluster/x$(id)"
    command = render_exec_command(
        pod="bioaf-notebook-1", namespace="bioaf-notebooks", container="notebook", context=context
    )
    argv = _argv(command)
    assert argv[argv.index("--context") + 1] == context
    assert "$(id)'" in command  # quoted, not left for the shell to expand


def test_legacy_setup_guide_is_provider_selected_once_cluster_settings_are_loaded(monkeypatch):
    from app.adapters.compute.kubernetes import KubernetesComputeProvider
    from app.adapters.kubernetes import exec_access
    from app.adapters.kubernetes.connection import KUBECTL_SETUP_GUIDE
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    assert "gcloud" not in KUBECTL_SETUP_GUIDE and "aws" not in KUBECTL_SETUP_GUIDE
    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "eks")
    for adapter in (KubernetesComputeProvider(), KubernetesNotebookProvider()):
        adapter._cluster_config = dict(EKS)
        guide = adapter.connection_setup_guide()
        assert "update-kubeconfig" in guide and "gcloud" not in guide
