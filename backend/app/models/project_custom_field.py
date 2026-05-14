from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProjectCustomField(Base):
    __tablename__ = "project_custom_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    field_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    project = relationship("Project", back_populates="custom_fields")
