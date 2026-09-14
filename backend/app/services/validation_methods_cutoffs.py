"""plan_8_2 section 3.1 and owner decision 2: a claim that states no cutoff inherits one only from a methods
sentence that explicitly covers every differential test in the claim's experiment.

The paper's methods paragraphs are in hand while the paper is read (``_jats_sections``), and nothing of them
was kept. ``record`` keeps the sentences that define a differential test's cutoff, verbatim, with the
cutoffs they state; ``inherited_cutoffs`` decides, from those sentences alone, whether one covers a claim's
experiment. No model is asked.

**A sentence states a cutoff** when it is in the methods, it is about a differential test ("differentially
expressed", "differentially bound", "DEGs"), it defines or thresholds ("were considered", "were defined",
"threshold", "all"), and it states a significance or fold-change cutoff with an operator bioAF reads. A
sentence naming a method but no cutoff, or a P value that is not about a differential test, states none.

**It covers the claim's experiment** when its differential words are that experiment's assay (a
differential binding cutoff is not a differential expression cutoff) and, when the paper has more than one
experiment of that assay with differential tests, it names the claim's experiment by words no other such
experiment has. Otherwise nothing is inherited, and the reason says why:

- a sentence that states an exception is preserved by inheriting nothing from it;
- two sentences covering the same experiment with different cutoffs are not resolved by picking one;
- a paper-level threshold with no such sentence, and a conventional threshold, are never supplied.
"""

from __future__ import annotations

import re

VERSION = 1

_DIFFERENTIAL = re.compile(
    r"differential(?:ly)?[\s-]+(?:expressed|expression|accessib\w*|bound|binding|abundan\w*|methylat\w*|regulated)"
    r"|\bDEGs?\b|\bDE genes\b|\bDARs?\b|\bDMRs?\b|\bDBRs?\b",
    re.IGNORECASE,
)
_DEFINING = re.compile(
    r"\b(?:considered|defined|deemed|regarded|called|classified|designated|termed|declared)\b"
    r"|\bthresholds?\b|\bcut-?offs?\b|\b(?:all|every|each)\b",
    re.IGNORECASE,
)
_EXCEPTION = re.compile(r"\bexcept\b|\bexceptions?\b|\bunless\b|\bwhereas\b|\binstead\b", re.IGNORECASE)

# An assay family: the words that mark a sentence's differential test as that family's, and the words that
# mark an experiment's assay as that family.
_FAMILIES = (
    (
        "expression",
        re.compile(r"express|\bDEGs?\b|\bDE genes\b", re.I),
        re.compile(r"rna|transcriptom|microarray", re.I),
    ),
    ("accessibility", re.compile(r"accessib|\bDARs?\b", re.I), re.compile(r"atac|dnase|accessib", re.I)),
    (
        "binding",
        re.compile(r"\bbound\b|\bbinding\b|occupan|\bDBRs?\b", re.I),
        re.compile(r"chip|cut ?& ?(?:run|tag)", re.I),
    ),
    ("methylation", re.compile(r"methylat|\bDMRs?\b", re.I), re.compile(r"methyl|bisulfite|wgbs|rrbs", re.I)),
    ("abundance", re.compile(r"abundan", re.I), re.compile(r"proteom|mass spec", re.I)),
)

