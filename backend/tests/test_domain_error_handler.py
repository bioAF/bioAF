"""Tests for the central DomainError -> HTTP response handler.

A single handler maps every DomainError subclass to its status code and a
``{detail, code}`` envelope, so routes can let domain errors propagate instead
of catching ValueError and re-raising HTTPException.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.error_handlers import register_error_handlers
from app.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    SamplesMissingFilesError,
    StateError,
    ValidationError,
)


def _client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/validation")
    def _validation():
        raise ValidationError("bad input")

    @app.get("/notfound")
    def _notfound():
        raise NotFoundError("pipeline 7 not found")

    @app.get("/conflict")
    def _conflict():
        raise ConflictError("already exists")

    @app.get("/state")
    def _state():
        raise StateError("not running")

    @app.get("/forbidden")
    def _forbidden():
        raise PermissionDeniedError("nope")

    @app.get("/missingfiles")
    def _missingfiles():
        raise SamplesMissingFilesError(
            "Some selected samples have no linked input files",
            details={"samples_without_files": [{"id": 2, "external_id": "SAMPLE-102"}]},
        )

    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_maps_to_400():
    r = _client().get("/validation")
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "bad input"
    assert body["code"] == "validation_error"


def test_not_found_maps_to_404():
    r = _client().get("/notfound")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert r.json()["detail"] == "pipeline 7 not found"


def test_conflict_maps_to_409():
    r = _client().get("/conflict")
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


def test_state_error_maps_to_409():
    r = _client().get("/state")
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"


def test_permission_denied_maps_to_403():
    r = _client().get("/forbidden")
    assert r.status_code == 403
    assert r.json()["code"] == "permission_denied"


def test_samples_missing_files_carries_structured_details():
    r = _client().get("/missingfiles")
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "samples_missing_files"
    # the structured payload the frontend reads to offer "drop and proceed"
    offending = body["details"]["samples_without_files"]
    assert offending == [{"id": 2, "external_id": "SAMPLE-102"}]


def test_plain_domain_error_omits_details_key():
    """Errors raised without details must not grow an empty details key."""
    r = _client().get("/validation")
    assert "details" not in r.json()
