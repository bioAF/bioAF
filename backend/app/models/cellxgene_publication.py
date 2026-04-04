from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CellxgenePublication(Base):
    __tablename__ = "cellxgene_publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("files.id"), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stable_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    access_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="publishing")
    published_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unpublished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    file = relationship("File")
    experiment = relationship("Experiment")
    published_by = relationship("User")
