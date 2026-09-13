"""plan_8_1 section 1.2: an extraction answer is checked against its schema before it is read as facts.

``parse_extraction`` turned a missing key into an empty list, so an answer that omitted ``claims`` read as
"the paper has no claims", and an empty ``method: {}`` read as a paper with methods too thin to name an
assay. A missing field is a fact about the answer, never about the paper.

The schema declares each required field, its type, and whether an explicit unknown answer is allowed:

- **Core parts** (``claims``, ``method``, ``accessions``) and their required nested fields are
  structural: missing or mistyped, the answer is incomplete and the read spends its recovery attempt.
- **Every other part**, and every optional nested field, missing or mistyped is recorded as not read.
- An explicit empty collection (``claims: []``) is a complete answer, and an explicit empty or null
  value where the schema allows one (``"assay": null``) is an assessed "not stated".
"""

from app.services.validation_extraction_schema import check_extraction, rejection_note


def _complete(**overrides) -> dict:
    answer = {
        "accessions": ["GSE1"],
        "sample_structure": {"organism": "Mus musculus", "sample_count": 6},
        "method": {"assay": "bulk RNA-seq", "tools": ["DESeq2"], "reference_build": "GRCm38"},
        "reported_experiments": [
            {"id": "e1", "assay": "bulk RNA-seq", "reference": {"assembly": "GRCm38", "annotation": ""}}
        ],
        "resources": [],
        "differential_design": {"contrasts": []},
        "claims": [{"metric_key": "", "claim_text": "Hundreds of genes changed.", "value": 300}],
        "significance_ambiguities": [],
        "data_availability": "deposited",
        "code_availability": [],
        "blockers": [],
    }
    answer.update(overrides)
    return answer


class TestACompleteAnswer:
    def test_has_no_problems_and_nothing_unread(self):
        check = check_extraction(_complete())
        assert check.core_problems == []
        assert check.not_read == []
        assert check.complete

    def test_present_empty_claims_is_an_answer(self):
        check = check_extraction(_complete(claims=[]))
        assert check.complete

    def test_an_explicit_null_assay_is_an_assessed_not_stated(self):
        assert check_extraction(_complete(method={"assay": None, "tools": [], "reference_build": ""})).complete


class TestACorePartMissingOrMistyped:
    def test_missing_claims(self):
        answer = _complete()
        del answer["claims"]
        check = check_extraction(answer)
        assert check.core_problems == ["claims is missing"]
        assert not check.complete

    def test_claims_of_the_wrong_type(self):
        assert check_extraction(_complete(claims={"a": 1})).core_problems == ["claims should be a list"]

    def test_missing_accessions(self):
        answer = _complete()
        del answer["accessions"]
        assert check_extraction(answer).core_problems == ["accessions is missing"]

    def test_an_empty_method_does_not_establish_absent_methods(self):
        check = check_extraction(_complete(method={}))
        assert check.core_problems == ["method.assay is missing"]

    def test_a_claim_missing_its_claim_text(self):
        answer = _complete(claims=[{"metric_key": "", "claim_text": "A.", "value": 1}, {"metric_key": "x", "value": 2}])
        assert check_extraction(answer).core_problems == ["claims[1].claim_text is missing"]

    def test_a_claim_that_is_not_an_object(self):
        assert check_extraction(_complete(claims=["just a sentence"])).core_problems == [
            "claims[0] should be an object"
        ]


class TestAnyOtherPartIsNotRead:
    def test_missing_code_availability_is_not_read(self):
        answer = _complete()
        del answer["code_availability"]
        check = check_extraction(answer)
        assert check.complete
        assert check.not_read == ["code_availability"]

    def test_a_mistyped_part_is_not_read(self):
        check = check_extraction(_complete(sample_structure="six mice"))
        assert check.complete
        assert "sample_structure" in check.not_read

    def test_an_omitted_nested_optional_field_is_not_read(self):
        check = check_extraction(_complete(sample_structure={"organism": "Mus musculus"}))
        assert check.not_read == ["sample_structure.sample_count"]

    def test_an_omitted_reference_part_is_not_read_while_an_empty_one_is_stated_empty(self):
        experiments = [{"id": "e1", "assay": "bulk RNA-seq", "reference": {"annotation": ""}}]
        check = check_extraction(_complete(reported_experiments=experiments))
        assert check.not_read == ["reported_experiments[0].reference.assembly"]

    def test_an_omitted_method_reference_is_not_read(self):
        check = check_extraction(_complete(method={"assay": "bulk RNA-seq", "tools": []}))
        assert check.complete
        assert check.not_read == ["method.reference_build"]


class TestTheRecoveryNote:
    def test_it_names_each_rejected_field_path(self):
        note = rejection_note(["method.assay is missing", "claims[1].claim_text is missing"])
        assert "method.assay is missing" in note
        assert "claims[1].claim_text is missing" in note
        assert "complete" in note.lower()
