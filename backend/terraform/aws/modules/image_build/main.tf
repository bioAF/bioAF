terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {} # Configured dynamically by the executor (S3 state).
}

provider "aws" {
  region = var.region
  # Ambient instance-profile creds (the bioaf-app role), like the other AWS
  # modules. No explicit credentials block.

  default_tags {
    tags = local.ownership_tags
  }
}

data "aws_partition" "current" {}

# --- Installation ownership (plan 04B) -------------------------------------------
#
# The narrowed bioaf-app policy lets the app change EC2 resources only when they carry this
# installation's tag, and create roles only under the installer's permissions boundary. Both are
# derived from the account and region (installer/aws_iam.sh and app/platform/aws_installation.py
# use the same naming). The default tags put the ownership tag on every resource at creation.

locals {
  installation_id = var.account_id == "" ? "" : "${var.account_id}-${var.region}"
  ownership_tags  = local.installation_id == "" ? {} : { "bioaf:installation" = local.installation_id }
  role_boundary   = var.account_id == "" ? null : "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:policy/bioaf-app-created-role-boundary"
}

# The AWS analog of GCP's Cloud Build: Cloud Build is serverless (the app just
# submits a build), but CodeBuild needs a provisioned project + service role
# before StartBuild. ECR repos are created app-side (CreateRepository, mirroring
# the app-side Artifact Registry ensure), so this module owns only the CodeBuild
# project and its role. There is no GCP counterpart module: on GCP build capability
# is granted in install-gcp.sh and Cloud Build needs no infrastructure.

locals {
  # Stable, install-level names (one bioAF install per AWS account, like the
  # foundation module). The app reads the project name from the output, so the
  # exact string is not load-bearing for the app, only for redeploy idempotency.
  project_name = "bioaf-image-build"
  role_name    = "bioaf-codebuild-image-build"
}

# --- CodeBuild service role (the identity the build runs as) -------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name                 = local.role_name
  permissions_boundary = local.role_boundary
  assume_role_policy   = data.aws_iam_policy_document.assume.json

  tags = {
    managed_by = "bioaf"
    purpose    = "image-build"
  }
}

# The account's default VPC: where the Packer builder instance runs (no compute dependency).
data "aws_vpc" "default" {
  default = true
}

locals {
  account   = var.account_id == "" ? "*" : var.account_id
  ec2_arn   = "arn:${data.aws_partition.current.partition}:ec2:${var.region}:${local.account}"
  owned     = local.installation_id == "" ? "unset" : local.installation_id
  canonical = "099720109477" # Ubuntu's AMI owner, the Packer source image.
}

