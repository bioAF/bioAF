"""plan_8_4 milestone B: the code section, assessed from the source a paper actually supplied.

Static inspection only. Parsing is done by a real parser, never by a regular expression that thinks
it knows a language; loading, installing, compiling and running are none of this module's business,
because they execute untrusted code and belong behind the existing isolated execution path and its
approval. Those obligations stay undetermined here and say why.

Section 3.4's rule is the sharp edge: a style, a format, a lint preference, deprecated-but-working
syntax and the mere age of a tool never cost a point. A failure names a specific unmet obligation with
its cause and its impact.
"""

from app.services.validation_code_checks import SUPPORTED_LANGUAGES, assess_code
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

_GOOD = """
import os
import numpy as np
import pandas as pd


def load(path):
    return pd.read_csv(path)


def main():
    frame = load(os.environ["INPUT"])
    print(np.mean(frame["value"]))


if __name__ == "__main__":
    main()
"""

_MANIFEST = {"path": "requirements.txt", "text": "numpy==1.26.4\npandas==2.2.1\n"}
_ENVIRONMENT = {"path": "Dockerfile", "text": "FROM python:3.11.8-slim\nRUN pip install -r requirements.txt\n"}


def _sources(text=_GOOD, path="analysis.py", language="python"):
    return [{"path": path, "language": language, "text": text}]


def _assess(**kw):
    return assess_code(
        sources=kw.pop("sources", _sources()),
        manifests=kw.pop("manifests", [_MANIFEST, _ENVIRONMENT]),
        defects=kw.pop("defects", None),
        execution=kw.pop("execution", None),
        **kw,
    )


class TestAParserDecidesWhetherTheSourceParses:
    def test_source_that_parses_verifies_the_syntax_obligation(self):
        found = _assess()["C1.A"]
        assert found["outcome"] == VERIFIED
        assert found["method"] == "measurement"
        assert "analysis.py" in found["scope"]

    def test_a_real_syntax_error_fails_it_and_names_where(self):
        found = _assess(sources=_sources("def main(:\n    pass\n"))["C1.A"]
        assert found["outcome"] == FAILED
        assert "analysis.py" in found["rationale"]
        assert found["impact"]

    def test_deprecated_but_working_syntax_costs_nothing(self):
        """Section 3.4: a deprecated-but-functional construct is not a defect. `is` on a literal warns
        on modern Python and parses and runs."""
        found = _assess(sources=_sources("x = 1\nif x is 1:\n    print(x)\n"))["C1.A"]
        assert found["outcome"] == VERIFIED

    def test_a_language_with_no_declared_parser_is_undetermined_not_failed(self):
        """Section 6.2: an explicit language capability declaration. plan_8_5 section 3.5 added R,
        which is what study 55 supplied; Julia is a language bioAF still cannot parse, and its syntax
        is unknown rather than wrong."""
        assert "julia" not in SUPPORTED_LANGUAGES
        found = _assess(sources=_sources("f(x) = x + 1\n", path="code.jl", language="julia"))["C1.A"]
        assert found["outcome"] == UNDETERMINED
        assert "julia" in found["rationale"].lower()

    def test_the_language_it_can_parse_is_declared_rather_than_assumed(self):
        assert "python" in SUPPORTED_LANGUAGES and "r" in SUPPORTED_LANGUAGES

    def test_loading_and_building_stay_undetermined_behind_the_approval(self):
        found = _assess()["C1.B"]
        assert found["outcome"] == UNDETERMINED
        assert "execut" in found["rationale"]


class TestImportsAreCheckedAgainstWhatWasDeclared:
    def test_every_import_declared_or_standard_verifies_the_obligation(self):
        assert _assess()["C2.A"]["outcome"] == VERIFIED

    def test_an_import_nothing_declares_fails_with_the_name(self):
        found = _assess(sources=_sources("import scanpy\nprint(scanpy)\n"))["C2.A"]
        assert found["outcome"] == FAILED
        assert "scanpy" in found["rationale"]

    def test_a_module_another_supplied_file_defines_is_not_missing(self):
        sources = [
            {"path": "main.py", "language": "python", "text": "import helpers\nprint(helpers)\n"},
            {"path": "helpers.py", "language": "python", "text": "VALUE = 1\n"},
        ]
        assert _assess(sources=sources)["C2.A"]["outcome"] == VERIFIED

    def test_a_relative_import_is_not_a_missing_package(self):
        sources = [{"path": "pkg/main.py", "language": "python", "text": "from . import util\nprint(util)\n"}]
        assert _assess(sources=sources)["C2.A"]["outcome"] == VERIFIED

    def test_dynamic_import_machinery_leaves_it_undetermined_rather_than_failing(self):
        """Section 3.4: dynamic imports must be considered before declaring an import missing."""
        found = _assess(sources=_sources("import importlib\nm = importlib.import_module('scanpy')\n"))["C2.A"]
        assert found["outcome"] == UNDETERMINED
        assert "dynamic" in found["rationale"]

    def test_resolving_versions_stays_undetermined_behind_the_approval(self):
        assert _assess()["C2.B"]["outcome"] == UNDETERMINED


