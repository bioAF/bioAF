"""Literature payload assembly for Agent Review (ADR-057).

Resolves the per-scope `agent_review_literature_config` toggles, gathers
associated papers + comments (and optional full text), orders them per
the ordering rule, applies the token budget, and renders the
`## Associated Literature` Markdown section.

The payload builder is pure aside from one DB pass: it returns the rendered
Markdown plus a structured summary (papers included, papers truncated,
toggles_applied) for the audit log."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.literature import (
    AgentReviewLiteratureConfig,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperComment,
    LiteraturePaperDismissal,
    PROVENANCE_LIT_REVIEW_RUN,
    PROVENANCE_SOURCE_SEARCH,
    PROVENANCE_USER_UPLOAD,
    SCOPE_EXPERIMENT,
    SCOPE_PROJECT,
)
from app.models.user import User


DEFAULT_MAX_TOKENS = 100_000


@dataclass
class LiteratureToggles:
    abstracts_enabled: bool = True
    comments_enabled: bool = True
    full_text_enabled: bool = False
    max_tokens: int = DEFAULT_MAX_TOKENS


@dataclass
class LiteraturePayloadResult:
    markdown: str
    included_paper_ids: list[int] = field(default_factory=list)
    truncated_paper_ids: list[int] = field(default_factory=list)
    toggles_applied: dict[str, bool | int] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """4-chars-per-token heuristic when no provider tokenizer is available
    (per ADR-053). Good enough for budget planning."""
    if not text:
        return 0
    return max(1, len(text) // 4)


async def resolve_toggles(
    session: AsyncSession,
    *,
    org_id: int,
    scope_type: str,
    scope_id: int,
) -> LiteratureToggles:
    """Resolution order per ADR-057: scope-specific row -> parent-project
    row (when reviewing an experiment) -> org-level row -> built-in defaults."""
    candidates: list[tuple[str, int | None]] = [(scope_type, scope_id)]
    if scope_type == SCOPE_EXPERIMENT:
        project_id = await _experiment_project_id(session, scope_id)
        if project_id is not None:
            candidates.append((SCOPE_PROJECT, project_id))
    candidates.append(("org", None))

    for stype, sid in candidates:
        query = select(AgentReviewLiteratureConfig).where(
            AgentReviewLiteratureConfig.organization_id == org_id,
            AgentReviewLiteratureConfig.scope_type == stype,
        )
        if sid is None:
            query = query.where(AgentReviewLiteratureConfig.scope_id.is_(None))
        else:
            query = query.where(AgentReviewLiteratureConfig.scope_id == sid)
        row = (await session.execute(query)).scalar_one_or_none()
        if row is not None:
            return LiteratureToggles(
                abstracts_enabled=row.abstracts_enabled,
                comments_enabled=row.comments_enabled,
                full_text_enabled=row.full_text_enabled,
                max_tokens=row.max_tokens,
            )
    return LiteratureToggles()


async def _experiment_project_id(session: AsyncSession, experiment_id: int) -> int | None:
    rs = await session.execute(select(Experiment.project_id).where(Experiment.id == experiment_id))
    return rs.scalar_one_or_none()


async def gather_papers_for_scope(
    session: AsyncSession,
    *,
    org_id: int,
    scope_type: str,
    scope_id: int,
    expand_to_project: bool = False,
) -> list[LiteraturePaper]:
    scopes: list[tuple[str, int]] = [(scope_type, scope_id)]
    if expand_to_project and scope_type == SCOPE_EXPERIMENT:
        project_id = await _experiment_project_id(session, scope_id)
        if project_id is not None:
            scopes.append((SCOPE_PROJECT, project_id))

    rows: list[LiteraturePaper] = []
    seen: set[int] = set()
    for stype, sid in scopes:
        rs = await session.execute(
            select(LiteraturePaper)
            .join(LiteratureAssociation, LiteratureAssociation.paper_id == LiteraturePaper.id)
            .where(
                LiteraturePaper.organization_id == org_id,
                LiteratureAssociation.scope_type == stype,
                LiteratureAssociation.scope_id == sid,
                LiteratureAssociation.removed_at.is_(None),
            )
        )
        for paper in rs.scalars().all():
            if paper.id in seen:
                continue
            seen.add(paper.id)
            rows.append(paper)

    # Drop dismissed papers.
    if not rows:
        return rows
    ids = [p.id for p in rows]
    rs = await session.execute(
        select(LiteraturePaperDismissal.paper_id).where(
            LiteraturePaperDismissal.paper_id.in_(ids),
            LiteraturePaperDismissal.reversed_at.is_(None),
        )
    )
    dismissed = {pid for (pid,) in rs.fetchall()}
    return [p for p in rows if p.id not in dismissed]


def _provenance_tier(paper: LiteraturePaper, comments_by_paper: dict[int, list]) -> int:
    has_comments = bool(comments_by_paper.get(paper.id))
    if paper.provenance == PROVENANCE_USER_UPLOAD:
        return 1 if has_comments else 2
    if paper.provenance == PROVENANCE_SOURCE_SEARCH:
        return 3
    if paper.provenance == PROVENANCE_LIT_REVIEW_RUN:
        return 4
    return 5


def _date_key(paper: LiteraturePaper) -> tuple:
    if paper.publication_date:
        return (paper.publication_date.year, paper.publication_date.month, paper.publication_date.day)
    return (0, 0, 0)


async def load_comments_for_papers(
    session: AsyncSession, paper_ids: Iterable[int]
) -> dict[int, list[tuple[LiteraturePaperComment, str]]]:
    """Return non-deleted comments per paper, with the author's display label."""
    paper_id_list = list(paper_ids)
    if not paper_id_list:
        return {}
    rs = await session.execute(
        select(LiteraturePaperComment, User.email)
        .join(User, User.id == LiteraturePaperComment.user_id)
        .where(
            LiteraturePaperComment.paper_id.in_(paper_id_list),
            LiteraturePaperComment.deleted_at.is_(None),
        )
        .order_by(LiteraturePaperComment.paper_id, LiteraturePaperComment.created_at)
    )
    result: dict[int, list[tuple[LiteraturePaperComment, str]]] = {}
    for comment, email in rs.all():
        result.setdefault(comment.paper_id, []).append((comment, email or f"user {comment.user_id}"))
    return result


