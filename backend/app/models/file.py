import uuid as uuid_pkg
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._storage_uri_sync import register_storage_uri_sync


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The asset's catalogue identity, in the owner's framing an ISBN: bioAF did
    # not write the book but catalogues it for later recall. Assigned on ingest
    # whatever the provenance (uploaded, produced by a pipeline, pulled from a
    # public archive) and never reissued.
    #
    # It exists because a path cannot be an identity. Reassigning a file between
    # experiments physically moves the object and rewrites the row, so this id
    # and the integer one survive while `storage_uri` does not.
    #
    # Added ALONGSIDE the integer key, never replacing it, which is the same
    # split projects, experiments and samples have carried since migration 080:
    # the integer is a storage detail that never leaves the system, and the UUID
    # is the contract with the outside world and is never used for joins. v4
    # today; v7 from the PostgreSQL 18 upgrade onward, which needs no migration
    # because nothing distinguishes them in the schema. Never treat their
    # ordering as creation order: use created_at.
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), unique=True
    )
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    # Opaque object-store URI. storage_uri is the AUTHORITATIVE backend-neutral
    # column (app code reads/writes it); gcs_uri is a RETAINED legacy mirror kept
    # in sync (see _storage_uri_sync, storage_uri canonical). We do NOT drop
    # gcs_uri; an operator drops it later once nothing depends on it (and makes
    # storage_uri NOT NULL at that point). storage_uri is always populated today.
    gcs_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    md5_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upload_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    uploader_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    tags_json: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    file_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ingest_source: Mapped[str | None] = mapped_column(String(20), server_default="manual", nullable=True)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True, index=True)
    # Valid source_type values: "upload", "pipeline_output", "notebook_output"
    source_type: Mapped[str] = mapped_column(String(30), server_default="upload", nullable=False)
    source_pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=True)
    source_notebook_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("compute_sessions.id"), nullable=True
    )
    sha256_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sequencing_batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sequencing_batches.id"), nullable=True)
    # Sequencing identity: which unit of sequencing this file came from. bioAF's
    # model is sample -> files with nothing in between, so a sample sequenced
    # over several lanes, or re-sequenced in a top-up, had nowhere to record it
    # and three services improvised `lane:`/`read:` strings in tags_json instead.
    # Untyped strings are why two spellings of one lane could coexist ("1" from
    # one writer, "001" from another), which split one physical lane into two
    # units and left a sample's mates unpaired.
    #
    # Every column is nullable and NULL means "not known", never a sentinel: a
    # lab receiving pre-merged FASTQs from a CRO has no lane at all and must be
    # wholly unaffected. Lane is the physical lane number (1-based, so 0 is not a
    # lane); read_type is R1/R2/I1/I2.
    lane: Mapped[int | None] = mapped_column(Integer, nullable=True)
    read_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # A lane number alone collides across flow cells (L001 on two flow cells is
    # two different lanes), so the read-group axis is (flowcell_id, lane). Both
    # of these live in the FASTQ header rather than the filename, and whether
    # bioAF reads that header is an open decision; nothing populates them yet.
    flowcell_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_sequence: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The archive run a fetched FASTQ came from (ENA/SRA, e.g. SRR390728). Its
    # own field because it is NOT a lane: fetchngs used to fabricate lane numbers
    # so a sample's sibling runs stayed on separate sheet rows, which promoted a
    # fiction into the lane axis. It tells sequencing units apart the same way a
    # lane does, without claiming to be one.
    source_run_accession: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_deleted: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    # When this file was deleted from view, and by whom. NULL means it is live.
    #
    # Deletion is SOFT for the RECORD (decision of 2026-08-19): the row and its
    # `uuid` are never removed, because a catalogue number that stops resolving
    # the moment somebody tidies up is not a catalogue number, and a published
    # provenance record must never dangle.
    #
    # It is HARD for the BYTES (issue #86): a delete that reclaims no space is
    # not a delete, so the object is erased and `storage_deleted` above is set
    # to say so. The two columns stay distinct because either can still happen
    # without the other: a stack teardown frees storage under records that are
    # very much live, and an object two live records share outlives the retiring
    # of one of them.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    is_global: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The caller's own identifier for this file (a LIMS file ID), opaque and kept exactly as supplied.
    # NULL means none was assigned; it is never filled in from the bioAF id, a name, a path, or a
    # checksum. Unique per organization when set, deleted records included, so it is never reassigned.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Where registration first saw a pipeline output. storage_uri is where the canonical bytes are now
    # and can change (a protected verification copy, file organization); this does not, so rediscovering
    # the original output finds the same File. Unique per (organization, source run) when set.
    source_storage_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # When the file's bytes were first verified available (FileService.mark_ready). NULL means readiness
    # has not been established under that contract, not that an older file is unusable. Historical: it
    # is not reset on deletion.
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The superseded object this File's bytes were copied from, to be removed once the new reference
    # is committed: ``{"uri", "version", "size_bytes", "attempts", "next_attempt_at"}``, the old
    # object's pinned identity plus a bounded retry schedule. NULL when nothing is pending. Written in
    # the same transaction as the new ``storage_uri`` and cleared only by its owned cleanup
    # (app/services/storage_cleanup.py), so a rollback keeps the original and a crash after commit
    # leaves the cleanup to the next pass. At most one per File.
    pending_storage_cleanup: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_files_experiment_id", "experiment_id"),
        Index(
            "uq_files_org_external_id",
            "organization_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_files_pipeline_source_identity",
            "organization_id",
            "source_pipeline_run_id",
            "source_storage_uri",
            unique=True,
            postgresql_where=text("source_storage_uri IS NOT NULL"),
        ),
        Index(
            "ix_files_pending_storage_cleanup",
            "id",
            postgresql_where=text("pending_storage_cleanup IS NOT NULL"),
        ),
    )

    organization = relationship("Organization")
    # Both of these point at `users`, so each names its own column: SQLAlchemy
    # cannot choose between two paths to the same table.
    uploader = relationship("User", foreign_keys=[uploader_user_id])
    deleted_by = relationship("User", foreign_keys=[deleted_by_user_id])
    project = relationship("Project")
    experiment = relationship("Experiment")
    source_pipeline_run = relationship("PipelineRun", foreign_keys=[source_pipeline_run_id])
    source_notebook_session = relationship("ComputeSession", foreign_keys=[source_notebook_session_id])
    sequencing_batch = relationship("SequencingBatch", back_populates="files")
    consumed_by_runs = relationship("PipelineRunInputFile", cascade="all, delete-orphan", passive_deletes=True)
    notebook_sessions = relationship("NotebookSessionFile", foreign_keys="NotebookSessionFile.file_id")


register_storage_uri_sync(File)