_NUMBER = r"(?P<number>\d+(?:\.\d+)?(?:\s*[x×]\s*10\s*[-−–]\s*\d+|[eE][-−–]?\d+)?)\s*(?P<percent>%)?"
_AT_MOST = (r"<=|≤|=<|less than or equal to|at most|not exceeding", "<=")
_BELOW = (r"<|less than|lower than|smaller than|below|under", "<")
_AT_LEAST = (r">=|≥|=>|greater than or equal to|at least", ">=")
_ABOVE = (r">|greater than|more than|higher than|above|over|exceeding", ">")
_SIGNIFICANCE = re.compile(
    r"(?P<kind>benjamini[- ]hochberg[- ]adjusted\s+p[- ]?values?|bh[- ]adjusted\s+p[- ]?values?"
    r"|bonferroni[- ](?:corrected|adjusted)\s+p[- ]?values?|adjusted\s+p[- ]?values?|adj\.?\s*p(?:[- ]?values?)?"
    r"|p\.?adj|padj|fdr|false discovery rate|q[- ]?values?|p[- ]?values?|\bp\b)"
    r"\s*(?:\([^)]{0,20}\)\s*)?(?:of\s+)?(?P<op>" + "|".join(p for p, _ in (_AT_MOST, _BELOW)) + r")\s*" + _NUMBER,
    re.IGNORECASE,
)
_LOG2 = re.compile(
    r"\|?\s*log\s*(?:2|₂)\s*[- ]?\(?\s*(?:fold[- ]changes?|FC)\s*\)?\s*\|?\s*(?:of\s+)?"
    r"(?P<op>" + "|".join(p for p, _ in (_AT_LEAST, _ABOVE)) + r")\s*" + _NUMBER,
    re.IGNORECASE,
)
_FOLD = re.compile(
    r"fold[- ]changes?\s*(?:\(FC\)\s*)?(?:of\s+)?(?P<op>"
    + "|".join(p for p, _ in (_AT_LEAST, _ABOVE))
    + r")\s*"
    + _NUMBER,
    re.IGNORECASE,
)
_FOLD_WORDS = {"two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0, "ten": 10.0}
_FOLD_WORD = re.compile(
    r"(?P<op>" + "|".join(p for p, _ in (_AT_LEAST, _ABOVE)) + r")\s*(?P<n>two|three|four|five|ten|\d+(?:\.\d+)?)"
    r"[- ]?fold",
    re.IGNORECASE,
)


def _operator(text: str, pairs) -> str:
    lowered = " ".join(text.lower().split())
    for pattern, operator in pairs:
        if re.fullmatch(pattern, lowered):
            return operator
    return pairs[-1][1]


def _value(number: str, percent: str | None) -> float:
    text = re.sub(r"\s+", "", number).replace("−", "-").replace("–", "-")
    scientific = re.fullmatch(r"(\d+(?:\.\d+)?)[x×]10-(\d+)", text)
    value = float(scientific.group(1)) * 10 ** -int(scientific.group(2)) if scientific else float(text)
    return value / 100 if percent else value


def _significance(match, sentence: str) -> dict:
    kind_words = match.group("kind").lower()
    adjusted = any(w in kind_words for w in ("adj", "fdr", "false discovery", "q", "bonferroni", "benjamini", "bh"))
    cutoff = {
        "kind": "padj" if adjusted else "pvalue",
        "operator": _operator(match.group("op"), (_AT_MOST, _BELOW)),
        "value": _value(match.group("number"), match.group("percent")),
    }
    lowered = sentence.lower()
    if adjusted:
        if "benjamini" in lowered or re.search(r"\bbh\b", lowered):
            cutoff["adjustment"] = "BH"
        elif "bonferroni" in lowered:
            cutoff["adjustment"] = "Bonferroni"
        elif kind_words.startswith("q"):
            cutoff["adjustment"] = "q-value"
        elif "fdr" in kind_words or "false discovery" in kind_words:
            cutoff["adjustment"] = "FDR unspecified"
    return cutoff


def _cutoffs(sentence: str) -> list[dict]:
    found: list[dict] = []
    for match in _SIGNIFICANCE.finditer(sentence):
        found.append(_significance(match, sentence))
    rest = sentence
    for match in _LOG2.finditer(sentence):
        found.append(
            {
                "kind": "abs_log2fc",
                "operator": _operator(match.group("op"), (_AT_LEAST, _ABOVE)),
                "value": _value(match.group("number"), None),
            }
        )
        rest = rest.replace(match.group(0), " ")
    for match in _FOLD.finditer(rest):
        found.append(
            {
                "kind": "fold_change",
                "operator": _operator(match.group("op"), (_AT_LEAST, _ABOVE)),
                "value": _value(match.group("number"), None),
            }
        )
    for match in _FOLD_WORD.finditer(rest):
        n = match.group("n").lower()
        found.append(
            {
                "kind": "fold_change",
                "operator": _operator(match.group("op"), (_AT_LEAST, _ABOVE)),
                "value": _FOLD_WORDS.get(n) or float(n),
            }
        )
    unique: list[dict] = []
    for cutoff in found:
        if cutoff not in unique:
            unique.append(cutoff)
    return unique


def _families(sentence: str) -> list[str]:
    return [name for name, words, _ in _FAMILIES if words.search(sentence)]


def methods_statements(paragraphs: list[str]) -> list[dict]:
    """Every methods sentence that defines a differential test's cutoff, verbatim, with the cutoffs it states."""
    from app.services.validation_table_binding import sentences

    statements: list[dict] = []
    for index, paragraph in enumerate(paragraphs or []):
        for sentence in sentences(paragraph or ""):
            if not (_DIFFERENTIAL.search(sentence) and _DEFINING.search(sentence)):
                continue
            cutoffs = _cutoffs(sentence)
            if not cutoffs:
                continue
            significance = {c["value"] for c in cutoffs if c["kind"] in ("padj", "pvalue")}
            statements.append(
                {
                    "quote": sentence,
                    "cutoffs": cutoffs,
                    "families": _families(sentence),
                    # Two significance cutoffs in one sentence are an exception too: which test each covers
                    # is the paper's to say.
                    "exception": bool(_EXCEPTION.search(sentence)) or len(significance) > 1,
                    "paragraph": index,
                }
            )
    return statements


def record(paragraphs: list[str] | None, *, source: str | None) -> dict:
    """What the study keeps of its methods: the defining sentences, and how many paragraphs were read."""
    return {
        "version": VERSION,
        "source": source,
        "paragraphs": len(paragraphs or []),
        "statements": methods_statements(paragraphs or []),
    }


def _family_of(assay: str | None) -> str | None:
    return next((name for name, _, assays in _FAMILIES if assays.search(assay or "")), None)


def _has_tests(experiment: dict, contrasts: list[dict]) -> bool:
    return bool(experiment.get("contrast_indices")) or any(
        isinstance(c, dict) and c.get("reported_experiment_id") == experiment.get("id") for c in contrasts
    )


_STRUCTURAL = {
    "versus",
    "with",
    "from",
    "into",
    "between",
    "and",
    "the",
    "for",
    "cells",
    "cell",
    "samples",
    "sample",
    "data",
    "experiment",
    "analysis",
    "seq",
    "rna",
    "bulk",
    "using",
    "type",
    "wild",
}


def _words(experiment: dict) -> set[str]:
    text = " ".join(
        [
            str(experiment.get("description") or ""),
            " ".join(str(c) for c in experiment.get("conditions") or []),
            " ".join(str(t) for t in experiment.get("time_points") or []),
        ]
    ).lower()
    return {w for w in re.split(r"[^a-z0-9]+", text) if len(w) >= 3 and w not in _STRUCTURAL}


def _names(sentence: str, own: set[str]) -> bool:
    tokens = set(re.split(r"[^a-z0-9]+", sentence.lower()))
    return any(w in tokens or (len(w) >= 6 and any(t.startswith(w[:-1]) for t in tokens if len(t) >= 5)) for w in own)


def inherited_cutoffs(
    experiment_id: str | None, *, experiments: list[dict], contrasts: list[dict], recorded: dict | None
) -> dict:
    """``{"cutoffs": [...] | None, "quote": str | None, "reason": str | None}``: the cutoffs a claim of this
    experiment inherits from the methods, with the sentence they come from, or why none are inherited.
    ``reason`` is None when the methods were not read or state no cutoff for any differential test."""
    nothing = {"cutoffs": None, "quote": None, "reason": None}
    statements = [s for s in (recorded or {}).get("statements") or [] if isinstance(s, dict)]
    if not statements:
        return nothing
    experiments = [e for e in experiments or [] if isinstance(e, dict)]
    contrasts = [c for c in contrasts or [] if isinstance(c, dict)]
    experiment = next((e for e in experiments if experiment_id and e.get("id") == experiment_id), None)
    if experiment is None:
        tested = [e for e in experiments if _has_tests(e, contrasts)]
        experiment = tested[0] if len(tested) == 1 else None
    if experiment is None:
        return {
            **nothing,
            "reason": "the claim's experiment is not established, so no methods sentence can be said to cover it",
        }
    family = _family_of(experiment.get("assay"))
    relevant = [s for s in statements if family and family in (s.get("families") or [])]
    if not relevant:
        return {
            **nothing,
            "reason": "no methods sentence bioAF recorded states a cutoff that covers this experiment's differential tests",
        }
    siblings = [
        e
        for e in experiments
        if e is not experiment and _family_of(e.get("assay")) == family and _has_tests(e, contrasts)
    ]
    covering = relevant
    if siblings:
        others = set().union(*(_words(e) for e in siblings))
        own = _words(experiment) - others
        foreign = [_words(e) - _words(experiment) for e in siblings]
        covering = [s for s in relevant if _names(s["quote"], own) and not any(_names(s["quote"], f) for f in foreign)]
        if not covering:
            quote = relevant[0]["quote"]
            return {
                **nothing,
                "reason": f'the methods sentence ("{quote}") does not say which of the paper\'s {len(siblings) + 1} '
                f"{experiment.get('assay') or 'such'} experiments it covers",
            }
    exception = next((s for s in covering if s.get("exception")), None)
    if exception is not None:
        return {
            **nothing,
            "reason": f'the methods sentence ("{exception["quote"]}") states an exception, so which of this '
            "experiment's tests each cutoff covers is not established",
        }
    distinct = []
    for statement in covering:
        key = sorted((c["kind"], c["operator"], c["value"]) for c in statement["cutoffs"])
        if key not in distinct:
            distinct.append(key)
    if len(distinct) > 1:
        quotes = "; ".join(f'"{s["quote"]}"' for s in covering)
        return {
            **nothing,
            "reason": f"the methods state different cutoffs for this experiment's differential tests ({quotes}), and "
            "bioAF does not choose between them",
        }
    chosen = covering[0]
    return {"cutoffs": [dict(c) for c in chosen["cutoffs"]], "quote": chosen["quote"], "reason": None}
