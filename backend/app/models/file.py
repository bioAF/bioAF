from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._storage_uri_sync import register_storage_uri_sync


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    is_global: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_files_experiment_id", "experiment_id"),)

    organization = relationship("Organization")
    uploader = relationship("User")
    project = relationship("Project")
    experiment = relationship("Experiment")
    source_pipeline_run = relationship("PipelineRun", foreign_keys=[source_pipeline_run_id])
    source_notebook_session = relationship("ComputeSession", foreign_keys=[source_notebook_session_id])
    sequencing_batch = relationship("SequencingBatch", back_populates="files")
    consumed_by_runs = relationship("PipelineRunInputFile", cascade="all, delete-orphan", passive_deletes=True)
    notebook_sessions = relationship("NotebookSessionFile", foreign_keys="NotebookSessionFile.file_id")


register_storage_uri_sync(File)
