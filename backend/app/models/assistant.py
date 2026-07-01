"""Persisted entities for the conversational assistant (ai_pipeline_run, spec-02).

Four tightly-coupled models back one feature, so they live in one module (cf. role.py,
which holds Role + RolePermission). Names are feature-prefixed (Assistant*) to match the
agent-review convention and to avoid claiming generic table names like "conversations".

- AssistantConversation: a multi-turn session, org-scoped and bound to the user whose RBAC
  bounds every action taken within it.
- AssistantMessage: one turn (user | assistant | tool).
- AssistantToolInvocation: one tool call the agent makes; the unit the enforcement wrapper
  (T2) acts on. Carries the consequence class and the proposed -> confirmed -> executed state.
- AssistantActionPlan: a proposed plan surfaced at the plan-then-confirm gate (G1).

All ids are BigInteger (conversational volume); organization_id / user_id reference the
Integer-keyed organizations / users tables. Loose cross-references (tool_invocation_id,
audit_id) are plain BigInteger columns, mirroring how agent_review_job references related
ids without a hard FK.
"""

import uuid as uuid_pkg
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), unique=True
    )
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages = relationship("AssistantMessage", back_populates="conversation", cascade="all, delete-orphan")
    tool_invocations = relationship(
        "AssistantToolInvocation", back_populates="conversation", cascade="all, delete-orphan"
    )
    action_plans = relationship("AssistantActionPlan", back_populates="conversation", cascade="all, delete-orphan")


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("assistant_conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated on assistant turns that propose tool calls.
    tool_calls_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Populated on tool-result messages; loose reference to AssistantToolInvocation.id.
    tool_invocation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("AssistantConversation", back_populates="messages")


class AssistantToolInvocation(Base):
    __tablename__ = "assistant_tool_invocations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("assistant_conversations.id"), nullable=False, index=True
    )
    message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("assistant_messages.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # read_only | mutating | spend
    consequence_class: Mapped[str] = mapped_column(String(16), nullable=False)
    # proposed | awaiting_confirmation | confirmed | executing | succeeded | failed | declined
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="proposed", server_default="proposed")
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    confirmed_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Loose link to the audit_log row the wrapper writes for this call.
    audit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("AssistantConversation", back_populates="tool_invocations")


class AssistantActionPlan(Base):
    __tablename__ = "assistant_action_plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("assistant_conversations.id"), nullable=False, index=True
    )
    # Ordered tool calls with resolved entities.
    steps_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # proposed | approved | declined | executed | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", server_default="proposed")
    approved_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("AssistantConversation", back_populates="action_plans")
