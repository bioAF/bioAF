"""plan_8_5 section 3.5: an actual parser for R, so C1.A can be established for an R paper.

bioAF parses Python with the standard library's own parser. R had none, so every paper whose analysis
is written in R had its whole code section declared a capability limit, whatever source it supplied.

What this is: a tokenizer and a precedence parser over R's expression grammar. It never evaluates
anything it reads. What it is not: an R runtime. So its refusals are graded. Unbalanced delimiters
and an unterminated string are defects in the source and are reported as such. A construct the
parser does not implement is bioAF's own limit and is reported as one, because a paper must never be
told its code is broken by a parser that simply could not read it.
"""

import pytest

from app.services.r_parser import CANNOT_ESTABLISH, PARSED, REFUSED, parse_r

_VALID = [
    "x <- 1",
    "x = 1; y = 2",
    'df <- read.table("f.tab", header = TRUE, stringsAsFactors = FALSE)',
    "f <- function(a, b = 2, ...) { a + b }",
    "if (x > 1) { print('big') } else { print('small') }",
    "for (i in seq_along(files)) { message(i) }",
    "while (TRUE) { break }",
    "repeat { next }",
    "res <- lapply(files, function(f) read.csv(f))",
    "counts[, 1] <- counts[, 1] + 1",
    "m[[2]]$name <- 'x'",
    "obj@slot <- 3",
    "dds <- DESeqDataSetFromTximport(txi, colData = md, design = ~Sampletype)",
    "x %in% y",
    "a %>% filter(b > 1) %>% summarise(n = n())",
    "x <- c(1L, 2.5, 1e-3, 0x1F, TRUE, NA, NULL, Inf, NaN)",
    "`odd name` <- 1",
    "s <- 'it\\'s fine'",
    'd <- "a \\"quoted\\" thing"',
    "stats::median(x)",
    "x <- -y",
    "x <- !TRUE",
    "plot(x, y,\n     main = 'across lines')",
    "x <- 1 +\n  2",
    "result <- if (a) 1 else 2",
    "z <- x[y == 2 & !is.na(y)]",
    "names(files) <- filenames",
    "# a comment alone",
    "x <- 1 # trailing comment",
    "",
    "f(function(x) x^2)",
    "l <- list(a = 1, b = list(c = 2))",
    "x <- matrix(1:6, nrow = 2)",
    "invisible(sapply(1:3, function(i) i))",
    "tryCatch(expr = f(), error = function(e) NULL)",
    "x[['key']]",
    "quote(a + b)",
    "x <- y[i, , drop = FALSE]",
]

_BROKEN = [
    ("f <- function(a { a }", "an unclosed call"),
    ("x <- c(1, 2", "an unbalanced bracket"),
    ("s <- 'unterminated", "an unterminated string"),
    ("if (x) { print(1) ", "an unclosed block"),
    ("x <- 1 +", "an expression that ends on its operator"),
    ("x <- )", "a closing bracket with nothing open"),
]


class TestItReadsRealR:
    @pytest.mark.parametrize("source", _VALID)
    def test_valid_r_parses(self, source):
        result = parse_r(source)
        assert result["status"] == PARSED, f"{source}: {result.get('message')}"

    def test_the_result_names_the_parser_that_read_it(self):
        result = parse_r("x <- 1")
        assert result["parser"]
        assert result["language"] == "r"
        assert result["version"]


class TestABrokenSourceIsRefusedWithItsPlace:
    @pytest.mark.parametrize("source,why", _BROKEN)
    def test_broken_r_is_refused(self, source, why):
        result = parse_r(source)
        assert result["status"] == REFUSED, why
        assert result["message"]
        assert result["line"] >= 1

    def test_the_refusal_says_where_it_gave_up(self):
        result = parse_r("x <- 1\ny <- c(1, 2\nz <- 3")
        assert result["status"] == REFUSED
        assert result["line"] >= 2


class TestWhatItCannotReadIsBioafsOwnLimit:
    def test_a_construct_the_parser_does_not_implement_is_not_the_papers_defect(self):
        """A paper must never be told its code is broken by a parser that could not read it."""
        from app.services.r_parser import _grade

        assert _grade(balanced=True, certain=False) == CANNOT_ESTABLISH
        assert _grade(balanced=False, certain=False) == REFUSED
