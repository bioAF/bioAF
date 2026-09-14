"""plan_8_2 section 1.1: bind a table to a claim's contrast before any value in it is compared.

Groff's XX-versus-XY table was the only result table the paper published, so it was compared with the
aneuploidy, morphology and morphokinetic counts too, and three invalid disagreements followed (194 against
53, 10 and 9). SAMD1's undifferentiated claims were matched to its differentiation table because filename
matching compared distinguishing words only within one experiment, where the shared gene name looked
distinctive. One contract now decides applicability for deposit selection, supplement checking, queued
consistency and the legacy projection alike.

**What establishes a binding** (owner decision 1, 2026-09-14): any one of

- the table's own columns name the contrast's arms (for a table pooling several contrasts, the columns
  naming this contrast are its selector);
- a verbatim passage (the paper's text, a supplement legend, deposit metadata) that cites the file and
  names the contrast's arms, verified in its source; a model's proposal counts only when its quote is
  found there;
- a recorded confirmation.

A filename alone keeps a table a candidate, and so does being the only table. Every accepted binding also
establishes the experiment and resolves what distinguishes repeated contrasts: competing contrasts are
every contrast the source serves, across experiments, and matching arm names alone cannot tell two of
them apart. Known contradictory metadata (a name, header or passage that names another contrast of the
paper) rejects the table until recorded evidence resolves the conflict.
"""

from __future__ import annotations

import re

# Bumped whenever a table's binding for the same evidence can change. A binding made under an earlier
# version is not established: its outcome is pending re-evaluation.
BINDING_VERSION = 1

ESTABLISHED = "established"
CANDIDATE = "candidate"
REJECTED = "rejected"
UNRESOLVED = "unresolved"
STATUSES = (ESTABLISHED, CANDIDATE, REJECTED, UNRESOLVED)

_STOP = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "v",
    "versus",
    "vs",
    "with",
}
_EXTENSIONS = {"txt", "tsv", "csv", "gz", "xls", "xlsx", "zip", "bz2", "xz", "tab", "dat"}
_LFC = ("log2", "logfc", "log2fc", "foldchange", "lfc", "fc")
_PADJ = ("padj", "fdr", "qvalue", "adj")
_PVALUE = ("pvalue", "pval")


def _lower_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def tokens(text: str) -> set[str]:
    """Every word a name, header or passage holds, including the words run together around ``vs`` and the
    parts either side of a letter-digit boundary (``SAMD1KOvsWT`` holds ``samd1ko``, ``ko`` and ``wt``)."""
    found: set[str] = set()
    for raw in _lower_tokens(text):
        parts = [raw]
        if "vs" in raw and not raw.startswith("vs") and not raw.endswith("vs"):
            parts.extend(p for p in raw.split("vs") if p)
        for part in list(parts):
            parts.extend(re.findall(r"[a-z]+|[0-9]+", part))
        found.update(parts)
    return found


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _words(*texts) -> set[str]:
    return {w for text in texts for w in _lower_tokens(text) if w not in _STOP}


def mentions(text: str, word: str) -> bool:
    """Whether ``text`` names ``word``: as a word of its own, or, for a word of four or more characters,
    run together with others (``Differentiation-SAMD1KOvsWT`` names ``differentiation``)."""
    return word in tokens(text) or (len(word) >= 4 and word in _squash(text))


def _names_all(text: str, words: set[str]) -> bool:
    return bool(words) and all(mentions(text, w) for w in words)


def _arms_of_name(contrast: dict) -> tuple[str, str]:
    name = str(contrast.get("name") or "")
    parts = re.split(r"\s+(?:vs\.?|versus|v\.?)\s+", name, maxsplit=1, flags=re.IGNORECASE)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def arm_words(contrast: dict) -> tuple[set[str], set[str]]:
    """The words only the test arm has, and the words only the reference arm has."""
    test = contrast.get("test_condition") or ""
    reference = contrast.get("reference_condition") or ""
    if not (test and reference):
        test, reference = _arms_of_name(contrast)
    test_words, reference_words = _words(test), _words(reference)
    return test_words - reference_words, reference_words - test_words


def names_arms(text: str, contrast: dict) -> bool:
    test, reference = arm_words(contrast)
    return _names_all(text, test) and _names_all(text, reference)


def _all_words(contrast: dict) -> set[str]:
    return _words(contrast.get("name"), contrast.get("test_condition"), contrast.get("reference_condition"))


