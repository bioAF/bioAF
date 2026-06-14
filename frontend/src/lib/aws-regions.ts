// Common AWS regions for the AWS settings panel (stage 8d). Parallel to
// gcp-regions.ts. Not exhaustive; the install picks one and it is persisted.
export const AWS_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "eu-west-1",
  "eu-west-2",
  "eu-central-1",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-northeast-1",
] as const;

export const DEFAULT_AWS_REGION = "us-east-1";
