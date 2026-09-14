"""plan_8_2 section 3.1: evidence-backed table interpretation, and a recorded confirmation when bioAF's own
evidence is insufficient.

A table's column roles, effect scale and ratio orientation come from its header, a legend, the methods, or
a person's recorded confirmation, which carries the evidence it rests on. The interpretation is stored
apart from any comparison, with its version; a comparison names the interpretation it applied. Values can
reject an interpretation (a P value above 1), never establish one. A headerless table is not deficient
for lacking one: the check says which columns it needs and waits.

SAMD1's differentiation table is headerless and linked to its contrast by filename only, so it stays
unresolved, with no assumed DESeq2 column order, until a person records that it reports the contrast and
which column is which; its check then runs, and no other claim's check is touched. The table's values here
are synthetic under the deposited layout (seven unnamed columns), per the fixture README.
"""

import gzip

import pytest
import pytest_asyncio

from app.models.audit_log import AuditLog
from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services import validation_table_binding as binding
from app.services.validation_author_consistency import check_claim
from app.services.validation_predicate import build_predicate
from app.services.validation_table_confirmations import (
    ConfirmationRefused,
    confirmation_entry,
    interpretation_of,
    latest,
)
from tests.test_binding_acceptance import _BASE, _P01, _SAMD1_CONTRASTS, _SAMD1_TABLES, _Fetcher, _count, _deposit_study

_DIFFERENTIATION = "GSE999001_DeSeq2-Differentiation-SAMD1KOvsWT.txt.gz"
# Seven unnamed columns: a gene, then six numbers. Three rows below P 0.01 in the sixth column, and the
# seventh column below 0.01 in five rows, so reading the wrong column gives a different count.
_HEADERLESS = "\n".join(
    [f"g{i}\t100\t1.5\t0.2\t7.1\t0.001\t0.004" for i in range(3)]
    + [f"h{i}\t100\t0.2\t0.2\t0.4\t0.3\t0.005" for i in range(2)]
    + [f"n{i}\t100\t0.1\t0.2\t0.1\t0.8\t0.9" for i in range(4)]
)
_CONTRAST = _SAMD1_CONTRASTS[1]["name"]
_COLUMNS = {"id": 0, "lfc": 2, "pvalue": 5, "padj": 6}


def _entry(**overrides):
    fields = {
        "table": _DIFFERENTIATION,
        "contrast": _CONTRAST,
        "reports_contrast": True,
        "columns": _COLUMNS,
        "effect_scale": "log2",
        "orientation": None,
        "selected_list": False,
        "note": "The series' README lists the columns as GeneID, baseMean, log2FC, lfcSE, stat, pvalue, padj.",
        "confirmed_by": "reviewer@example.org",
        "at": "2026-09-14T12:00:00+00:00",
    }
    return confirmation_entry(**{**fields, **overrides})


class TestARecordedConfirmation:
    def test_it_keeps_what_was_confirmed_the_evidence_and_its_version(self):
        entry = _entry()
        assert entry["version"] == 1 and entry["columns"] == _COLUMNS and entry["reports_contrast"] is True
        assert entry["note"].startswith("The series' README")

    @pytest.mark.parametrize(
        "overrides,words",
        [
            ({"note": "  "}, "evidence"),
            ({"columns": {"fold": 2}}, "role"),
            ({"columns": {"id": 0, "lfc": 0}}, "same column"),
            ({"columns": {"id": -1}}, "column"),
            ({"effect_scale": "ln"}, "scale"),
            ({"orientation": "sideways"}, "orientation"),
            ({"reports_contrast": False, "columns": None, "effect_scale": None}, "nothing"),
        ],
    )
    def test_a_confirmation_that_does_not_say_what_it_rests_on_or_says_nothing_is_refused(self, overrides, words):
        with pytest.raises(ConfirmationRefused, match=words):
            _entry(**overrides)

    def test_the_latest_for_a_table_and_contrast_applies_and_earlier_ones_stay(self):
        first, second = _entry(effect_scale="linear"), _entry()
        evidence = {"table_confirmations": [first, second]}
        assert latest(evidence, _DIFFERENTIATION, _CONTRAST) is second
        assert latest(evidence, _DIFFERENTIATION, "another contrast") is None
        assert interpretation_of(second)["columns"] == _COLUMNS

    def test_a_confirmation_of_columns_alone_does_not_bind_the_table(self):
        table = {"name": _DIFFERENTIATION, "source": "deposit"}
        contrast = _SAMD1_CONTRASTS[1]
        columns_only = _entry(reports_contrast=False)
        assert binding.bind(table, contrast, competitors=[], confirmation=columns_only)["status"] != binding.ESTABLISHED
        assert binding.bind(table, contrast, competitors=[], confirmation=_entry())["status"] == binding.ESTABLISHED


