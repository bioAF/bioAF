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


@pytest.mark.parametrize("module", ["compute", "image_build"])
def test_modules_write_no_role_policies_and_attach_only_installer_policies(module):
    """Review finding 1: the app must not be able to give a role arbitrary permissions."""
    main = (AWS_MODULES / module / "main.tf").read_text()
    assert 'resource "aws_iam_role_policy" ' not in main
    attachments = re.findall(r'resource "aws_iam_role_policy_attachment" "[^"]+" \{(.*?)\n\}', main, re.S)
    for body in attachments:
        arn = re.search(r"policy_arn\s*=\s*(.+)", body).group(1)
        assert "iam::aws:policy/" in arn or "bioaf-role-" in arn, arn


@pytest.mark.parametrize("module", ["compute", "image_build"])
def test_each_role_and_profile_keeps_its_own_path_or_takes_the_new_one(module):
    main = (AWS_MODULES / module / "main.tf").read_text()
    for kind in ("aws_iam_role", "aws_iam_instance_profile"):
        for body in re.findall(rf'resource "{kind}" "[^"]+" \{{(.*?)\n\}}', main, re.S):
            name = re.search(r"\bname\s*=\s*(.+)", body).group(1).strip()
            assert f"path = lookup(var.role_paths, {name}, var.new_role_path)" in re.sub(r"\s+", " ", body), body[:120]
    variables = (AWS_MODULES / module / "variables.tf").read_text()
    assert re.search(r'variable "role_paths" \{[^}]*default\s*=\s*\{\}', variables, re.S)
    assert re.search(r'variable "new_role_path" \{[^}]*default\s*=\s*"/"', variables, re.S)


def _iam_state(*items):
    return {
        "resources": [
            {"mode": "managed", "type": kind, "name": key, "instances": [{"attributes": {"name": name, "path": path}}]}
            for kind, key, name, path in items
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, paths",
    [
        ({"resources": []}, {}),
        (
            _iam_state(("aws_iam_role", "codebuild", "bioaf-codebuild-image-build", "/")),
            {"bioaf-codebuild-image-build": "/"},
        ),
        (
            _iam_state(
                ("aws_iam_role", "node", "bioaf-a-1-node", "/"),
                ("aws_iam_instance_profile", "work_node", "bioaf-work-node-1", "/"),
            ),
            {"bioaf-a-1-node": "/", "bioaf-work-node-1": "/"},
        ),
    ],
)
async def test_existing_roles_keep_their_paths_and_any_missing_role_goes_under_bioaf(tmp_path, state, paths):
    """Review finding 5: a legacy module that lost one role recreates it under /bioaf/."""

    async def pull():
        return state

    config = await AwsTerraformCloud().prepare_run(
        None, "image_build", dict(CONFIG), phase="plan", run_id=1, pull_state=pull
    )
    tfvars = AwsTerraformCloud().write_tfvars(tmp_path, "image_build", config)
    assert tfvars["role_paths"] == paths
    assert tfvars["new_role_path"] == "/bioaf/"


@pytest.mark.asyncio
async def test_unreadable_state_refuses_the_image_build_plan():
    from app.exceptions import ValidationError

    async def pull():
        return None

    with pytest.raises(ValidationError, match="state"):
        await AwsTerraformCloud().prepare_run(
            None, "image_build", dict(CONFIG), phase="plan", run_id=1, pull_state=pull
        )


def test_the_packer_builder_group_is_created_by_terraform_in_the_default_vpc():
    main = (AWS_MODULES / "image_build" / "main.tf").read_text()
    group = _block(main, 'resource "aws_security_group" "packer_builder"')
    assert 'name        = "bioaf-packer-builder"' in group
    assert "vpc_id      = data.aws_vpc.default.id" in group
