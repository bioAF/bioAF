from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotebookSessionFile(Base):
    __tablename__ = "notebook_session_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("compute_sessions.id"), nullable=True)
    file_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("files.id"), nullable=True)
    access_type: Mapped[str | None] = mapped_column(String(20), nullable=True, default="input")
    accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("session_id", "file_id", "access_type", name="uq_notebook_session_files_session_file_access"),
    )