def shares_arms(one: dict, other: dict) -> bool:
    """Two contrasts whose arms read the same: evidence that names one names the other."""
    first, second = arm_words(one), arm_words(other)
    return bool(first[0] and first[1]) and first == second


def own_words(contrast: dict, competitor: dict) -> set[str]:
    """The words that distinguish ``contrast`` from a competitor with the same arms."""
    return _all_words(contrast) - _all_words(competitor)


def _statement(name: str) -> tuple[str, str] | None:
    """The comparison a file's name states (``A_vs_B``, ``A-v-B``, ``AvsB``), as the words either side."""
    words = [w for w in _lower_tokens(name) if w not in _EXTENSIONS]
    for i, word in enumerate(words):
        if word in ("vs", "v", "versus") and 0 < i < len(words) - 1:
            return " ".join(words[max(0, i - 3) : i]), " ".join(words[i + 1 : i + 4])
        if "vs" in word and not word.startswith("vs") and not word.endswith("vs"):
            left, _sep, right = word.partition("vs")
            return " ".join(words[max(0, i - 2) : i] + [left]), " ".join([right] + words[i + 1 : i + 3])
    return None


def _statement_names(statement: tuple[str, str], contrast: dict) -> bool:
    test, reference = arm_words(contrast)
    left, right = statement
    return (_names_all(left, test) and _names_all(right, reference)) or (
        _names_all(left, reference) and _names_all(right, test)
    )


def _statement_contradicts(statement: tuple[str, str], contrast: dict) -> bool:
    """A stated comparison that matches one of this contrast's arms and names a different other arm."""
    test, reference = arm_words(contrast)
    left, right = statement
    if not (test and reference):
        return False
    return (_names_all(right, reference) and not _names_all(left, test) and bool(left.strip())) or (
        _names_all(left, reference) and not _names_all(right, test) and bool(right.strip())
    )


def _citation_keys(text: str) -> set[str]:
    from app.services.supplement_inventory import _citations

    return {c["key"] for c in _citations(text or "")}


def _cites(passage: str, table: dict) -> bool:
    """Whether a passage cites this table, by one of its labels or by its filename."""
    keys = {k for label in table.get("labels") or [] for k in _citation_keys(label)}
    if keys and keys & _citation_keys(passage):
        return True
    name = str(table.get("name") or "")
    return bool(name) and name in (passage or "")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _verified_proposal(proposal: dict | None, sources: dict | None, table: dict) -> dict | None:
    """A model's proposed passage, kept only when its quote is found verbatim in the source it cites."""
    if not isinstance(proposal, dict):
        return None
    quote = _normalized(proposal.get("quote"))
    source = proposal.get("source")
    text = _normalized((sources or {}).get(source))
    if not quote or not text or quote not in text:
        return None
    return {"text": quote, "source": source, "proposed_by": proposal.get("model") or "model"}


def _role_of(column: str) -> str | None:
    squashed = _squash(column)
    if any(w in squashed for w in _PADJ):
        return "padj"
    if any(w in squashed for w in _PVALUE):
        return "pvalue"
    if any(w in squashed for w in _LFC) or "fold" in squashed:
        return "lfc"
    return None


def _selector(header: list[str], contrast: dict) -> dict:
    """The columns naming this contrast's arms, by role."""
    found: dict[str, int] = {}
    for index, column in enumerate(header or []):
        if not names_arms(column, contrast):
            continue
        role = _role_of(column)
        if role and role not in found:
            found[role] = index
    return found


def _source(table: dict) -> dict:
    return {
        "name": table.get("name"),
        "source": table.get("source"),
        "accession": table.get("accession"),
        "supplement_index": table.get("supplement_index"),
        "checksum": table.get("checksum"),
    }


def _result(status: str, table: dict, contrast: dict, *, reason: str | None, evidence=None, **extra) -> dict:
    return {
        "version": BINDING_VERSION,
        "status": status,
        "source": _source(table),
        "experiment": contrast.get("reported_experiment_id"),
        "contrast": {
            "name": contrast.get("name"),
            "test_condition": contrast.get("test_condition"),
            "reference_condition": contrast.get("reference_condition"),
        },
        "evidence": list(evidence or []),
        "reason": reason,
        "selector": extra.pop("selector", None),
        **extra,
    }