def _predicate():
    return build_predicate(
        {
            "claim_text": "3 genes",
            "claimed_value": 3,
            "output_type": "gene_set_size",
            "contrast_index": 1,
            "cutoffs": _P01,
        },
        contrast=_SAMD1_CONTRASTS[1],
    )


class TestTheCheck:
    def test_a_headerless_table_says_which_columns_it_needs_and_counts_nothing(self):
        record = check_claim({}, _predicate(), {"name": _DIFFERENTIATION, "text": _HEADERLESS})
        assert record["outcome"] == "unresolved" and record["rows_passing"] is None
        assert "headerless" in record["reason"]
        assert "7 columns" in record["reason"] and "identifier" in record["reason"] and "P value" in record["reason"]
        assert record["candidate_roles"]["pvalue"]

    def test_a_confirmed_interpretation_is_applied_and_named_on_the_record(self):
        record = check_claim(
            {},
            _predicate(),
            {"name": _DIFFERENTIATION, "text": _HEADERLESS},
            interpretation=interpretation_of(_entry()),
        )
        assert (record["outcome"], record["rows_passing"]) == ("agree", 3)
        assert record["interpretation"]["source"] == "confirmation"
        assert record["interpretation"]["version"] == 1
        assert record["interpretation"]["columns"]["pvalue"] == "column 6"

    def test_values_can_reject_an_interpretation_but_never_establish_one(self):
        wrong = interpretation_of(_entry(columns={"id": 0, "lfc": 2, "pvalue": 4, "padj": 6}))
        record = check_claim({}, _predicate(), {"name": _DIFFERENTIATION, "text": _HEADERLESS}, interpretation=wrong)
        assert record["outcome"] == "unresolved" and record["rows_passing"] is None
        assert "rejected" in record["reason"] and "column 5" in record["reason"]

    def test_a_headed_tables_interpretation_names_its_header_as_the_evidence(self):
        text = "gene\tlog2FoldChange\tpvalue\tpadj\ng1\t2\t0.001\t0.01\n"
        record = check_claim({}, _predicate(), {"name": "t.txt", "text": text})
        assert record["interpretation"]["source"] == "header"
        assert record["interpretation"]["columns"]["pvalue"] == "pvalue"


async def _samd1(session, user):
    return await _deposit_study(
        session,
        user,
        contrasts=_SAMD1_CONTRASTS,
        experiments=[
            {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [0, 1]},
            {"id": "e3", "assay": "bulk RNA-seq", "claim_indices": [2]},
        ],
        claims=[_count(257, 0, _P01, "up"), _count(524, 0, _P01, "down"), _count(3, 1, _P01)],
        tables=_SAMD1_TABLES,
        input_table="GSE999001_RNA-Seq_DeSeq2.txt.gz",
        selected=0,
    )


def _fetcher():
    return _Fetcher({_BASE + _DIFFERENTIATION: gzip.compress(_HEADERLESS.encode())})


class TestSamd1sHeaderlessDifferentiationTable:
    @pytest.mark.asyncio
    async def test_it_stays_unresolved_until_a_person_records_how_it_reads_and_then_it_is_checked(
        self, session, admin_user
    ):
        from app.services.validation_table_confirmations import record_confirmation

        study, plan, targets = await _samd1(session, admin_user)
        await consistency.enqueue(session, study, plan)
        # The two ES-cell checks are left as they are: this test is about the differentiation table.
        for record in await queue.records_for(session, study.id):
            if record.comparison_target_id != targets[2].id:
                await queue.finish(session, record, state=queue.UNRESOLVED, outcome={"outcome": "unresolved"})
        await consistency.run_pending(session, study, plan, fetcher=_fetcher())
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        unresolved = records[targets[2].id]
        assert unresolved.state == queue.UNRESOLVED and unresolved.outcome_json.get("rows_passing") is None
        before = {r.comparison_target_id: r.revision for r in records.values()}

        await record_confirmation(session, study, plan, _entry(), reason="a person recorded how the table reads")
        await session.flush()
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        assert records[targets[2].id].revision == before[targets[2].id] + 1
        assert records[targets[0].id].revision == before[targets[0].id]
        assert records[targets[1].id].revision == before[targets[1].id]

        await consistency.run_pending(session, study, plan, fetcher=_fetcher())
        checked = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}[targets[2].id]
        assert checked.state == queue.DONE
        assert (checked.outcome_json["outcome"], checked.outcome_json["rows_passing"]) == ("agree", 3)
        assert checked.outcome_json["binding"]["evidence"][0]["kind"] == "confirmation"
        assert checked.outcome_json["interpretation"]["source"] == "confirmation"
        assert checked.history_json[-1]["superseded_because"] == "a person recorded how the table reads"


