"""change_7.2 section 2: who is working on a study right now, and until when.

Its own table rather than a column on the study. The claim is taken, renewed and released while the
owned work is writing to the study row, and a renewal on a second connection would block behind that
work's own uncommitted transaction: the claim would expire under exactly the worker it exists to
protect. Keeping it separate also stops a thirty-second heartbeat stamping `updated_at` on the study.

`token` is a fencing token, not a flag. Every write performed under a claim carries it and is
rejected if it no longer matches, so an expired worker that finishes late cannot land its result over
the worker that replaced it. Two writers ran `read_and_plan` on study 33 and both committed a plan;
a `state != "requested"` guard could not see the other's uncommitted state.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ValidationStudyClaim(Base):
    __tablename__ = "validation_study_claims"

    validation_study_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("validation_studies.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    holder: Mapped[str] = mapped_column(String(64), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
