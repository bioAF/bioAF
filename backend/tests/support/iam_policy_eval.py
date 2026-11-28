"""A small, static IAM policy evaluator for testing bioAF's own policy documents (plan 04B).

It answers "would these identity policies allow this request?" for the subset of IAM the bioAF
documents use: Allow/Deny statements, Action/Resource/NotResource wildcards, and the condition operators
StringEquals, StringNotEquals, StringLike, ArnLike, ArnEquals, Null, plus the ForAnyValue/ForAllValues set
qualifiers, and ${...} policy variables taken from the request context. An explicit Deny wins; otherwise any matching Allow allows.

This is not AWS's evaluator. It ignores SCPs, resource policies, session policies and service
specific context. It proves what the documents say, not what AWS does; live simulation and a test
installation are still required.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field


@dataclass
class Request:
    action: str
    resource: str
    context: dict = field(default_factory=dict)


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _match(pattern: str, value: str, case_insensitive: bool = False) -> bool:
    if case_insensitive:
        return fnmatch.fnmatchcase(value.lower(), pattern.lower())
    return fnmatch.fnmatchcase(value, pattern)


def _single(operator: str, expected: list, actual) -> bool:
    """Evaluate one condition operator against the context value (None when absent)."""
    if operator == "Null":
        want_absent = str(expected[0]).lower() == "true"
        return (actual is None) == want_absent
    negated = operator.startswith("StringNot") or operator.startswith("ArnNot")
    if actual is None:
        return negated
    values = _as_list(actual)
    if operator == "StringEquals":
        return any(v in expected for v in values)
    if operator == "StringNotEquals":
        return all(v not in expected for v in values)
    if operator in ("StringLike", "ArnLike", "ArnEquals"):
        return any(_match(p, v) for p in expected for v in values)
    raise NotImplementedError(operator)


def _condition(block: dict, context: dict) -> bool:
    for raw_operator, keys in block.items():
        qualifier, _, operator = raw_operator.rpartition(":")
        for key, expected in keys.items():
            expected = _as_list(expected)
            actual = context.get(key)
            if qualifier == "ForAnyValue":
                ok = actual is not None and any(_single(operator, expected, v) for v in _as_list(actual))
            elif qualifier == "ForAllValues":
                ok = actual is None or all(_single(operator, expected, v) for v in _as_list(actual))
            else:
                ok = _single(operator, expected, actual)
            if not ok:
                return False
    return True


_VARIABLE = re.compile(r"\$\{([^}]+)\}")


def _resolve(value, context: dict):
    """Substitute IAM policy variables such as ${aws:PrincipalTag/x} from the request context.
    An unresolvable variable becomes a value that matches nothing, as in IAM."""
    if isinstance(value, list):
        return [_resolve(v, context) for v in value]
    if not isinstance(value, str):
        return value
    return _VARIABLE.sub(lambda m: str(context.get(m.group(1), "\x00unresolved\x00")), value)


def _applies(statement: dict, request: Request) -> bool:
    statement = {
        **statement,
        **{k: _resolve(statement[k], request.context) for k in ("Resource", "NotResource") if k in statement},
        "Condition": {
            op: {key: _resolve(v, request.context) for key, v in keys.items()}
            for op, keys in statement.get("Condition", {}).items()
        },
    }
    actions = _as_list(statement.get("Action", []))
    if not any(_match(a, request.action, case_insensitive=True) for a in actions):
        return False
    if "NotResource" in statement:
        if any(_match(r, request.resource) for r in _as_list(statement["NotResource"])):
            return False
    elif not any(_match(r, request.resource) for r in _as_list(statement.get("Resource", []))):
        return False
    return _condition(statement.get("Condition", {}), request.context)


def decide(policies: list[dict], request: Request) -> str:
    """'allow', 'explicit_deny' or 'implicit_deny'."""
    statements = [s for policy in policies for s in policy["Statement"]]
    if any(s["Effect"] == "Deny" and _applies(s, request) for s in statements):
        return "explicit_deny"
    if any(s["Effect"] == "Allow" and _applies(s, request) for s in statements):
        return "allow"
    return "implicit_deny"


def allowed(policies: list[dict], request: Request) -> bool:
    return decide(policies, request) == "allow"


def allowed_with_boundary(identity: list[dict], boundary: dict, request: Request) -> bool:
    """Effective permission of a role that has a permissions boundary: both must allow."""
    return allowed(identity, request) and allowed([boundary], request)
