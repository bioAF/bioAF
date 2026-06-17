terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
  backend "s3" {} # Configured dynamically by the executor (S3 state), as in the AWS storage module.
}

provider "aws" {
  region = var.region
  # No explicit credentials: on AWS the app runs on an EC2 VM whose bioaf-app
  # instance profile is the ambient credential (same as the storage module).
}

# --- Naming + shared locals ---------------------------------------------------
#
# The cluster name mirrors the GKE module: bioaf-${org_slug}-${stack_uid}. The
# runner namespaces/KSAs match the GCP module exactly so the app's K8s
# orchestration (namespaces, serviceAccountName bindings) is cloud-neutral; only
# the pod-identity annotation differs (IRSA role ARN vs Workload Identity SA).

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_partition" "current" {}

locals {
  cluster_name = "bioaf-${var.org_slug}-${var.stack_uid}"

  # Two AZs is the EKS minimum; public subnets keep the deploy cheap (no NAT
  # gateway). Nodes get public IPs and reach S3 / ECR / the control plane via the
  # internet gateway. Tighten to private subnets + NAT or VPC endpoints post-MVP.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  # S3 ARNs scoped to bioaf-* buckets, the IAM-policy analog of the GCP module's
  # resource.name.startsWith("projects/_/buckets/bioaf-") IAM Condition.
  s3_bucket_arns = ["arn:${data.aws_partition.current.partition}:s3:::bioaf-*"]
  s3_object_arns = ["arn:${data.aws_partition.current.partition}:s3:::bioaf-*/*"]

  # The three pod-identity runners (namespace + KSA), identical to the GKE module.
  runners = {
    notebook  = { namespace = "bioaf-notebooks", ksa = "bioaf-notebook-runner", readonly = false }
    pipeline  = { namespace = "bioaf-pipelines", ksa = "bioaf-pipeline-runner", readonly = false }
    cellxgene = { namespace = "bioaf-cellxgene", ksa = "bioaf-cellxgene-runner", readonly = true }
  }

  tags = {
    managed_by = "bioaf"
    org        = var.org_slug
  }
}

# --- VPC + public networking --------------------------------------------------

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${local.cluster_name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.cluster_name}-igw" })
}

resource "aws_subnet" "public" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = true

  tags = merge(local.tags, {
    Name = "${local.cluster_name}-public-${count.index}"
    # EKS uses this tag to place public load balancers.
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.tags, { Name = "${local.cluster_name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Cluster IAM role ---------------------------------------------------------

data "aws_iam_policy_document" "cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    effect  = "Allow"
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${local.cluster_name}-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
}

# --- EKS cluster --------------------------------------------------------------

resource "aws_eks_cluster" "this" {
  name     = local.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_public_access  = true
    endpoint_private_access = true
  }

  access_config {
    # API_AND_CONFIG_MAP lets the app authorize the bioaf-app role via EKS Access
    # Entries (the out-of-cluster RBAC path the ClusterAuth seam relies on).
    authentication_mode = "API_AND_CONFIG_MAP"
    # When app_role_arn is set we grant it admin via an explicit access entry
    # below, so the implicit creator grant is off (avoids a duplicate-entry
    # conflict when the creator IS bioaf-app). When unset, fall back to granting
    # the creator admin so the cluster is never locked out.
    bootstrap_cluster_creator_admin_permissions = var.app_role_arn == ""
  }

  tags = local.tags

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

# --- IRSA OIDC provider -------------------------------------------------------

data "tls_certificate" "oidc" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "oidc" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]
  tags            = local.tags
}

locals {
  oidc_provider_arn = aws_iam_openid_connect_provider.oidc.arn
  oidc_host         = replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

# --- Node IAM role ------------------------------------------------------------

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    effect  = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.cluster_name}-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "node_eks_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Node-level S3 access to bioaf-* (the analog of the GCP module granting the
# default node SA storage.objectAdmin). Pods that use IRSA get scoped creds via
# their runner role; this covers node-level / non-IRSA access.
data "aws_iam_policy_document" "node_s3" {
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = local.s3_object_arns
  }
  statement {
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = local.s3_bucket_arns
  }
}

resource "aws_iam_role_policy" "node_s3" {
  name   = "bioaf-node-s3"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.node_s3.json
}

# --- Node groups (the AWS analogs of the GKE node pools) ----------------------
#
# system: always-on (min 1) for addons; on-demand.
# pipelines: scale-to-zero, Spot (cheap, preemptible task pods).
# interactive: scale-to-zero, on-demand (notebook sessions).
# pipeline_head: scale-to-zero, on-demand (Nextflow head must survive Spot).
#
# NOTE: EKS managed node groups define min/max here, but pod-driven scale-up
# requires an in-cluster autoscaler (Cluster Autoscaler / Karpenter) -- unlike
# GKE, whose control plane autoscales natively. Installing that component is a
# documented Stage-6e follow-up; the groups below give the correct shape + the
# always-on system floor.

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "bioaf-system"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.public[*].id
  instance_types  = [var.system_instance_type]
  capacity_type   = "ON_DEMAND"
  disk_size       = 30

  scaling_config {
    min_size     = 1
    desired_size = 1
    max_size     = var.system_max_nodes
  }

  labels = { "bioaf.io/pool" = "system" }

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_eks_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

