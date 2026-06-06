from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LabDocument(Base):
    """Operational/institutional document (ADR-059, ADR-061). Metadata only;
    file bytes live in GCS. ``gcs_uri`` always points at the current version."""

    __tablename__ = "lab_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    gcs_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    md5_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    organization = relationship("Organization")
    created_by = relationship("User")
    versions = relationship(
        "LabDocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="LabDocumentVersion.version_number",
    )
    tag_assignments = relationship("LabDocumentTagAssignment", back_populates="document", cascade="all, delete-orphan")


class LabDocumentVersion(Base):
    """Append-only version history for a lab document (ADR-061)."""

    __tablename__ = "lab_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number", name="uq_lab_document_versions_doc_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("lab_documents.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    gcs_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    md5_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    uploaded_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    document = relationship("LabDocument", back_populates="versions")
    uploaded_by = relationship("User")


class LabDocumentNote(Base):
    """A free-text note a user adds to a lab document (ADR-059). Mirrors the
    literature paper-comment pattern: org-scoped, soft-deletable, ordered by
    creation. Flat (no threading) in v1."""

    __tablename__ = "lab_document_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    document = relationship("LabDocument")
    user = relationship("User", foreign_keys=[user_id])


class LabDocumentTag(Base):
    """Org-scoped, admin-controlled tag vocabulary (ADR-060). Distinct from the
    global ``controlled_vocabularies`` table."""

    __tablename__ = "lab_document_tags"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_lab_document_tags_org_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    organization = relationship("Organization")
    created_by = relationship("User")


class LabDocumentTagAssignment(Base):
    """Many-to-many join between documents and tags (ADR-060)."""

    __tablename__ = "lab_document_tag_assignments"

    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("lab_documents.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("lab_document_tags.id"), primary_key=True)

    document = relationship("LabDocument", back_populates="tag_assignments")
    tag = relationship("LabDocumentTag")
