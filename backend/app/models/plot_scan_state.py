"""The Plot Archive watcher's durable health: one row for the installation's results store.

The watcher wakes every minute but lists the results store only when it is due: after a confirmed
problem (missing, refused, not this installation's) it probes slowly, after a temporary failure it
backs off with a cap, and a configuration change or an administrator's Check again makes it due at
once. ``last_success_at`` advances only after a scan that listed and indexed successfully.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlotScanState(Base):
    __tablename__ = "plot_scan_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    results_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "ok", or why scanning is not happening: missing, no_access, not_confirmed, temporary.
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
