"""A saved answer to "what goes in this pipeline's samplesheet columns".

bioAF fills a samplesheet from the pipeline's own contract, and some columns have
no source it may read: mag's ``group`` controls co-assembly, rnasplice's
``condition`` defines the differential contrast. Those are stated by a scientist,
and re-stating them for every run of the same design is the cost this table
removes.

**Scoped to an EXPERIMENT by default, because the binding depends on the
experiment.** Treating a saved mapping as the pipeline's missing contract was the
earlier assumption and it is wrong: the right column for one experiment is the
wrong one for the next, so a per-pipeline mapping would propagate a binding that
is correct once and silently wrong afterwards.

Promotable to the project and then to the organization, deliberately at each
rung, never automatically. The organization rung exists for a real population:
a core facility or service lab runs the same assay across many unrelated
projects, and with the project as the ceiling it would reconfigure indefinitely.
A discovery lab simply never promotes and is unaffected.

**One current mapping per pipeline per scope**, enforced below. Comparative work
(two co-assembly groupings, two contrasts) happens by running twice; each run
keeps its own snapshot, so nothing is lost.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The rungs of the ladder, narrowest first. Resolution walks this order and the
# most specific match wins, so an organization-wide binding never overrides one
# somebody set for this experiment.
MAPPING_SCOPES = ["experiment", "project", "organization"]


class SamplesheetMapping(Base):
    __tablename__ = "samplesheet_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    pipeline_key: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    # Set for the scope that owns this mapping and null for the others. An
    # organization-scoped mapping carries neither, since organization_id already
    # says which one it belongs to.
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)

    # RULES, naming no sample: `fasta` comes from the sample's attached assembly,
    # `strandedness` is the literal "reverse". These travel anywhere, including
    # up to organization scope, because they describe how to find a value rather
    # than what the value is for one sample.
    bindings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # PER-SAMPLE VALUES, the design itself: {"<sample_id>": {"group": {...}}}.
    # Keyed by sample id, never by row position, so they simply do not apply in
    # an experiment whose samples they do not name.
    values_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # THE COLUMNS THEMSELVES, for a pipeline that publishes no contract:
    # {"fields": [{name, type, required, binding}]}, the same shape the
    # experiment field editor already uses, each column carrying a binding that
    # says where its value comes from. NULL means nothing was declared, which
    # every reader answers with today's generic sheet.
    columns_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
    experiment = relationship("Experiment")
    project = relationship("Project")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    # One current mapping per pipeline per scope. Expressed as three partial
    # indexes rather than one composite constraint because PostgreSQL treats
    # NULLs as distinct in a unique constraint, so a single constraint over the
    # nullable scope columns would let organization-scoped duplicates through.
    __table_args__ = (
        Index(
            "uq_samplesheet_mapping_experiment",
            "experiment_id",
            "pipeline_key",
            unique=True,
            postgresql_where=text("scope = 'experiment'"),
        ),
        Index(
            "uq_samplesheet_mapping_project",
            "project_id",
            "pipeline_key",
            unique=True,
            postgresql_where=text("scope = 'project'"),
        ),
        Index(
            "uq_samplesheet_mapping_organization",
            "organization_id",
            "pipeline_key",
            unique=True,
            postgresql_where=text("scope = 'organization'"),
        ),
    )
