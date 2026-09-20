"""plan_8_5 section 3.5: an actual parser for R, so C1.A can be settled for a paper written in R.

Python is parsed with the standard library's own parser. R had nothing, so every paper whose analysis
is R had its entire code section declared a capability limit however much source it supplied, and
Groff's supplement (five R Markdown documents, a hundred chunks) sat unread.

This is a tokenizer and a precedence parser over R's expression grammar. **It never evaluates what it
reads**: no source is imported, sourced, installed or run, which is what keeps a syntax check out of
the isolated execution path and its approval.

Its refusals are graded, because a parser is also capable of being wrong:

- **refused** where the source itself is broken: an unterminated string, an unbalanced delimiter, a
  closing bracket with nothing open, an expression that ends on its operator. These are defects in
  what was supplied, and rubric v3 lets them fail an obligation.
- **cannot establish** where the grammar here ran out: the delimiters balance and the literals close,
  and the parser simply met something it does not implement. That is bioAF's limit, and a paper is
  never told its code is broken by a reader that could not read it.
"""

from __future__ import annotations

import re

PARSER = "bioAF R parser"
PARSER_VERSION = 1
LANGUAGE = "r"

PARSED = "parsed"
REFUSED = "refused"
CANNOT_ESTABLISH = "cannot_establish"