async def _load_full_text(session: AsyncSession, paper: LiteraturePaper) -> str | None:
    """Best-effort fetch of full text from the literature bucket. Returns None
    when the bucket is unconfigured or the blob cannot be read."""
    if not paper.has_full_text or not paper.extracted_text_uri:
        return None
    import asyncio

    from app.services.gcs_storage import GcsStorageService

    try:
        credentials = await GcsStorageService.get_credentials(session)
        from google.cloud import storage as gcs

        loop = asyncio.get_running_loop()

        def _download() -> str:
            client = gcs.Client(credentials=credentials)
            parts = paper.extracted_text_uri.replace("gs://", "").split("/", 1)
            blob = client.bucket(parts[0]).blob(parts[1])
            return blob.download_as_text()

        return await loop.run_in_executor(None, _download)
    except Exception:
        return None


def _render_paper(
    *,
    index: int,
    paper: LiteraturePaper,
    comments: list[tuple[LiteraturePaperComment, str]] | None,
    full_text: str | None,
    toggles: LiteratureToggles,
) -> str:
    lines = [f"### Paper {index}"]
    if toggles.abstracts_enabled:
        lines.append(f"Title: {paper.title}")
        if paper.authors_json:
            authors_str = ", ".join(
                f"{a.get('given', '').strip()} {a.get('family', '').strip()}".strip()
                for a in paper.authors_json
                if a.get("family") or a.get("given")
            )
            if authors_str:
                lines.append(f"Authors: {authors_str}")
        if paper.publication_date:
            lines.append(f"Year: {paper.publication_date.year}")
        if paper.journal:
            lines.append(f"Journal: {paper.journal}")
        if paper.doi:
            lines.append(f"DOI: {paper.doi}")
        if paper.abstract:
            lines.append("")
            lines.append("Abstract:")
            lines.append(paper.abstract)
    if toggles.comments_enabled and comments:
        lines.append("")
        lines.append("Team comments:")
        for comment, user_label in comments:
            date_str = comment.created_at.date().isoformat() if comment.created_at else "n.d."
            lines.append(f"- Comment by {user_label} on {date_str}: {comment.body}")
    if toggles.full_text_enabled and full_text:
        lines.append("")
        lines.append('Full text (page markers shown as "[Page N]"; cite the page when referencing this paper):')
        lines.append(full_text)
    return "\n".join(lines)


