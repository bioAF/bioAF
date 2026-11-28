"""The installer's bioaf-app policies and the documents app-created roles may hold (plan 04B),
evaluated statically.

The documents come from ``installer/aws_iam.sh`` exactly as the installer renders them. Each test
asks the static evaluator in ``tests/support/iam_policy_eval.py`` whether a request the app makes
(or must never make) is allowed. This proves what the documents say; it is not an AWS policy
simulation or a live result.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.iam_policy_eval import Request, allowed, allowed_with_boundary, decide

REPO = Path(__file__).resolve().parents[2]
A, R, VPC, DVPC = "123456789012", "us-west-1", "vpc-0a1b2c3d4e5f60718", "vpc-0d0d0d0d0d0d0d0d0"
APP_POLICIES = (
    "bioaf-app-access-core",
    "bioaf-app-access-control",
    "bioaf-app-access-roles",
    "bioaf-app-access-services",
)
ALL_POLICIES = (
    "bioaf-app-created-role-boundary",
    "bioaf-role-s3-readwrite",
    "bioaf-role-s3-readonly",
    "bioaf-role-cluster-autoscaler",
    "bioaf-role-image-build",
    *APP_POLICIES,
)
ID = f"{A}-{R}"
BOUNDARY = f"arn:aws:iam::{A}:policy/bioaf-app-created-role-boundary"
EC2 = f"arn:aws:ec2:{R}:{A}"
APP_VPC = f"{EC2}:vpc/{VPC}"
OWNED = {"aws:ResourceTag/bioaf:installation": ID}
TAGGED = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation", "Name"]}

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to render the installer documents")


def _render(function: str, *args: str) -> dict:
    script = f'source "{REPO}/installer/aws_iam.sh"; {function} ' + " ".join(f'"{a}"' for a in args)
    out = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def _doc(name: str, region: str = R, vpc: str = VPC, dvpc: str = DVPC) -> dict:
    return _render("aws_iam_policy_document", name, A, region, vpc, dvpc)


@pytest.fixture(scope="module")
def app():
    return [_doc(name) for name in APP_POLICIES]


@pytest.fixture(scope="module")
def boundary():
    return _doc("bioaf-app-created-role-boundary")


@pytest.fixture(scope="module")
def build_role():
    return [_doc("bioaf-role-image-build")]


def _role(name: str) -> str:
    return f"arn:aws:iam::{A}:role/{name}"


# --- Size and shape -----------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_POLICIES)
def test_every_document_fits_the_managed_policy_limit_for_the_longest_region(name):
    rendered = _doc(name, "ap-southeast-3", "vpc-0123456789abcdef0", "vpc-0fedcba9876543210")
    assert len(json.dumps(rendered, separators=(",", ":"))) < 6144


def test_no_service_is_granted_wholesale(app):
    for statement in [st for doc in app for st in doc["Statement"]]:
        if statement["Effect"] != "Allow":
            continue
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        assert "ec2:*" not in actions and "iam:*" not in actions
        if "eks:*" in actions:
            assert all(r.startswith(f"arn:aws:eks:{R}:{A}:") for r in statement["Resource"])
        if "s3:*" in actions:
            assert statement["Condition"]["StringEquals"]["aws:ResourceAccount"] == A


# --- The app cannot expand its own permissions -----------------------------------------


@pytest.mark.parametrize(
    "action",
    ["iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole"],
)
def test_the_app_cannot_edit_its_own_role(app, action):
    ctx = {"iam:PermissionsBoundary": BOUNDARY, "iam:PolicyARN": "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"}
    assert decide(app, Request(action, _role("bioaf-app"), ctx)) == "explicit_deny"


def test_the_app_cannot_pass_its_own_role(app):
    ctx = {"iam:PassedToService": "ec2.amazonaws.com", **OWNED}
    assert decide(app, Request("iam:PassRole", _role("bioaf-app"), ctx)) == "explicit_deny"


def test_the_app_cannot_change_the_boundary_policy(app):
    for action in ("iam:CreatePolicyVersion", "iam:DeletePolicy", "iam:SetDefaultPolicyVersion"):
        assert not allowed(app, Request(action, BOUNDARY))


def _new(name: str) -> str:
    """A role the app creates now: under the /bioaf/ path, which nothing can change later."""
    return f"arn:aws:iam::{A}:role/bioaf/{name}"


EKS_POLICY = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"


def test_roles_are_created_only_under_the_bioaf_path_and_the_boundary(app):
    bounded = {"iam:PermissionsBoundary": BOUNDARY}
    assert allowed(app, Request("iam:CreateRole", _new("bioaf-acme-1-node"), bounded))
    assert not allowed(app, Request("iam:CreateRole", _new("bioaf-acme-1-node")))
    other = f"arn:aws:iam::{A}:policy/AdministratorAccessBoundary"
    assert not allowed(app, Request("iam:CreateRole", _new("bioaf-acme-1-node"), {"iam:PermissionsBoundary": other}))
    assert not allowed(app, Request("iam:CreateRole", _role("bioaf-acme-1-node"), bounded))
    assert not allowed(app, Request("iam:CreateRole", _role("ops-recovery"), bounded))


@pytest.mark.parametrize("role", [_new("bioaf-acme-1-node"), _role("bioaf-acme-1-node")])
def test_the_app_can_never_write_a_role_policy(app, role):
    """Review finding 1: an arbitrary inline policy on a role the app can pass is an escalation."""
    ctx = {"iam:PermissionsBoundary": BOUNDARY, **OWNED}
    assert decide(app, Request("iam:PutRolePolicy", role, ctx)) == "explicit_deny"


def test_the_boundary_cannot_be_removed_or_replaced(app):
    for action in ("iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary"):
        ctx = {"iam:PermissionsBoundary": BOUNDARY, **OWNED}
        assert decide(app, Request(action, _new("bioaf-acme-1-node"), ctx)) == "explicit_deny"


def test_only_aws_eks_and_installer_policies_attach_to_their_own_role_kind(app):
    bounded = {"iam:PermissionsBoundary": BOUNDARY}
    for policy in (EKS_POLICY, f"arn:aws:iam::{A}:policy/bioaf-role-s3-readwrite"):
        assert allowed(
            app, Request("iam:AttachRolePolicy", _new("bioaf-acme-1-node"), {**bounded, "iam:PolicyARN": policy})
        )
    for policy in ("arn:aws:iam::aws:policy/AdministratorAccess", f"arn:aws:iam::{A}:policy/ops-wide"):
        assert not allowed(
            app, Request("iam:AttachRolePolicy", _new("bioaf-acme-1-node"), {**bounded, "iam:PolicyARN": policy})
        )
    unbounded = {"iam:PolicyARN": EKS_POLICY}
    assert not allowed(app, Request("iam:AttachRolePolicy", _role("bioaf-operator-made"), unbounded))


def test_the_installer_policies_themselves_are_off_limits(app):
    for action in ("iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:DeletePolicy"):
        assert decide(app, Request(action, f"arn:aws:iam::{A}:policy/bioaf-role-image-build")) == "explicit_deny"


def test_roles_are_passed_only_when_owned_and_only_to_their_own_service(app):
    def pass_to(role, service, owned=True):
        ctx = {"iam:PassedToService": service, **(OWNED if owned else {})}
        return allowed(app, Request("iam:PassRole", role, ctx))

    assert pass_to(_new("bioaf-acme-1-cluster"), "eks.amazonaws.com")
    assert pass_to(_new("bioaf-acme-1-node"), "eks.amazonaws.com")
    assert pass_to(_new("bioaf-work-node-1"), "ec2.amazonaws.com")
    assert pass_to(_new("bioaf-codebuild-image-build"), "codebuild.amazonaws.com")
    assert pass_to(_role("bioaf-acme-1-cluster"), "eks.amazonaws.com")
    assert not pass_to(_role("bioaf-acme-1-cluster"), "eks.amazonaws.com", owned=False)
    # Review round 2, finding 1: a role cannot be passed to a service it is not meant for.
    assert not pass_to(_new("bioaf-acme-1-cluster"), "codebuild.amazonaws.com")
    assert not pass_to(_new("bioaf-acme-1-cluster"), "ec2.amazonaws.com")
    assert not pass_to(_new("bioaf-acme-1-node"), "codebuild.amazonaws.com")
    assert not pass_to(_new("bioaf-notebook-runner-1"), "codebuild.amazonaws.com")
    assert not pass_to(_new("bioaf-acme-1-cluster"), "lambda.amazonaws.com")


@pytest.mark.parametrize(
    "role, policy",
    [
        ("bioaf-codebuild-image-build", "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"),
        ("bioaf-work-node-1", "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"),
        ("bioaf-notebook-runner-1", "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"),
        ("bioaf-acme-1-node", "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"),
        ("bioaf-acme-1-cluster", f"arn:aws:iam::{A}:policy/bioaf-role-image-build"),
        ("bioaf-notebook-runner-1", f"arn:aws:iam::{A}:policy/bioaf-role-image-build"),
        ("bioaf-codebuild-image-build", f"arn:aws:iam::{A}:policy/bioaf-role-s3-readwrite"),
    ],
)
def test_a_policy_attaches_only_to_the_role_it_is_for(app, role, policy):
    """Review round 2, finding 1: the cluster policy on a role passable to CodeBuild was an escape."""
    ctx = {"iam:PermissionsBoundary": BOUNDARY, "iam:PolicyARN": policy}
    assert not allowed(app, Request("iam:AttachRolePolicy", _new(role), ctx))


@pytest.mark.parametrize(
    "role, policy",
    [
        ("bioaf-acme-1-cluster", "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"),
        ("bioaf-acme-1-node", "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"),
        ("bioaf-acme-1-ebs-csi", "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"),
        ("bioaf-cellxgene-runner-1", f"arn:aws:iam::{A}:policy/bioaf-role-s3-readonly"),
        ("bioaf-work-node-1", f"arn:aws:iam::{A}:policy/bioaf-role-s3-readwrite"),
        ("bioaf-acme-1-cluster-autoscaler", f"arn:aws:iam::{A}:policy/bioaf-role-cluster-autoscaler"),
        ("bioaf-codebuild-image-build", f"arn:aws:iam::{A}:policy/bioaf-role-image-build"),
    ],
)
def test_each_role_gets_its_own_policies(app, role, policy):
    ctx = {"iam:PermissionsBoundary": BOUNDARY, "iam:PolicyARN": policy}
    assert allowed(app, Request("iam:AttachRolePolicy", _new(role), ctx))


def test_trust_policies_cannot_be_edited(app):
    for role in (_new("bioaf-acme-1-cluster"), _role("bioaf-acme-1-cluster")):
        assert decide(app, Request("iam:UpdateAssumeRolePolicy", role, OWNED)) == "explicit_deny"


def test_an_unrelated_role_cannot_be_claimed_by_tagging_it(app):
    """Review finding 2: tagging a role the app does not own must not make it passable."""
    claim = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation"]}
    assert decide(app, Request("iam:TagRole", _role("bioaf-operator-made"), claim)) == "explicit_deny"
    assert (
        decide(app, Request("iam:TagInstanceProfile", f"arn:aws:iam::{A}:instance-profile/bioaf-x", claim))
        == "explicit_deny"
    )
    # Without the tag the role stays unpassable and its trust untouchable.
    eks = {"iam:PassedToService": "eks.amazonaws.com"}
    assert not allowed(app, Request("iam:PassRole", _role("bioaf-operator-made"), eks))
    assert not allowed(app, Request("iam:UpdateAssumeRolePolicy", _role("bioaf-operator-made")))
    # Roles the app creates under /bioaf/ carry the tag from creation.
    assert allowed(app, Request("iam:TagRole", _new("bioaf-acme-1-node"), claim))


def test_protected_operator_roles_are_off_limits(app):
    protected = {"aws:ResourceTag/bioaf:protected": "true", "iam:PermissionsBoundary": BOUNDARY, **OWNED}
    for action in ("iam:AttachRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:DeleteRole", "iam:PassRole"):
        assert decide(app, Request(action, _new("bioaf-ops-recovery"), protected)) == "explicit_deny"
    untag = {"aws:TagKeys": ["bioaf:protected"]}
    assert decide(app, Request("iam:UntagRole", _role("bioaf-ops-recovery"), untag)) == "explicit_deny"


def test_ownership_cannot_be_untagged_from_existing_roles(app):
    ctx = {"aws:TagKeys": ["bioaf:installation"], **OWNED}
    assert decide(app, Request("iam:UntagRole", _role("bioaf-acme-1-node"), ctx)) == "explicit_deny"


# --- Effective permissions of roles the app creates (review finding 1) --------------------------


@pytest.fixture(scope="module")
def anything():
    """The widest identity policy a role could hold; the boundary alone must contain it."""
    return [{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}]


def test_a_bounded_role_cannot_terminate_or_stop_unrelated_instances(anything, boundary):
    for action in ("ec2:TerminateInstances", "ec2:StopInstances"):
        assert not allowed_with_boundary(anything, boundary, Request(action, f"{EC2}:instance/i-unrelated"))
        assert allowed_with_boundary(anything, boundary, Request(action, f"{EC2}:instance/i-builder", OWNED))


def test_a_bounded_role_cannot_delete_log_history_or_unrelated_storage(anything, boundary):
    group = f"arn:aws:logs:{R}:{A}:log-group:/aws/eks/payments/cluster"
    assert not allowed_with_boundary(anything, boundary, Request("logs:DeleteLogGroup", group))
    assert not allowed_with_boundary(anything, boundary, Request("logs:PutLogEvents", f"{group}:log-stream:x"))
    assert not allowed_with_boundary(anything, boundary, Request("ec2:DeleteVolume", f"{EC2}:volume/vol-unrelated"))
    csi = {"aws:ResourceTag/ebs.csi.aws.com/cluster": "true"}
    assert allowed_with_boundary(anything, boundary, Request("ec2:DeleteVolume", f"{EC2}:volume/vol-pvc", csi))
    image = f"arn:aws:ec2:{R}::image/"
    assert not allowed_with_boundary(anything, boundary, Request("ec2:DeregisterImage", f"{image}ami-x"))
    assert allowed_with_boundary(anything, boundary, Request("ec2:DeregisterImage", f"{image}ami-1", OWNED))


def test_a_bounded_role_cannot_claim_resources_or_share_images(anything, boundary):
    claim = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation"]}
    assert not allowed_with_boundary(
        anything, boundary, Request("ec2:CreateTags", f"{EC2}:instance/i-unrelated", claim)
    )
    image = f"arn:aws:ec2:{R}::image/ami-1"
    assert not allowed_with_boundary(anything, boundary, Request("ec2:ModifyImageAttribute", image, OWNED))


def test_app_created_roles_never_gain_iam_or_secret_access(anything, boundary):
    roles = anything
    for action in (
        "iam:PutRolePolicy",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
        "sts:AssumeRole",
        "secretsmanager:GetSecretValue",
        "organizations:ListAccounts",
    ):
        assert not allowed_with_boundary(roles, boundary, Request(action, "*")), action
    assert allowed_with_boundary(roles, boundary, Request("ec2:CreateNetworkInterface", f"{EC2}:network-interface/*"))
    assert not allowed_with_boundary(
        roles, boundary, Request("s3:GetObject", "arn:aws:s3:::bioaf-other/x", {"aws:ResourceAccount": "999999999999"})
    )


# --- EC2: owned resources only ----------------------------------------------------------


def test_work_nodes_launch_with_owned_tags_into_owned_networks(app):
    for resource in ("instance/*", "volume/*", "network-interface/*"):
        assert allowed(app, Request("ec2:RunInstances", f"{EC2}:{resource}", TAGGED))
    for resource in ("subnet/subnet-1", "security-group/sg-1"):
        assert allowed(app, Request("ec2:RunInstances", f"{EC2}:{resource}", OWNED))
    assert allowed(app, Request("ec2:RunInstances", f"arn:aws:ec2:{R}::image/ami-1", {"ec2:Owner": A}))


def test_a_launch_without_the_ownership_tag_or_into_another_network_is_refused(app):
    assert not allowed(app, Request("ec2:RunInstances", f"{EC2}:instance/*", {"aws:RequestTag/Name": "x"}))
    assert not allowed(app, Request("ec2:RunInstances", f"{EC2}:subnet/subnet-foreign", TAGGED))
    assert not allowed(app, Request("ec2:RunInstances", f"{EC2}:security-group/sg-foreign", TAGGED))
    assert not allowed(app, Request("ec2:RunInstances", f"arn:aws:ec2:{R}::image/ami-x", {"ec2:Owner": "099720109477"}))


def test_only_owned_instances_are_terminated(app):
    assert allowed(app, Request("ec2:TerminateInstances", f"{EC2}:instance/i-1", OWNED))
    assert not allowed(app, Request("ec2:TerminateInstances", f"{EC2}:instance/i-2"))
    assert not allowed(
        app, Request("ec2:TerminateInstances", f"{EC2}:instance/i-3", {"aws:ResourceTag/bioaf:installation": "x-y"})
    )


def test_tags_cannot_claim_or_reassign_ownership(app):
    claim = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation"]}
    assert not allowed(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-foreign", claim))
    reassign = {**OWNED, "aws:RequestTag/bioaf:installation": "other", "aws:TagKeys": ["bioaf:installation"]}
    assert decide(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-1", reassign)) == "explicit_deny"
    remove = {**OWNED, "aws:TagKeys": ["bioaf:installation"]}
    assert decide(app, Request("ec2:DeleteTags", f"{EC2}:vpc/vpc-1", remove)) == "explicit_deny"
    # Other tags on owned resources, and the ownership tag at creation, are fine.
    assert allowed(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-1", {**OWNED, "aws:TagKeys": ["Name"]}))
    at_create = {**TAGGED, "ec2:CreateAction": "CreateVpc"}
    assert allowed(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-new", at_create))


def test_the_compute_network_is_created_and_torn_down_only_when_owned(app):
    assert allowed(app, Request("ec2:CreateVpc", f"{EC2}:vpc/*", TAGGED))
    assert not allowed(app, Request("ec2:CreateVpc", f"{EC2}:vpc/*"))
    assert allowed(app, Request("ec2:CreateSubnet", f"{EC2}:vpc/vpc-1", OWNED))
    assert allowed(app, Request("ec2:CreateSubnet", f"{EC2}:subnet/*", TAGGED))
    assert not allowed(app, Request("ec2:CreateSubnet", f"{EC2}:vpc/vpc-foreign", TAGGED))
    assert allowed(app, Request("ec2:DeleteVpc", f"{EC2}:vpc/vpc-1", OWNED))
    assert not allowed(app, Request("ec2:DeleteVpc", f"{EC2}:vpc/vpc-foreign"))


def test_the_application_network_is_changed_only_inside_its_vpc(app):
    in_vpc = {"ec2:Vpc": APP_VPC}
    assert allowed(app, Request("ec2:AuthorizeSecurityGroupIngress", f"{EC2}:security-group/sg-app", in_vpc))
    assert allowed(app, Request("ec2:CreateRoute", f"{EC2}:route-table/rtb-app", in_vpc))
    elsewhere = {"ec2:Vpc": f"{EC2}:vpc/vpc-other"}
    assert not allowed(app, Request("ec2:AuthorizeSecurityGroupIngress", f"{EC2}:security-group/sg-x", elsewhere))
    assert not allowed(app, Request("ec2:CreateRoute", f"{EC2}:route-table/rtb-x", elsewhere))


def test_images_and_snapshots_cannot_be_shared(app):
    for action in ("ec2:ModifyImageAttribute", "ec2:ModifySnapshotAttribute"):
        assert decide(app, Request(action, f"arn:aws:ec2:{R}::image/ami-1", OWNED)) == "explicit_deny"


# --- Other services ------------------------------------------------------------------------


def test_eks_changes_stay_on_bioaf_clusters_in_this_account_and_region(app):
    assert allowed(app, Request("eks:UpdateClusterConfig", f"arn:aws:eks:{R}:{A}:cluster/bioaf-acme-1"))
    assert not allowed(app, Request("eks:UpdateClusterConfig", f"arn:aws:eks:{R}:{A}:cluster/payments"))
    assert not allowed(app, Request("eks:DeleteCluster", f"arn:aws:eks:us-east-1:{A}:cluster/bioaf-acme-1"))


def test_s3_access_is_limited_to_this_accounts_bioaf_buckets(app):
    assert allowed(app, Request("s3:PutObject", "arn:aws:s3:::bioaf-raw-acme-1/x", {"aws:ResourceAccount": A}))
    assert not allowed(
        app, Request("s3:PutObject", "arn:aws:s3:::bioaf-raw-acme-1/x", {"aws:ResourceAccount": "9" * 12})
    )
    assert not allowed(app, Request("s3:PutObject", "arn:aws:s3:::lab-archive/x", {"aws:ResourceAccount": A}))


def test_control_plane_logs_are_managed_but_not_read_or_deleted(app):
    group = f"arn:aws:logs:{R}:{A}:log-group:/aws/eks/bioaf-acme-1/cluster"
    assert allowed(app, Request("logs:PutRetentionPolicy", group))
    assert allowed(app, Request("logs:CreateLogGroup", group))
    assert not allowed(app, Request("logs:GetLogEvents", f"{group}:log-stream:audit"))
    assert not allowed(app, Request("logs:FilterLogEvents", group))
    assert decide(app, Request("logs:DeleteLogGroup", group)) == "explicit_deny"
    build = f"arn:aws:logs:{R}:{A}:log-group:/aws/codebuild/bioaf-image-build:log-stream:x"
    assert allowed(app, Request("logs:GetLogEvents", build))


def test_secrets_and_unrelated_services_are_not_granted(app):
    for action, resource in (
        ("secretsmanager:GetSecretValue", "*"),
        ("kms:Decrypt", "*"),
        ("sts:AssumeRole", _role("ops-admin")),
        ("lambda:CreateFunction", "*"),
    ):
        assert not allowed(app, Request(action, resource)), action


# --- The image-build role (review finding 6) and the autoscaler ------------------------------------


def test_the_build_role_owns_images_only_through_tags_at_creation(build_role):
    image, snapshot = f"arn:aws:ec2:{R}::image/ami-new", f"arn:aws:ec2:{R}::snapshot/snap-new"
    claim = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation", "bioaf-managed"]}
    for resource in (image, snapshot):
        # Packer passes run_tags (with the ownership tag) inside CreateImage: allowed.
        assert allowed(build_role, Request("ec2:CreateTags", resource, {**claim, "ec2:CreateAction": "CreateImage"}))
        # The same tag on an existing image or snapshot is a claim: denied.
        assert not allowed(build_role, Request("ec2:CreateTags", resource, claim))
        # Later non-ownership tags (Packer's tags and snapshot_tags) are fine.
        assert allowed(build_role, Request("ec2:CreateTags", resource, {"aws:TagKeys": ["bioaf-managed"]}))


def test_the_build_role_cleans_up_only_owned_images_and_never_shares(build_role):
    image, snapshot = f"arn:aws:ec2:{R}::image/ami-1", f"arn:aws:ec2:{R}::snapshot/snap-1"
    for action, resource in (("ec2:DeregisterImage", image), ("ec2:DeleteSnapshot", snapshot)):
        assert allowed(build_role, Request(action, resource, OWNED)), action
        assert not allowed(build_role, Request(action, resource)), action
    assert not allowed(build_role, Request("ec2:ModifyImageAttribute", image, OWNED))


@pytest.mark.parametrize(
    "policy, action, resource, ctx",
    [
        (
            "bioaf-role-image-build",
            "logs:CreateLogStream",
            "log-group:/aws/codebuild/bioaf-image-build:log-stream:b",
            {},
        ),
        ("bioaf-role-image-build", "logs:PutLogEvents", "log-group:/aws/codebuild/bioaf-image-build:log-stream:b", {}),
        ("bioaf-role-image-build", "ecr:PutImage", "repository/bioaf-env-x", {}),
        ("bioaf-role-image-build", "s3:GetObject", "bioaf-working-x/context.tar.gz", {"aws:ResourceAccount": A}),
        ("bioaf-role-image-build", "ec2:RunInstances", "instance/*", TAGGED),
        ("bioaf-role-image-build", "ec2:CreateImage", "instance/i-b", OWNED),
        ("bioaf-role-image-build", "ec2:TerminateInstances", "instance/i-b", OWNED),
        ("bioaf-role-image-build", "ec2:DeregisterImage", "image/ami-1", OWNED),
        ("bioaf-role-image-build", "ec2:DeleteSnapshot", "snapshot/snap-1", OWNED),
        ("bioaf-role-image-build", "ec2:CreateKeyPair", "key-pair/packer_1", {}),
        ("bioaf-role-s3-readwrite", "s3:PutObject", "bioaf-results-x/a", {"aws:ResourceAccount": A}),
        ("bioaf-role-s3-readonly", "s3:GetObject", "bioaf-results-x/a", {"aws:ResourceAccount": A}),
        ("bioaf-role-cluster-autoscaler", "autoscaling:SetDesiredCapacity", "asg", {}),
    ],
)
def test_what_each_role_needs_survives_the_boundary(boundary, policy, action, resource, ctx):
    """Review round 2, finding 3: a grant the boundary removes breaks the workflow silently."""
    service = action.split(":")[0]
    arn = {
        "logs": f"arn:aws:logs:{R}:{A}:{resource}",
        "ecr": f"arn:aws:ecr:{R}:{A}:{resource}",
        "s3": f"arn:aws:s3:::{resource}",
        "autoscaling": f"arn:aws:autoscaling:{R}:{A}:autoScalingGroup:u:autoScalingGroupName/x",
    }.get(service)
    if arn is None:
        regional = resource.startswith(("image/", "snapshot/"))
        arn = f"arn:aws:ec2:{R}::{resource}" if regional else f"{EC2}:{resource}"
    if policy == "bioaf-role-cluster-autoscaler":
        ctx = {
            "aws:ResourceTag/k8s.io/cluster-autoscaler/enabled": "true",
            "aws:ResourceTag/eks:cluster-name": "bioaf-acme-1",
            "aws:PrincipalTag/bioaf:cluster": "bioaf-acme-1",
        }
    assert allowed_with_boundary([_doc(policy)], boundary, Request(action, arn, ctx))


def test_the_build_role_launches_only_ubuntu_into_the_default_vpc(build_role):
    for resource in ("instance/*", "volume/*"):
        assert allowed(build_role, Request("ec2:RunInstances", f"{EC2}:{resource}", TAGGED))
        assert not allowed(build_role, Request("ec2:RunInstances", f"{EC2}:{resource}"))
    in_default = {"ec2:Vpc": f"{EC2}:vpc/{DVPC}"}
    assert allowed(build_role, Request("ec2:RunInstances", f"{EC2}:subnet/subnet-d", in_default))
    assert not allowed(build_role, Request("ec2:RunInstances", f"{EC2}:subnet/subnet-c", {"ec2:Vpc": APP_VPC}))
    assert allowed(
        build_role, Request("ec2:RunInstances", f"arn:aws:ec2:{R}::image/ami-u", {"ec2:Owner": "099720109477"})
    )
    assert not allowed(build_role, Request("ec2:RunInstances", f"arn:aws:ec2:{R}::image/ami-x", {"ec2:Owner": A}))
    assert not allowed(build_role, Request("ec2:CreateSecurityGroup", f"{EC2}:security-group/*", TAGGED))
    assert allowed(build_role, Request("ec2:TerminateInstances", f"{EC2}:instance/i-b", OWNED))
    assert not allowed(build_role, Request("ec2:TerminateInstances", f"{EC2}:instance/i-x"))


def test_the_autoscaler_scales_only_its_own_clusters_enabled_groups():
    """Review round 2, finding 6: the autoscaler role's bioaf:cluster tag names its cluster."""
    autoscaler = [_doc("bioaf-role-cluster-autoscaler")]
    asg = f"arn:aws:autoscaling:{R}:{A}:autoScalingGroup:uuid:autoScalingGroupName/eks-x"
    mine = {"aws:PrincipalTag/bioaf:cluster": "bioaf-acme-1"}
    tags = {
        "aws:ResourceTag/k8s.io/cluster-autoscaler/enabled": "true",
        "aws:ResourceTag/eks:cluster-name": "bioaf-acme-1",
    }
    assert allowed(autoscaler, Request("autoscaling:SetDesiredCapacity", asg, {**tags, **mine}))
    other = {**tags, "aws:ResourceTag/eks:cluster-name": "bioaf-acme-2"}
    assert not allowed(autoscaler, Request("autoscaling:SetDesiredCapacity", asg, {**other, **mine}))
    assert not allowed(autoscaler, Request("autoscaling:TerminateInstanceInAutoScalingGroup", asg, mine))
    assert not allowed(autoscaler, Request("autoscaling:SetDesiredCapacity", asg, tags))
    nodegroup = f"arn:aws:eks:{R}:{A}:nodegroup/bioaf-acme-2/ng/1"
    assert not allowed(autoscaler, Request("eks:DescribeNodegroup", nodegroup, mine))
    assert allowed(
        autoscaler, Request("eks:DescribeNodegroup", f"arn:aws:eks:{R}:{A}:nodegroup/bioaf-acme-1/ng/1", mine)
    )


