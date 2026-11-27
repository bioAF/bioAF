"""The AWS compute module carries EKS endpoint access and control-plane logging as explicit inputs.

Text checks of the module (the repository's Terraform test style). The backend decides the values
(see test_eks_endpoint_policy.py); the module only applies them. A plan or apply is not run here.
"""

import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "terraform" / "aws" / "modules" / "compute"
LOG_TYPES = ["api", "audit", "authenticator", "controllerManager", "scheduler"]


def _main() -> str:
    return (MODULE / "main.tf").read_text()


def _block(text: str, header: str) -> str:
    start = text.index(header)
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            return text[start:i]


def _variable(name: str) -> str:
    return _block((MODULE / "variables.tf").read_text(), f'variable "{name}"')


def test_endpoint_inputs_default_to_private_only():
    assert re.search(r"default\s*=\s*false", _variable("endpoint_public_access"))
    assert re.search(r"default\s*=\s*true", _variable("endpoint_private_access"))
    assert re.search(r"default\s*=\s*\[\]", _variable("public_access_cidrs"))


def test_the_cluster_takes_its_endpoint_settings_from_the_inputs():
    vpc = _block(_block(_main(), 'resource "aws_eks_cluster" "this"'), "vpc_config")
    assert "endpoint_public_access  = var.endpoint_public_access" in vpc
    assert "endpoint_private_access = var.endpoint_private_access" in vpc
    assert "public_access_cidrs" in vpc and "var.public_access_cidrs" in vpc
    assert "= true" not in vpc


def test_at_least_one_endpoint_is_required():
    assert "var.endpoint_public_access || var.endpoint_private_access" in _variable("endpoint_private_access")


def test_all_five_log_types_are_enabled_by_default():
    block = _variable("control_plane_log_types")
    for log_type in LOG_TYPES:
        assert f'"{log_type}"' in block
    cluster = _block(_main(), 'resource "aws_eks_cluster" "this"')
    assert "enabled_cluster_log_types = var.control_plane_log_types" in cluster


def test_retention_defaults_to_thirty_days_and_is_validated():
    block = _variable("control_plane_log_retention_days")
    assert re.search(r"default\s*=\s*30\b", block)
    assert "validation" in block and "contains(" in block


def test_the_log_group_exists_before_export_and_survives_teardown():
    main = _main()
    group = _block(main, 'resource "aws_cloudwatch_log_group" "control_plane"')
    assert 'name              = "/aws/eks/${local.cluster_name}/cluster"' in group
    assert "retention_in_days = var.control_plane_log_retention_days" in group
    assert "skip_destroy      = true" in group
    cluster = _block(main, 'resource "aws_eks_cluster" "this"')
    depends_on = re.search(r"depends_on\s*=\s*\[([^\]]*)\]", cluster).group(1)
    assert "aws_cloudwatch_log_group.control_plane" in depends_on


def test_an_existing_log_group_is_adopted_rather_than_recreated():
    main = _main()
    adopt = re.sub(r"\s+", " ", _block(main, "import {"))
    assert "to = aws_cloudwatch_log_group.control_plane" in adopt
    assert "var.adopt_control_plane_log_group" in adopt
    assert re.search(r"default\s*=\s*false", _variable("adopt_control_plane_log_group"))
    terraform = _block(main, "terraform {")
    assert 'required_version = ">= 1.9"' in terraform


def test_the_reader_policy_grants_only_reads_on_this_clusters_log_group():
    main = _main()
    doc = _block(main, 'data "aws_iam_policy_document" "control_plane_log_reader"')
    assert "aws_cloudwatch_log_group.control_plane.arn" in doc
    actions = set(re.findall(r'"(logs:[A-Za-z]+)"', doc))
    assert actions and all(
        a.split(":")[1].startswith(("Get", "Filter", "Describe", "StartQuery", "StopQuery")) for a in actions
    )
    assert 'resource "aws_iam_policy" "control_plane_log_reader"' in main
    assert 'output "control_plane_log_reader_policy_arn"' in (MODULE / "outputs.tf").read_text()
    # The module never attaches it: operators attach it to their designated roles.
    assert "aws_iam_policy.control_plane_log_reader.arn" not in re.sub(r'output "[^"]+" \{[^}]*\}', "", main)


def test_the_app_reaches_the_private_api_only_on_443_from_its_security_group():
    main = _main()
    rule = _block(main, 'resource "aws_vpc_security_group_ingress_rule" "app_api"')
    assert "count                        = local.backend_sync_private ? 1 : 0" in rule
    assert "security_group_id            = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id" in rule
    assert "referenced_security_group_id = var.app_security_group_id" in rule
    assert "from_port                    = 443" in rule and "to_port                      = 443" in rule
    assert "cidr_ipv4" not in rule
    # The backend-sync group still allows SSH only.
    sync = _block(main, 'resource "aws_security_group" "backend_sync"')
    assert "443" not in sync
