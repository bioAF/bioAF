"""An explicit decision that a legacy unlinked file stays Experiment-level for one GEO Sample.

Before Sample links decided GEO file ownership, a file whose name started with a Sample's external ID
was referenced on that Sample's row. When such a file has no links, a person either links it to the
Sample that owns it or acknowledges here that it should stay an Experiment-level asset without that
Sample's reference. An acknowledgment is never a link and certifies nothing about the file's contents.

It holds only while ``fingerprint`` still matches the file/Sample state it was made against (file name and
storage scope, Sample identity, links) under ``policy_revision``; any change makes it stale.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeoFileAssociationAcknowledgment(Base):
    __tablename__ = "geo_file_association_acknowledgments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    experiment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    sample_id: Mapped[int] = mapped_column(Integer, ForeignKey("samples.id", ondelete="CASCADE"), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "file_id", "sample_id", "policy_revision", name="uq_geo_file_association_ack_pair"
        ),
        Index("ix_geo_file_association_ack_org_experiment", "organization_id", "experiment_id"),
    )
