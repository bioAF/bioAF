"""plan_8_3 section 3.1: a repository sample record as the ATTRIBUTES it states, and an arm compared
against them.

A paper writes a condition as prose ("SAMD1 KO mouse ES cells"). A repository writes the same fact as
characteristics ("antibody: none; genotype: SAMD1KO; cell line: E14TG2a"). Deciding compatibility by
asking whether the paper's phrase occurs in the record's text therefore refuses correct evidence: it
refused all eight rows of study 56's mapping, the reference arm as well as the test arm, and it would
do so on any paper whose contrast names are prose.

What an arm states is a VALUE of one of the attributes the deposit uses. So the arm is placed against
an attribute first, from its own words and the names the deposit's records use, and then compared with
what THIS record states for that attribute. Three answers, because they have three different remedies:

- ``compatible``: the record states the arm's value for every attribute the arm names.
- ``contradicted``: the record states that attribute and states something else. Still refused.
- ``unresolved``: the arm cannot be placed against any attribute the deposit states, or this record is
  silent about the one it names. An absent attribute is never read as agreement.
- ``not_stated``: there is nothing to compare. The contrast states no condition for this arm, or the
  deposit's records state no facts at all, and the arm assignment rests on other evidence.

Spelling is normalized only WITHIN one attribute's value ("SAMD1 KO" and "SAMD1KO" are one genotype;
"Tissue culture surface (TCS)" states TCS). Never across attributes, and never to make an approximate
scientific value pass. This repairs the wording comparison and establishes nothing about biological
independence: clone, replicate and donor identity are stage 5's, and are resolved through its own
control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMPATIBLE = "compatible"
CONTRADICTED = "contradicted"
UNRESOLVED = "unresolved"
NOT_STATED = "not_stated"

# What an attribute the deposit gave no name to is called where a reason names it.
UNNAMED = "the words its sample record states"

# Words that describe the ACT of applying a condition rather than the condition. A deposit writes
# "treated with Mucoderm®" where a paper writes "Mucoderm®", and they are the same treatment.
_FILLER = frozenset({"treated", "with", "grown", "cultured", "on", "in", "the", "a", "an"})
# A value this short is matched on its own word rather than as a substring, so a two-letter genotype
# cannot be found inside an unrelated word.
_SHORT = 3


def _squashed(text: str) -> str:
    """Letters and digits only, lowercased. Punctuation, spacing and symbols are presentation."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _normalized(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _without_filler(text: str) -> str:
    return " ".join(w for w in re.split(r"[^A-Za-z0-9]+", str(text or "")) if w and w.lower() not in _FILLER)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z0-9]+", str(text or "")) if w]


def _initialism(words: list[str]) -> str:
    return "".join(w[0] for w in words).lower()


def _spellings(value: str) -> frozenset[str]:
    """The squashed forms one attribute value is written in.

    Its own words, with the filler dropped. A parenthesis counts as a second spelling of the SAME
    value only where it is an initialism of the words beside it: "Tissue culture surface (TCS)" states
    TCS, and "SAMD1 (self-made)" does not state SAMD1, it states an antibody whose name happens to
    begin with a gene's. A combined series holds both, and reading the parenthesis as an alternative
    spelling either way put study 56's arms against its ChIP antibody.
    """
    text = str(value or "")
    forms = {text}
    outer = re.sub(r"\([^)]*\)", " ", text)
    for inner in re.findall(r"\(([^)]*)\)", text):
        if _squashed(inner) == _initialism(_words(outer)) or _squashed(outer) == _initialism(_words(inner)):
            forms |= {inner, outer}
    cores = set()
    # A value written in words is also written as its initials, which is how "wild type" and "WT" are
    # one genotype. Only for a short run of plain words, and only matched as a word of its own.
    spelled = [w for w in _words(outer) if w.isalpha() and len(w) > 1]
    if 2 <= len(spelled) <= 4 and len(spelled) == len(_words(outer)):
        cores.add(_initialism(spelled))
    for form in forms:
        for candidate in (form, _without_filler(form)):
            core = _squashed(candidate)
            if len(core) >= 2:
                cores.add(core)
    return frozenset(cores)


