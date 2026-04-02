from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ComponentState(Base):
    __tablename__ = "component_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="disabled")
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_terraform_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("terraform_runs.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TerraformRun(Base):
    __tablename__ = "terraform_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    triggered_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    component_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plan_summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 17 columns
    module_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resources_planned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resources_completed: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    deploy_phase: Mapped[str | None] = mapped_column(String(50), nullable=True)
    completed_resources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    apply_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    terraform_state_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformConfig(Base):
    __tablename__ = "platform_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
