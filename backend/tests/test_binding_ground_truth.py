"""plan_8_2 section 1.1 through deposit selection: the authors' table the input choice identified is bound
to the selected claim's contrast before it becomes the reanalysis's ground truth or is checked against
the claims.

The input choice's model names the authors' table with no cited passage, so its identification is a
proposal: a candidate until the table's columns, a verified passage or a recorded confirmation bind it.
An unbound table is never a ground truth. The reanalysis still runs and reports its count, and the study
records why no concordance was computed.
"""

import pytest

from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_level3_service import resolve_level3_from_deposit
from tests.test_processed_reanalysis_compared import _Storage, _study, deposit  # noqa: F401 - fixture

_UNNAMED = "gene\tlog2FoldChange\tpvalue\tpadj\ng1\t1.5\t0.001\t0.02\ng2\t2.0\t0.005\t0.03\n"
_NAMED = "gene\tlog2FoldChange(KO/WT)\tpvalue\tpadj\ng1\t1.5\t0.001\t0.02\ng2\t2.0\t0.005\t0.03\n"


@pytest.mark.asyncio
async def test_an_identified_table_whose_columns_name_the_arms_is_the_ground_truth(session, admin_user, deposit):  # noqa: F811
    study, plan = await _study(session, admin_user, deposit)
    decision = await resolve_level3_from_deposit(
        session, study, plan, evidence=study.evidence_json, storage_adapter=_Storage({"s3://x/t.tsv": _NAMED})
    )
    assert decision.inputs is not None, decision.reason
    assert decision.inputs["ground_truth"]["binding"]["status"] == "established"
    assert {e["id"] for e in decision.inputs["paper_finding_set"]["entities"]} == {"g1", "g2"}


@pytest.mark.asyncio
async def test_an_identified_table_nothing_binds_is_never_the_ground_truth(session, admin_user, deposit):  # noqa: F811
    study, plan = await _study(session, admin_user, deposit)
    decision = await resolve_level3_from_deposit(
        session, study, plan, evidence=study.evidence_json, storage_adapter=_Storage({"s3://x/t.tsv": _UNNAMED})
    )
    assert decision.inputs is not None, decision.reason
    assert decision.inputs["paper_finding_set"] is None
    assert decision.inputs["ground_truth"] is None
    unbound = study.evidence_json["author_table_unbound"]
    assert unbound["filename"] == "GSE1_DESeq2.txt"
    assert unbound["binding"]["status"] == "candidate"


@pytest.mark.asyncio
async def test_the_acquired_table_is_checked_only_against_claims_it_is_bound_to(session, admin_user, deposit):  # noqa: F811
    from app.services.reproduction_plan_service import ReproductionPlanService

    study, plan = await _study(session, admin_user, deposit)
    await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                "metric_key": "",
                "claim_text": "2 genes up",
                "claimed_value": 2,
                "output_type": "gene_set_size",
                "direction": "up",
                "contrast_index": 0,
                "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}],
                "count_relation": "=",
            }
        ],
    )
    evidence = dict(study.evidence_json)
    await ValidationDriverService._check_author_table(session, evidence, plan, _Storage({"s3://x/t.tsv": _UNNAMED}))
    (unbound,) = evidence["author_consistency"]["records"]
    assert unbound["outcome"] == "unresolved" and unbound["binding"]["status"] == "candidate"
    assert unbound["rows_passing"] is None

    evidence = dict(study.evidence_json)
    await ValidationDriverService._check_author_table(session, evidence, plan, _Storage({"s3://x/t.tsv": _NAMED}))
    (bound,) = evidence["author_consistency"]["records"]
    assert bound["outcome"] == "agree" and bound["binding"]["status"] == "established"