def _contradiction(table: dict, contrast: dict, competitors: list[dict], header, passages) -> str | None:
    """Why the table's own metadata names another contrast of the paper, or None."""
    name = str(table.get("name") or "")
    statement = _statement(name)
    others = [c for c in competitors if not shares_arms(c, contrast)]
    same = [c for c in competitors if shares_arms(c, contrast)]
    if statement is not None and not _statement_names(statement, contrast):
        named = next((c for c in others if _statement_names(statement, c)), None)
        if named is not None:
            return f"its name, {name}, states the arms of {named.get('name')}, not this claim's contrast"
        if _statement_contradicts(statement, contrast):
            return f"its name, {name}, states a comparison that is not this claim's contrast"
    if statement is None:
        named = next((c for c in others if names_arms(name, c)), None)
        if named is not None and not names_arms(name, contrast):
            return f"its name names the arms of {named.get('name')}, not this claim's contrast"
    for competitor in same:
        theirs = own_words(competitor, contrast)
        ours = own_words(contrast, competitor)
        word = next((w for w in sorted(theirs) if mentions(name, w)), None)
        if word is not None and not any(mentions(name, w) for w in ours):
            return f"its name names {word}, which distinguishes {competitor.get('name')} from this claim's contrast"
    if header:
        ours = [c for c in header if names_arms(c, contrast)]
        if not ours:
            named = next((c for c in others if any(names_arms(col, c) for col in header)), None)
            if named is not None:
                return f"its columns name the arms of {named.get('name')}, not this claim's contrast"
    if passages:
        if not any(names_arms(p["text"], contrast) for p in passages):
            named = next((c for c in others if any(names_arms(p["text"], c) for p in passages)), None)
            if named is not None:
                return f"the paper's text links it to {named.get('name')}, not to this claim's contrast"
    return None


def bind(
    table: dict,
    contrast: dict,
    *,
    competitors: list[dict],
    header: list[str] | None = None,
    confirmation: dict | None = None,
    proposal: dict | None = None,
    sources: dict | None = None,
) -> dict:
    """The binding of one table to one claim's contrast.

    ``competitors`` are every other contrast the table's source serves, across experiments. ``header`` is
    the table's column names when its bytes are in hand. ``table["passages"]`` are verbatim passages from
    the paper or its legends that cite the table, each with its ``source``. Pure: no database, no model."""
    passages = [
        p for p in (table.get("passages") or []) if isinstance(p, dict) and p.get("text") and _cites(p["text"], table)
    ]
    verified = _verified_proposal(proposal, sources, table)
    if verified is not None and _cites(verified["text"], table):
        passages.append(verified)

    contradiction = _contradiction(table, contrast, competitors, header, passages)
    if contradiction is not None and not (confirmation or {}).get("resolves_conflict"):
        return _result(REJECTED, table, contrast, reason=contradiction)

    if confirmation:
        evidence = [
            {
                "kind": "confirmation",
                "confirmed_by": confirmation.get("confirmed_by"),
                "at": confirmation.get("at"),
                "note": confirmation.get("note") or confirmation.get("resolves_conflict"),
            }
        ]
        return _result(
            ESTABLISHED,
            table,
            contrast,
            reason=None,
            evidence=evidence,
            selector=confirmation.get("selector"),
            experiment_basis="a recorded confirmation",
        )

    header = list(header or [])
    columns = [c for c in header if names_arms(c, contrast)]
    naming = [p for p in passages if names_arms(p["text"], contrast)]
    others = [c for c in competitors if not shares_arms(c, contrast)]
    pooled = any(
        any(names_arms(col, c) for col in header) or any(names_arms(p["text"], c) for p in naming) for c in others
    )

    evidence: list[dict] = []
    selector = None
    if columns:
        evidence.append({"kind": "columns", "columns": columns})
        if pooled:
            selector = _selector(header, contrast)
    elif naming:
        evidence.extend(
            {
                "kind": "passage",
                "text": p["text"],
                "source": p.get("source"),
                **({"proposed_by": p["proposed_by"]} if p.get("proposed_by") else {}),
            }
            for p in naming[:1]
        )
    if not evidence:
        if names_arms(str(table.get("name") or ""), contrast):
            reason = (
                "only its name links it to this claim's contrast; its columns, the paper's text and its legends "
                "do not establish it"
            )
        else:
            reason = (
                "nothing establishes which contrast it reports: its columns do not name the contrast's arms, and "
                "no passage or confirmation links it to this claim"
            )
        return _result(CANDIDATE, table, contrast, reason=reason)

    if pooled and not selector:
        return _result(
            UNRESOLVED,
            table,
            contrast,
            reason="the table reports several contrasts, and none of its columns identifies this claim's contrast's results",
            evidence=evidence,
        )

    # Repeated contrasts: the evidence that names the arms must also say which of the contrasts sharing
    # them this is, and must not name what distinguishes another.
    texts = columns if columns else [p["text"] for p in naming]
    resolved: list[str] = []
    for competitor in (c for c in competitors if shares_arms(c, contrast)):
        ours = own_words(contrast, competitor)
        theirs = own_words(competitor, contrast)
        text = " ".join(texts)
        hits = sorted(w for w in ours if mentions(text, w))
        if not hits or any(mentions(text, w) for w in theirs):
            return _result(
                UNRESOLVED,
                table,
                contrast,
                reason=(
                    f"the evidence names the contrast's arms, which {competitor.get('name')} shares, and nothing "
                    "it holds says which of the two the table reports"
                ),
                evidence=evidence,
            )
        resolved.extend(hits)
    basis = (
        f"the evidence names {', '.join(sorted(set(resolved)))}, which no other contrast with these arms has"
        if resolved
        else "no other contrast this source serves has these arms"
    )
    return _result(
        ESTABLISHED,
        table,
        contrast,
        reason=None,
        evidence=evidence,
        selector=selector,
        experiment_basis=basis,
        qualifiers=sorted(set(resolved)),
    )


