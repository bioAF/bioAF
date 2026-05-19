"""Literature Library models (ADR-056, ADR-057).

Ten tables for the v1 Literature Library and Lit Review Run feature.
All tables use BigInteger primary keys to match the rest of the codebase.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.types import EncryptedString


PROVENANCE_USER_UPLOAD = "user_upload"
PROVENANCE_SOURCE_SEARCH = "source_search"
PROVENANCE_LIT_REVIEW_RUN = "lit_review_run"
ALL_PROVENANCES = (PROVENANCE_USER_UPLOAD, PROVENANCE_SOURCE_SEARCH, PROVENANCE_LIT_REVIEW_RUN)

EXTRACTION_NONE = "none"
EXTRACTION_PENDING = "pending"
EXTRACTION_COMPLETE = "complete"
EXTRACTION_FAILED = "failed"
ALL_EXTRACTION_STATUSES = (EXTRACTION_NONE, EXTRACTION_PENDING, EXTRACTION_COMPLETE, EXTRACTION_FAILED)

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
SCOPE_EXPERIMENT = "experiment"
ALL_SCOPES = (SCOPE_GLOBAL, SCOPE_PROJECT, SCOPE_EXPERIMENT)

SOURCE_PUBMED = "pubmed"
SOURCE_BIORXIV = "biorxiv"
SOURCE_EUROPEPMC = "europepmc"
SOURCE_SEMANTICSCHOLAR = "semanticscholar"
SOURCE_UPLOAD = "upload"
EXTERNAL_SOURCES = (SOURCE_PUBMED, SOURCE_BIORXIV, SOURCE_EUROPEPMC, SOURCE_SEMANTICSCHOLAR)
ALL_SOURCES = (*EXTERNAL_SOURCES, SOURCE_UPLOAD)

READING_UNREAD = "unread"
READING_READING = "reading"
READING_READ = "read"
ALL_READING_STATUSES = (READING_UNREAD, READING_READING, READING_READ)

SEARCH_QUEUED = "queued"
SEARCH_RUNNING = "running"
SEARCH_COMPLETE = "complete"
SEARCH_PARTIAL = "partial"
SEARCH_FAILED = "failed"
ALL_SEARCH_STATUSES = (SEARCH_QUEUED, SEARCH_RUNNING, SEARCH_COMPLETE, SEARCH_PARTIAL, SEARCH_FAILED)

REC_PENDING = "pending"
REC_ACCEPTED = "accepted"
REC_DISMISSED = "dismissed"
ALL_REC_STATUSES = (REC_PENDING, REC_ACCEPTED, REC_DISMISSED)

BUCKET_HIGH = "high"
BUCKET_MEDIUM = "medium"
BUCKET_LOW = "low"

BUCKET_HIGH_THRESHOLD = 0.66
BUCKET_MEDIUM_THRESHOLD = 0.33


def derive_bucket(score: float) -> str:
    if score >= BUCKET_HIGH_THRESHOLD:
        return BUCKET_HIGH
    if score >= BUCKET_MEDIUM_THRESHOLD:
        return BUCKET_MEDIUM
    return BUCKET_LOW


class LiteraturePaper(Base):
    __tablename__ = "literature_papers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    doi: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    biorxiv_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    authors_json: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    first_author_key: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    last_author_key: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    journal: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)

    gcs_pdf_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_full_text: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    extracted_text_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EXTRACTION_NONE, server_default=EXTRACTION_NONE
    )
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    added_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "doi", name="uq_literature_papers_org_doi"),
        UniqueConstraint(
            "organization_id",
            "title_normalized",
            "first_author_key",
            "last_author_key",
            name="uq_literature_papers_org_fallback",
        ),
        Index("ix_literature_papers_doi", "doi"),
        Index("ix_literature_papers_org_provenance", "organization_id", "provenance"),
        Index("ix_literature_papers_org_pubdate", "organization_id", "publication_date"),
    )


class LiteraturePaperComment(Base):
    __tablename__ = "literature_paper_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("literature_papers.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("literature_paper_comments.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_literature_paper_comments_paper", "paper_id"),
        Index("ix_literature_paper_comments_user", "user_id"),
        Index("ix_literature_paper_comments_parent", "parent_id"),
    )


class LiteratureAssociation(Base):
    __tablename__ = "literature_associations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("literature_papers.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_literature_associations_paper", "paper_id"),
        Index("ix_literature_associations_scope", "scope_type", "scope_id"),
        Index(
            "uq_literature_associations_paper_global_active",
            "paper_id",
            "scope_type",
            unique=True,
            postgresql_where="removed_at IS NULL AND scope_id IS NULL",
        ),
        Index(
            "uq_literature_associations_paper_scoped_active",
            "paper_id",
            "scope_type",
            "scope_id",
            unique=True,
            postgresql_where="removed_at IS NULL AND scope_id IS NOT NULL",
        ),
    )


class LiteraturePaperReadingStatus(Base):
    __tablename__ = "literature_paper_reading_status"

    paper_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("literature_papers.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LiteraturePaperDismissal(Base):
    __tablename__ = "literature_paper_dismissals"

    paper_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("literature_papers.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    dismissed_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)


class LiteratureSourcesConfig(Base):
    __tablename__ = "literature_sources_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    api_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    rate_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("organization_id", "source", name="uq_literature_sources_org_source"),)


class LiteratureSearch(Base):
    __tablename__ = "literature_searches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    per_source_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SEARCH_QUEUED, server_default=SEARCH_QUEUED)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_literature_searches_org_created", "organization_id", "created_at"),
        Index("ix_literature_searches_user_created", "user_id", "created_at"),
    )


class LiteratureSearchResult(Base):
    __tablename__ = "literature_search_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    search_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("literature_searches.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("literature_papers.id"), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_literature_search_results_search", "search_id"),
        Index("ix_literature_search_results_paper", "paper_id"),
    )


class LiteratureReviewRun(Base):
    __tablename__ = "literature_review_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    triggered_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SEARCH_QUEUED, server_default=SEARCH_QUEUED)
    llm_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(255), nullable=False)
    expansion_queries_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    candidate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommendation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_recommendations: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    score_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.33, server_default="0.33")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_literature_review_runs_experiment_created", "experiment_id", "created_at"),
        Index("ix_literature_review_runs_org_created", "organization_id", "created_at"),
    )


class LiteratureRecommendation(Base):
    __tablename__ = "literature_recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    paper_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("literature_papers.id"), nullable=False)
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    review_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("literature_review_runs.id"), nullable=False)

    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    relevance_bucket: Mapped[str] = mapped_column(String(8), nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=REC_PENDING, server_default=REC_PENDING)
    decided_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "paper_id", "experiment_id", name="uq_literature_recommendations_org_paper_experiment"
        ),
        Index("ix_literature_recommendations_experiment_status", "experiment_id", "status"),
        Index("ix_literature_recommendations_review_run", "review_run_id"),
    )


class AgentReviewLiteratureConfig(Base):
    """Per-scope toggles for Literature inputs to Agent Review (ADR-057)."""

    __tablename__ = "agent_review_literature_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstracts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    comments_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    full_text_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=100_000, server_default="100000")
    updated_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_agent_review_literature_config_org_scope", "organization_id", "scope_type", "scope_id"),
        Index(
            "uq_agent_review_literature_config_org_scope_null",
            "organization_id",
            "scope_type",
            unique=True,
            postgresql_where="scope_id IS NULL",
        ),
        Index(
            "uq_agent_review_literature_config_org_scope_id",
            "organization_id",
            "scope_type",
            "scope_id",
            unique=True,
            postgresql_where="scope_id IS NOT NULL",
        ),
    )
