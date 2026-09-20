"""plan_8_5 section 3.5: what an R source declares and uses, read from its own tokens.

C2 to C5 ask what the code requires, what it runs and where it reads its inputs from. For Python
those come from the standard library's AST. R has no AST here, so they come from the token stream,
which is the same evidence a reader of the file would use, and never from a guess about a name.
"""

from app.services.r_parser import read_r

_SOURCE = """
library(DESeq2)
require(tximport)
suppressPackageStartupMessages(library(dplyr))
source("helpers.R")

rsem_dir <- "/Volumes/valor2/users/agroff/seq"
meta <- read.table("~/Dropbox/manuscripts/metadata.txt", header = TRUE)
res <- stats::median(c(1, 2, 3))
f <- function(x) x + 1
"""


class TestWhatTheSourceDeclares:
    def test_it_finds_every_attached_package(self):
        found = read_r(_SOURCE)
        assert {"DESeq2", "tximport", "dplyr"} <= set(found["packages"])

    def test_it_finds_a_namespaced_package_that_is_never_attached(self):
        assert "stats" in read_r(_SOURCE)["namespaced"]

    def test_it_finds_the_files_the_source_reads_in(self):
        assert "helpers.R" in read_r(_SOURCE)["sourced"]

    def test_a_package_named_in_a_variable_is_not_invented(self):
        """``library(pkg, character.only = TRUE)`` names nothing bioAF can read, and it says so."""
        found = read_r("pkg <- 'DESeq2'\nlibrary(pkg, character.only = TRUE)")
        assert found["packages"] == []
        assert found["dynamic"] is True


class TestWhatTheSourceReadsAndRuns:
    def test_it_keeps_the_string_literals_so_a_path_check_can_read_them(self):
        strings = read_r(_SOURCE)["strings"]
        assert "/Volumes/valor2/users/agroff/seq" in strings
        assert "~/Dropbox/manuscripts/metadata.txt" in strings

    def test_a_source_that_only_defines_functions_runs_nothing(self):
        assert read_r("f <- function(x) x + 1\ng <- function(y) y")["runs"] is False

    def test_a_source_with_a_top_level_call_runs_something(self):
        assert read_r("f <- function(x) x\nprint(f(1))")["runs"] is True

    def test_an_assignment_from_a_call_is_work_the_script_does(self):
        assert read_r('counts <- read.table("f.tab")')["runs"] is True

    def test_a_comment_or_a_bare_name_is_not_work(self):
        assert read_r("# nothing here\nx")["runs"] is False