async def build_literature_payload(
    session: AsyncSession,
    *,
    org_id: int,
    scope_type: str,
    scope_id: int,
    expand_to_project: bool = False,
) -> LiteraturePayloadResult:
    toggles = await resolve_toggles(session, org_id=org_id, scope_type=scope_type, scope_id=scope_id)
    toggles_applied = {
        "abstracts_enabled": toggles.abstracts_enabled,
        "comments_enabled": toggles.comments_enabled,
        "full_text_enabled": toggles.full_text_enabled,
        "max_tokens": toggles.max_tokens,
        "expand_to_project": expand_to_project,
    }
    if not (toggles.abstracts_enabled or toggles.comments_enabled or toggles.full_text_enabled):
        return LiteraturePayloadResult(markdown="", toggles_applied=toggles_applied)

    papers = await gather_papers_for_scope(
        session,
        org_id=org_id,
        scope_type=scope_type,
        scope_id=scope_id,
        expand_to_project=expand_to_project,
    )
    if not papers:
        return LiteraturePayloadResult(markdown="", toggles_applied=toggles_applied)

    comments_by_paper = (
        await load_comments_for_papers(session, [p.id for p in papers]) if toggles.comments_enabled else {}
    )
    full_text_by_paper: dict[int, str] = {}
    if toggles.full_text_enabled:
        for paper in papers:
            text = await _load_full_text(session, paper)
            if text:
                full_text_by_paper[paper.id] = text

    # Sort by ordering rule.
    papers_sorted = sorted(
        papers,
        key=lambda p: (
            _provenance_tier(p, comments_by_paper),
            -(_date_key(p)[0] * 372 + _date_key(p)[1] * 31 + _date_key(p)[2]),
            -p.id,
        ),
    )

    header = (
        "## Associated Literature\n\n"
        "The following papers are associated with this experiment. Check the run's\n"
        "results against this prior work and flag any result that is unexpected or\n"
        "contradicts it. When you cite a paper, name it by title or DOI; when full\n"
        'text with page markers ("[Page N]") is shown, cite the specific page.\n'
    )

    sections: list[str] = []
    included: list[int] = []
    truncated: list[int] = []
    running = estimate_tokens(header)

    for i, paper in enumerate(papers_sorted, start=1):
        rendered = _render_paper(
            index=i,
            paper=paper,
            comments=comments_by_paper.get(paper.id),
            full_text=full_text_by_paper.get(paper.id),
            toggles=toggles,
        )
        cost = estimate_tokens(rendered) + 4
        if running + cost > toggles.max_tokens and sections:
            truncated.extend([p.id for p in papers_sorted[i - 1 :]])
            break
        sections.append(rendered)
        included.append(paper.id)
        running += cost

    if not sections:
        return LiteraturePayloadResult(markdown="", toggles_applied=toggles_applied)

    body = "\n\n".join(sections)
    if truncated:
        warning = f"\n\n_{len(truncated)} papers truncated from Literature payload due to token budget._"
        body = body + warning

    return LiteraturePayloadResult(
        markdown=header + "\n" + body + "\n",
        included_paper_ids=included,
        truncated_paper_ids=truncated,
        toggles_applied=toggles_applied,
    )


# ---------------------------------------------------------------------------
# DOI link post-processor
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


async def rewrite_dois_to_library_links(
    session: AsyncSession,
    *,
    org_id: int,
    text: str,
) -> str:
    """Find DOIs in the text and rewrite each one into a Markdown link.

    DOIs that resolve to a local Paper link to the in-app paper detail page;
    those that do not, link out to https://doi.org/{doi}."""
    if not text:
        return text
    dois_found: list[str] = []
    for m in _DOI_RE.finditer(text):
        dois_found.append(m.group(0).strip(".,;:)").lower())
    if not dois_found:
        return text

    unique = list({d for d in dois_found})
    rs = await session.execute(
        select(LiteraturePaper.id, LiteraturePaper.doi).where(
            LiteraturePaper.organization_id == org_id,
            LiteraturePaper.doi.in_(unique),
        )
    )
    by_doi: dict[str, int] = {doi: pid for pid, doi in rs.fetchall() if doi}

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        stripped = raw.strip(".,;:)").lower()
        paper_id = by_doi.get(stripped)
        if paper_id is not None:
            return f"[{stripped}](/data/literature/papers/{paper_id})"
        return f"[{stripped}](https://doi.org/{stripped})"

    return _DOI_RE.sub(_replace, text)
