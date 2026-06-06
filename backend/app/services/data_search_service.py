"""Unified document/file search (LK-SPEC-D, F-LKD-04).

A single capability that returns a normalized union of Data & Files ``File`` rows
and Lab Knowledge ``LabDocument`` rows for a free-text query. Built once and
consumed twice: the Data & Files browser (D3) surfaces lab documents alongside
files, and the glossary scan document picker (D2) lets a user pick either store
by searching instead of typing a database id.

A lab document is still a file-like thing, so a search in Data & Files should find
it too. The match rules mirror the global search precedent in ``search_service``:
files match on ``filename``; lab documents match on title/description and exclude
archived rows. Org-scoping and permission gating are the caller's contract: the
caller passes ``include_files`` / ``include_lab_documents`` reflecting the viewer's
``files:view`` / ``lab_documents:view`` permissions.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.lab_document import LabDocument

# Per-store fetch cap and overall merged cap. A picker/list never needs more than
# this many candidates for a single query.
_PER_STORE_LIMIT = 100
_DEFAULT_LIMIT = 50


def _file_item(f: File) -> dict:
    return {
        "kind": "file",
        "id": f.id,
        "name": f.filename,
        "file_type": f.file_type,
        "size_bytes": f.size_bytes,
        # ``File`` has no updated_at; created_at is its only timeline anchor.
        "updated_at": f.created_at,
        "href": f"/data/files?file={f.id}",
        "experiment_id": f.experiment_id,
        "source": f.source_type,
    }


def _lab_document_item(d: LabDocument) -> dict:
    return {
        "kind": "lab_document",
        "id": d.id,
        "name": d.title,
        "file_type": d.mime_type,
        "size_bytes": d.file_size_bytes,
        "updated_at": d.updated_at,
        "href": f"/lab-knowledge/documents/{d.id}",
        "experiment_id": None,  # lab documents carry no experiment provenance
        "source": "lab_knowledge",
    }


async def unified_document_file_search(
    session: AsyncSession,
    *,
    org_id: int,
    query: str,
    include_files: bool,
    include_lab_documents: bool,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """Return a normalized union of files and lab documents matching ``query``.

    Results are org-scoped, exclude archived lab documents, and are ordered most
    recent first. ``include_files`` / ``include_lab_documents`` gate each store so
    a caller without one permission silently gets the other's results only."""
    q = (query or "").strip()
    if not q or (not include_files and not include_lab_documents):
        return []
    pattern = f"%{q}%"
    items: list[dict] = []

    if include_files:
        rows = (
            (
                await session.execute(
                    select(File)
                    .where(File.organization_id == org_id, File.filename.ilike(pattern))
                    .order_by(File.created_at.desc(), File.id.desc())
                    .limit(_PER_STORE_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        items.extend(_file_item(f) for f in rows)

    if include_lab_documents:
        rows = (
            (
                await session.execute(
                    select(LabDocument)
                    .where(
                        LabDocument.organization_id == org_id,
                        LabDocument.is_archived.is_(False),
                        or_(LabDocument.title.ilike(pattern), LabDocument.description.ilike(pattern)),
                    )
                    .order_by(LabDocument.updated_at.desc(), LabDocument.id.desc())
                    .limit(_PER_STORE_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        items.extend(_lab_document_item(d) for d in rows)

    items.sort(key=lambda i: i["updated_at"], reverse=True)
    return items[:limit]