class TestTheEnvironmentIsJudgedOnWhatWasInspected:
    def test_pinned_result_sensitive_dependencies_verify_it(self):
        assert _assess()["C3.A"]["outcome"] == VERIFIED

    def test_an_imported_package_the_manifest_leaves_unpinned_fails_it(self):
        found = _assess(manifests=[{"path": "requirements.txt", "text": "numpy\npandas==2.2.1\n"}, _ENVIRONMENT])[
            "C3.A"
        ]
        assert found["outcome"] == FAILED
        assert "numpy" in found["rationale"]

    def test_an_unpinned_package_the_source_never_imports_costs_nothing(self):
        """Section 3.4: a generic "not every package is pinned" lint rule is insufficient. Only a
        dependency this analysis actually uses is result-sensitive."""
        manifests = [{"path": "requirements.txt", "text": "numpy==1.26.4\npandas==2.2.1\nblack\n"}, _ENVIRONMENT]
        assert _assess(manifests=manifests)["C3.A"]["outcome"] == VERIFIED

    def test_no_manifest_at_all_is_undetermined_rather_than_failed(self):
        assert _assess(manifests=[])["C3.A"]["outcome"] == UNDETERMINED

    def test_a_runtime_specification_verifies_the_reconstruction_obligation(self):
        assert _assess()["C3.B"]["outcome"] == VERIFIED

    def test_an_inspected_environment_spec_stating_no_runtime_version_fails_it(self):
        found = _assess(
            manifests=[_MANIFEST, {"path": "Dockerfile", "text": "FROM python\nRUN pip install -r r.txt\n"}]
        )["C3.B"]
        assert found["outcome"] == FAILED
        assert "version" in found["rationale"]

    def test_an_old_but_pinned_version_is_not_a_defect(self):
        """Pinning an older version can support faithful reproduction (section 3.4)."""
        manifests = [{"path": "requirements.txt", "text": "numpy==1.11.0\npandas==0.19.2\n"}, _ENVIRONMENT]
        found = _assess(manifests=manifests)
        assert found["C3.A"]["outcome"] == VERIFIED
        assert found["C5.B"]["outcome"] != FAILED


class TestExecutionCompleteness:
    def test_an_entry_point_and_a_declared_step_verify_it(self):
        assert _assess()["C4.A"]["outcome"] == VERIFIED

    def test_source_with_no_entry_point_at_all_is_undetermined(self):
        found = _assess(sources=_sources("def helper():\n    return 1\n"))["C4.A"]
        assert found["outcome"] == UNDETERMINED

    def test_coherent_source_verifies_the_second_obligation(self):
        assert _assess()["C4.B"]["outcome"] == VERIFIED

    def test_a_name_nothing_binds_fails_it(self):
        """A call to something neither defined, imported nor built in would raise the moment that line
        runs. That is an incoherence in what was supplied, not a style preference."""
        found = _assess(sources=_sources("def main():\n    return normalise(1)\n\nmain()\n"))["C4.B"]
        assert found["outcome"] == FAILED
        assert "normalise" in found["rationale"]

    def test_a_hard_coded_path_from_the_authors_machine_fails_it(self):
        found = _assess(sources=_sources("open('/Users/someone/Desktop/data.csv')\n"))["C4.B"]
        assert found["outcome"] == FAILED
        assert "/Users/someone/Desktop/data.csv" in found["rationale"]


class TestToolFitnessRestsOnAnEvidenceBackedReview:
    def test_no_review_leaves_both_obligations_undetermined(self):
        found = _assess()
        assert found["C5.A"]["outcome"] == UNDETERMINED
        assert found["C5.B"]["outcome"] == UNDETERMINED

    def test_a_review_that_found_the_operation_appropriate_verifies_the_first(self):
        review = {
            "kind": "fitness",
            "established": True,
            "operation": "pandas.read_csv",
            "evidence": "the docs",
            "verdict": "appropriate",
        }
        assert _assess(defects=[review])["C5.A"]["outcome"] == VERIFIED

    def test_an_established_defect_in_an_operation_the_code_uses_fails_the_second(self):
        defect = {
            "kind": "defect",
            "established": True,
            "operation": "pandas.read_csv",
            "version": "2.2.1",
            "evidence": "the release notes for 2.2.1 record it",
            "impact": "the values this analysis reads are wrong",
        }
        found = _assess(defects=[defect])["C5.B"]
        assert found["outcome"] == FAILED
        assert "pandas.read_csv" in found["rationale"]
        assert found["impact"]

    def test_an_unestablished_concern_leaves_it_undetermined_rather_than_failing(self):
        concern = {"kind": "defect", "established": False, "operation": "pandas.read_csv", "evidence": "a forum post"}
        assert _assess(defects=[concern])["C5.B"]["outcome"] == UNDETERMINED

    def test_a_defect_whose_only_ground_is_the_version_being_old_is_refused(self):
        stale = {
            "kind": "defect",
            "established": True,
            "operation": "pandas.read_csv",
            "evidence": "the version is old",
        }
        found = _assess(defects=[stale])["C5.B"]
        assert found["outcome"] == UNDETERMINED
        assert "age" in found["rationale"]


