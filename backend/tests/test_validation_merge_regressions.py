"""Regression controls for the September 28 merge review, using held evidence only."""

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.validation_code_checks import assess_code
from app.services.validation_code_inspection import inspect_archive, inspect_code


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("code, expected", [("x <- 1\nprint(x)", "verified"), ("x <- (", "failed")])
def test_rmarkdown_inspection_parses_only_author_chunks(archive, code, expected):
    text = f"---\ntitle: Analysis\n---\nProse is not R.\n```{{r calculation}}\n{code}\n```\n"
    blob = text.encode()
    if archive:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            bundle.writestr("repo/analysis.rmd", blob)
        inspected = inspect_archive(stream.getvalue(), origin="https://example.org/repo")
    else:
        inspected = inspect_code([{"filename": "analysis.rmd", "role": "code"}], bytes_for={"analysis.rmd": blob})
    result = assess_code(sources=inspected["sources"])["C1.A"]
    assert result["outcome"] == expected
    assert inspected["sources"][0]["segments"][0]["line"] == 6
    if expected == "failed":
        assert result["evidence"]["line"] >= 6


@pytest.mark.parametrize("text", ["```{julia}\nx = 1\n```", "An inline result: `r mean(x)`", "```{r}\nx <- 1"])
def test_unsupported_or_incomplete_document_is_not_a_paper_syntax_failure(text):
    inspected = inspect_code([{"filename": "a.rmd", "role": "code"}], bytes_for={"a.rmd": text.encode()})
    result = assess_code(sources=inspected["sources"])["C1.A"]
    assert result["outcome"] == "undetermined"
    assert result["capability_limit"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["conclusion", "context", "comparison", "version", "unchanged"])
async def test_interpretation_cache_tracks_content_and_reviewer_version(monkeypatch, change):
    from app.services import validation_assessment as assessment
    from app.services import validation_interpretation_review as review

    conclusions = [{"id": "c1", "statement": "The effect is positive"}]
    context = [{"id": "m1", "text": "Adjusted p values"}]
    comparisons = [{"id": "r1", "paper": 1, "ours": 1, "agrees": True}]
    monkeypatch.setattr(assessment, "_stated_conclusions", AsyncMock(return_value=conclusions))
    monkeypatch.setattr(assessment, "_interpretation_context", lambda _: context)
    monkeypatch.setattr(assessment, "_completed_comparisons", lambda _: comparisons)
    reviewer = AsyncMock(return_value={"reviews": {"c1": {"outcome": "supported"}}})
    monkeypatch.setattr(review, "review_interpretations", reviewer)
    study = SimpleNamespace(id=1, evidence_json={})
    session = SimpleNamespace(flush=AsyncMock())
    await assessment.refresh_interpretation_review(session, study, model="m")
    if change == "conclusion":
        conclusions[0]["statement"] = "The effect is negative"
    elif change == "context":
        context[0]["text"] = "Unadjusted p values"
    elif change == "comparison":
        comparisons[0].update(ours=9, agrees=False)
    elif change == "version":
        monkeypatch.setattr(review, "REVIEW_VERSION", review.REVIEW_VERSION + 1)
    reviewer.return_value = {"reviews": {"c1": {"outcome": "contradicted"}}}
    result = await assessment.refresh_interpretation_review(session, study, model="m")
    assert reviewer.await_count == (1 if change == "unchanged" else 2)
    assert result["reviews"]["c1"]["outcome"] == ("supported" if change == "unchanged" else "contradicted")


@pytest.mark.parametrize("organism", ["Mus musculus (mESC)", "Homo sapiens"])
def test_unmapped_deposit_is_not_a_measured_species_mismatch(organism):
    from app.services.validation_rubric_evidence import assess_evidence

    result = assess_evidence(
        plan={"reported_experiments": [{"organism": organism}]},
        evidence={
            "sample_records": {
                "deposits": [{"accession": "GSE1", "samples": [{"accession": "GSM1", "organism": "Mus musculus"}]}]
            }
        },
    )
    assert result["S1.B"]["outcome"] == "undetermined"


@pytest.mark.parametrize("count", [36, 143])
def test_count_equality_or_difference_does_not_establish_membership(count):
    from app.services.validation_rubric_evidence import assess_evidence

    result = assess_evidence(
        plan={"sample_sheet": {"sample_count": count}},
        evidence={
            "sample_records": {
                "deposits": [{"accession": "GSE1", "samples": [{"accession": f"GSM{i}"} for i in range(143)]}]
            }
        },
    )
    assert result["S4.B"]["outcome"] == "undetermined"


@pytest.mark.parametrize("leaf", ["S1.B", "S4.B"])
@pytest.mark.parametrize("outcome", ["met", "unmet"])
@pytest.mark.parametrize("comparable", [True, False])
def test_sample_judgments_require_cited_common_population(leaf, outcome, comparable):
    from app.services.validation_judgment import build_request, judgment_from

    passages = [
        {"id": "p1", "kind": "design", "text": "Experiment e1 used six mouse samples GSM1-GSM6."},
        {"id": "d1", "kind": "deposit", "text": "GSE1: GSM1-GSM6, Mus musculus, six samples."},
    ]
    request = build_request(leaf, passages=passages)
    assert "sample_scope" in request["system"]
    answer = {
        "outcome": outcome,
        "rationale": "The cited experiment and deposit describe the same six samples.",
        "scope": "e1",
        "citations": ["p1", "d1"],
        "basis": "contradiction",
        "impact": "a mismatch",
        "observations": [
            {"citation": "p1", "states": "six mouse samples"},
            {"citation": "d1", "states": "the deposited records"},
        ],
        "sample_scope": {
            "comparable": comparable,
            "paper": "p1",
            "deposit": "d1",
            "membership": "GSM1-GSM6 constitute experiment e1",
        },
    }
    result = judgment_from(leaf, answer, passages=passages)
    assert result["outcome"] == (("verified" if outcome == "met" else "failed") if comparable else "undetermined")
