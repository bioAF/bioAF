"""Which candidate files a GEO export references, decided by Sample links (``sample_files``) alone.

Candidates are the export's existing scope (the Organization's files stored under the Experiment's
``/experiments/{id}/`` prefix); links only filter that set, they never widen it.

- Linked to a selected Sample: referenced once export-wide, and by each selected Sample linked to it.
- Linked, but to no selected Sample (excluded Samples, or another Experiment's): omitted.
- No links anywhere: an Experiment-level asset, referenced export-wide and by no Sample.

Shared and unlinked files are referenced whole; their contents are never inspected. Filename resemblance
is never an ownership rule.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.sample import sample_files


@dataclass
class FileSelection:
    files: list[File]
    """Export-wide references, each once, in candidate order."""
    per_sample: dict[int, list[File]] = field(default_factory=dict)
    """Selected Sample id -> the files linked to it."""
    unlinked: list[File] = field(default_factory=list)
    """Candidates with no Sample links anywhere (Experiment-level assets)."""


async def load_links(session: AsyncSession, file_ids: list[int]) -> dict[int, set[int]]:
    """Every Sample each file is linked to, in one query."""
    links: dict[int, set[int]] = defaultdict(set)
    if file_ids:
        rows = await session.execute(
            select(sample_files.c.file_id, sample_files.c.sample_id).where(sample_files.c.file_id.in_(file_ids))
        )
        for file_id, sample_id in rows:
            links[file_id].add(sample_id)
    return links


def select_files(candidates: list[File], links: dict[int, set[int]], selected_sample_ids: set[int]) -> FileSelection:
    selection = FileSelection(files=[], per_sample={sid: [] for sid in selected_sample_ids})
    for f in candidates:
        linked = links.get(f.id, set())
        if not linked:
            selection.files.append(f)
            selection.unlinked.append(f)
            continue
        owners = linked & selected_sample_ids
        if owners:
            selection.files.append(f)
            for sid in owners:
                selection.per_sample[sid].append(f)
    return selection