class TestTheExecutionGatedObligationsNeedARecordedResult:
    """Section 6.2: loading imports, installation, compilation and smoke execution run untrusted code
    and use the existing isolated execution path. Static inspection never stands in for them, and a
    recorded result from that path is what verifies them."""

    def test_without_a_recorded_result_they_stay_undetermined(self):
        found = _assess()
        assert found["C1.B"]["outcome"] == UNDETERMINED
        assert found["C2.B"]["outcome"] == UNDETERMINED

    def test_a_recorded_successful_load_verifies_the_build_obligation(self):
        found = _assess(execution={"load": {"status": "succeeded", "environment": "python:3.11.8", "ref": "run-1"}})
        assert found["C1.B"]["outcome"] == VERIFIED
        assert "run-1" in str(found["C1.B"]["evidence"])

    def test_a_recorded_failed_load_fails_it_with_what_the_run_said(self):
        found = _assess(
            execution={"load": {"status": "failed", "reason": "ImportError: no module named scanpy", "ref": "run-2"}}
        )["C1.B"]
        assert found["outcome"] == FAILED
        assert "scanpy" in found["rationale"]

    def test_a_recorded_dependency_resolution_verifies_the_second_obligation(self):
        found = _assess(execution={"dependency_resolution": {"status": "succeeded", "ref": "run-3"}})
        assert found["C2.B"]["outcome"] == VERIFIED


class TestTheOwnersCodeExample:
    """Milestone B's stated acceptance: correct syntax, dependencies, environment and completeness,
    plus one documented relevant defect, gives 18 / 20 as 18 verified and 2 failed. The same evidence
    with the defect downgraded to an unverified concern gives 18 verified and 2 undetermined.

    The fixture is controlled: it supplies the recorded results of the isolated execution path for the
    obligations that need one, because those cannot be established by reading the source."""

    _EXECUTION = {
        "load": {"status": "succeeded", "environment": "python:3.11.8", "ref": "run-1"},
        "dependency_resolution": {"status": "succeeded", "ref": "run-1"},
    }

    _FITNESS = {
        "kind": "fitness",
        "established": True,
        "operation": "pandas.read_csv",
        "evidence": "the documented behaviour for this input",
        "verdict": "appropriate",
    }

    def _card(self, defects):
        from app.services.validation_rubric_v3 import allocate, default_profile, score

        assessed = assess_code(
            sources=_sources(), manifests=[_MANIFEST, _ENVIRONMENT], defects=defects, execution=self._EXECUTION
        )
        # The other obligations are not this example's subject; only the code section is scored.
        leaves = [leaf for leaf in allocate(default_profile()) if leaf["section"] == "C"]
        return score(leaves, assessed)["sections"]["C"]

    def test_a_documented_relevant_defect_gives_eighteen_verified_and_two_failed(self):
        defect = {
            "kind": "defect",
            "established": True,
            "operation": "pandas.read_csv",
            "version": "2.2.1",
            "evidence": "the release notes for 2.2.1 record it",
            "impact": "the values this analysis reads are wrong",
        }
        code = self._card([self._FITNESS, defect])
        assert (code["verified"], code["failed"], code["undetermined"]) == (18, 2, 0)

    def test_an_unverified_concern_gives_eighteen_verified_and_two_undetermined(self):
        concern = {"kind": "defect", "established": False, "operation": "pandas.read_csv", "evidence": "a forum post"}
        code = self._card([self._FITNESS, concern])
        assert (code["verified"], code["failed"], code["undetermined"]) == (18, 0, 2)

    def test_tool_age_alone_changes_neither_number(self):
        stale = {
            "kind": "defect",
            "established": True,
            "operation": "pandas.read_csv",
            "evidence": "it is five years old",
        }
        assert self._card([self._FITNESS, stale]) == self._card([self._FITNESS])


