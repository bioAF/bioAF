"""change_7.1 section 7: the report states the limitations and what was actually checked.

Study 32's report said "No pre-processed data to reproduce the finding from is published for this
paper" and nothing else. One sentence, a claim about the paper rather than the deposit, and no
statement of the checks that DID run against the paper's own attachments.
"""

from app.services.provenance.markdown_renderer import _append_completion

_COMPLETION = {
    "classification": "missing_data",
    "reason": "EGAS00001003667 publishes no pre-processed data to reproduce the finding from.",
    "limitations": [
        {
            "kind": "controlled_access",
            "resource": "EGAS00001003667",
            "operation": "pipeline",
            "detail": "EGAS00001003667 publishes raw sequencing reads under controlled access",
        },
        {
            "kind": "missing_input",
            "resource": "EGAS00001003667",
            "operation": "deposit",
            "detail": "EGAS00001003667 publishes no pre-processed data",
        },
    ],
    "processed_results_available": True,
    "reproduction_input_available": False,
    "checks_completed": ["Supplemental File S3: published results read, available for consistency checking"],
    "checks_not_completed": ["Supplemental File S9: not retrieved"],
}


class TestTheLimitationsAreAllShown:
    def test_every_limitation_appears(self):
        parts: list[str] = []
        _append_completion(parts, _COMPLETION)
        rendered = "\n".join(parts)
        assert "controlled access" in rendered
        assert "no pre-processed data" in rendered

    def test_each_one_names_its_resource_and_operation(self):
        parts: list[str] = []
        _append_completion(parts, _COMPLETION)
        rendered = "\n".join(parts)
        assert "EGAS00001003667" in rendered
        assert "pipeline" in rendered and "deposit" in rendered


class TestWhatWasCheckedIsStated:
    def test_the_completed_checks_are_listed(self):
        parts: list[str] = []
        _append_completion(parts, _COMPLETION)
        assert "Supplemental File S3" in "\n".join(parts)

    def test_the_unfinished_ones_are_listed_too(self):
        parts: list[str] = []
        _append_completion(parts, _COMPLETION)
        assert "Supplemental File S9" in "\n".join(parts)

    def test_consistency_is_not_described_as_reproduction(self):
        parts: list[str] = []
        _append_completion(parts, _COMPLETION)
        rendered = "\n".join(parts).lower()
        assert "consistency" in rendered

    def test_processed_results_and_reproduction_input_are_distinguished(self):
        parts: list[str] = []
        _append_completion(parts, _COMPLETION)
        rendered = "\n".join(parts)
        assert "Processed results published" in rendered
        assert "Reproduction input available" in rendered


def test_nothing_is_rendered_without_a_completion():
    parts: list[str] = []
    _append_completion(parts, {})
    assert parts == []