def established(value: dict | None) -> bool:
    """A binding this build accepts: established, under the current version."""
    return isinstance(value, dict) and value.get("status") == ESTABLISHED and value.get("version") == BINDING_VERSION


def header_of(text: str | None) -> list[str]:
    """A delimited table's first row, as its column names."""
    first = next((line for line in str(text or "").splitlines() if line.strip()), "")
    delimiter = "\t" if "\t" in first else ","
    return [c.strip().strip('"') for c in first.split(delimiter)] if first else []


# plan_8_2 labels, pending the owner's sign-off.
NOT_THIS_CONTRAST = "this table does not report the claim's contrast"
UNBOUND = "which table reports this claim's contrast is not established"


def binding_reason(bound: dict | None) -> str:
    """Why a claim was not compared with a table, in the report's words."""
    detail = (bound or {}).get("reason")
    head = NOT_THIS_CONTRAST if (bound or {}).get("status") == REJECTED else UNBOUND
    return f"{head}: {detail}" if detail else head


# plan_8_2 section 3.2: evidence that a table is the claim's complete selected list, not its universe or a part.
_ABBREVIATIONS = ("fig.", "figs.", "e.g.", "i.e.", "et al.", "vs.", "no.", "suppl.", "ref.", "refs.", "approx.")
_PARTIAL_WORDS = ("top ", "selected ", "examples", "representative", "a subset", "subset of", "shortlist")


def sentences(text: str) -> list[str]:
    """A passage's sentences, not split after an abbreviation ("Fig. S2C", "et al.")."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", _normalized(text))
    joined: list[str] = []
    for part in parts:
        if joined and joined[-1].lower().endswith(_ABBREVIATIONS):
            joined[-1] = f"{joined[-1]} {part}"
        else:
            joined.append(part)
    return [s for s in joined if s]


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text or "")}


def list_evidence(table: dict, predicate: dict, *, confirmation: dict | None = None) -> dict | None:
    """The sentence that establishes ``table`` as the claim's complete selected list, or None.

    A recorded confirmation that says so establishes it. Otherwise a verified passage citing the table must
    state the claim's own count in the sentence that cites it. A sentence calling the file a part of the
    list ("the top N", "selected", "examples") establishes that it is not the complete list."""
    if (confirmation or {}).get("selected_list"):
        return {"text": confirmation.get("note") or "a recorded confirmation", "source": "confirmation"}
    value = ((predicate or {}).get("count") or {}).get("value")
    if value is None:
        return None
    for passage in table.get("passages") or []:
        if not isinstance(passage, dict) or not _cites(passage.get("text") or "", table):
            continue
        for sentence in sentences(passage["text"]):
            if not _cites(sentence, table) or float(value) not in _numbers(sentence):
                continue
            partial = next((w.strip() for w in _PARTIAL_WORDS if w in sentence.lower()), None)
            return {"text": sentence, "source": passage.get("source"), "partial": partial}
    return None
