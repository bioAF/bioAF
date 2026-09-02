"""plan_6 step 6: the features that can name their own model.

Literature validation and literature review are different jobs. Validation reads a whole paper and
has to hold its methods section, its results and a controlled vocabulary in mind at once; review
scores relevance over many short abstracts. One model for both is a compromise, and the compromise
is invisible: a lab that picks a cheap model to keep review affordable silently gets a validation
feature that cannot bind a claim.

Two overrides only. This is not a general per-call model registry, and adding a feature here means
deciding what its model is FOR, not merely that a caller exists.
"""

from __future__ import annotations

FEATURE_LITERATURE_VALIDATION = "literature_validation"
FEATURE_LITERATURE_REVIEW = "literature_review"
VALID_FEATURES: tuple[str, ...] = (FEATURE_LITERATURE_VALIDATION, FEATURE_LITERATURE_REVIEW)
