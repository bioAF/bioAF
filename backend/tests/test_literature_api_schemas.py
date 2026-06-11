"""Locks the Literature API schema layering convention.

The Literature REST API (``app.api.literature``) historically declared all of
its Pydantic request/response models inline, in spite of the dedicated
``app.schemas`` layer that every other router uses. This test pins the schemas
to ``app.schemas.literature`` and asserts the router reuses those exact class
objects, so the inline-schema God-module pattern cannot silently return.

This is a pure-structure test: it asserts where the contract lives, not new
behavior. The behavioral guarantees stay in the other ``test_literature_*``
suites, which must remain green across the move.
"""

from __future__ import annotations

import app.api.literature as lit_api
import app.schemas.literature as lit_schemas

# Every Pydantic model the Literature API exposes. If a new request/response
# schema is added to the API, add it here and define it in app.schemas.literature.
SCHEMA_NAMES = [
    "AuthorPayload",
    "AssociationPayload",
    "PaperResponse",
    "RecommendationNotePayload",
    "PaperListResponse",
    "CreatePaperRequest",
    "UpdatePaperRequest",
    "CommentPayload",
    "CommentListResponse",
    "CreateCommentRequest",
    "UpdateCommentRequest",
    "ReadingStatusResponse",
    "ReadingStatusRequest",
    "DismissalRequest",
    "DismissalResponse",
    "AssociationCreateRequest",
    "CitationBulkRequest",
    "LitReviewSettingsPayload",
    "LitReviewSettingsUpdateRequest",
    "BulkAddToLibraryRequest",
    "BulkAddToLibraryResponse",
    "BulkDismissRequest",
    "BulkDismissResponse",
    "SourceConfigPayload",
    "SourceConfigListResponse",
    "SourceConfigUpdateRequest",
    "SourceTestResponse",
    "SearchSubmitRequest",
    "SearchPayload",
    "SearchListResponse",
    "LiteratureConfigPayload",
    "LiteratureConfigUpdateRequest",
    "LitReviewRunPayload",
    "LitReviewRunListResponse",
    "CreateLitReviewRunRequest",
    "RecommendationPayload",
    "RecommendationListResponse",
]


def test_all_schemas_live_in_schemas_layer():
    """Each API schema is defined in app.schemas.literature, not inline."""
    missing = [name for name in SCHEMA_NAMES if not hasattr(lit_schemas, name)]
    assert not missing, f"schemas missing from app.schemas.literature: {missing}"


def test_router_reuses_schema_objects():
    """The router references the schemas-layer classes, not inline redefinitions.

    Identity (``is``) is the lock: if someone re-declares a model inline in the
    API module, this fails because the two objects diverge.
    """
    for name in SCHEMA_NAMES:
        api_obj = getattr(lit_api, name, None)
        schema_obj = getattr(lit_schemas, name)
        assert api_obj is schema_obj, f"{name} in app.api.literature is not the app.schemas.literature class"


def test_api_module_declares_no_inline_basemodels():
    """No BaseModel subclass is defined in the API module's own source.

    Reads the source rather than the namespace so imported schemas (which are
    BaseModel subclasses) do not count as inline definitions.
    """
    import ast
    from pathlib import Path

    source = (Path(lit_api.__file__)).read_text()
    tree = ast.parse(source)
    inline = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "BaseModel" for base in node.bases)
    ]
    assert not inline, f"API module still defines schemas inline: {inline}"