resource "aws_eks_node_group" "pipelines" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "bioaf-pipelines"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.public[*].id
  instance_types  = [var.pipeline_instance_type]
  capacity_type   = var.pipeline_use_spot ? "SPOT" : "ON_DEMAND"
  disk_size       = 100

  scaling_config {
    min_size     = 0
    desired_size = 0
    max_size     = var.pipeline_max_nodes
  }

  labels = { "bioaf.io/pool" = "pipelines" }

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_eks_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

resource "aws_eks_node_group" "interactive" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "bioaf-interactive"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.public[*].id
  instance_types  = [var.interactive_instance_type]
  capacity_type   = "ON_DEMAND"
  disk_size       = 100

  scaling_config {
    min_size     = 0
    desired_size = 0
    max_size     = var.interactive_max_nodes
  }

  labels = { "bioaf.io/pool" = "interactive" }

  # Match the GKE interactive pool taint so only notebook pods (which carry the
  # toleration) land here.
  taint {
    key    = "bioaf.io/pool"
    value  = "interactive"
    effect = "NO_SCHEDULE"
  }

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_eks_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

resource "aws_eks_node_group" "pipeline_head" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "bioaf-pipeline-head"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.public[*].id
  instance_types  = [var.pipeline_head_instance_type]
  capacity_type   = "ON_DEMAND"
  disk_size       = 30

  scaling_config {
    min_size     = 0
    desired_size = 0
    max_size     = var.pipeline_head_max_nodes
  }

  labels = { "bioaf.io/pool" = "pipeline-head" }

  taint {
    key    = "bioaf.io/pool"
    value  = "pipeline-head"
    effect = "NO_SCHEDULE"
  }

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_eks_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

# --- EBS CSI driver (POSIX scratch volumes for the DataStaging seam) ----------
#
# GKE ships an in-tree PD CSI; EKS needs the EBS CSI driver installed as an addon
# with its own IRSA role. The DataStaging ScratchWorkDir (Nextflow workDir on a
# POSIX volume) provisions EBS-backed PVCs through this.

data "aws_iam_policy_document" "ebs_csi_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${local.cluster_name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# --- EKS addons (vpc-cni, kube-proxy, coredns auto-install with the cluster;
# pin them explicitly + add the EBS CSI driver) ---

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "vpc-cni"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.system]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "kube-proxy"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.system]
}

resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "coredns"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  # CoreDNS pods need a node to schedule on.
  depends_on = [aws_eks_node_group.system]
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "aws-ebs-csi-driver"
  service_account_role_arn    = aws_iam_role.ebs_csi.arn
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.system]
}

# --- IRSA runner roles (the EKS analog of the GKE Workload Identity SAs) -------
#
# One IAM role per runner, trusting the cluster OIDC provider for that
# namespace:serviceaccount. The PodIdentity seam annotates the KSA with
# eks.amazonaws.com/role-arn = <this role>, the way the GCP module binds the GSA
# via iam.workloadIdentityUser. S3 access is scoped to bioaf-* (read-only for
# cellxgene, read/write for notebook + pipeline), matching the GCP scoping.

data "aws_iam_policy_document" "runner_trust" {
  for_each = local.runners
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:${each.value.namespace}:${each.value.ksa}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runner" {
  for_each           = local.runners
  name               = "bioaf-${each.key}-runner-${var.stack_uid}"
  assume_role_policy = data.aws_iam_policy_document.runner_trust[each.key].json
  tags               = merge(local.tags, { runner = each.key })
}

data "aws_iam_policy_document" "runner_s3" {
  for_each = local.runners
  statement {
    effect = "Allow"
    actions = each.value.readonly ? ["s3:GetObject"] : [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject"
    ]
    resources = local.s3_object_arns
  }
  statement {
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = local.s3_bucket_arns
  }
}

resource "aws_iam_role_policy" "runner_s3" {
  for_each = local.runners
  name     = "bioaf-${each.key}-runner-s3"
  role     = aws_iam_role.runner[each.key].id
  policy   = data.aws_iam_policy_document.runner_s3[each.key].json
}

# --- Cluster access for the bioaf-app role (out-of-cluster control) -----------
#
# The EKS Access Entry + cluster-admin association that lets the backend (running
# as bioaf-app) drive the cluster API from the VM, via the ClusterAuth seam's STS
# token. Created only when app_role_arn is provided; otherwise the cluster
# creator was granted admin at create time (see access_config above).

resource "aws_eks_access_entry" "app" {
  count         = var.app_role_arn == "" ? 0 : 1
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.app_role_arn
  type          = "STANDARD"
  tags          = local.tags
}

