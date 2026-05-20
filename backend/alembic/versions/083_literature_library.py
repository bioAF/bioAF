"""Literature Library and Lit Review Run (ADR-056, ADR-057).

Revision ID: 083
Revises: 082
Create Date: 2026-05-18

Additive only:
- literature_papers, literature_paper_comments, literature_associations,
  literature_paper_reading_status, literature_paper_dismissals,
  literature_sources_config, literature_searches, literature_search_results,
  literature_review_runs, literature_recommendations,
  agent_review_literature_config.
- Backfill role_permissions: admin gets all literature actions; comp_bio gets
  the comp_bio subset; bench gets the bench subset; viewer gets literature:view.
- Seed literature_sources_config rows for every existing org so the four
  built-in sources are enabled by default.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


LITERATURE_ACTIONS_ADMIN = (
    "view",
    "upload",
    "comment",
    "associate",
    "delete_own_comment",
    "delete_any_comment",
    "delete_paper",
    "dismiss",
    "reverse_dismiss",
    "run_search",
    "run_lit_review",
    "configure_sources",
)

LITERATURE_ACTIONS_COMP_BIO = (
    "view",
    "upload",
    "comment",
    "associate",
    "delete_own_comment",
    "delete_paper",
    "dismiss",
    "run_search",
    "run_lit_review",
    "configure_sources",
)

LITERATURE_ACTIONS_BENCH = (
    "view",
    "upload",
    "comment",
    "associate",
    "delete_own_comment",
    "run_search",
)

LITERATURE_ACTIONS_VIEWER = ("view",)

LITERATURE_SOURCES = ("pubmed", "biorxiv", "europepmc", "semanticscholar")


def upgrade() -> None:
    # literature_papers
    op.create_table(
        "literature_papers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("doi", sa.String(512), nullable=True),
        sa.Column("pmid", sa.String(64), nullable=True),
        sa.Column("arxiv_id", sa.String(64), nullable=True),
        sa.Column("biorxiv_id", sa.String(128), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_normalized", sa.Text(), nullable=False),
        sa.Column(
            "authors_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("first_author_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("last_author_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("journal", sa.Text(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("gcs_pdf_uri", sa.Text(), nullable=True),
        sa.Column("has_full_text", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("extracted_text_uri", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("added_by_user_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "doi", name="uq_literature_papers_org_doi"),
        sa.UniqueConstraint(
            "organization_id",
            "title_normalized",
            "first_author_key",
            "last_author_key",
            name="uq_literature_papers_org_fallback",
        ),
    )
    op.create_index("ix_literature_papers_organization_id", "literature_papers", ["organization_id"])
    op.create_index("ix_literature_papers_doi", "literature_papers", ["doi"])
    op.create_index("ix_literature_papers_org_provenance", "literature_papers", ["organization_id", "provenance"])
    op.create_index("ix_literature_papers_org_pubdate", "literature_papers", ["organization_id", "publication_date"])

    # literature_paper_comments
    op.create_table(
        "literature_paper_comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["literature_paper_comments.id"]),
        sa.ForeignKeyConstraint(["deleted_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_paper_comments_paper", "literature_paper_comments", ["paper_id"])
    op.create_index("ix_literature_paper_comments_user", "literature_paper_comments", ["user_id"])
    op.create_index("ix_literature_paper_comments_parent", "literature_paper_comments", ["parent_id"])

    # literature_associations
    op.create_table(
        "literature_associations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("added_by_user_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["removed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_associations_paper", "literature_associations", ["paper_id"])
    op.create_index("ix_literature_associations_scope", "literature_associations", ["scope_type", "scope_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_literature_associations_paper_global_active "
        "ON literature_associations (paper_id, scope_type) "
        "WHERE removed_at IS NULL AND scope_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_literature_associations_paper_scoped_active "
        "ON literature_associations (paper_id, scope_type, scope_id) "
        "WHERE removed_at IS NULL AND scope_id IS NOT NULL"
    )

    # literature_paper_reading_status
    op.create_table(
        "literature_paper_reading_status",
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("paper_id", "user_id"),
    )

    # literature_paper_dismissals
    op.create_table(
        "literature_paper_dismissals",
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("dismissed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["dismissed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reversed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("paper_id"),
    )
    op.create_index(
        "ix_literature_paper_dismissals_organization_id",
        "literature_paper_dismissals",
        ["organization_id"],
    )

    # literature_sources_config
    op.create_table(
        "literature_sources_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("rate_limit_override", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "source", name="uq_literature_sources_org_source"),
    )
    op.create_index("ix_literature_sources_config_organization_id", "literature_sources_config", ["organization_id"])

    # literature_searches
    op.create_table(
        "literature_searches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column(
            "sources_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "per_source_status",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_searches_organization_id", "literature_searches", ["organization_id"])
    op.create_index("ix_literature_searches_org_created", "literature_searches", ["organization_id", "created_at"])
    op.create_index("ix_literature_searches_user_created", "literature_searches", ["user_id", "created_at"])

    # literature_search_results
    op.create_table(
        "literature_search_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("search_id", sa.BigInteger(), nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["search_id"], ["literature_searches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_search_results_search", "literature_search_results", ["search_id"])
    op.create_index("ix_literature_search_results_paper", "literature_search_results", ["paper_id"])

    # literature_review_runs
    op.create_table(
        "literature_review_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("llm_provider", sa.String(32), nullable=False),
        sa.Column("llm_model", sa.String(255), nullable=False),
        sa.Column("expansion_queries_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("recommendation_count", sa.Integer(), nullable=True),
        sa.Column("max_recommendations", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("score_threshold", sa.Float(), nullable=False, server_default="0.33"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_review_runs_organization_id", "literature_review_runs", ["organization_id"])
    op.create_index(
        "ix_literature_review_runs_experiment_created", "literature_review_runs", ["experiment_id", "created_at"]
    )
    op.create_index(
        "ix_literature_review_runs_org_created", "literature_review_runs", ["organization_id", "created_at"]
    )

    # literature_recommendations
    op.create_table(
        "literature_recommendations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("review_run_id", sa.BigInteger(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("relevance_bucket", sa.String(8), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["review_run_id"], ["literature_review_runs.id"]),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "paper_id",
            "experiment_id",
            name="uq_literature_recommendations_org_paper_experiment",
        ),
    )
    op.create_index("ix_literature_recommendations_organization_id", "literature_recommendations", ["organization_id"])
    op.create_index(
        "ix_literature_recommendations_experiment_status",
        "literature_recommendations",
        ["experiment_id", "status"],
    )
    op.create_index("ix_literature_recommendations_review_run", "literature_recommendations", ["review_run_id"])

    # agent_review_literature_config
    op.create_table(
        "agent_review_literature_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("abstracts_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("comments_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("full_text_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="100000"),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_review_literature_config_org_scope",
        "agent_review_literature_config",
        ["organization_id", "scope_type", "scope_id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_review_literature_config_org_scope_null "
        "ON agent_review_literature_config (organization_id, scope_type) "
        "WHERE scope_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_review_literature_config_org_scope_id "
        "ON agent_review_literature_config (organization_id, scope_type, scope_id) "
        "WHERE scope_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # Backfill RBAC permissions for all existing organizations.
    # ------------------------------------------------------------------
    role_actions = {
        "admin": LITERATURE_ACTIONS_ADMIN,
        "comp_bio": LITERATURE_ACTIONS_COMP_BIO,
        "bench": LITERATURE_ACTIONS_BENCH,
        "viewer": LITERATURE_ACTIONS_VIEWER,
    }
    for role_name, actions in role_actions.items():
        for action in actions:
            op.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role_id, resource, action)
                    SELECT r.id, 'literature', :action
                    FROM roles r
                    WHERE r.name = :role_name AND r.is_system = true
                      AND NOT EXISTS (
                          SELECT 1 FROM role_permissions rp
                          WHERE rp.role_id = r.id
                            AND rp.resource = 'literature'
                            AND rp.action = :action
                      )
                    """
                ).bindparams(role_name=role_name, action=action)
            )

    # ------------------------------------------------------------------
    # Seed literature_sources_config for every existing organization.
    # ------------------------------------------------------------------
    for source in LITERATURE_SOURCES:
        op.execute(
            sa.text(
                """
                INSERT INTO literature_sources_config (organization_id, source, enabled)
                SELECT o.id, :source, true
                FROM organizations o
                WHERE NOT EXISTS (
                    SELECT 1 FROM literature_sources_config c
                    WHERE c.organization_id = o.id AND c.source = :source
                )
                """
            ).bindparams(source=source)
        )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE resource = 'literature'")

    op.execute("DROP INDEX IF EXISTS uq_agent_review_literature_config_org_scope_id")
    op.execute("DROP INDEX IF EXISTS uq_agent_review_literature_config_org_scope_null")
    op.drop_index("ix_agent_review_literature_config_org_scope", table_name="agent_review_literature_config")
    op.drop_table("agent_review_literature_config")

    op.drop_index("ix_literature_recommendations_review_run", table_name="literature_recommendations")
    op.drop_index("ix_literature_recommendations_experiment_status", table_name="literature_recommendations")
    op.drop_index("ix_literature_recommendations_organization_id", table_name="literature_recommendations")
    op.drop_table("literature_recommendations")

    op.drop_index("ix_literature_review_runs_org_created", table_name="literature_review_runs")
    op.drop_index("ix_literature_review_runs_experiment_created", table_name="literature_review_runs")
    op.drop_index("ix_literature_review_runs_organization_id", table_name="literature_review_runs")
    op.drop_table("literature_review_runs")

    op.drop_index("ix_literature_search_results_paper", table_name="literature_search_results")
    op.drop_index("ix_literature_search_results_search", table_name="literature_search_results")
    op.drop_table("literature_search_results")

    op.drop_index("ix_literature_searches_user_created", table_name="literature_searches")
    op.drop_index("ix_literature_searches_org_created", table_name="literature_searches")
    op.drop_index("ix_literature_searches_organization_id", table_name="literature_searches")
    op.drop_table("literature_searches")

    op.drop_index("ix_literature_sources_config_organization_id", table_name="literature_sources_config")
    op.drop_table("literature_sources_config")

    op.drop_index("ix_literature_paper_dismissals_organization_id", table_name="literature_paper_dismissals")
    op.drop_table("literature_paper_dismissals")

    op.drop_table("literature_paper_reading_status")

    op.execute("DROP INDEX IF EXISTS uq_literature_associations_paper_scoped_active")
    op.execute("DROP INDEX IF EXISTS uq_literature_associations_paper_global_active")
    op.drop_index("ix_literature_associations_scope", table_name="literature_associations")
    op.drop_index("ix_literature_associations_paper", table_name="literature_associations")
    op.drop_table("literature_associations")

    op.drop_index("ix_literature_paper_comments_parent", table_name="literature_paper_comments")
    op.drop_index("ix_literature_paper_comments_user", table_name="literature_paper_comments")
    op.drop_index("ix_literature_paper_comments_paper", table_name="literature_paper_comments")
    op.drop_table("literature_paper_comments")

    op.drop_index("ix_literature_papers_org_pubdate", table_name="literature_papers")
    op.drop_index("ix_literature_papers_org_provenance", table_name="literature_papers")
    op.drop_index("ix_literature_papers_doi", table_name="literature_papers")
    op.drop_index("ix_literature_papers_organization_id", table_name="literature_papers")
    op.drop_table("literature_papers")
