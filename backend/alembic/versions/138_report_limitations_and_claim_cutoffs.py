"""change_7.3: separate what bioAF could not do from what the paper lacks.

Study 34 reported a transient download failure as an established absence, took one claim's cutoff for
another, and could not see a blocker it had been told about because the blocker was prose. Four
columns and one plan column carry the facts that report needs as structure rather than as sentences.

- ``validation_study_issues.technical_detail``: the URL, HTTP status, error class, attempt count and
  times behind a plain issue sentence. The sentence stays plain on screen; this sits under a
  collapsed "Technical details" element and still goes to the logs.
- ``comparison_targets.contrast_index`` and ``cutoffs``: a claim's own cutoffs ("padj < 0.05 and
  |log2FC| > 2" is two of them) and the contrast it belongs to. One scalar threshold could not hold
  the first, and nothing linked a claim to a contrast, so a contrast took whichever cutoff the
  extractor gave it and the ground truth followed.
- ``comparison_targets.unresolved_reason``: why a claim's context is marked unresolved rather than
  silently rewritten, for example a post-QC count equal to the deposit's registered inventory.
- ``reproduction_plans.blocker_kinds_json``: a kind for each blocker. The consistency pass matched
  blockers with two regexes, and a paraphrase read as no contradiction.

Revision ID: 138
Revises: 137
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "138"
down_revision = "137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("validation_study_issues", sa.Column("technical_detail", JSONB(), nullable=True))
    op.add_column("comparison_targets", sa.Column("contrast_index", sa.Integer(), nullable=True))
    op.add_column("comparison_targets", sa.Column("cutoffs", JSONB(), nullable=True))
    op.add_column("comparison_targets", sa.Column("unresolved_reason", sa.Text(), nullable=True))
    op.add_column("reproduction_plans", sa.Column("blocker_kinds_json", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("reproduction_plans", "blocker_kinds_json")
    op.drop_column("comparison_targets", "unresolved_reason")
    op.drop_column("comparison_targets", "cutoffs")
    op.drop_column("comparison_targets", "contrast_index")
    op.drop_column("validation_study_issues", "technical_detail")
