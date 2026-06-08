"""Domain exception hierarchy for bioAF services.

Services raise these typed, semantic exceptions instead of a bare
``ValueError``. A single FastAPI exception handler (registered in
``app.main``) maps each subclass to its HTTP status code and a structured
``{detail, code}`` envelope, so API routes no longer need repetitive
``except ValueError`` blocks.

Why a dedicated hierarchy (not ValueError subclasses):
- ``ValueError`` was the most-connected non-entity node in the codebase graph,
  acting as a bridge across nearly every service community. Collapsing every
  domain failure into one generic type erased intent and made debugging and
  observability worse.
- Each subclass declares its own ``status_code`` and machine-readable ``code``,
  so the HTTP mapping lives in one place and stays consistent across routes.

``ValueError`` is still correct inside Pydantic validators (the framework maps
it to a 422). Those are intentionally left alone; this hierarchy is for the
service and adapter layers.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all expected, caller-facing domain failures.

    The central handler reads ``status_code`` and ``code`` off the instance
    (which default to the class attributes) to build the HTTP response.
    """

    status_code: int = 400
    code: str = "domain_error"


class ValidationError(DomainError):
    """Input or value is invalid (a bad request the caller can fix)."""

    status_code = 400
    code = "validation_error"


class NotFoundError(DomainError):
    """A referenced entity does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """The request conflicts with current state (e.g. a duplicate)."""

    status_code = 409
    code = "conflict"


class StateError(DomainError):
    """The entity is not in a state that permits the requested action."""

    status_code = 409
    code = "invalid_state"


class PermissionDeniedError(DomainError):
    """The caller is authenticated but not allowed to perform the action."""

    status_code = 403
    code = "permission_denied"