class TestAWildcardImportIsNotAnUndefinedName:
    """The owner, 2026-09-21: C4.B's Cytoflow deduction is a FALSE NEGATIVE.

    The notebook begins `from cytoflow import *`, and that module exports `Tube`, `ImportOp`,
    `PolygonOp` and the rest. bioAF's static checker does not resolve wildcard imports, concluded
    the names were undefined, and asserted that "the analysis stops at that line" -- a claim about
    EXECUTION it never performed. A wildcard import means bioAF cannot say what is bound, which is
    not the same fact as a name being unbound.
    """

    def _sources(self, text):
        return [{"path": "flow/cytoflow_example.ipynb", "language": "python", "text": text}]

    def test_names_that_may_come_from_a_wildcard_are_not_reported_as_undefined(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=self._sources("from cytoflow import *\nt = Tube(file='a.fcs')\nImportOp([t])\n"))
        assert found["C4.B"]["outcome"] != "failed"

    def test_the_wildcard_is_recorded_as_why_bioaf_cannot_say(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=self._sources("from cytoflow import *\nTube(file='a.fcs')\n"))
        assert "cytoflow" in found["C4.B"]["rationale"]
        assert "*" in found["C4.B"]["rationale"] or "wildcard" in found["C4.B"]["rationale"]
        assert found["C4.B"]["next_action"]

    def test_a_genuinely_undefined_name_is_still_a_finding(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=self._sources("import os\nresult = undefined_helper(os.getcwd())\n"))
        assert found["C4.B"]["outcome"] == "failed"
        assert "undefined_helper" in found["C4.B"]["rationale"]

    def test_no_claim_is_made_about_where_execution_would_stop(self):
        """bioAF did not run it. `impact` may say what a reader cannot check, not what the run does."""
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=self._sources("import os\nresult = undefined_helper(os.getcwd())\n"))
        assert "stops at that line" not in (found["C4.B"].get("impact") or "")


class TestAPositivePointCannotContradictANegativeOne:
    """The owner, 2026-09-21: C3.A said every dependency this analysis uses is declared at a fixed
    version while C2.A was simultaneously reporting undeclared dependencies. The implementation
    checked whether DECLARED, used packages are pinned and overlooked used packages absent from the
    manifest entirely."""

    _MANIFEST = [{"path": "env.yml", "text": "name: x\ndependencies:\n  - scanpy=1.8.2\n"}]
    _SOURCE = [{"path": "a.py", "language": "python", "text": "import scanpy\nimport bioinfokit\nbioinfokit.x()\n"}]

    def test_an_undeclared_dependency_stops_c3_a_claiming_everything_is_pinned(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=self._SOURCE, manifests=self._MANIFEST)
        assert found["C2.A"]["outcome"] == "failed", "the undeclared package is what C2.A is for"
        assert found["C3.A"]["outcome"] != "verified"
        assert "bioinfokit" in found["C3.A"]["rationale"]

    def test_a_fully_declared_and_pinned_analysis_still_verifies_c3_a(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(
            sources=[{"path": "a.py", "language": "python", "text": "import scanpy\nscanpy.pp.pca(1)\n"}],
            manifests=self._MANIFEST,
        )
        assert found["C3.A"]["outcome"] == "verified"


class TestCoverageIsNotTheMerePresenceOfAStatement:
    """The owner, 2026-09-21: C4.A awarded complete-analysis coverage merely because the scripts
    contain executable statements. The rubric asks whether the supplied entry points, scripts and
    configuration COVER THE CLAIMED ANALYSIS STEPS, which a top-level assignment does not establish."""

    def test_a_script_that_merely_runs_does_not_establish_coverage_on_its_own(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=[{"path": "a.py", "language": "python", "text": "x = 1 + 1\nprint(x)\n"}])
        assert found["C4.A"]["outcome"] != "verified"
        assert found["C4.A"]["next_action"]

    def test_the_rationale_says_what_was_and_was_not_established(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=[{"path": "a.py", "language": "python", "text": "x = 1 + 1\nprint(x)\n"}])
        assert "top-level" in found["C4.A"]["rationale"]
        assert "cover" in found["C4.A"]["rationale"] or "steps" in found["C4.A"]["rationale"]

    def test_a_declared_entry_point_is_a_different_fact_and_still_verifies(self):
        """A `__main__` guard says THIS is how the analysis starts. Whether it covers the claimed
        steps is a further question flagged to the owner, not silently changed here."""
        from app.services.validation_code_checks import assess_code

        text = "def main():\n    return 1\n\n\nif __name__ == '__main__':\n    main()\n"
        found = assess_code(sources=[{"path": "a.py", "language": "python", "text": text}])
        assert found["C4.A"]["outcome"] == "verified"

    def test_a_source_that_starts_nothing_is_still_the_weaker_statement(self):
        from app.services.validation_code_checks import assess_code

        found = assess_code(sources=[{"path": "a.py", "language": "python", "text": "def helper():\n    return 1\n"}])
        assert found["C4.A"]["outcome"] != "verified"
        assert "runs none" in found["C4.A"]["rationale"]
