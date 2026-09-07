"""ValidationStudyIssue: a step that hit an error while validating a paper (plan_7 step 14a/14c).

A refusal was invisible at every layer. Some models decline biotech queries outright, and no
provider client read the field that says so, so a model refusing every claim binding produced a plan
that had silently fallen back to the alias table with nothing on screen. The same held for a rate
limit, a 502, and an answer that came back as prose.

**Study-scoped, not plan-scoped.** ``AiDecisionList`` is derived per-claim from ``comparison_target``
rows, so it can only show an issue that has a claim to hang off. A refusal while choosing a
deposited file or ratifying a verdict has no comparison target, and a refusal while reading the
paper happens before a plan exists at all.

**A table rather than a key on ``evidence_json``**, because two writers with different transaction
lifetimes append to it: the request path (extraction, before the study has a plan) and the
background driver, which rewrites the whole evidence bundle on nearly every tick.

``impact`` is what stops the report crying wolf. A step that fell back and carried on
(``degraded``) is not a step that produced nothing (``blocked``), and a section that cannot tell
them apart is one users learn to ignore.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# What went wrong, from `llm_decision`. `refusal` is the one that names a model an administrator can
# request an account exception for; the others separate "we could not reach the LLM" from "bioAF hit
# an internal error" from "the answer was not in the format we asked for".
ISSUE_OUTCOMES = ("refusal", "unreachable", "internal", "unparseable")

# Whether the step still produced something usable. Without this distinction every fallback would
# read as a failure.
ISSUE_IMPACTS = ("degraded", "blocked")


class ValidationStudyIssue(Base):
    __tablename__ = "validation_study_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_study_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("validation_studies.id"), nullable=False, index=True
    )

    # The step in the user's language ("binding the paper's claims to measurable metrics"), never the
    # function name. It is what the report renders.
    step: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    impact: Mapped[str] = mapped_column(String(20), nullable=False)
    # One plain sentence. Technical detail belongs in the logs, per the repo's error-copy rule.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Which model, so a refusal can be acted on. Null where the step was not a model call.
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
