"""plan_8_1 section 1.2: what an extraction answer must hold before any of it is read as a fact.

``parse_extraction`` turned a missing key into an empty list, so an answer that omitted ``claims`` read as
"the paper has no claims", and ``method: {}`` read as methods too thin to name an assay. A missing field
is a fact about the ANSWER. This module says which fields an answer must hold, of which type, and whether
an explicit unknown is a valid answer, so the read can tell three things apart:

- **complete**: every core part and every required nested field is present and well typed;
- **incomplete**: a core part (``claims``, ``method``, ``accessions``) or a required nested field is
  missing or mistyped. The read spends its one recovery attempt, naming the rejected paths;
- **not read**: any other part, or any optional nested field, is missing or mistyped. That part is
  recorded as not read (null), which is not the same as the paper stating nothing, and the rest of the
  plan stands.

An explicit empty collection (``claims: []``) is a complete answer where the schema allows it, and an
explicit empty or null value (``"assay": null``, ``"reference_build": ""``) is the reading's assessed "not
stated". Only an explicit assessed absence can support an absence statement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CORE_PARTS = ("claims", "method", "accessions")

_STR_OR_NULL = (str, type(None))
_NUMBER_OR_TEXT = (int, float, str, type(None))

# Top-level parts and their types. A core part missing or mistyped leaves the answer incomplete; any
# other part missing or mistyped is not read.
_PART_TYPES: dict[str, tuple[type, ...]] = {
    "accessions": (list,),
    "sample_structure": (dict,),
    "method": (dict,),
    "reported_experiments": (list,),
    "resources": (list,),
    "differential_design": (dict,),
    "claims": (list,),
    "significance_ambiguities": (list,),
    "data_availability": (str,),
    "code_availability": (list,),
    "blockers": (list,),
}

# Nested fields whose absence would otherwise be read as a fact about the paper. Required ones make an
# answer incomplete; optional ones are recorded as not read. Each allows an explicit null or empty
# answer where the paper can state nothing, except a claim's own words, without which it is no claim.
_METHOD_REQUIRED = {"assay": _STR_OR_NULL}
_METHOD_OPTIONAL = {"reference_build": _STR_OR_NULL, "tools": (list, type(None))}
_CLAIM_REQUIRED = {"claim_text": (str,)}
_SAMPLE_OPTIONAL = {"organism": _STR_OR_NULL, "sample_count": _NUMBER_OR_TEXT}
_EXPERIMENT_OPTIONAL = {"assay": _STR_OR_NULL, "reference": (dict,)}
_REFERENCE_OPTIONAL = {"assembly": _STR_OR_NULL, "annotation": _STR_OR_NULL}

_TYPE_WORDS = {list: "a list", dict: "an object", str: "text", int: "a number", float: "a number"}


def _type_words(types: tuple[type, ...]) -> str:
    words = [_TYPE_WORDS[t] for t in types if t in _TYPE_WORDS]
    return " or ".join(dict.fromkeys(words)) or "a value"


def _typed(value, types: tuple[type, ...]) -> bool:
    if isinstance(value, bool):
        return bool in types
    return isinstance(value, types)


@dataclass
class ExtractionCheck:
    """What an answer holds: the structural problems that leave it incomplete, and what it did not read."""

    core_problems: list[str] = field(default_factory=list)
    not_read: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.core_problems

    def omitted(self, path: str) -> bool:
        """Whether ``path`` was not read: named itself, or inside a part that was not read."""
        return any(path == p or path.startswith(f"{p}.") or path.startswith(f"{p}[") for p in self.not_read)


def _fields(value: dict, rules: dict[str, tuple[type, ...]], prefix: str) -> list[tuple[str, str]]:
    """(path, problem) for each rule ``value`` breaks: missing, or of the wrong type."""
    found = []
    for name, types in rules.items():
        path = f"{prefix}.{name}"
        if name not in value:
            found.append((path, f"{path} is missing"))
        elif not _typed(value[name], types):
            found.append((path, f"{path} should be {_type_words(types)}"))
    return found


def check_extraction(data: dict) -> ExtractionCheck:
    """The schema's verdict on one parsed extraction answer."""
    check = ExtractionCheck()
    for part, types in _PART_TYPES.items():
        core = part in CORE_PARTS
        if part not in data:
            (check.core_problems if core else check.not_read).append(f"{part} is missing" if core else part)
            continue
        if not _typed(data[part], types):
            (check.core_problems if core else check.not_read).append(
                f"{part} should be {_type_words(types)}" if core else part
            )
            continue

    method = data.get("method")
    if isinstance(method, dict):
        check.core_problems += [problem for _path, problem in _fields(method, _METHOD_REQUIRED, "method")]
        check.not_read += [path for path, _problem in _fields(method, _METHOD_OPTIONAL, "method")]

    claims = data.get("claims")
    if isinstance(claims, list):
        for index, claim in enumerate(claims):
            prefix = f"claims[{index}]"
            if not isinstance(claim, dict):
                check.core_problems.append(f"{prefix} should be an object")
                continue
            check.core_problems += [problem for _path, problem in _fields(claim, _CLAIM_REQUIRED, prefix)]

    sample = data.get("sample_structure")
    if isinstance(sample, dict):
        check.not_read += [path for path, _problem in _fields(sample, _SAMPLE_OPTIONAL, "sample_structure")]

    experiments = data.get("reported_experiments")
    if isinstance(experiments, list):
        for index, experiment in enumerate(experiments):
            prefix = f"reported_experiments[{index}]"
            if not isinstance(experiment, dict):
                check.not_read.append(prefix)
                continue
            broken = _fields(experiment, _EXPERIMENT_OPTIONAL, prefix)
            check.not_read += [path for path, _problem in broken]
            reference = experiment.get("reference")
            if isinstance(reference, dict):
                check.not_read += [
                    path for path, _problem in _fields(reference, _REFERENCE_OPTIONAL, f"{prefix}.reference")
                ]
    return check


def rejection_note(problems: list[str]) -> str:
    """What the recovery attempt tells the model about the answer it is replacing."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return (
        "Your previous answer to this request was rejected because it was structurally incomplete:\n"
        f"{listed}\n\n"
        "Answer again with the COMPLETE schema. Give every required field, in the type the schema shows. "
        "Where the paper does not state something, give the field with null or an empty value; never "
        "leave a field out."
    )
