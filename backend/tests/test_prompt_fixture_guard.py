"""change_7.5 section 1.6: no fixture value in a production prompt.

SAMD1 and Groff are fixtures that expose bioAF's defects, never specifications. The extraction prompt
nonetheless carried SAMD1's own count ("8733 significant peaks is peak_count", "never samd1_chip_peaks")
from 2026-09-01, steering exactly the binding under review, and Groff's "194 genes ... 88 of which"
as its example of a set and a subset.

This renders every lit_validation prompt and fails on any value the two fixture READMEs list under
"Values that must never reach production". A new fixture value is added to its README, not here.
"""

import pathlib
import re

import pytest

from app.services.code_execution_service import _ENTRY_POINT_SYSTEM
from app.services.column_resolution import ROLES, build_column_prompt
from app.services.contrast_selection import build_contrast_prompt
from app.services.deposit_selection import build_selection_prompt
from app.services.generated_analysis import _SYSTEM as GENERATION_SYSTEM
from app.services.literature.deposit_inventory_service import DepositEntry
from app.services.validation_extraction_service import build_binding_prompt, build_extraction_prompt
from app.services.validation_precompute_checks import _JUDGMENT_SYSTEM, _METHODS_QUESTION, _SAMPLES_QUESTION
from app.services.validation_ratification import build_ratification_prompt

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_HEADING = "## Values that must never reach production"


def _values(readme: pathlib.Path) -> list[str]:
    """The backticked values listed under the README's heading."""
    text = readme.read_text()
    assert _HEADING in text, f"{readme} lists no values that must never reach production"
    section = text.split(_HEADING, 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- `([^`]+)`", section, re.M)


_FIXTURE_VALUES = sorted({v for name in ("samd1", "groff") for v in _values(_FIXTURES / name / "README.md")})


def _prompts() -> dict[str, str]:
    """Every lit_validation prompt, rendered with neutral inputs. A payload's variable parts are the
    paper's own data at run time; the fixed words around them are what is checked."""
    neutral_claim = {
        "metric_key": "",
        "claim_text": "a claim",
        "value": 1,
        "unit": "genes",
        "source_locator": "Results",
    }
    prompts: dict[str, str] = {}
    prompts["extraction"] = "\n".join(build_extraction_prompt(""))
    prompts["binding"] = "\n".join(build_binding_prompt([neutral_claim]))
    prompts["binding with context"] = "\n".join(
        build_binding_prompt(
            [neutral_claim],
            previous=[{"claim_index": 0, "bound_key": None, "reason": "r"}],
            inventory="i",
            statements=["s"],
        )
    )
    for kind in ROLES:
        prompts[f"column resolution ({kind})"] = "\n".join(build_column_prompt(["a", "b"], kind=kind))
    prompts["contrast selection"] = "\n".join(
        build_contrast_prompt(
            [{"name": "a vs b", "assay": "bulk RNA-seq"}, {"name": "c vs d", "assay": "ChIP-seq"}],
            pipeline_key="nf-core/rnaseq",
            assay="bulk RNA-seq",
            accession="ACC1",
            sample_titles=["s1"],
        )
    )
    system, payload = build_selection_prompt(
        [DepositEntry(filename="matrix.tsv.gz", url="u", classification="matrix_counts", level="series")],
        pipeline_key="nf-core/rnaseq",
        kind="gene",
    )
    prompts["deposit selection"] = f"{system}\n{payload}"
    prompts["generated analysis"] = GENERATION_SYSTEM
    prompts["sufficiency judgment"] = "\n".join((_JUDGMENT_SYSTEM, _METHODS_QUESTION, _SAMPLES_QUESTION))
    prompts["entry point"] = _ENTRY_POINT_SYSTEM
    prompts["ratification"] = "\n".join(build_ratification_prompt({}))
    return prompts


def _found(value: str, prompt: str) -> bool:
    """Whether ``value`` appears in ``prompt`` as itself: a number never inside another number, and a
    count written with thousands separators still found."""
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        spellings = {value}
        if len(value) > 3 and "." not in value:
            spellings.add(f"{int(value):,}")
        return any(re.search(rf"(?<![\d.,]){re.escape(s)}(?![\d]|[.,]\d)", prompt) for s in spellings)
    return value.lower() in prompt.lower()


def test_the_fixture_readmes_list_their_values():
    assert "8733" in _FIXTURE_VALUES
    assert "samd1_chip_peaks" in _FIXTURE_VALUES
    assert "194" in _FIXTURE_VALUES


@pytest.mark.parametrize("name, prompt", sorted(_prompts().items()))
def test_no_fixture_value_appears_in_a_production_prompt(name, prompt):
    leaked = [v for v in _FIXTURE_VALUES if _found(v, prompt)]
    assert not leaked, f"the {name} prompt carries fixture values: {leaked}"


def test_the_guard_would_catch_a_fixture_count():
    assert _found("8733", 'a sentence like "8733 significant peaks" is peak_count')
    assert _found("5904", "5,904 genes")
    assert not _found("54", "0.54 and 154 and 5.4")