@dataclass(frozen=True)
class Attribute:
    """One fact a record states: its name where the deposit gives one, its value, and the spellings
    that value is written in."""

    name: str | None
    value: str
    spellings: frozenset[str]

    def carried_by(self, phrase: str) -> bool:
        """Whether ``phrase`` states this value, however the two spelled it."""
        squashed, words = _squashed(phrase), _normalized(phrase)
        for spelling in self.spellings:
            if len(spelling) > _SHORT:
                if spelling in squashed:
                    return True
            elif re.search(rf"(?<![a-z0-9]){re.escape(spelling)}(?![a-z0-9])", _squashed_words(words)):
                return True
        return False


def _squashed_words(text: str) -> str:
    """Each word squashed, the spaces kept, so a short value is matched on its own word."""
    return " ".join(_squashed(word) for word in str(text or "").split())


def attributes(record: dict | None) -> tuple[Attribute, ...]:
    """The attributes a repository record states.

    A GEO record's characteristics arrive as one ``"name: value; name: value"`` field. A part with no
    name is still a fact the record states, and keeps its value.
    """
    text = str((record or {}).get("condition") or "")
    found: list[Attribute] = []
    for part in (p.strip() for p in text.split(";")):
        if not part:
            continue
        name, _, value = part.partition(":")
        name, value = name.strip(), value.strip()
        if not value:
            name, value = None, name
        if not value:
            continue
        found.append(Attribute(name=name or None, value=value, spellings=_spellings(value)))
    return tuple(found)


def _placed(arm: str, deposit_records: list[dict] | None) -> list[str | None]:
    """The attributes this arm states a value of, from the arm's own words and what the deposit's
    records state. Ordered as the deposit's records state them.

    An attribute the deposit gave no name to counts: a deposit that writes its characteristics without
    names still states facts, and the arm is placed against those words as one anonymous attribute.
    """
    names: list[str | None] = []
    for record in deposit_records or []:
        for attribute in attributes(record):
            if attribute.name not in names and attribute.carried_by(arm):
                names.append(attribute.name)
    return names


def available_names(deposit_records: list[dict] | None) -> list[str]:
    """Every attribute name the deposit's records state, sorted. An unnamed one is named as such."""
    found = {a.name or UNNAMED for r in deposit_records or [] for a in attributes(r)}
    return sorted(found)


def condition_match(arm: str | None, record: dict | None, deposit_records: list[dict] | None) -> dict:
    """Whether ``record`` states the condition ``arm`` names.

    ``{"status", "attribute", "stated", "available"}``. ``attribute`` is the attribute the arm was
    placed against, ``stated`` what this record states for it, and ``available`` the attribute names
    the deposit uses, which is what an unresolved answer names.
    """
    available = available_names(deposit_records)
    phrase = str(arm or "").strip()
    if not phrase or not available:
        # Nothing to compare: the contrast states no condition for this arm, or the deposit's records
        # state no facts at all. The arm assignment rests on the evidence that placed it.
        return {"status": NOT_STATED, "attribute": None, "stated": None, "available": available}
    mine = attributes(record)
    names = _placed(phrase, deposit_records)
    if not names:
        return {"status": UNRESOLVED, "attribute": None, "stated": None, "available": available}
    for name in names:
        stated = [a for a in mine if a.name == name]
        if not stated:
            # The arm names this attribute and this record is silent about it. Silence is not assent.
            return {"status": UNRESOLVED, "attribute": name or UNNAMED, "stated": None, "available": available}
        if not any(a.carried_by(phrase) for a in stated):
            return {
                "status": CONTRADICTED,
                "attribute": name or UNNAMED,
                "stated": "; ".join(a.value for a in stated),
                "available": available,
            }
    first = next(a for a in mine if a.name == names[0])
    return {"status": COMPATIBLE, "attribute": names[0] or UNNAMED, "stated": first.value, "available": available}
