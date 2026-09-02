"""plan_6 step 9: the feature is called literature validation.

It was "Paper Validation" in the docs, "Validation Studies" in the nav, and "paper validation" in
half the internal prose, for one feature. The owner's definition settles it:

    Literature validation attempts to validate the findings of a paper enough to determine whether
    the paper is worth further review by a human scientist. Its output depends on the LLM model
    configured for it and should be treated as informational only.

`/api/validation-studies` is deliberately NOT renamed: the route is not user-visible terminology and
renaming it is breakage with no upside.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCANNED = (
    _REPO / "backend" / "app",
    _REPO / "frontend" / "src",
    _REPO / "docs",
)
_SUFFIXES = {".py", ".ts", ".tsx", ".md"}
_SKIP_DIRS = {"node_modules", ".next", "__pycache__", ".venv"}


def _files():
    for root in _SCANNED:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in _SUFFIXES or not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            yield path


def test_nothing_calls_the_feature_paper_validation():
    """One feature, one name. A user who reads "Paper Validation" in the docs and looks for it in
    the nav under "Validation Studies" has been given two names for one thing."""
    offenders = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if "paper validation" in lowered or "paper-validation" in lowered:
                offenders.append(f"{path.relative_to(_REPO)}:{i}")
    assert offenders == [], f"these still say 'paper validation': {offenders}"


def test_the_api_route_is_not_renamed():
    """Owner's decision: zero breakage risk, and the route is not user-visible terminology. This is
    asserted so a later tidy-up does not quietly take it."""
    api = _REPO / "backend" / "app" / "api" / "validation_studies.py"
    assert api.exists()
    assert 'prefix="/api/validation-studies"' in api.read_text(encoding="utf-8")


def test_the_guide_exists_under_its_new_name_and_is_indexed():
    guide = _REPO / "docs" / "guides" / "literature-validation.md"
    assert guide.exists(), "the guide was not renamed"
    assert not (_REPO / "docs" / "guides" / "paper-validation.md").exists(), "the old guide is still there"
    assert "guides/literature-validation.md" in (_REPO / "docs" / "README.md").read_text(encoding="utf-8")


def test_the_guide_states_what_the_feature_is_for_and_what_its_output_is_worth():
    """The owner's definition, and the two things that make it honest: the output depends on the
    model, and it is informational."""
    text = (_REPO / "docs" / "guides" / "literature-validation.md").read_text(encoding="utf-8").lower()
    assert "worth further review by a human scientist" in text
    assert "informational" in text
    assert "depends on the llm model" in text


def test_the_guide_covers_the_two_autonomy_modes_and_the_per_feature_model():
    text = (_REPO / "docs" / "guides" / "literature-validation.md").read_text(encoding="utf-8").lower()
    assert "assisted" in text and "autonomous" in text
    # The C1 gate is human in BOTH modes, and the doc is where that is easiest to get wrong.
    assert "both modes" in text
    assert "level 1" in text and "level 4" in text
