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

    ``details`` is an optional structured payload merged into the JSON envelope
    so a client can act on the error programmatically (e.g. the list of samples
    that blocked a pipeline launch). It defaults to empty for the common case of
    ``raise SomeError("message")``.
    """

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details: dict = details or {}


class ValidationError(DomainError):
    """Input or value is invalid (a bad request the caller can fix)."""

    status_code = 400
    code = "validation_error"


class SamplesMissingFilesError(ValidationError):
    """A FASTQ-consuming pipeline was launched with sample(s) that have no
    linked input files.

    Carries ``details["samples_without_files"]`` so the caller can offer to drop
    the offending samples and retry with ``drop_samples_without_files=True``.
    """

    code = "samples_missing_files"


class PipelineNotSampleLaunchableError(ValidationError):
    """The pipeline does not consume per-sample sequencing reads, so it cannot be
    launched from bioAF samples at all.

    Its own ``assets/schema_input.json`` declares no FASTQ column: it wants
    assemblies, variant sets, alignments, images or spectra. Emitting a
    ``sample,fastq_1,fastq_2`` sheet for it produces a run that dies inside
    Nextflow minutes after launch, so the launch is refused instead.

    Carries ``details["required_inputs"]`` (what the pipeline actually asks for)
    so the message can tell the user why, not just that.
    """

    code = "pipeline_not_sample_launchable"


class SamplesMissingRequiredFieldsError(ValidationError):
    """A column the pipeline requires has no value bioAF can supply.

    Either the sourcing field is empty on some samples (sarek's ``patient``
    comes from ``Sample.donor_source``), or the column defines experimental
    design and is deliberately never guessed (mag's ``group`` controls
    co-assembly).

    Carries ``details["missing_columns"]``, keyed by column, each with the
    ``sample_field`` that would supply it (``None`` when nothing would), any
    ``allowed_values`` from the schema's enum, and the ids and external ids of
    the ``samples`` actually missing it, so the UI can link straight to them.
    """

    code = "samples_missing_required_fields"


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


class StorageUnavailableError(DomainError):
    """The object store could not carry out an operation the caller depends on.

    Raised where failing quietly would leave the catalogue asserting something
    about the bytes that is not true: deleting a file, for instance, must not
    retire the record while the object survives in the bucket.
    """

    status_code = 502
    code = "storage_unavailable"
