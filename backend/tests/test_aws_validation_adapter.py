"""Unit tests for the AWS credentials validation adapter (DB-free).

Patches the ``_get_sts_client`` seam so no real AWS / boto3 call is made.
"""

from unittest.mock import MagicMock, patch

from app.adapters.validation.aws import validate_aws_credentials

_PATCH_TARGET = "app.adapters.validation.aws._get_sts_client"


def _fake_sts(account="043671579834", arn="arn:aws:iam::043671579834:user/brent"):
    client = MagicMock()
    client.get_caller_identity.return_value = {"Account": account, "Arn": arn}
    return client


class TestValidateAwsCredentials:
    def test_all_checks_pass_when_account_matches(self):
        with patch(_PATCH_TARGET, return_value=_fake_sts()):
            result = validate_aws_credentials(account_id="043671579834", region="us-west-1")
        assert result.passed is True
        assert result.account_id == "043671579834"
        assert {c.name for c in result.checks} == {"credentials", "account_match", "region"}
        assert all(c.passed for c in result.checks)

    def test_account_mismatch_fails(self):
        with patch(_PATCH_TARGET, return_value=_fake_sts(account="999999999999")):
            result = validate_aws_credentials(account_id="043671579834", region="us-west-1")
        assert result.passed is False
        match = next(c for c in result.checks if c.name == "account_match")
        assert match.passed is False
        assert "does not match" in match.message

    def test_no_configured_account_uses_detected(self):
        with patch(_PATCH_TARGET, return_value=_fake_sts(account="111111111111")):
            result = validate_aws_credentials(account_id=None, region="us-west-1")
        assert result.passed is True
        match = next(c for c in result.checks if c.name == "account_match")
        assert "111111111111" in match.message

    def test_missing_region_fails(self):
        with patch(_PATCH_TARGET, return_value=_fake_sts()):
            result = validate_aws_credentials(account_id="043671579834", region=None)
        assert result.passed is False
        region = next(c for c in result.checks if c.name == "region")
        assert region.passed is False

    def test_no_credentials_returns_single_failed_check(self):
        with patch(_PATCH_TARGET, side_effect=RuntimeError("Unable to locate credentials")):
            result = validate_aws_credentials(account_id="043671579834", region="us-west-1")
        assert result.passed is False
        assert len(result.checks) == 1
        assert result.checks[0].name == "credentials"
        assert result.checks[0].passed is False

    def test_get_caller_identity_error_is_caught(self):
        bad = MagicMock()
        bad.get_caller_identity.side_effect = RuntimeError("AccessDenied")
        with patch(_PATCH_TARGET, return_value=bad):
            result = validate_aws_credentials(account_id="043671579834", region="us-west-1")
        assert result.passed is False
        assert result.checks[0].name == "credentials"
