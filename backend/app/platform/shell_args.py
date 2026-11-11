"""Neutral argument encoding for the shell commands bioAF generates.

Storage adapters return copy/sync/auth commands as shell text, because the init containers,
sidecars, EXIT traps and build templates that run them compose them with other shell. Every
dynamic operand in that text is one of two things:

- a literal (a URI, a path, a filename from an upload): quoted with ``shlex.quote`` so the shell
  passes it to the program as exactly one argument and never interprets it;
- a ``ShellExpr``: a fixed expression the *caller* owns and the shell must evaluate, such as the
  loop variable ``"$f"``. It is inserted verbatim, so it must never contain data.

There is deliberately no third kind: a caller cannot hand an adapter a preassembled fragment of
data and syntax.
"""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Iterable

from app.exceptions import ValidationError


class ShellExpr(str):
    """A caller-owned shell expression, inserted verbatim (never built from data)."""


def shell_word(value: str) -> str:
    """One argument: a ``ShellExpr`` verbatim, anything else quoted as a literal."""
    if isinstance(value, ShellExpr):
        return str(value)
    if "\0" in value:
        raise ValidationError("A command argument cannot contain a NUL byte")
    return shlex.quote(value)


def shell_join(words: Iterable[str]) -> str:
    return " ".join(shell_word(w) for w in words)


def absolute_path(path: str) -> str:
    """A literal local path operand: absolute, so no CLI can read it as an option."""
    if isinstance(path, ShellExpr):
        return path
    if not path or not path.startswith("/") or "\0" in path:
        raise ValidationError(f"A local path operand must be absolute: {path!r}")
    return path


def safe_relative_path(rel: str) -> str:
    """A relative path that stays inside the directory it is joined to.

    No absolute path, no empty, ``.`` or ``..`` segment, no NUL: a staged input named ``..`` or
    ``a/../../etc`` must not land outside its working directory. Any other character (a newline,
    a quote) is data once quoted, so it is not refused here.
    """
    if not rel or rel.startswith("/") or "\0" in rel:
        raise ValidationError(f"Unsafe relative path: {rel!r}")
    if any(seg in ("", ".", "..") for seg in rel.split("/")):
        raise ValidationError(f"Unsafe relative path: {rel!r}")
    return posixpath.normpath(rel)
