from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotebookSessionFile(Base):
    __tablename__ = "notebook_session_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("compute_sessions.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("files.id"), nullable=False)
    access_type: Mapped[str] = mapped_column(String(20), nullable=False, default="input")
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_id", "file_id", "access_type", name="uq_notebook_session_files_session_file_access"),
    )
