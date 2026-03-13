"""Unit tests for GCP configuration Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.gcp_config import GCPConfigResponse, GCPConfigUpdate, GCPValidationCheck, GCPValidationResult


class TestGCPConfigUpdate:
    def test_valid_update_all_fields(self):
        body = GCPConfigUpdate(
            gcp_project_id="my-project-123",
            gcp_region="us-east1",
            gcp_zone="us-east1-b",
            org_slug="bioaf-demo",
            gcp_credential_source="vm_default",
        )
        assert body.gcp_project_id == "my-project-123"
        assert body.org_slug == "bioaf-demo"

    def test_valid_update_partial_fields(self):
        body = GCPConfigUpdate(gcp_region="us-west1")
        assert body.gcp_region == "us-west1"
        assert body.gcp_project_id is None

    # org_slug validation tests
    def test_org_slug_too_short(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="ab")

    def test_org_slug_too_long(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="a" * 31)

    def test_org_slug_leading_hyphen(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="-invalid")

    def test_org_slug_trailing_hyphen(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="invalid-")

    def test_org_slug_consecutive_hyphens(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="in--valid")

    def test_org_slug_uppercase_rejected(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="MyOrg")

    def test_org_slug_special_chars_rejected(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(org_slug="my_org")

    def test_org_slug_valid_min_length(self):
        body = GCPConfigUpdate(org_slug="abc")
        assert body.org_slug == "abc"

    def test_org_slug_valid_max_length(self):
        body = GCPConfigUpdate(org_slug="a" * 30)
        assert body.org_slug == "a" * 30

    def test_org_slug_valid_with_hyphens(self):
        body = GCPConfigUpdate(org_slug="my-bio-lab")
        assert body.org_slug == "my-bio-lab"

    def test_org_slug_none_is_allowed(self):
        body = GCPConfigUpdate(org_slug=None)
        assert body.org_slug is None

    def test_credential_source_invalid(self):
        with pytest.raises(ValidationError):
            GCPConfigUpdate(gcp_credential_source="invalid_source")

    def test_credential_source_service_account(self):
        body = GCPConfigUpdate(gcp_credential_source="service_account_key")
        assert body.gcp_credential_source == "service_account_key"


class TestGCPConfigResponse:
    def test_response_fields(self):
        resp = GCPConfigResponse(
            gcp_project_id="proj-123",
            gcp_region="us-central1",
            gcp_zone="us-central1-a",
            org_slug="my-org",
            gcp_credentials_configured=False,
            gcp_validation_status=None,
            gcp_credential_source="vm_default",
            gcp_service_account_email=None,
        )
        assert resp.gcp_project_id == "proj-123"
        assert resp.gcp_credentials_configured is False
        assert resp.gcp_service_account_email is None

    def test_response_no_service_account_key_field(self):
        """Response must never expose the raw service account key."""
        resp = GCPConfigResponse(
            gcp_project_id="proj",
            gcp_region="us-central1",
            gcp_zone="us-central1-a",
            org_slug=None,
            gcp_credentials_configured=False,
            gcp_validation_status=None,
            gcp_credential_source="vm_default",
            gcp_service_account_email=None,
        )
        assert not hasattr(resp, "service_account_key")
        assert not hasattr(resp, "gcp_service_account_key")


class TestGCPValidationCheck:
    def test_passed_check(self):
        check = GCPValidationCheck(name="credentials_loaded", passed=True, message="OK")
        assert check.passed is True
        assert check.status == "ok"

    def test_failed_check(self):
        check = GCPValidationCheck(name="project_accessible", passed=False, message="403 Forbidden")
        assert check.passed is False
        assert check.status == "failed"

    def test_skipped_check(self):
        check = GCPValidationCheck(
            name="storage_api_enabled",
            passed=False,
            message="Skipped",
            status="skipped",
        )
        assert check.status == "skipped"


class TestGCPValidationResult:
    def test_all_passed(self):
        checks = [GCPValidationCheck(name=f"check_{i}", passed=True, message="OK") for i in range(6)]
        result = GCPValidationResult(passed=True, checks=checks)
        assert result.passed is True
        assert len(result.checks) == 6

    def test_failed_result(self):
        checks = [
            GCPValidationCheck(name="credentials_loaded", passed=False, message="Bad JSON"),
            GCPValidationCheck(name="project_accessible", passed=False, message="Skipped", status="skipped"),
        ]
        result = GCPValidationResult(passed=False, checks=checks)
        assert result.passed is False
