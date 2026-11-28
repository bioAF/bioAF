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
  path                 = lookup(var.role_paths, local.role_name, var.new_role_path)
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

# The role's permissions are the installer-owned bioaf-role-image-build policy (installer/aws_iam.sh):
# ECR push to bioaf-* repositories, this project's logs, build-context reads, and Packer limited to
# Ubuntu images launched into the default VPC with the builder group, owned builders and volumes,
# and no image removal, sharing or ownership tagging. The app attaches it; it cannot write it.
resource "aws_iam_role_policy_attachment" "codebuild" {
  role       = aws_iam_role.codebuild.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:policy/bioaf-role-image-build"
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
