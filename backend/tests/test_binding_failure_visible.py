"""change_7.1 section 3: a binding the model never made must not become a silent comparison.

When the binding call returned something unparseable, `bind_claims` returned an empty list and
every target kept `bound_by="alias_table"` with a NULL `bound_key`. The comparison then resolved
the claim through the alias table anyway and reported a verdict. Groff's claim binding failed
exactly this way, and the study still showed comparisons as though a model had approved them.

**A failure and a decline are different.** Declining is a correct answer the model gives on
purpose, and the alias table is the right fallback for it. A transport error or an unreadable
response is not an answer at all, and treating it as one manufactures agreement out of an outage.
"""

import pytest

from app.services.validation_classifier_service import compare_targets
from app.services.validation_extraction_service import BINDING_FAILED, bind_claims


class _Decision:
    def __init__(self, ok, text=""):
        self.ok, self.text = ok, text

    def as_issue(self, impact):
        return {"step": "binding", "outcome": "unreachable", "impact": impact, "message": "boom", "model": None}


class TestAFailedBindingIsMarked:
    @pytest.mark.asyncio
    async def test_an_unparseable_response_marks_every_claim(self, monkeypatch):
        from app.services import validation_extraction_service as ext

        async def _decide(**_kw):
            return _Decision(ok=False)

        monkeypatch.setattr(ext, "decide", _decide)
        decisions = await bind_claims(
            [{"metric_key": "transcripts detected", "value": 10500}], client=None, model="m", api_key=None
        )
        assert [d["bound_by"] for d in decisions] == [BINDING_FAILED]

    @pytest.mark.asyncio
    async def test_the_failure_still_reaches_the_issues_list(self, monkeypatch):
        from app.services import validation_extraction_service as ext

        async def _decide(**_kw):
            return _Decision(ok=False)

        monkeypatch.setattr(ext, "decide", _decide)
        issues: list[dict] = []
        await bind_claims(
            [{"metric_key": "transcripts detected", "value": 10500}],
            client=None,
            model="m",
            api_key=None,
            on_issue=issues.append,
        )
        assert issues


class TestAFailedBindingIsNotComparedThroughTheAliasTable:
    def test_a_claim_whose_binding_failed_is_not_given_a_verdict(self):
        """`total_genes_detected` is in the alias table, so before this the claim compared cleanly
        and the report showed agreement nobody had established."""
        rows = compare_targets(
            [
                {
                    "metric_key": "total_genes_detected",
                    "claimed_value": 10500,
                    "bound_key": None,
                    "bound_by": BINDING_FAILED,
                }
            ],
            {"total_genes_detected": 10500},
        )
        assert rows[0]["verdict"] == "not_compared"
        assert rows[0]["mapped_key"] is None

    def test_the_reason_says_the_binding_failed(self):
        rows = compare_targets(
            [{"metric_key": "total_genes_detected", "claimed_value": 10500, "bound_by": BINDING_FAILED}],
            {"total_genes_detected": 10500},
        )
        assert "could not be established" in (rows[0]["advisory_reason"] or "")

    def test_a_declined_binding_still_falls_back_to_the_alias_table(self):
        """Declining is a real answer, deliberately given. The alias table is the right fallback
        for it and this change must not take that away."""
        rows = compare_targets(
            [
                {
                    "metric_key": "total_genes_detected",
                    "claimed_value": 10500,
                    "bound_key": None,
                    "bound_by": "alias_table",
                }
            ],
            {"total_genes_detected": 10500},
        )
        assert rows[0]["mapped_key"] == "total_genes_detected"
        assert rows[0]["verdict"] is not None
        assert rows[0]["verdict"] != "not_compared"