class _Refused(Exception):
    """The source is broken, and the words say where and why."""

    def __init__(self, message: str, line: int, column: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


class _Unreadable(Exception):
    """The parser ran out of grammar. bioAF's limit, not the paper's."""

    def __init__(self, message: str, line: int, column: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


# ---- tokens --------------------------------------------------------------------------------------

_NUMBER = re.compile(r"(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)[iL]?")
_NAME = re.compile(r"[A-Za-z.][A-Za-z0-9._]*")
_SPECIAL = re.compile(r"%[^%\n]*%")
# Longest first: a two-character operator must never be read as two one-character ones.
_OPERATORS = (
    "<<-",
    "->>",
    ":::",
    "<-",
    "->",
    "<=",
    ">=",
    "==",
    "!=",
    "&&",
    "||",
    "::",
    "|>",
    "**",
    "=",
    "<",
    ">",
    "+",
    "-",
    "*",
    "/",
    "^",
    "!",
    "&",
    "|",
    "~",
    "?",
    ":",
    "@",
    "$",
)
_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {")", "]", "}"}


class _Token:
    __slots__ = ("kind", "text", "line", "column")

    def __init__(self, kind: str, text: str, line: int, column: int):
        self.kind = kind
        self.text = text
        self.line = line
        self.column = column

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.kind} {self.text!r} line {self.line}>"


def _string_token(source: str, i: int, line: int, column: int) -> tuple[_Token, int, int]:
    """A quoted string, including R's raw strings (``r"(...)"``). Raises where it never closes."""
    start_line = line
    quote = source[i]
    raw = False
    if quote in "rR" and i + 1 < len(source) and source[i + 1] in "\"'":
        raw = True
        quote = source[i + 1]
        j = i + 2
        dashes = 0
        while j < len(source) and source[j] == "-":
            dashes += 1
            j += 1
        if j >= len(source) or source[j] not in "([{":
            raise _Unreadable("a raw string bioAF's parser cannot read", line, column)
        closing = {"(": ")", "[": "]", "{": "}"}[source[j]] + "-" * dashes + quote
        end = source.find(closing, j + 1)
        if end == -1:
            raise _Refused("a raw string is never closed", start_line, column)
        text = source[i : end + len(closing)]
        return _Token("string", text, start_line, column), end + len(closing), line + text.count("\n")
    j = i + 1
    while j < len(source):
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            text = source[i : j + 1]
            return _Token("string", text, start_line, column), j + 1, line + text.count("\n")
        if c == "\n":
            line += 1
        j += 1
    raise _Refused("a string is never closed", start_line, column)


def tokenize(source: str) -> list[_Token]:
    """R's tokens, with newlines kept: in R a newline can end a statement."""
    tokens: list[_Token] = []
    opened: list[str] = []
    i = 0
    line = 1
    line_start = 0
    length = len(source)
    while i < length:
        c = source[i]
        column = i - line_start + 1
        if c == "\n":
            tokens.append(_Token("newline", "\n", line, column))
            i += 1
            line += 1
            line_start = i
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        if c == "#":
            while i < length and source[i] != "\n":
                i += 1
            continue
        if c in "\"'" or (c in "rR" and i + 1 < length and source[i + 1] in "\"'"):
            token, i, line = _string_token(source, i, line, column)
            tokens.append(token)
            continue
        if c == "`":
            end = source.find("`", i + 1)
            if end == -1:
                raise _Refused("a backtick name is never closed", line, column)
            tokens.append(_Token("name", source[i : end + 1], line, column))
            i = end + 1
            continue
        if c.isdigit() or (c == "." and i + 1 < length and source[i + 1].isdigit()):
            match = _NUMBER.match(source, i)
            tokens.append(_Token("number", match.group(0), line, column))
            i = match.end()
            continue
        if c == "%":
            match = _SPECIAL.match(source, i)
            if match is None:
                raise _Refused("a %special% operator is never closed", line, column)
            tokens.append(_Token("operator", match.group(0), line, column))
            i = match.end()
            continue
        if c == "_" or c.isalpha() or c == ".":
            match = _NAME.match(source, i)
            if match is None:
                raise _Unreadable(f"bioAF's parser does not know the token {c!r}", line, column)
            tokens.append(_Token("name", match.group(0), line, column))
            i = match.end()
            continue
        if c in _OPEN:
            if c == "[" and source.startswith("[[", i):
                opened.append("[[")
                tokens.append(_Token("open", "[[", line, column))
                i += 2
                continue
            opened.append(c)
            tokens.append(_Token("open", c, line, column))
            i += 1
            continue
        if c in _CLOSE:
            # ``]]`` is one token only where it closes a ``[[``. Study 55's own code has
            # ``binary_TPM[, colnames(x) %in% metadata[...,"ProcessingID"]]``, where the two
            # brackets close two separate single-bracket indexes, and reading them as one token
            # refused source that is perfectly good R.
            if c == "]" and source.startswith("]]", i) and opened and opened[-1] == "[[":
                opened.pop()
                tokens.append(_Token("close", "]]", line, column))
                i += 2
                continue
            if opened:
                opened.pop()
            tokens.append(_Token("close", c, line, column))
            i += 1
            continue
        if c in ",;":
            tokens.append(_Token(c, c, line, column))
            i += 1
            continue
        for operator in _OPERATORS:
            if source.startswith(operator, i):
                tokens.append(_Token("operator", operator, line, column))
                i += len(operator)
                break
        else:
            raise _Unreadable(f"bioAF's parser does not know the token {c!r}", line, column)
    tokens.append(_Token("end", "", line, 1))
    return tokens


# ---- grammar -------------------------------------------------------------------------------------
#
# R's operator precedence, loosest first. `<-` is right-associative and binds looser than everything
# but `?`; `=` in an expression is an assignment of the same shape. Unary operators are handled in
# the prefix position below.

_BINARY: dict[str, tuple[int, str]] = {
    "?": (1, "left"),
    "=": (2, "right"),
    "<-": (3, "right"),
    "<<-": (3, "right"),
    "->": (4, "left"),
    "->>": (4, "left"),
    "~": (5, "left"),
    "||": (6, "left"),
    "|": (6, "left"),
    "&&": (7, "left"),
    "&": (7, "left"),
    "==": (9, "left"),
    "!=": (9, "left"),
    "<": (9, "left"),
    ">": (9, "left"),
    "<=": (9, "left"),
    ">=": (9, "left"),
    "+": (10, "left"),
    "-": (10, "left"),
    "*": (11, "left"),
    "/": (11, "left"),
    "|>": (12, "left"),
    ":": (13, "left"),
    "^": (16, "right"),
    "$": (17, "left"),
    "@": (17, "left"),
    "::": (18, "left"),
    ":::": (18, "left"),
}
_UNARY = {"-": 14, "+": 14, "!": 8, "~": 5, "?": 1}
_KEYWORDS = {"if", "for", "while", "repeat", "function", "break", "next", "else", "in"}
_CONSTANTS = {"TRUE", "FALSE", "NA", "NULL", "Inf", "NaN", "NA_integer_", "NA_real_", "NA_character_", "T", "F"}


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.i = 0

    # -- token helpers
    @property
    def token(self) -> _Token:
        return self.tokens[self.i]

    def advance(self) -> _Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def skip_newlines(self) -> None:
        while self.token.kind == "newline":
            self.i += 1

    def skip_separators(self) -> None:
        while self.token.kind in ("newline", ";"):
            self.i += 1

    def expect_close(self, closing: str) -> None:
        self.skip_newlines()
        token = self.token
        if token.kind != "close" or token.text != closing:
            if token.kind == "end":
                raise _Refused(f"'{closing}' is missing before the end of the source", token.line, token.column)
            raise _Refused(f"'{closing}' was expected and {token.text!r} was found", token.line, token.column)
        self.advance()

    # -- entry
    def program(self) -> None:
        self.skip_separators()
        while self.token.kind != "end":
            self.expression(0)
            token = self.token
            if token.kind in ("newline", ";"):
                self.skip_separators()
                continue
            if token.kind == "end":
                break
            if token.kind == "close":
                raise _Refused(f"{token.text!r} closes something that was never opened", token.line, token.column)
            raise _Refused(f"{token.text!r} does not continue the expression before it", token.line, token.column)

    # -- expressions
    def expression(self, minimum: int):
        self.left()
        while True:
            token = self.token
            if token.kind == "operator" and (token.text in _BINARY or token.text.startswith("%")):
                power, associativity = _BINARY.get(token.text, (12, "left"))
                if power < minimum:
                    return
                self.advance()
                self.skip_newlines()
                if self.token.kind == "end":
                    raise _Refused(
                        f"the expression ends on {token.text!r}, which has nothing to its right",
                        token.line,
                        token.column,
                    )
                self.expression(power if associativity == "right" else power + 1)
                continue
            if token.kind == "open" and token.text in ("(", "[", "[["):
                self.call_or_index(token.text)
                continue
            return

    def call_or_index(self, opening: str) -> None:
        self.advance()
        closing = {"(": ")", "[": "]", "[[": "]]"}[opening]
        self.skip_newlines()
        if self.token.kind == "close" and self.token.text == closing:
            self.advance()
            return
        while True:
            self.skip_newlines()
            if self.token.kind == ",":
                # An empty argument is legal in an index: x[, 1] and x[i, ].
                self.advance()
                continue
            if self.token.kind == "close" and self.token.text == closing:
                self.advance()
                return
            self.expression(0)
            self.skip_newlines()
            if self.token.kind == ",":
                self.advance()
                continue
            self.expect_close(closing)
            return

    def block(self) -> None:
        self.advance()
        self.skip_separators()
        while not (self.token.kind == "close" and self.token.text == "}"):
            if self.token.kind == "end":
                raise _Refused("'}' is missing before the end of the source", self.token.line, self.token.column)
            self.expression(0)
            if self.token.kind in ("newline", ";"):
                self.skip_separators()
                continue
            if self.token.kind == "close" and self.token.text == "}":
                break
            raise _Refused(
                f"{self.token.text!r} does not continue the expression before it",
                self.token.line,
                self.token.column,
            )
        self.advance()

    def left(self) -> None:
        token = self.token
        if token.kind in ("number", "string"):
            self.advance()
            return
        if token.kind == "operator" and token.text in _UNARY:
            self.advance()
            self.skip_newlines()
            self.expression(_UNARY[token.text])
            return
        if token.kind == "open":
            if token.text == "{":
                self.block()
                return
            if token.text == "(":
                self.advance()
                self.skip_newlines()
                self.expression(0)
                self.expect_close(")")
                return
            raise _Refused(f"{token.text!r} cannot begin an expression", token.line, token.column)
        if token.kind == "close":
            raise _Refused(f"{token.text!r} closes something that was never opened", token.line, token.column)
        if token.kind == "end":
            raise _Refused("the source ends where an expression was expected", token.line, token.column)
        if token.kind != "name":
            raise _Refused(f"{token.text!r} cannot begin an expression", token.line, token.column)
        word = token.text
        if word in ("break", "next"):
            self.advance()
            return
        if word == "function" or word == "\\":
            self.advance()
            self.parameters()
            self.skip_newlines()
            self.expression(0)
            return
        if word == "if":
            self.advance()
            self.condition()
            self.skip_newlines()
            self.expression(0)
            saved = self.i
            self.skip_separators()
            if self.token.kind == "name" and self.token.text == "else":
                self.advance()
                self.skip_newlines()
                self.expression(0)
            else:
                self.i = saved
            return
        if word == "while":
            self.advance()
            self.condition()
            self.skip_newlines()
            self.expression(0)
            return
        if word == "repeat":
            self.advance()
            self.skip_newlines()
            self.expression(0)
            return
        if word == "for":
            self.advance()
            self.skip_newlines()
            if not (self.token.kind == "open" and self.token.text == "("):
                raise _Refused("'(' was expected after 'for'", self.token.line, self.token.column)
            self.advance()
            self.skip_newlines()
            if self.token.kind != "name":
                raise _Refused("'for' needs the name of its variable", self.token.line, self.token.column)
            self.advance()
            self.skip_newlines()
            if not (self.token.kind == "name" and self.token.text == "in"):
                raise _Refused("'in' was expected in 'for'", self.token.line, self.token.column)
            self.advance()
            self.skip_newlines()
            self.expression(0)
            self.expect_close(")")
            self.skip_newlines()
            self.expression(0)
            return
        if word in ("else", "in"):
            raise _Refused(f"{word!r} has no matching statement", token.line, token.column)
        self.advance()
        return

    def condition(self) -> None:
        self.skip_newlines()
        if not (self.token.kind == "open" and self.token.text == "("):
            raise _Refused("'(' was expected after the keyword", self.token.line, self.token.column)
        self.advance()
        self.skip_newlines()
        self.expression(0)
        self.expect_close(")")

    def parameters(self) -> None:
        self.skip_newlines()
        if not (self.token.kind == "open" and self.token.text == "("):
            raise _Refused("'(' was expected after 'function'", self.token.line, self.token.column)
        self.advance()
        self.skip_newlines()
        while not (self.token.kind == "close" and self.token.text == ")"):
            if self.token.kind == "end":
                raise _Refused("')' is missing from the parameter list", self.token.line, self.token.column)
            if self.token.kind != "name":
                raise _Refused(
                    f"{self.token.text!r} is not a parameter name", self.token.line, self.token.column
                )
            self.advance()
            self.skip_newlines()
            if self.token.kind == "operator" and self.token.text == "=":
                self.advance()
                self.skip_newlines()
                self.expression(3)
                self.skip_newlines()
            if self.token.kind == ",":
                self.advance()
                self.skip_newlines()
        self.advance()


def _balanced(source: str) -> bool:
    """Whether the delimiters and literals of this source close, judged on tokens alone."""
    try:
        tokens = tokenize(source)
    except _Refused:
        # A literal that never closes IS the source's own defect, whatever else is in the file.
        return False
    except _Unreadable:
        # A token bioAF does not know says nothing about whether the brackets close.
        return True
    stack: list[str] = []
    for token in tokens:
        if token.kind == "open":
            stack.append({"(": ")", "[": "]", "[[": "]]", "{": "}"}[token.text])
        elif token.kind == "close":
            if not stack or stack.pop() != token.text:
                return False
    return not stack


def _grade(*, balanced: bool, certain: bool) -> str:
    """What a failure to parse establishes: the source's defect, or bioAF's limit.

    plan_8_5 section 3.5 and plan_8_4 section 3.4: a failure of bioAF's own parser produces
    undetermined points, never failed ones. So only a certain defect, or source whose delimiters and
    literals do not close, is refused.
    """
    if certain or not balanced:
        return REFUSED
    return CANNOT_ESTABLISH


def parse_r(source: str) -> dict:
    """``{"status", "message", "line", "column", "parser", "version", "language"}`` for one R source.

    ``status`` is ``parsed``, ``refused`` (the source is broken) or ``cannot_establish`` (bioAF's
    parser ran out of grammar). Nothing here executes, imports or installs anything.
    """
    result = {
        "parser": PARSER,
        "version": PARSER_VERSION,
        "language": LANGUAGE,
        "status": PARSED,
        "message": None,
        "line": 1,
        "column": 1,
    }
    try:
        _Parser(tokenize(source)).program()
        return result
    except _Refused as refusal:
        return {
            **result,
            "status": _grade(balanced=_balanced(source), certain=True),
            "message": refusal.message,
            "line": refusal.line,
            "column": refusal.column,
        }
    except _Unreadable as limit:
        return {
            **result,
            "status": _grade(balanced=_balanced(source), certain=False),
            "message": limit.message,
            "line": limit.line,
            "column": limit.column,
        }
    except RecursionError:
        return {
            **result,
            "status": CANNOT_ESTABLISH,
            "message": "bioAF's R parser ran out of depth reading this source",
        }


# ---- what a source declares and uses ---------------------------------------------------------------
#
# plan_8_5 section 3.5: C2 to C5 ask what the code requires, what it runs and where it reads its
# inputs from. For Python those come from the standard library's AST. Here they come from the token
# stream, which is the same evidence a reader of the file has, and never from a guess about a name.

_ATTACHING = ("library", "require", "requireNamespace", "loadNamespace")
_SOURCING = ("source", "sys.source")


def _literal(token: _Token) -> str | None:
    """The text inside a quoted string token, or None for a token that is not one."""
    if token.kind != "string":
        return None
    text = token.text
    if text[:1] in "rR":
        opening = text.index("(") if "(" in text[:4] else text.index("[") if "[" in text[:4] else -1
        return text[opening + 1 : text.rindex(")" if opening != -1 and text[opening] == "(" else "]")] if opening != -1 else text
    return text[1:-1].replace("\\'", "'").replace('\\"', '"')


def read_r(source: str) -> dict:
    """``{"packages", "namespaced", "sourced", "strings", "runs", "dynamic"}`` for one R source.

    ``packages`` are attached by name with ``library()`` or ``require()``; ``namespaced`` are used as
    ``pkg::fn`` without being attached; ``sourced`` are the files the source reads in as code.
    ``runs`` is whether the source does work when it is run, rather than only defining functions.
    ``dynamic`` is set where a package is named by a variable, which no reading of the text can resolve.
    """
    found = {
        "packages": [],
        "namespaced": [],
        "sourced": [],
        "strings": [],
        "runs": False,
        "dynamic": False,
    }
    try:
        tokens = tokenize(source)
    except (_Refused, _Unreadable):
        return found
    depth = 0
    statement_start = True
    for index, token in enumerate(tokens):
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if token.kind == "open":
            depth += 1
        elif token.kind == "close":
            depth = max(0, depth - 1)
        elif token.kind in ("newline", ";"):
            if depth == 0:
                statement_start = True
            continue
        if token.kind == "string":
            text = _literal(token)
            if text is not None and text not in found["strings"]:
                found["strings"].append(text)
        if token.kind == "name" and nxt is not None and nxt.kind == "open" and nxt.text == "(":
            name = token.text
            argument = tokens[index + 2] if index + 2 < len(tokens) else None
            if name in _ATTACHING and argument is not None:
                if argument.kind == "name" and argument.text not in _CONSTANTS:
                    following = tokens[index + 3] if index + 3 < len(tokens) else None
                    if following is not None and following.kind == "close":
                        if argument.text not in found["packages"]:
                            found["packages"].append(argument.text)
                    else:
                        found["dynamic"] = True
                elif argument.kind == "string":
                    package = _literal(argument)
                    if package and package not in found["packages"]:
                        found["packages"].append(package)
                else:
                    found["dynamic"] = True
            elif name in _SOURCING and argument is not None and argument.kind == "string":
                path = _literal(argument)
                if path and path not in found["sourced"]:
                    found["sourced"].append(path)
            # Work the script does when it runs: a call at the top level, whether or not its result is
            # assigned. A function definition is not work, and neither is a bare name.
            if depth == 0 and name not in ("function",) and name not in _KEYWORDS:
                found["runs"] = True
        if token.kind == "name" and nxt is not None and nxt.kind == "operator" and nxt.text in ("::", ":::"):
            if token.text not in found["namespaced"]:
                found["namespaced"].append(token.text)
        if token.kind not in ("newline", ";"):
            statement_start = False
    found["namespaced"] = [p for p in found["namespaced"] if p not in found["packages"]]
    return found
