"""Unit tests for AWS configuration Pydantic schemas (DB-free)."""

import pytest
from pydantic import ValidationError

from app.schemas.aws_config import (
    AwsConfigResponse,
    AwsConfigUpdate,
    AwsValidationCheck,
    AwsValidationResult,
)


class TestAwsConfigUpdate:
    def test_valid_update_all_fields(self):
        body = AwsConfigUpdate(
            aws_account_id="043671579834",
            aws_region="us-west-1",
            aws_app_role_arn="arn:aws:iam::043671579834:role/bioaf-app",
            aws_bootstrap_role_arn="arn:aws:iam::043671579834:role/bioaf-bootstrap",
            org_slug="bioaf-demo",
        )
        assert body.aws_account_id == "043671579834"
        assert body.org_slug == "bioaf-demo"

    def test_valid_update_partial_fields(self):
        body = AwsConfigUpdate(aws_region="eu-west-1")
        assert body.aws_region == "eu-west-1"
        assert body.aws_account_id is None

    # org_slug validation is shared with the GCP schema.
    def test_org_slug_too_short(self):
        with pytest.raises(ValidationError):
            AwsConfigUpdate(org_slug="ab")

    def test_org_slug_uppercase_rejected(self):
        with pytest.raises(ValidationError):
            AwsConfigUpdate(org_slug="MyOrg")

    def test_org_slug_consecutive_hyphens(self):
        with pytest.raises(ValidationError):
            AwsConfigUpdate(org_slug="in--valid")

    def test_org_slug_valid_with_hyphens(self):
        body = AwsConfigUpdate(org_slug="my-bio-lab")
        assert body.org_slug == "my-bio-lab"

    def test_org_slug_none_is_allowed(self):
        body = AwsConfigUpdate(org_slug=None)
        assert body.org_slug is None


class TestAwsConfigResponse:
    def test_response_fields(self):
        resp = AwsConfigResponse(
            aws_account_id="043671579834",
            aws_region="us-west-1",
            aws_app_role_arn="arn:aws:iam::043671579834:role/bioaf-app",
            aws_bootstrap_role_arn=None,
            org_slug="my-org",
            aws_credential_source="instance_profile",
            aws_credentials_configured=False,
            aws_validation_status=None,
        )
        assert resp.aws_account_id == "043671579834"
        assert resp.aws_credentials_configured is False
        assert resp.aws_credential_source == "instance_profile"

    def test_response_has_no_secret_fields(self):
        """AWS uses ambient creds; no secret key should ever appear on the response."""
        resp = AwsConfigResponse(
            aws_account_id=None,
            aws_region="us-east-1",
            aws_app_role_arn=None,
            aws_bootstrap_role_arn=None,
            org_slug=None,
            aws_credential_source="instance_profile",
            aws_credentials_configured=False,
            aws_validation_status=None,
        )
        assert not hasattr(resp, "secret_access_key")
        assert not hasattr(resp, "aws_secret_access_key")


class TestAwsValidationCheck:
    def test_passed_check_status(self):
        check = AwsValidationCheck(name="credentials", passed=True, message="OK")
        assert check.status == "ok"

    def test_failed_check_status(self):
        check = AwsValidationCheck(name="account_match", passed=False, message="mismatch")
        assert check.status == "failed"

    def test_explicit_status_preserved(self):
        check = AwsValidationCheck(name="region", passed=False, message="skip", status="skipped")
        assert check.status == "skipped"


class TestAwsValidationResult:
    def test_all_passed(self):
        checks = [AwsValidationCheck(name=f"c{i}", passed=True, message="OK") for i in range(3)]
        result = AwsValidationResult(passed=True, checks=checks, account_id="043671579834")
        assert result.passed is True
        assert result.account_id == "043671579834"
        assert len(result.checks) == 3

    def test_failed_result_defaults_account_none(self):
        result = AwsValidationResult(
            passed=False,
            checks=[AwsValidationCheck(name="credentials", passed=False, message="no creds")],
        )
        assert result.passed is False
        assert result.account_id is None
