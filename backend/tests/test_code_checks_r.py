"""plan_8_5 section 3.5: the code section, for a paper written in R.

Every obligation in section C was a declared capability limit for an R paper, because bioAF could
parse only Python. What changes here is the parser and the reading behind it, not the rubric: the
same obligations, the same rules about what may fail, applied to the language the paper used.
"""

import pytest

from app.services.validation_code_checks import assess_code

_GOOD = {
    "path": "analysis.R",
    "language": "r",
    "text": 'library(DESeq2)\ncounts <- read.table("counts.tab", header = TRUE)\nprint(nrow(counts))\n',
}


def _outcome(assessed, leaf):
    return assessed[leaf]["outcome"]


class TestSyntaxIsReadByARealParser:
    def test_r_that_parses_verifies_the_syntax_obligation_and_names_the_parser(self):
        assessed = assess_code(sources=[_GOOD])
        assert _outcome(assessed, "C1.A") == "verified"
        assert "R" in str(assessed["C1.A"]["evidence"]["parser"])

    def test_r_that_does_not_parse_fails_it_with_the_line(self):
        broken = {**_GOOD, "text": "counts <- read.table(\nprint(1)\n"}
        assessed = assess_code(sources=[broken])
        assert _outcome(assessed, "C1.A") == "failed"
        assert assessed["C1.A"]["evidence"]["line"] >= 1
        assert assessed["C1.A"]["impact"]

    def test_a_construct_the_parser_cannot_read_is_bioafs_limit_not_the_papers_defect(self):
        unreadable = {**_GOOD, "text": "x <- 1\n§ weird §\n"}
        assessed = assess_code(sources=[unreadable])
        assert _outcome(assessed, "C1.A") == "undetermined"
        assert assessed["C1.A"]["capability_limit"] is True

    def test_a_language_with_no_parser_is_still_a_declared_limit(self):
        assessed = assess_code(sources=[{**_GOOD, "language": "julia"}])
        assert _outcome(assessed, "C1.A") == "undetermined"
        assert assessed["C1.A"]["capability_limit"] is True


class TestWhatTheSourceRequires:
    def test_attached_packages_satisfy_the_declaration_obligation(self):
        assessed = assess_code(sources=[_GOOD])
        assert _outcome(assessed, "C2.A") == "verified"
        assert "DESeq2" in str(assessed["C2.A"]["evidence"])

    def test_a_sourced_file_that_was_never_supplied_fails_it(self):
        needs = {**_GOOD, "text": 'source("helpers.R")\nlibrary(DESeq2)\n'}
        assessed = assess_code(sources=[needs])
        assert _outcome(assessed, "C2.A") == "failed"
        assert "helpers.R" in assessed["C2.A"]["rationale"]
        assert assessed["C2.A"]["impact"]

    def test_a_sourced_file_that_was_supplied_beside_it_does_not(self):
        helper = {"path": "helpers.R", "language": "r", "text": "f <- function(x) x\n"}
        needs = {**_GOOD, "text": 'source("helpers.R")\nlibrary(DESeq2)\nprint(1)\n'}
        assert _outcome(assess_code(sources=[needs, helper]), "C2.A") == "verified"

    def test_a_package_named_by_a_variable_leaves_it_untested(self):
        dynamic = {**_GOOD, "text": "pkg <- 'DESeq2'\nlibrary(pkg, character.only = TRUE)\nprint(1)\n"}
        assessed = assess_code(sources=[dynamic])
        assert _outcome(assessed, "C2.A") == "undetermined"


class TestWhatTheSourceCoversAndWhereItReads:
    def test_a_source_that_does_work_has_an_entry_point(self):
        assert _outcome(assess_code(sources=[_GOOD]), "C4.A") == "verified"

    def test_a_source_that_only_defines_functions_states_no_way_to_start(self):
        definitions = {**_GOOD, "text": "f <- function(x) x + 1\ng <- function(y) y - 1\n"}
        assert _outcome(assess_code(sources=[definitions]), "C4.A") == "undetermined"

    def test_a_path_on_one_machine_fails_the_coherence_obligation(self):
        personal = {**_GOOD, "text": 'counts <- read.table("/Volumes/valor2/users/agroff/counts.tab")\n'}
        assessed = assess_code(sources=[personal])
        assert _outcome(assessed, "C4.B") == "failed"
        assert "/Volumes/valor2/users/agroff/counts.tab" in str(assessed["C4.B"]["evidence"])
        assert assessed["C4.B"]["impact"]

    def test_a_home_relative_path_is_the_same_defect(self):
        personal = {**_GOOD, "text": 'meta <- read.table("~/Dropbox/manuscripts/metadata.txt")\n'}
        assert _outcome(assess_code(sources=[personal]), "C4.B") == "failed"

    def test_without_one_the_rest_of_coherence_stays_untested_for_r(self):
        """bioAF resolves no R name, so "every name is defined" is not established by reading it."""
        assessed = assess_code(sources=[_GOOD])
        assert _outcome(assessed, "C4.B") == "undetermined"
        assert assessed["C4.B"]["next_action"]


class TestTheEnvironmentObligationsReadAnRManifest:
    def test_a_pinned_renv_lock_verifies_the_versions_this_analysis_uses(self):
        manifest = {
            "path": "renv.lock",
            "text": '{"R": {"Version": "4.1.2"}, "Packages": {"DESeq2": {"Package": "DESeq2", "Version": "1.34.0"}}}',
        }
        assessed = assess_code(sources=[_GOOD], manifests=[manifest])
        assert _outcome(assessed, "C3.A") == "verified"
        assert _outcome(assessed, "C3.B") == "verified"

    def test_an_unpinned_package_this_analysis_uses_fails_the_repeatability_obligation(self):
        manifest = {"path": "DESCRIPTION", "text": "Imports:\n    DESeq2,\n    dplyr\n"}
        assessed = assess_code(sources=[_GOOD], manifests=[manifest])
        assert _outcome(assessed, "C3.A") == "failed"
        assert "DESeq2" in assessed["C3.A"]["rationale"]
