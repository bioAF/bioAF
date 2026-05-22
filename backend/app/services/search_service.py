import logging
from datetime import datetime
from urllib.parse import quote

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.experiment import Experiment
from app.models.file import File
from app.models.literature import LiteraturePaper
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.sample import Sample, sample_files

logger = logging.getLogger("bioaf.search_service")

# Entity types the full search page covers, in stable order. The endpoint passes
# the permission-filtered subset; this list is the universe.
FULL_SEARCH_TYPES = [
    "experiment",
    "sample",
    "pipeline_run",
    "file",
    "project",
    "pipeline_definition",
    "literature_paper",
]

# Per-type fetch cap and overall merged cap. Both 300 so a single selected type can
# still be paged to the overall cap (decision: capped per-type, total limited to 300).
_PER_TYPE_FETCH = 300
_MAX_RESULTS = 300


def _ts(dt: datetime | None) -> float:
    return dt.timestamp() if dt else 0.0


def _join(parts: list[str | None]) -> str | None:
    """Join present context pieces with a middle dot, or None if nothing."""
    kept = [p for p in parts if p]
    return " · ".join(kept) if kept else None


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


class SearchService:
    @staticmethod
    async def quick_search(
        session: AsyncSession,
        org_id: int,
        query: str,
        limit_per_type: int = 5,
    ) -> list[dict]:
        """Name-only "jump to" search for the header.

        Matches by display name (case-insensitive substring) across experiments,
        samples, pipeline runs, and files, scoped to the org. Returns at most
        ``limit_per_type`` hits per entity type. Unlike the unified full-text
        search, this only looks at each entity's name, never its other fields.
        """
        q = query.strip()
        if not q:
            return []
        pattern = f"%{q}%"
        results: list[dict] = []

        exp_rows = await session.execute(
            select(Experiment)
            .where(Experiment.organization_id == org_id, Experiment.name.ilike(pattern))
            .order_by(Experiment.name)
            .limit(limit_per_type)
        )
        for e in exp_rows.scalars():
            results.append({"entity_type": "experiment", "entity_id": e.id, "name": e.name, "experiment_id": e.id})

        sample_rows = await session.execute(
            select(Sample)
            .where(
                Sample.experiment_id.in_(select(Experiment.id).where(Experiment.organization_id == org_id)),
                Sample.external_id.ilike(pattern),
            )
            .order_by(Sample.external_id)
            .limit(limit_per_type)
        )
        for s in sample_rows.scalars():
            results.append(
                {
                    "entity_type": "sample",
                    "entity_id": s.id,
                    "name": s.external_id or f"Sample {s.id}",
                    "experiment_id": s.experiment_id,
                }
            )

        run_rows = await session.execute(
            select(PipelineRun)
            .where(PipelineRun.organization_id == org_id, PipelineRun.pipeline_name.ilike(pattern))
            .order_by(PipelineRun.id.desc())
            .limit(limit_per_type)
        )
        for r in run_rows.scalars():
            results.append(
                {
                    "entity_type": "pipeline_run",
                    "entity_id": r.id,
                    "name": f"{r.pipeline_name} (Run #{r.id})",
                    "experiment_id": r.experiment_id,
                }
            )

        file_rows = await session.execute(
            select(File)
            .where(File.organization_id == org_id, File.filename.ilike(pattern))
            .order_by(File.filename)
            .limit(limit_per_type)
        )
        for f in file_rows.scalars():
            results.append(
                {
                    "entity_type": "file",
                    "entity_id": f.id,
                    "name": f.filename,
                    "experiment_id": f.experiment_id,
                }
            )

        return results

    @staticmethod
    async def full_search(
        session: AsyncSession,
        org_id: int,
        query: str,
        entity_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 25,
        count_types: list[str] | None = None,
    ) -> tuple[list[dict], int, dict[str, int]]:
        """Full search for the dedicated results page.

        Substring (ILIKE) match over each type's name AND content fields. Results
        cover the types in ``entity_types`` (default: all). Per-type ``type_counts``
        cover ``count_types`` (default: the result types) so the type filter can show
        a count for every type the user may view even while results are narrowed to
        one. The caller restricts both lists to types the user may view.

        Results are a single ranked list ordered by match quality (exact name >
        prefix > substring > content-only) then recency. Up to ``_PER_TYPE_FETCH``
        rows per type are fetched and merged; the merged list is capped at
        ``_MAX_RESULTS`` and then paginated.

        Returns ``(results, total, type_counts)`` where ``total`` is the capped
        merged count (so it never exceeds ``_MAX_RESULTS``) and ``type_counts`` are
        the accurate per-type match counts (which may exceed the cap).
        """
        q = (query or "").strip()
        if not q:
            return [], 0, {}

        result_types = [t for t in (entity_types or FULL_SEARCH_TYPES) if t in FULL_SEARCH_TYPES]
        cnt_types = [t for t in (count_types or result_types) if t in FULL_SEARCH_TYPES]
        pattern = f"%{q}%"

        counts: dict[str, int] = {}
        for t in cnt_types:
            model, where = SearchService._type_where(t, org_id, pattern)
            counts[t] = await SearchService._count(session, model, where)

        builders = {
            "experiment": SearchService._experiment_hits,
            "sample": SearchService._sample_hits,
            "pipeline_run": SearchService._pipeline_run_hits,
            "file": SearchService._file_hits,
            "project": SearchService._project_hits,
            "pipeline_definition": SearchService._pipeline_definition_hits,
            "literature_paper": SearchService._literature_hits,
        }
        raw: list[dict] = []
        for t in result_types:
            raw.extend(await builders[t](session, org_id, pattern))

        q_lower = q.lower()

        def sort_key(h: dict) -> tuple[int, float]:
            name = (h["_match_name"] or "").lower()
            if name == q_lower:
                tier = 0
            elif name.startswith(q_lower):
                tier = 1
            elif q_lower in name:
                tier = 2
            else:
                tier = 3
            return (tier, -h["_recency"])

        raw.sort(key=sort_key)
        merged = raw[:_MAX_RESULTS]
        total = len(merged)

        start = (max(page, 1) - 1) * page_size
        page_hits = merged[start : start + page_size]
        # Context lines are looked up from related rows, so enrich only the page we
        # return (not the full merged set).
        await SearchService._enrich_snippets(session, page_hits)
        results = [{k: v for k, v in h.items() if not k.startswith("_")} for h in page_hits]
        return results, total, counts

    # --- shared WHERE so results and counts for a type never diverge -----------

    @staticmethod
    def _type_where(t: str, org_id: int, pattern: str):
        if t == "experiment":
            return Experiment, and_(
                Experiment.organization_id == org_id,
                or_(
                    Experiment.name.ilike(pattern),
                    Experiment.description.ilike(pattern),
                    Experiment.hypothesis.ilike(pattern),
                ),
            )
        if t == "sample":
            org_exps = select(Experiment.id).where(Experiment.organization_id == org_id)
            return Sample, and_(
                Sample.experiment_id.in_(org_exps),
                or_(
                    Sample.external_id.ilike(pattern),
                    Sample.organism.ilike(pattern),
                    Sample.tissue_type.ilike(pattern),
                ),
            )
        if t == "pipeline_run":
            return PipelineRun, and_(PipelineRun.organization_id == org_id, PipelineRun.pipeline_name.ilike(pattern))
        if t == "file":
            return File, and_(File.organization_id == org_id, File.filename.ilike(pattern))
        if t == "project":
            return Project, and_(
                Project.organization_id == org_id,
                or_(
                    Project.name.ilike(pattern),
                    Project.description.ilike(pattern),
                    Project.hypothesis.ilike(pattern),
                ),
            )
        if t == "pipeline_definition":
            return PipelineCatalogEntry, and_(
                PipelineCatalogEntry.organization_id == org_id,
                PipelineCatalogEntry.enabled.is_(True),
                or_(
                    PipelineCatalogEntry.name.ilike(pattern),
                    PipelineCatalogEntry.pipeline_key.ilike(pattern),
                    PipelineCatalogEntry.description.ilike(pattern),
                ),
            )
        if t == "literature_paper":
            return LiteraturePaper, and_(
                LiteraturePaper.organization_id == org_id,
                LiteraturePaper.in_library.is_(True),
                or_(
                    LiteraturePaper.title.ilike(pattern),
                    LiteraturePaper.journal.ilike(pattern),
                    LiteraturePaper.abstract.ilike(pattern),
                    cast(LiteraturePaper.authors_json, Text).ilike(pattern),
                ),
            )
        raise ValueError(f"unknown search type: {t}")

    @staticmethod
    async def _count(session: AsyncSession, model, where) -> int:
        return int(await session.scalar(select(func.count()).select_from(model).where(where)) or 0)

    # --- per-type builders: each returns the raw hits (with private fields) -----

    @staticmethod
    async def _experiment_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("experiment", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "entity_type": "experiment",
                "entity_id": e.id,
                "title": e.name,
                # Filled in during enrichment: project, sample/file/run counts, activity.
                "snippet": None,
                "url": f"/experiments/{e.id}",
                "experiment_id": e.id,
                "relevance_score": None,
                "_match_name": e.name,
                "_recency": _ts(e.created_at),
                "_project_id": e.project_id,
                "_updated": e.updated_at,
            }
            for e in rows
        ]

    @staticmethod
    async def _sample_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("sample", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "entity_type": "sample",
                "entity_id": s.id,
                "title": s.external_id or f"Sample {s.id}",
                "snippet": None,
                "url": f"/experiments/{s.experiment_id}?tab=samples",
                "experiment_id": s.experiment_id,
                "relevance_score": None,
                "_match_name": s.external_id or f"Sample {s.id}",
                "_recency": _ts(s.created_at),
                "_exp_id": s.experiment_id,
                "_organism": s.organism,
                "_tissue": s.tissue_type,
            }
            for s in rows
        ]

    @staticmethod
    async def _pipeline_run_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("pipeline_run", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "entity_type": "pipeline_run",
                "entity_id": r.id,
                "title": f"{r.pipeline_name} (Run #{r.id})",
                "snippet": None,
                "url": f"/pipelines/runs/{r.id}",
                "experiment_id": r.experiment_id,
                "relevance_score": None,
                "_match_name": r.pipeline_name,
                "_recency": _ts(r.created_at),
                "_exp_id": r.experiment_id,
                "_status": r.status,
            }
            for r in rows
        ]

    @staticmethod
    async def _file_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("file", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "entity_type": "file",
                "entity_id": f.id,
                "title": f.filename,
                "snippet": None,
                "url": f"/data/files?file={f.id}",
                "experiment_id": f.experiment_id,
                "relevance_score": None,
                "_match_name": f.filename,
                "_recency": _ts(f.created_at),
                "_exp_id": f.experiment_id,
                "_project_id": f.project_id,
                "_run_id": f.source_pipeline_run_id,
                "_file_id": f.id,
                "_file_type": f.file_type,
            }
            for f in rows
        ]

    @staticmethod
    async def _project_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("project", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "entity_type": "project",
                "entity_id": p.id,
                "title": p.name,
                "snippet": _join([p.status, p.code]),
                "url": f"/projects/{p.id}",
                "experiment_id": None,
                "relevance_score": None,
                "_match_name": p.name,
                "_recency": _ts(p.created_at),
            }
            for p in rows
        ]

    @staticmethod
    async def _pipeline_definition_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("pipeline_definition", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        hits = []
        for c in rows:
            if c.source_type == "custom" and c.custom_pipeline_id is not None:
                url = f"/pipelines/custom/{c.custom_pipeline_id}"
            else:
                url = f"/pipelines/launch/{quote(c.pipeline_key, safe='')}"
            kind = "Built-in" if c.is_builtin else "Custom"
            desc = (c.description or "").strip()
            hits.append(
                {
                    "entity_type": "pipeline_definition",
                    "entity_id": c.id,
                    "title": c.name,
                    "snippet": _join([kind, desc[:120] or None]),
                    "url": url,
                    "experiment_id": None,
                    "relevance_score": None,
                    "_match_name": c.name,
                    "_recency": _ts(c.created_at),
                }
            )
        return hits

    @staticmethod
    async def _literature_hits(session: AsyncSession, org_id: int, pattern: str) -> list[dict]:
        model, where = SearchService._type_where("literature_paper", org_id, pattern)
        rows = (
            (
                await session.execute(
                    select(model).where(where).order_by(model.created_at.desc(), model.id.desc()).limit(_PER_TYPE_FETCH)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "entity_type": "literature_paper",
                "entity_id": paper.id,
                "title": paper.title,
                "snippet": SearchService._paper_snippet(paper),
                "url": f"/data/literature/papers/{paper.id}",
                "experiment_id": None,
                "relevance_score": None,
                "_match_name": paper.title,
                "_recency": _ts(paper.created_at),
            }
            for paper in rows
        ]

    @staticmethod
    def _paper_snippet(paper: LiteraturePaper) -> str | None:
        authors = paper.authors_json or []
        author_str = None
        if authors:
            first = authors[0] if isinstance(authors[0], dict) else {}
            fam = first.get("family") or first.get("given") or ""
            author_str = (fam + (" et al." if len(authors) > 1 else "")) or None
        year = paper.publication_date.year if paper.publication_date else None
        return _join([author_str, paper.journal, str(year) if year else None])

    @staticmethod
    async def _name_map(session: AsyncSession, id_col, name_col, ids: set[int]) -> dict[int, str]:
        if not ids:
            return {}
        rows = (await session.execute(select(id_col, name_col).where(id_col.in_(ids)))).all()
        return {r[0]: r[1] for r in rows}

    @staticmethod
    async def _count_and_latest(
        session: AsyncSession, group_col, time_col, ids: set[int]
    ) -> dict[int, tuple[int, datetime | None]]:
        """Per-group ``(count, max(time_col))`` for the rows whose ``group_col`` is in
        ``ids``. One grouped query feeds both the experiment counts and last-activity."""
        if not ids:
            return {}
        rows = (
            await session.execute(
                select(group_col, func.count(), func.max(time_col)).where(group_col.in_(ids)).group_by(group_col)
            )
        ).all()
        return {r[0]: (r[1], r[2]) for r in rows}

    @staticmethod
    async def _enrich_snippets(session: AsyncSession, hits: list[dict]) -> None:
        """Fill the context line for experiment / sample / pipeline_run / file hits,
        which need names and roll-ups looked up from related rows. Batched to avoid
        per-hit queries; called only on the page of hits being returned."""
        exp_ids = {h["_exp_id"] for h in hits if h.get("_exp_id")}
        proj_ids = {h["_project_id"] for h in hits if h.get("_project_id")}
        run_ids = {h["_run_id"] for h in hits if h.get("_run_id")}
        file_ids = {h["_file_id"] for h in hits if h.get("_file_id")}
        exp_hit_ids = {h["entity_id"] for h in hits if h["entity_type"] == "experiment"}

        exp_names = await SearchService._name_map(session, Experiment.id, Experiment.name, exp_ids)
        proj_names = await SearchService._name_map(session, Project.id, Project.name, proj_ids)
        run_names = await SearchService._name_map(session, PipelineRun.id, PipelineRun.pipeline_name, run_ids)

        # Roll-ups for experiment cards: counts and last activity per experiment.
        sample_stats = await SearchService._count_and_latest(
            session, Sample.experiment_id, Sample.updated_at, exp_hit_ids
        )
        file_stats = await SearchService._count_and_latest(session, File.experiment_id, File.created_at, exp_hit_ids)
        run_stats = await SearchService._count_and_latest(
            session, PipelineRun.experiment_id, PipelineRun.created_at, exp_hit_ids
        )

        file_samples: dict[int, list[str]] = {}
        if file_ids:
            q = (
                select(sample_files.c.file_id, Sample.external_id)
                .select_from(sample_files)
                .join(Sample, Sample.id == sample_files.c.sample_id)
                .where(sample_files.c.file_id.in_(file_ids))
            )
            for fid, ext in (await session.execute(q)).all():
                file_samples.setdefault(fid, []).append(ext or "Sample")

        for h in hits:
            t = h["entity_type"]
            if t == "experiment":
                eid = h["entity_id"]
                s_count, s_latest = sample_stats.get(eid, (0, None))
                f_count, f_latest = file_stats.get(eid, (0, None))
                r_count, r_latest = run_stats.get(eid, (0, None))
                latest = max([d for d in (h.get("_updated"), s_latest, f_latest, r_latest) if d], default=None)
                h["snippet"] = _join(
                    [
                        proj_names.get(h.get("_project_id")),
                        _plural(s_count, "sample"),
                        _plural(f_count, "file"),
                        _plural(r_count, "run"),
                        f"Updated {latest.strftime('%b %d, %Y')}" if latest else None,
                    ]
                )
            elif t == "sample":
                h["snippet"] = _join([exp_names.get(h.get("_exp_id")), h.get("_organism"), h.get("_tissue")])
            elif t == "pipeline_run":
                h["snippet"] = _join([h.get("_status"), exp_names.get(h.get("_exp_id"))])
            elif t == "file":
                run_name = run_names.get(h.get("_run_id"))
                labels = file_samples.get(h.get("_file_id"))
                h["snippet"] = _join(
                    [
                        h.get("_file_type"),
                        f"from {run_name}" if run_name else None,
                        ", ".join(labels) if labels else None,
                        exp_names.get(h.get("_exp_id")),
                        proj_names.get(h.get("_project_id")),
                    ]
                )

    @staticmethod
    async def _is_meilisearch_enabled(session: AsyncSession) -> bool:
        """Check if Meilisearch component is enabled."""
        from app.models.component import ComponentState

        result = await session.execute(
            select(ComponentState.enabled).where(ComponentState.component_key == "meilisearch")
        )
        enabled = result.scalar_one_or_none()
        return bool(enabled)

    @staticmethod
    async def reindex_all(session: AsyncSession, org_id: int) -> dict:
        """Reindex all entities for an organization."""
        if not await SearchService._is_meilisearch_enabled(session):
            return {"status": "skipped", "reason": "Meilisearch not enabled"}

        try:
            import meilisearch

            client = meilisearch.Client("http://meilisearch:7700")
            indexed = {"experiments": 0, "samples": 0, "files": 0, "documents": 0}

            # Index experiments
            result = await session.execute(select(Experiment).where(Experiment.organization_id == org_id))
            experiments = list(result.scalars().all())
            docs = [
                {"id": e.id, "title": e.name, "description": e.description or "", "status": e.status}
                for e in experiments
            ]
            if docs:
                client.index(f"experiments_{org_id}").add_documents(docs)
                indexed["experiments"] = len(docs)

            # Index files
            result = await session.execute(select(File).where(File.organization_id == org_id))
            files = list(result.scalars().all())
            docs = [{"id": f.id, "title": f.filename, "file_type": f.file_type} for f in files]
            if docs:
                client.index(f"files_{org_id}").add_documents(docs)
                indexed["files"] = len(docs)

            # Index documents
            result = await session.execute(select(Document).where(Document.organization_id == org_id))
            documents = list(result.scalars().all())
            docs = [
                {
                    "id": d.id,
                    "title": d.title or "",
                    "content": (d.extracted_text or "")[:10000],
                    "experiment_id": d.linked_experiment_id,
                }
                for d in documents
            ]
            if docs:
                client.index(f"documents_{org_id}").add_documents(docs)
                indexed["documents"] = len(docs)

            return {"status": "completed", "indexed": indexed}
        except ImportError:
            return {"status": "error", "reason": "meilisearch package not installed"}
        except Exception:
            logger.exception("Search reindex failed for org %s", org_id)
            return {"status": "error", "reason": "reindex failed"}
