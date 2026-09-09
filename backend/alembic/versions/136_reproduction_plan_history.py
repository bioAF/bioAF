"""change_7.2 section 2: a study carries exactly one ACTIVE reproduction plan, and its history is kept.

`POST /{id}/read` and the driver both ran `read_and_plan` on the same study, so every recent study
carries two plans, both back-linked through `ReproductionPlan.validation_study_id`: study 31 has
plans 29 and 30, study 32 has 31 and 32, study 33 has 33 and 34. The study's forward
`reproduction_plan_id` names one and the other is orphaned with a full set of comparison targets,
and `_claimed_thresholds` read the cutoffs off both.

**The superseded plans are preserved, never deleted.** Study 33's discarded plan records a different
model interpretation of the same claim and is part of the evidence explaining that run. Deleting it
to satisfy a constraint would destroy the record this change exists to improve.

The active plan is the one the study points at. Where a study points at nothing, the newest plan is
adopted as active, because that is the one whose extraction finished last.

Revision ID: 136
Revises: 135
"""

import sqlalchemy as sa
from alembic import op

revision = "136"
down_revision = "135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reproduction_plans", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reproduction_plans", sa.Column("superseded_reason", sa.Text(), nullable=True))

    # History first, uniqueness second. Every plan that its study does not point at becomes history,
    # and a study pointing at nothing adopts its newest plan so no study is left with zero.
    op.execute(
        """
        UPDATE reproduction_plans p
           SET superseded_at = now(),
               superseded_reason = 'superseded by the study''s active plan (change_7.2 section 2 migration)'
          FROM validation_studies s
         WHERE p.validation_study_id = s.id
           AND s.reproduction_plan_id IS NOT NULL
           AND p.id <> s.reproduction_plan_id
           AND p.superseded_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE reproduction_plans p
           SET superseded_at = now(),
               superseded_reason = 'superseded by a later extraction of the same study (change_7.2 section 2 migration)'
          FROM validation_studies s
         WHERE p.validation_study_id = s.id
           AND s.reproduction_plan_id IS NULL
           AND p.superseded_at IS NULL
           AND p.id <> (
               SELECT max(q.id) FROM reproduction_plans q WHERE q.validation_study_id = s.id
           )
        """
    )
    # A study that had no forward pointer now has an unambiguous active plan; point it there so the
    # forward reference and the flag can never disagree.
    op.execute(
        """
        UPDATE validation_studies s
           SET reproduction_plan_id = (
               SELECT p.id FROM reproduction_plans p
                WHERE p.validation_study_id = s.id AND p.superseded_at IS NULL
                ORDER BY p.id DESC LIMIT 1
           )
         WHERE s.reproduction_plan_id IS NULL
           AND EXISTS (SELECT 1 FROM reproduction_plans p WHERE p.validation_study_id = s.id)
        """
    )

    op.create_index(
        "ux_reproduction_plans_one_active_per_study",
        "reproduction_plans",
        ["validation_study_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_reproduction_plans_one_active_per_study", table_name="reproduction_plans")
    op.drop_column("reproduction_plans", "superseded_reason")
    op.drop_column("reproduction_plans", "superseded_at")
