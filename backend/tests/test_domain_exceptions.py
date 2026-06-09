"""Tests for the domain exception hierarchy (app/exceptions.py).

These are the typed, semantic exceptions services raise instead of bare
ValueError. A single FastAPI handler maps each to its HTTP status + envelope,
so routes no longer need per-call ``except ValueError`` blocks.
"""

import pytest

from app.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    StateError,
    ValidationError,
)


class TestHierarchy:
    def test_all_domain_errors_subclass_domain_error(self):
        for cls in (ValidationError, NotFoundError, ConflictError, StateError, PermissionDeniedError):
            assert issubclass(cls, DomainError)

    def test_domain_error_does_not_subclass_value_error(self):
        # The clean-break design: domain errors are their own hierarchy, not
        # ValueError. The central handler (not per-route except ValueError)
        # owns the HTTP mapping.
        assert not issubclass(DomainError, ValueError)

    def test_domain_error_is_exception(self):
        assert issubclass(DomainError, Exception)


class TestStatusCodes:
    @pytest.mark.parametrize(
        ("cls", "expected_status"),
        [
            (ValidationError, 400),
            (NotFoundError, 404),
            (ConflictError, 409),
            (StateError, 409),
            (PermissionDeniedError, 403),
        ],
    )
    def test_status_code(self, cls, expected_status):
        assert cls.status_code == expected_status

    def test_base_default_status_is_400(self):
        assert DomainError.status_code == 400


class TestCodes:
    @pytest.mark.parametrize(
        ("cls", "expected_code"),
        [
            (DomainError, "domain_error"),
            (ValidationError, "validation_error"),
            (NotFoundError, "not_found"),
            (ConflictError, "conflict"),
            (StateError, "invalid_state"),
            (PermissionDeniedError, "permission_denied"),
        ],
    )
    def test_machine_readable_code(self, cls, expected_code):
        assert cls.code == expected_code


class TestMessage:
    def test_message_is_preserved(self):
        err = NotFoundError("pipeline 7 not found")
        assert str(err) == "pipeline 7 not found"

    def test_can_raise_and_catch_as_domain_error(self):
        with pytest.raises(DomainError):
            raise ConflictError("already exists")

    def test_instance_carries_class_status_and_code(self):
        err = ConflictError("dup")
        assert err.status_code == 409
        assert err.code == "conflict"