class TestTheApi:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    def _body(self, **overrides):
        return {
            "table": _DIFFERENTIATION,
            "contrast": _CONTRAST,
            "reports_contrast": True,
            "columns": _COLUMNS,
            "effect_scale": "log2",
            "note": "The series' README lists the columns.",
            **overrides,
        }

    @pytest.mark.asyncio
    async def test_a_requester_records_a_confirmation_and_it_is_audited(self, session, admin_user, admin_token, client):
        study, plan, targets = await _samd1(session, admin_user)
        await session.commit()
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = await client.post(
            f"/api/validation-studies/{study.id}/table-confirmations", json=self._body(), headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["confirmation"]["columns"] == _COLUMNS
        await session.refresh(study)
        (stored,) = study.evidence_json["table_confirmations"]
        assert stored["confirmed_by"] and stored["note"] == "The series' README lists the columns."
        audit = (
            await session.execute(
                AuditLog.__table__.select().where(
                    AuditLog.entity_type == "validation_study",
                    AuditLog.entity_id == study.id,
                    AuditLog.action == "table_confirmation",
                )
            )
        ).all()
        assert len(audit) == 1

    @pytest.mark.asyncio
    async def test_a_viewer_may_not_record_one(self, session, admin_user, viewer_token, client):
        study, plan, targets = await _samd1(session, admin_user)
        await session.commit()
        headers = {"Authorization": f"Bearer {viewer_token}"}
        r = await client.post(
            f"/api/validation-studies/{study.id}/table-confirmations", json=self._body(), headers=headers
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides",
        [{"note": ""}, {"table": "a table this study does not list.txt"}, {"contrast": "a contrast it does not have"}],
    )
    async def test_an_unsupported_or_unknown_confirmation_is_refused(
        self, session, admin_user, admin_token, client, overrides
    ):
        study, plan, targets = await _samd1(session, admin_user)
        await session.commit()
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = await client.post(
            f"/api/validation-studies/{study.id}/table-confirmations", json=self._body(**overrides), headers=headers
        )
        assert r.status_code == 422
        await session.refresh(study)
        assert "table_confirmations" not in (study.evidence_json or {})


class TestTheReportRow:
    def test_a_confirmed_reading_is_shown_with_who_recorded_it_and_why(self):
        from app.services.validation_report_summary import _consistency_row

        record = check_claim(
            {},
            _predicate(),
            {"name": _DIFFERENTIATION, "text": _HEADERLESS},
            interpretation=interpretation_of(_entry()),
        )
        row = _consistency_row({**record, "binding": {"status": "established", "version": 1, "evidence": []}})
        assert row["interpretation"]["source"] == "confirmation"
        assert row["interpretation"]["evidence"][0].startswith("recorded by reviewer@example.org: The series' README")

    def test_a_headerless_table_offers_its_candidate_columns(self):
        from app.services.validation_report_summary import _consistency_row

        record = check_claim({}, _predicate(), {"name": _DIFFERENTIATION, "text": _HEADERLESS})
        row = _consistency_row({**record, "binding": {"status": "established", "version": 1, "evidence": []}})
        assert row["candidate_roles"]["pvalue"] == [3, 5, 6]
        assert row["columns_count"] == 7


def test_the_markdown_export_says_how_a_person_recorded_the_table_reads():
    from app.services.provenance.markdown_renderer import _append_each_claim

    summary = {
        "claims": [
            {
                "description": "3 genes",
                "checks": [{"label": "Consistency with the authors' results", "status": "available"}],
                "consistency": {
                    "label": "Consistent with the authors' deposited results",
                    "table": _DIFFERENTIATION,
                    "rows_passing": 3,
                    "rows_tested": 9,
                    "interpretation": {
                        "source": "confirmation",
                        "evidence": ["recorded by reviewer@example.org: README"],
                    },
                },
            }
        ]
    }
    parts: list[str] = []
    _append_each_claim(parts, summary)
    assert "  - Table read as recorded by reviewer@example.org: README" in "\n".join(parts)