resource "aws_eks_access_policy_association" "app_admin" {
  count         = var.app_role_arn == "" ? 0 : 1
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.app_role_arn
  policy_arn    = "arn:${data.aws_partition.current.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope {
    type = "cluster"
  }
  depends_on = [aws_eks_access_entry.app]
}

# --- Cluster Autoscaler: IRSA role + ASG discovery tags (Stage 6e increment 3) -
#
# EKS managed node groups do NOT pod-autoscale natively (GKE's control plane
# does). The in-cluster Cluster Autoscaler scales each node group's underlying
# ASG 0<->N as pods go Pending/idle. Terraform provisions the two AWS-side
# prerequisites: (1) an IRSA role the CA assumes to call the EC2/ASG/EKS APIs,
# and (2) discovery + scale-from-zero hint tags on each managed node group's ASG.
# The CA workload itself (Deployment/RBAC/SA in kube-system) is installed
# app-side at compute-deploy through the cluster connection -- mirroring how the
# app already creates the pipeline namespace/SA/Job -- so it is deliberately NOT
# a terraform helm_release here (keeps the proven compute apply free of an
# in-cluster helm/kube provider and its exec-auth blast radius).

data "aws_iam_policy_document" "cluster_autoscaler_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:kube-system:cluster-autoscaler"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster_autoscaler" {
  name               = "${local.cluster_name}-cluster-autoscaler"
  assume_role_policy = data.aws_iam_policy_document.cluster_autoscaler_trust.json
  tags               = local.tags
}

# The canonical Cluster Autoscaler permission set. STARTER scope (Resource "*"),
# consistent with the install-aws.sh starter policy; TIGHTEN in Stage 7 (scope
# the mutating actions to this cluster's ASGs via an autoscaling:ResourceTag
# condition on k8s.io/cluster-autoscaler/${cluster_name}).
data "aws_iam_policy_document" "cluster_autoscaler" {
  statement {
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeLaunchConfigurations",
      "autoscaling:DescribeScalingActivities",
      "autoscaling:DescribeTags",
      "autoscaling:SetDesiredCapacity",
      "autoscaling:TerminateInstanceInAutoScalingGroup",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:GetInstanceTypesFromInstanceRequirements",
      "eks:DescribeNodegroup",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  name   = "bioaf-cluster-autoscaler"
  role   = aws_iam_role.cluster_autoscaler.id
  policy = data.aws_iam_policy_document.cluster_autoscaler.json
}

# Discovery + scale-from-zero tags on each scalable node group's ASG. EKS does
# NOT propagate managed-node-group tags down to the ASG, so we tag the ASG
# directly. The CA discovers ASGs by the enabled/<cluster> tags; the
# node-template/label + node-template/taint tags let it determine that scaling a
# 0-sized group WOULD satisfy a Pending pod's nodeSelector/toleration
# (scale-from-zero needs the would-be node's labels/taints, which it cannot read
# from a group that has no nodes yet). The always-on system group is left
# unmanaged (min 1, hosts the CA + addons).
locals {
  autoscaled_node_groups = {
    pipelines     = { asg = aws_eks_node_group.pipelines.resources[0].autoscaling_groups[0].name, pool = "pipelines", taint = "" }
    interactive   = { asg = aws_eks_node_group.interactive.resources[0].autoscaling_groups[0].name, pool = "interactive", taint = "interactive" }
    pipeline_head = { asg = aws_eks_node_group.pipeline_head.resources[0].autoscaling_groups[0].name, pool = "pipeline-head", taint = "pipeline-head" }
  }

  # Flatten {node_group -> {tags}} into a single map keyed by "<group>/<tag>" so
  # aws_autoscaling_group_tag (one resource per tag) can for_each over it. Keys
  # are static (known at plan); only the ASG-name values are computed.
  ca_asg_tags = merge([
    for ng_key, ng in local.autoscaled_node_groups : merge(
      {
        "${ng_key}/enabled" = { asg = ng.asg, key = "k8s.io/cluster-autoscaler/enabled", value = "true" }
        "${ng_key}/owned"   = { asg = ng.asg, key = "k8s.io/cluster-autoscaler/${local.cluster_name}", value = "owned" }
        "${ng_key}/label"   = { asg = ng.asg, key = "k8s.io/cluster-autoscaler/node-template/label/bioaf.io/pool", value = ng.pool }
      },
      ng.taint == "" ? {} : {
        "${ng_key}/taint" = { asg = ng.asg, key = "k8s.io/cluster-autoscaler/node-template/taint/bioaf.io/pool", value = "${ng.taint}:NoSchedule" }
      }
    )
  ]...)
}

resource "aws_autoscaling_group_tag" "ca" {
  for_each               = local.ca_asg_tags
  autoscaling_group_name = each.value.asg

  tag {
    key                 = each.value.key
    value               = each.value.value
    propagate_at_launch = false
  }
}
