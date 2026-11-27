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
