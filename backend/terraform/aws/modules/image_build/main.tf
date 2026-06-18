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
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.assume.json

  tags = {
    managed_by = "bioaf"
    purpose    = "image-build"
  }
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

  # Read the build context (the Dockerfile tar.gz the app uploaded to the working
  # bucket). Scoped to bioaf-* buckets, mirroring the bioaf-app s3:* on bioaf-*.
  statement {
    sid       = "ReadBuildContext"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["arn:aws:s3:::bioaf-*/*"]
  }

  # Build logs. STARTER scope (Resource "*"); tighten in Stage 7.
  statement {
    sid = "BuildLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["*"]
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
