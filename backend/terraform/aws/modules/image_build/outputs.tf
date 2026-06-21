# read_module_outputs maps this to the platform_config `aws_codebuild_project` key
# that resolve_image_platform reads, so the CodeBuildImageBuildProvider knows which
# project to StartBuild. The app is cloud-blind; this is the AWS analog of Cloud
# Build needing no project at all.
output "codebuild_project_name" {
  value       = aws_codebuild_project.image_build.name
  description = "Name of the CodeBuild project the app submits image builds to."
}

output "codebuild_role_arn" {
  value       = aws_iam_role.codebuild.arn
  description = "ARN of the CodeBuild service role (the identity image builds run as)."
}
