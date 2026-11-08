# Run the R SDK's testthat suite against the INSTALLED bioaf package, for CI.
#
#   Rscript scripts/ci/r_sdk_tests.R <tests/testthat dir> <junit.xml> <summary.md>
#
# Fails when a mandatory test dependency is missing, or when any test fails,
# errors or skips: a skip in CI means a required package (Seurat above all) was
# not installed, and that must not pass silently. arrow is optional (the suite
# does not exercise checkpoint uploads); its absence is reported, not fatal.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: r_sdk_tests.R <tests dir> <junit.xml> <summary.md>")
tests_dir <- args[[1]]
junit_path <- args[[2]]
summary_path <- args[[3]]

# xml2 is not an SDK dependency: testthat's JUnit reporter needs it for the CI report.
required <- c("bioaf", "Seurat", "testthat", "withr", "mockery", "xml2")
optional <- c("arrow")

# A package that is installed but fails to load (a missing system library, for
# example) counts as missing: its tests could not run.
version_of <- function(pkg) {
  if (requireNamespace(pkg, quietly = TRUE)) return(as.character(utils::packageVersion(pkg)))
  if (nzchar(system.file(package = pkg))) return("installed, fails to load")
  NA_character_
}
versions <- vapply(c(required, optional), version_of, character(1))
missing <- required[is.na(versions[required]) | !grepl("^[0-9]", versions[required])]

bioaf_path <- if ("bioaf" %in% missing) NA_character_ else find.package("bioaf")

lines <- c(
  "### R SDK tests (installed package)",
  "",
  sprintf("- R: %s", R.version.string),
  sprintf("- bioaf loaded from: `%s`", bioaf_path),
  "",
  "| Package | Role | Version |",
  "| --- | --- | --- |",
  sprintf("| %s | %s | %s |", names(versions),
          ifelse(names(versions) %in% required, "required", "optional"),
          ifelse(is.na(versions), "not installed", versions))
)

finish <- function(extra, status) {
  writeLines(c(lines, "", extra), summary_path)
  writeLines(c(lines, "", extra))
  quit(status = status, save = "no")
}

if (length(missing) > 0) {
  finish(sprintf("FAILED: required packages not installed or not loadable: %s", paste(missing, collapse = ", ")), 1)
}

results <- testthat::test_dir(
  tests_dir,
  package = "bioaf",
  load_package = "installed",
  reporter = testthat::MultiReporter$new(list(
    testthat::ProgressReporter$new(show_praise = FALSE),
    testthat::JunitReporter$new(file = junit_path)
  )),
  stop_on_failure = FALSE
)
df <- as.data.frame(results)
counts <- c(
  tests = nrow(df),
  expectations = sum(df$nb),
  failed = sum(df$failed),
  errors = sum(df$error),
  skipped = sum(df$skipped)
)
skipped_tests <- df$test[df$skipped]

lines <- c(lines, "", sprintf("- %s: %d", names(counts), counts))
if (length(skipped_tests) > 0) {
  lines <- c(lines, "", "Skipped (not allowed in CI):", sprintf("- %s", skipped_tests))
}

if (counts[["tests"]] == 0 || counts[["failed"]] > 0 || counts[["errors"]] > 0 || counts[["skipped"]] > 0) {
  finish("FAILED: every test must run and pass.", 1)
}
finish("PASSED", 0)
