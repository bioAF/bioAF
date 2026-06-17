# Outputs mirror the GKE compute module's contract so the shared deploy flow can
# read cluster_name / cluster_endpoint / cluster_ca_cert the same way on both
# clouds. The runner "identity" values are IAM role ARNs on AWS (vs GCP SA
# emails); they are surfaced under the SAME keys the GCP module uses so the
# stack-deploy output mapping needs no per-cloud special-casing, PLUS explicit
# *_role_arn names for the AWS pod-identity (IRSA) wiring.

output "cluster_name" {
  value       = aws_eks_cluster.this.name
  description = "Name of the EKS cluster."
}

output "cluster_endpoint" {
  value       = aws_eks_cluster.this.endpoint
  description = "EKS cluster API server endpoint URL."
}

output "cluster_ca_cert" {
  value       = aws_eks_cluster.this.certificate_authority[0].data
  description = "EKS cluster CA certificate (base64), as for GKE."
  sensitive   = true
}

output "notebook_runner_sa_email" {
  value       = aws_iam_role.runner["notebook"].arn
  description = "IRSA role ARN for notebook pods (GCP key name reused; value is an ARN)."
}

output "cellxgene_runner_sa_email" {
  value       = aws_iam_role.runner["cellxgene"].arn
  description = "IRSA role ARN for cellxgene pods (GCP key name reused; value is an ARN)."
}

output "notebook_runner_role_arn" {
  value       = aws_iam_role.runner["notebook"].arn
  description = "IRSA role ARN for notebook pods."
}

output "pipeline_runner_role_arn" {
  value       = aws_iam_role.runner["pipeline"].arn
  description = "IRSA role ARN for Nextflow pipeline pods."
}

output "cellxgene_runner_role_arn" {
  value       = aws_iam_role.runner["cellxgene"].arn
  description = "IRSA role ARN for cellxgene viewer pods."
}

output "oidc_provider_arn" {
  value       = aws_iam_openid_connect_provider.oidc.arn
  description = "ARN of the cluster IRSA OIDC provider."
}

output "cluster_autoscaler_role_arn" {
  value       = aws_iam_role.cluster_autoscaler.arn
  description = "IRSA role ARN for the in-cluster Cluster Autoscaler (the kube-system cluster-autoscaler SA is annotated with this so the CA can call the EC2/ASG/EKS APIs)."
}

output "oidc_provider_url" {
  value       = aws_eks_cluster.this.identity[0].oidc[0].issuer
  description = "OIDC issuer URL of the cluster (for IRSA trust policies)."
}

output "cluster_security_group_id" {
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  description = "EKS-managed cluster security group id."
}

output "vpc_id" {
  value       = aws_vpc.this.id
  description = "VPC the cluster runs in."
}