@pytest.mark.parametrize("name", ALL_POLICIES)
def test_no_document_repeats_a_key(name):
    """JSON keeps only the last of two equal keys, which silently drops a condition."""
    script = f'source "{REPO}/installer/aws_iam.sh"; aws_iam_policy_document "{name}" "{A}" "{R}" "{VPC}" "{DVPC}"'
    raw = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout

    def unique(pairs):
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), keys
        return dict(pairs)

    json.loads(raw, object_pairs_hook=unique)


# --- Review round 3: the configured Packer build sequence ----------------------------------------


def _packer_sequence() -> list[Request]:
    """The calls amazon-ebs makes for bioAF's template, in order, with the tags it sends.

    run_tags go into the temporary key pair's and the builder's TagSpecifications and into
    CreateImage; tags and snapshot_tags are applied afterwards with CreateTags.
    """
    run_tags = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation", "bioaf-managed"]}
    later = {"aws:TagKeys": ["bioaf-managed", "Description"]}
    image, snapshot = f"arn:aws:ec2:{R}::image/ami-new", f"arn:aws:ec2:{R}::snapshot/snap-new"
    return [
        Request("ec2:CreateKeyPair", f"{EC2}:key-pair/packer_6566", run_tags),
        Request("ec2:CreateTags", f"{EC2}:key-pair/packer_6566", {**run_tags, "ec2:CreateAction": "CreateKeyPair"}),
        Request("ec2:RunInstances", f"{EC2}:instance/*", run_tags),
        Request("ec2:CreateTags", f"{EC2}:instance/*", {**run_tags, "ec2:CreateAction": "RunInstances"}),
        Request("ec2:StopInstances", f"{EC2}:instance/i-b", OWNED),
        Request("ec2:CreateImage", f"{EC2}:instance/i-b", OWNED),
        Request("ec2:CreateImage", image, run_tags),
        Request("ec2:CreateTags", image, {**run_tags, "ec2:CreateAction": "CreateImage"}),
        Request("ec2:CreateTags", image, later),
        Request("ec2:CreateTags", snapshot, later),
        Request("ec2:TerminateInstances", f"{EC2}:instance/i-b", OWNED),
        Request("ec2:DeleteKeyPair", f"{EC2}:key-pair/packer_6566"),
    ]


@pytest.mark.parametrize("request_", _packer_sequence(), ids=lambda r: f"{r.action}:{r.resource.rsplit(':', 1)[-1]}")
def test_every_call_of_the_configured_packer_build_is_allowed(build_role, boundary, request_):
    """Review round 3, findings 1 and 2: a denied call stops the AMI build part way."""
    assert allowed_with_boundary(build_role, boundary, request_)


def test_the_key_pair_tag_is_allowed_only_while_creating_a_packer_key(build_role):
    claim = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation"]}
    assert not allowed(build_role, Request("ec2:CreateTags", f"{EC2}:key-pair/packer_1", claim))
    assert not allowed(
        build_role, Request("ec2:CreateTags", f"{EC2}:key-pair/operator", {**claim, "ec2:CreateAction": "CreateKeyPair"})
    )
