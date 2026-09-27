"""plan_8_7 stage 1: an entry point and a runtime token are observations, not the obligations.

The owner's September 21 assessment, on the deployed run:

    "A `main()` that prints `hello` earns C4.A. A runtime token alone earns C3.B."

C4.A asks whether the supplied entry points, scripts and configuration COVER THE ANALYSIS THE PAPER
CLAIMS. A `__main__` guard says how a file starts; it says nothing about which of the paper's steps
are in it. C3.B asks how the environment could be REBUILT. A version number says which runtime,
not how to reconstruct it or what it needs underneath.

Both narrow observations are preserved: they are what the parser established, and they travel to the
assessor that judges the obligation in the paper's context.
"""

from app.services.validation_code_checks import assess_code
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

_HELLO = "def main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n"
_RUNTIME_ONLY = {"path": "environment.txt", "text": "python 3.11\n"}
_PINNED = {"path": "requirements.txt", "text": "numpy==1.26.4\n"}


def _code(text=_HELLO, path="analysis.py", language="python"):
    return [{"path": path, "language": language, "text": text}]


class TestAnEntryPointDoesNotEstablishAnalysisCoverage:
    def test_a_declared_entry_point_that_prints_hello_earns_no_coverage_credit(self):
        found = assess_code(sources=_code())["C4.A"]
        assert found["outcome"] == UNDETERMINED
        assert found["next_action"]

    def test_the_entry_point_it_found_is_still_recorded_as_an_observation(self):
        """The parser's finding is true and stays true. It is evidence, not the answer."""
        found = assess_code(sources=_code())["C4.A"]
        assert found["observation"]["declared_entry_points"] == ["analysis.py"]
        assert found["observation"]["starts"] is True

    def test_a_bare_top_level_statement_is_the_weaker_observation(self):
        found = assess_code(sources=_code("x = 1 + 1\nprint(x)\n"))["C4.A"]
        assert found["outcome"] == UNDETERMINED
        assert found["observation"]["declared_entry_points"] == []
        assert found["observation"]["runs_top_level"] == ["analysis.py"]

    def test_a_file_of_definitions_starts_nothing_and_says_so(self):
        found = assess_code(sources=_code("def helper():\n    return 1\n"))["C4.A"]
        assert found["outcome"] == UNDETERMINED
        assert found["observation"]["starts"] is False
        assert "runs none" in found["rationale"]

    def test_an_r_source_that_does_work_is_also_only_an_observation(self):
        found = assess_code(sources=_code("library(DESeq2)\nprint(1)\n", path="a.R", language="r"))["C4.A"]
        assert found["outcome"] == UNDETERMINED
        assert found["observation"]["starts"] is True

    def test_the_second_half_of_c4_is_untouched(self):
        """C4.B is about the source standing on its own, which a parser does settle."""
        assert assess_code(sources=_code())["C4.B"]["outcome"] == VERIFIED


class TestARuntimeTokenDoesNotEstablishReconstruction:
    def test_a_runtime_version_with_no_procedure_earns_no_credit(self):
        found = assess_code(sources=_code(), manifests=[_PINNED, _RUNTIME_ONLY])["C3.B"]
        assert found["outcome"] == UNDETERMINED
        assert found["next_action"]

    def test_the_runtime_it_read_is_recorded_as_an_observation(self):
        found = assess_code(sources=_code(), manifests=[_PINNED, _RUNTIME_ONLY])["C3.B"]
        assert found["observation"]["runtime_stated"] is True
        assert found["observation"]["procedure_stated"] is False

    def test_a_runtime_and_a_rebuild_procedure_together_establish_it(self):
        docker = {"path": "Dockerfile", "text": "FROM python:3.11.8-slim\nRUN pip install -r requirements.txt\n"}
        found = assess_code(sources=_code(), manifests=[_PINNED, docker])["C3.B"]
        assert found["outcome"] == VERIFIED
        assert found["observation"]["procedure_stated"] is True

    def test_a_manifest_stating_no_runtime_at_all_is_still_an_established_omission(self):
        docker = {"path": "Dockerfile", "text": "FROM python\nRUN pip install -r r.txt\n"}
        found = assess_code(sources=_code(), manifests=[_PINNED, docker])["C3.B"]
        assert found["outcome"] == FAILED
        assert "version" in found["rationale"]

    def test_system_dependencies_the_analysis_needs_are_recorded(self):
        docker = {
            "path": "Dockerfile",
            "text": "FROM python:3.11.8-slim\nRUN apt-get install -y libhdf5-dev\nRUN pip install -r requirements.txt\n",
        }
        found = assess_code(sources=_code(), manifests=[_PINNED, docker])["C3.B"]
        assert found["observation"]["system_dependencies"] == ["libhdf5-dev"]
