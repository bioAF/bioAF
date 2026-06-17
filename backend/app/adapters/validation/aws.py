"""AWS credentials validation provider (sibling of ``validation/gcp.py``).

Confirms the install's AWS credentials (the EC2 instance profile / ambient
provider chain) resolve and match the configured account, so the setup wizard
and the AWS settings panel can gate infrastructure deploy the way the GCP path
does. boto3 lives here, behind the BAL boundary (the layering guard forbids it
in the service layer).

The check set is intentionally lighter than the GCP permission probe: AWS auth
is the ambient instance profile (there is no key to validate) and the starter
IAM policy is resource-scoped, so a broad permission probe would false-negative.
We verify that the credentials resolve (STS GetCallerIdentity), that the live
account matches the configured account, and that a region is set: enough to
prove the app's ambient-credential S3 / Terraform path will authenticate before
a deploy.
"""

from __future__ import annotations

from app.schemas.aws_config import AwsValidationCheck, AwsValidationResult


def _get_sts_client(region: str | None):
    """Build an STS client from the ambient credential chain.

    Lazily imports boto3 (mirrors the S3 storage adapter) and is the single seam
    tests patch to inject a fake STS client.
    """
    import boto3

    return boto3.client("sts", region_name=region or None)


def validate_aws_credentials(*, account_id: str | None, region: str | None) -> AwsValidationResult:
    """Validate the ambient AWS credentials via STS GetCallerIdentity."""
    checks: list[AwsValidationCheck] = []

    # Check 1: credentials resolve via the ambient chain (instance profile).
    try:
        identity = _get_sts_client(region).get_caller_identity()
    except Exception as exc:  # boto3/botocore raise many types; treat all as "no creds"
        checks.append(
            AwsValidationCheck(
                name="credentials",
                passed=False,
                message=f"Could not resolve AWS credentials: {exc}",
            )
        )
        return AwsValidationResult(passed=False, checks=checks)

    caller_account = identity.get("Account")
    caller_arn = identity.get("Arn", "")
    checks.append(
        AwsValidationCheck(
            name="credentials",
            passed=True,
            message=f"Credentials resolved ({caller_arn or 'unknown principal'})",
        )
    )

    # Check 2: the live account matches the configured account (when one is set).
    if account_id:
        matches = caller_account == account_id
        checks.append(
            AwsValidationCheck(
                name="account_match",
                passed=matches,
                message=(
                    f"Caller account {caller_account} matches configured {account_id}"
                    if matches
                    else f"Caller account {caller_account} does not match configured {account_id}"
                ),
            )
        )
    else:
        checks.append(
            AwsValidationCheck(
                name="account_match",
                passed=True,
                message=f"Using detected account {caller_account}",
            )
        )

    # Check 3: a region must be set for the S3 / Terraform backends to address.
    region_ok = bool(region)
    checks.append(
        AwsValidationCheck(
            name="region",
            passed=region_ok,
            message=f"Region {region}" if region_ok else "No region configured",
        )
    )

    return AwsValidationResult(
        passed=all(c.passed for c in checks),
        checks=checks,
        account_id=caller_account,
    )
