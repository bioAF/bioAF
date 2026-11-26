"""A single-use bootstrap ticket for opening a notebook session through the gateway (plan 03.5A).

Only the SHA-256 of the ticket is stored. A ticket is bound to the user, Organization, session and the
exact Pod (name, namespace, UID) confirmed when it was issued, and is consumed by one conditional UPDATE,
so single use holds across workers and restarts.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotebookAccessTicket(Base):
    __tablename__ = "notebook_access_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("compute_sessions.id"), nullable=False, index=True)
    pod_name: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    pod_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
