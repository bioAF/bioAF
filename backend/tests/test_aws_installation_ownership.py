"""AWS resources bioAF creates carry this installation's ownership tag; roles carry the boundary.

The narrowed app policy (plan 04B) lets the app change EC2 resources only when they carry
``bioaf:installation=<account>-<region>`` and create IAM roles only under the installer's
permissions boundary. These tests check that every creation path supplies both: the Terraform
modules (provider default tags, role boundaries), the tfvars that feed them, Work Node launches and
Packer builds. Text checks of the modules plus behavior of the tfvars and launch arguments.
"""

import re
from pathlib import Path

import pytest

from app.platform.aws_installation import (
    INSTALLATION_TAG,
    ROLE_BOUNDARY_NAME,
    installation_id,
    role_boundary_arn,
)
from app.services.terraform_cloud import AwsTerraformCloud

AWS_MODULES = Path(__file__).resolve().parents[1] / "terraform" / "aws" / "modules"
CONFIG = {"aws_region": "us-west-1", "aws_account_id": "123456789012", "org_slug": "acme"}


def test_the_installation_identity_is_derived_from_account_and_region():
    assert INSTALLATION_TAG == "bioaf:installation"
    assert installation_id("123456789012", "us-west-1") == "123456789012-us-west-1"
    assert installation_id("", "us-west-1") is None
    assert installation_id("123456789012", None) is None
    assert ROLE_BOUNDARY_NAME == "bioaf-app-created-role-boundary"
    assert role_boundary_arn("123456789012") == "arn:aws:iam::123456789012:policy/bioaf-app-created-role-boundary"
    assert role_boundary_arn("") is None


@pytest.mark.parametrize("module", ["compute", "image_build"])
def test_modules_tag_everything_they_create_with_the_installation(module):
    """The tag and boundary come from the account and region the module already receives, so they
    match the installer's naming without extra inputs."""
    main = (AWS_MODULES / module / "main.tf").read_text()
    provider = main[main.index('provider "aws"') :]
    provider = provider[: provider.index("\n}\n")]
    assert "default_tags" in provider and "local.ownership_tags" in provider
    flat = re.sub(r"\s+", " ", main)
    assert 'installation_id = var.account_id == "" ? "" : "${var.account_id}-${var.region}"' in flat
    assert '{ "bioaf:installation" = local.installation_id }' in flat
    assert (
        'role_boundary = var.account_id == "" ? null : '
        '"arn:${data.aws_partition.current.partition}:iam::${var.account_id}:policy/bioaf-app-created-role-boundary"'
    ) in flat


@pytest.mark.parametrize("module", ["compute", "image_build"])
def test_every_role_a_module_creates_carries_the_boundary(module):
    main = (AWS_MODULES / module / "main.tf").read_text()
    roles = re.findall(r'resource "aws_iam_role" "[^"]+" \{(.*?)\n\}', main, re.S)
    assert roles
    for body in roles:
        assert "permissions_boundary = local.role_boundary" in body


@pytest.mark.parametrize("module", ["compute", "image_build"])
def test_modules_receive_the_account_they_derive_ownership_from(tmp_path, module):
    tfvars = AwsTerraformCloud().write_tfvars(tmp_path, module, CONFIG)
    assert tfvars["account_id"] == "123456789012" and tfvars["region"] == "us-west-1"


def _block(text: str, header: str) -> str:
    start = text.index(header)
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            return text[start:i]


def _statements(doc: str) -> list[str]:
    out, rest = [], doc
    while "statement {" in rest:
        block = _block(rest, "statement {")
        out.append(block)
        rest = rest[rest.index(block) + len(block) :]
    return out


def test_the_autoscaler_can_scale_only_this_clusters_groups():
    main = (AWS_MODULES / "compute" / "main.tf").read_text()
    statements = _statements(_block(main, 'data "aws_iam_policy_document" "cluster_autoscaler"'))
    mutating = [s for s in statements if "autoscaling:SetDesiredCapacity" in s]
    assert len(mutating) == 1
    scale = mutating[0]
    assert "autoscaling:TerminateInstanceInAutoScalingGroup" in scale
    assert "aws:ResourceTag/k8s.io/cluster-autoscaler/${local.cluster_name}" in scale
    assert '"owned"' in scale
    reads = [s for s in statements if s is not scale]
    for statement in reads:
        actions = re.findall(r'"([a-z0-9]+:[A-Za-z]+)"', statement)
        assert all(a.split(":")[1].startswith(("Describe", "Get")) for a in actions), statement
    nodegroup = [s for s in statements if "eks:DescribeNodegroup" in s]
    assert nodegroup and "nodegroup/${local.cluster_name}/*" in nodegroup[0]


def _codebuild():
    main = (AWS_MODULES / "image_build" / "main.tf").read_text()
    return main, _statements(_block(main, 'data "aws_iam_policy_document" "codebuild"'))


def test_the_build_role_has_no_unconditioned_ec2_mutation():
    _, statements = _codebuild()
    for statement in statements:
        actions = re.findall(r'"(ec2:[A-Za-z]+)"', statement)
        mutating = [a for a in actions if not a.split(":")[1].startswith(("Describe", "Get"))]
        if mutating and '"*"' in statement:
            assert "condition" in statement, statement


def test_the_build_role_cannot_share_images_or_edit_security_groups():
    _, statements = _codebuild()
    actions = {a for s in statements for a in re.findall(r'"(ec2:[A-Za-z]+)"', s)}
    for forbidden in (
        "ec2:ModifyImageAttribute",
        "ec2:ModifySnapshotAttribute",
        "ec2:CopyImage",
        "ec2:CreateSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:DeleteSecurityGroup",
        "ec2:GetPasswordData",
    ):
        assert forbidden not in actions


def test_the_build_role_cleans_up_only_owned_images_and_snapshots():
    _, statements = _codebuild()
    cleanup = [s for s in statements if "ec2:DeregisterImage" in s]
    assert len(cleanup) == 1
    assert "ec2:DeleteSnapshot" in cleanup[0]
    assert "aws:ResourceTag/bioaf:installation" in cleanup[0]


def test_the_build_role_launches_only_into_the_default_vpc_from_canonical_images():
    main, statements = _codebuild()
    launch = [s for s in statements if "ec2:RunInstances" in s]
    joined = "\n".join(launch)
    assert "ec2:Vpc" in joined and "data.aws_vpc.default.arn" in joined
    assert "ec2:Owner" in joined and "local.canonical" in joined
    assert re.search(r'canonical\s*=\s*"099720109477"', main)
    assert "aws:RequestTag/bioaf:installation" in joined


def test_the_build_role_logs_and_context_reads_are_scoped():
    _, statements = _codebuild()
    logs = [s for s in statements if "logs:PutLogEvents" in s][0]
    assert "log-group:/aws/codebuild/${local.project_name}" in logs and '"*"' not in logs
    context = [s for s in statements if "s3:GetObject" in s][0]
    assert "aws:ResourceAccount" in context


def test_the_packer_builder_group_is_created_by_terraform_in_the_default_vpc():
    main, _ = _codebuild()
    group = _block(main, 'resource "aws_security_group" "packer_builder"')
    assert 'name        = "bioaf-packer-builder"' in group
    assert "vpc_id      = data.aws_vpc.default.id" in group