data "aws_iam_policy_document" "codebuild" {
  # ECR auth token is account-wide (cannot be resource-scoped).
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # Push/pull layers + manage the per-image bioaf-* ECR repos.
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:DescribeRepositories",
      "ecr:CreateRepository",
    ]
    resources = ["arn:aws:ecr:${var.region}:${var.account_id}:repository/bioaf-*"]
  }

  # Read the build context (the tar.gz the app uploaded to the working bucket), only from this
  # account's bioaf-* buckets.
  statement {
    sid       = "ReadBuildContext"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["arn:aws:s3:::bioaf-*/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account]
    }
  }

  # Build logs: only this project's CodeBuild log group.
  statement {
    sid = "BuildLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.region}:${local.account}:log-group:/aws/codebuild/${local.project_name}",
      "arn:${data.aws_partition.current.partition}:logs:${var.region}:${local.account}:log-group:/aws/codebuild/${local.project_name}:*",
    ]
  }

  # --- Packer (amazon-ebs) work-node AMI builds (plan 04B) ----------------------
  #
  # Packer launches a transient builder from Ubuntu's AMI into the default VPC using the
  # pre-created bioaf-packer-builder group below, registers the AMI, and terminates the builder.
  # Every instance, volume, snapshot and AMI carries bioaf:installation (the template's run_tags,
  # run_volume_tags, snapshot_tags and tags); changes and cleanup need that tag. The role cannot
  # create or edit security groups or share images.

  statement {
    sid = "PackerDescribe"
    actions = [
      "ec2:DescribeImageAttribute",
      "ec2:DescribeImages",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeRegions",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSnapshots",
      "ec2:DescribeSubnets",
      "ec2:DescribeTags",
      "ec2:DescribeVolumes",
      "ec2:DescribeVpcs",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeKeyPairs",
    ]
    resources = ["*"]
  }

  # RunInstances creates the instance and its volumes, which must carry the ownership tag.
  statement {
    sid       = "PackerLaunchOwned"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:instance/*", "${local.ec2_arn}:volume/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/bioaf:installation"
      values   = [local.owned]
    }
  }

  # ... into the default VPC only ...
  statement {
    sid       = "PackerLaunchNetwork"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:subnet/*", "${local.ec2_arn}:security-group/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:Vpc"
      values   = [data.aws_vpc.default.arn]
    }
  }

  # ... from Ubuntu's published image ...
  statement {
    sid       = "PackerLaunchSourceImage"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}::image/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:Owner"
      values   = [local.canonical]
    }
  }

  # ... with Packer's temporary key pair and a fresh network interface.
  statement {
    sid       = "PackerLaunchKeyAndInterface"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:key-pair/packer_*", "${local.ec2_arn}:network-interface/*"]
  }

  statement {
    sid       = "PackerTemporaryKeyPair"
    actions   = ["ec2:CreateKeyPair", "ec2:DeleteKeyPair"]
    resources = ["${local.ec2_arn}:key-pair/packer_*"]
  }

  # Tags at launch (run_tags / run_volume_tags), always including the ownership tag.
  statement {
    sid       = "PackerTagAtLaunch"
    actions   = ["ec2:CreateTags"]
    resources = ["${local.ec2_arn}:instance/*", "${local.ec2_arn}:volume/*", "${local.ec2_arn}:network-interface/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }

  # Work on the builder it launched: stop, terminate, image it.
  statement {
    sid = "PackerOwnedBuilder"
    actions = [
      "ec2:StopInstances",
      "ec2:TerminateInstances",
      "ec2:CreateImage",
      "ec2:AttachVolume",
      "ec2:DetachVolume",
      "ec2:DeleteVolume",
    ]
    resources = ["${local.ec2_arn}:instance/*", "${local.ec2_arn}:volume/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/bioaf:installation"
      values   = [local.owned]
    }
  }

  # CreateImage / RegisterImage produce the new AMI and its snapshots.
  statement {
    sid       = "PackerNewImage"
    actions   = ["ec2:CreateImage", "ec2:RegisterImage", "ec2:CreateSnapshot"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}::image/*", "arn:${data.aws_partition.current.partition}:ec2:${var.region}::snapshot/*"]
  }

  # Packer tags the AMI and its snapshots after creation, so these tags cannot be tied to the
  # create call; they must carry the ownership tag. (Residual: this role could tag another image
  # or snapshot in the account as owned. It runs only bioAF's own buildspec.)
  statement {
    sid       = "PackerTagImage"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}::image/*", "arn:${data.aws_partition.current.partition}:ec2:${var.region}::snapshot/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/bioaf:installation"
      values   = [local.owned]
    }
  }

  # Rebuild cleanup (force_deregister / force_delete_snapshot) and failed-build cleanup: only
  # images and snapshots this installation owns.
  statement {
    sid       = "PackerCleanupOwned"
    actions   = ["ec2:DeregisterImage", "ec2:DeleteSnapshot"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}::image/*", "arn:${data.aws_partition.current.partition}:ec2:${var.region}::snapshot/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/bioaf:installation"
      values   = [local.owned]
    }
  }
}

# The builder's security group, so Packer never creates or edits one. SSH from anywhere matches the
# temporary group Packer created before (temporary_security_group_source_cidrs defaults to
# 0.0.0.0/0): CodeBuild's egress address is not fixed. The builder lives only for one build.
resource "aws_security_group" "packer_builder" {
  name        = "bioaf-packer-builder"
  description = "bioAF Packer work-node AMI builder (transient instances only)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH from the CodeBuild Packer run"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Package mirrors and conda channels"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    managed_by = "bioaf"
    purpose    = "image-build"
  }
}

resource "aws_iam_role_policy" "codebuild" {
  name   = "bioaf-codebuild-image-build"
  role   = aws_iam_role.codebuild.id
  policy = data.aws_iam_policy_document.codebuild.json
}

# --- CodeBuild project ---------------------------------------------------------

resource "aws_codebuild_project" "image_build" {
  name         = local.project_name
  description  = "bioAF container image builds (notebook + cellxgene), pushed to ECR."
  service_role = aws_iam_role.codebuild.arn

  # Minutes; overridden per-build via timeoutInMinutesOverride (the heavy
  # R/Bioconductor notebook image needs ~2h, cellxgene minutes).
  build_timeout = 120

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    # MEDIUM default; the app overrides to LARGE per-build for the heavy image.
    compute_type = "BUILD_GENERAL1_MEDIUM"
    # standard:7.0 ships Docker; privileged_mode is required to run docker build.
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true
  }

  # NO_SOURCE: the app's inline buildspecOverride pulls the tar.gz context from S3
  # itself (S3-source would require a ZIP; the service emits tar.gz for both
  # clouds). A placeholder buildspec satisfies the required field; every StartBuild
  # overrides it.
  source {
    type      = "NO_SOURCE"
    buildspec = "version: 0.2\nphases:\n  build:\n    commands:\n      - echo overridden-per-build"
  }

  logs_config {
    cloudwatch_logs {
      status = "ENABLED"
    }
  }

  tags = {
    managed_by = "bioaf"
    purpose    = "image-build"
  }
}
