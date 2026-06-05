"""Lab Knowledge Phase A: Lab Documents (ADR-059, ADR-060, ADR-061).

Revision ID: 095
Revises: 094
Create Date: 2026-06-05

Creates the lab_documents / lab_document_versions / lab_document_tags /
lab_document_tag_assignments tables, seeds the default tag vocabulary for every
existing organization, and backfills the new RBAC permissions onto the existing
system roles (admin, comp_bio, bench, viewer). New orgs get all of this from the
bootstrap path (seed_builtin_roles + seed_default_lab_document_tags).
"""

import sqlalchemy as sa
from alembic import op

revision = "095"
down_revision = "094"
branch_labels = None
depends_on = None

DEFAULT_TAGS = ["manual", "contact", "procedure", "policy", "standard"]


def upgrade() -> None:
    op.create_table(
        "lab_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("gcs_uri", sa.String(length=1000), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("md5_checksum", sa.String(length=64), nullable=True),
        sa.Column("is_archived", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_lab_documents_org", "lab_documents", ["organization_id"])

    op.create_table(
        "lab_document_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("lab_documents.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("gcs_uri", sa.String(length=1000), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("md5_checksum", sa.String(length=64), nullable=True),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "version_number", name="uq_lab_document_versions_doc_number"),
    )
    op.create_index("idx_lab_document_versions_doc", "lab_document_versions", ["document_id"])

    op.create_table(
        "lab_document_tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_lab_document_tags_org_name"),
    )
    op.create_index("idx_lab_document_tags_org", "lab_document_tags", ["organization_id"])

    op.create_table(
        "lab_document_tag_assignments",
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("lab_documents.id"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("lab_document_tags.id"), primary_key=True),
    )

    # Seed default tags for every existing org. created_by_user_id is set to the
    # org's earliest admin-ish user; fall back to the lowest user id in the org.
    for tag in DEFAULT_TAGS:
        op.execute(
            sa.text(
                """
                INSERT INTO lab_document_tags (organization_id, name, created_by_user_id)
                SELECT o.id, :tag, (
                    SELECT u.id FROM users u
                    WHERE u.organization_id = o.id
                    ORDER BY u.id ASC LIMIT 1
                )
                FROM organizations o
                WHERE EXISTS (SELECT 1 FROM users u2 WHERE u2.organization_id = o.id)
                  AND NOT EXISTS (
                    SELECT 1 FROM lab_document_tags t
                    WHERE t.organization_id = o.id AND t.name = :tag
                  )
                """
            ).bindparams(tag=tag)
        )

    # Backfill RBAC. View for all system roles; manage/author/delete for admin
    # (and sdr:author also for comp_bio), mirroring 071_add_references_resource_permissions.
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, perm.resource, perm.action
        FROM roles r
        CROSS JOIN (VALUES
            ('lab_documents', 'view'),
            ('lab_glossary', 'view'),
            ('sdr', 'view')
        ) AS perm(resource, action)
        WHERE r.name IN ('admin', 'comp_bio', 'bench', 'viewer') AND r.is_system = true
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'sdr', 'author'
        FROM roles r
        WHERE r.name IN ('admin', 'comp_bio') AND r.is_system = true
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, perm.resource, perm.action
        FROM roles r
        CROSS JOIN (VALUES
            ('lab_documents', 'manage'),
            ('lab_document_tags', 'manage'),
            ('lab_glossary', 'manage'),
            ('lab_glossary', 'delete'),
            ('sdr', 'manage')
        ) AS perm(resource, action)
        WHERE r.name = 'admin' AND r.is_system = true
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE resource IN ('lab_documents', 'lab_document_tags', 'lab_glossary', 'sdr')
        """
    )
    op.drop_table("lab_document_tag_assignments")
    op.drop_index("idx_lab_document_tags_org", table_name="lab_document_tags")
    op.drop_table("lab_document_tags")
    op.drop_index("idx_lab_document_versions_doc", table_name="lab_document_versions")
    op.drop_table("lab_document_versions")
    op.drop_index("idx_lab_documents_org", table_name="lab_documents")
    op.drop_table("lab_documents")
