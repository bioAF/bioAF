"""change_7.5 sections 3.2 and 3.3: choose the input from the claim and the evidence, and interpret its
samples from evidence.

Study 37 chose a deposited matrix by its filename alone, read `KO Cl16` as condition "KO Cl", replicate
16, and asked for day-7 samples the chosen matrix never held. Filenames are not enough when one deposit
holds several experiments.

**Candidates** are the listed files of the selected experiment's datasets, by kind: matrices are
candidates for reanalysis; result tables go to the consistency route and to the comparison; coverage
tracks and other kinds are not analyzable.

**What the decision is given:** the selected claim with its predicate and contrast, the reported
experiment, the repository's sample records (GSM, title, characteristics) and bounded previews of the
candidate files (open question 6: the header and 5 rows, at most 8 KB per file and 6 files). Previews
are streamed and capped; no whole file is downloaded before selection.

**Deterministic first.** When exactly one candidate matrix's columns resolve both arms by exact
identifiers (a GSM, or a sample title equal to a column name), it is chosen without a model. Otherwise
the model proposes the file and the mapping together, with evidence for each column, and identifies
the authors' result table for the same contrast in the same decision.

**The proposal is validated deterministically** (7.4 section 2.3, carried): every assignment cites
evidence present in the inputs; no column sits in two arms; columns outside the contrast are excluded
explicitly; a trailing number is never replicate identity on its own; a technical group is confirmed
only by evidence beyond a name pattern and never spans two arms, units or time points; and a unit the
evidence does not state holds. Unresolved means hold, with the proposal and its evidence kept.
"""

from __future__ import annotations

import re

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import confidence_of, decide_with_recovery, fenced_json
from app.services.sample_record_attributes import CONTRADICTED
from app.services.sample_record_attributes import UNRESOLVED as ATTRIBUTE_UNRESOLVED
from app.services.sample_record_attributes import condition_match

PREVIEW_ROWS = 5
PREVIEW_BYTES = 8 * 1024
PREVIEW_FILES = 6
# Compressed bytes streamed per file: enough for a header and five rows of any sane table.
STREAM_BYTES = 64 * 1024

INPUT_CHOICE_INTENT = "choosing the deposited file and its sample mapping for the selected claim"

_MATRIX_KINDS = ("matrix_counts", "matrix_normalized")
_TABLE_KINDS = ("de_table", "da_table")
ARMS = ("test", "reference", "excluded")
_TRAILING_NUMBER = re.compile(r"(\d+)\s*$")


def input_candidates(entries) -> dict:
    """The listed files by what they can be used for."""
    matrices, tables, rest = [], [], []
    for entry in entries or []:
        kind = getattr(entry, "classification", None)
        (matrices if kind in _MATRIX_KINDS else tables if kind in _TABLE_KINDS else rest).append(entry)
    return {"matrices": matrices, "tables": tables, "not_analyzable": rest}


def _decode_partial(raw: bytes) -> tuple[str | None, bool, str | None]:
    """``(text, truncated, error)`` from the first bytes of a file. plan_8_2 section 1.2: the shared
    decoder's order (compression, a byte-order mark, strict decoding), over a prefix. A truncated gzip
    stream is decoded as far as it goes; nothing waits for the rest."""
    from app.services.table_decoding import decode_prefix

    return decode_prefix(raw, max_bytes=PREVIEW_BYTES * 4)


async def preview_file(url: str, filename: str, *, stream) -> dict:
    """The file's header and first rows, streamed and capped. Never raises.

    ``stream(url, max_bytes)`` returns at most ``max_bytes`` of the file's first bytes."""
    try:
        raw = await stream(url, STREAM_BYTES)
    except Exception as exc:  # noqa: BLE001 - a preview that could not be read is recorded, not raised
        return {"filename": filename, "header": [], "rows": [], "truncated": True, "error": str(exc)[:200]}
    text, truncated, error = _decode_partial(raw or b"")
    if text is None:
        return {"filename": filename, "header": [], "rows": [], "truncated": True, "error": error}
    lines = text.splitlines()
    if truncated and lines and not text.endswith("\n"):
        lines = lines[:-1]  # a partial last line is not a row
    lines = [ln for ln in lines if ln.strip()][: PREVIEW_ROWS + 1]
    delimiter = "\t" if lines and "\t" in lines[0] else ","
    header = [c.strip().strip('"') for c in lines[0].split(delimiter)] if lines else []
    rows = [[c.strip().strip('"') for c in ln.split(delimiter)] for ln in lines[1:]]
    preview = {
        "filename": filename,
        "header": header,
        "rows": rows,
        "truncated": truncated or len(lines) > PREVIEW_ROWS,
        "error": None,
    }
    # The cap is per file: long headers (hundreds of single-cell barcodes) are clipped, never the rule.
    while len(str(preview)) > PREVIEW_BYTES and (preview["rows"] or len(preview["header"]) > 2):
        if preview["rows"]:
            preview["rows"].pop()
        else:
            preview["header"] = preview["header"][: max(2, len(preview["header"]) // 2)]
        preview["truncated"] = True
    return preview


def _identifiers(pick: str, records: list[dict]) -> set[str]:
    """Every exact identifier a design pick names: itself, and the title and GSM of its record."""
    wanted = str(pick or "").strip()
    found = {wanted}
    for record in records or []:
        ids = {
            str(record.get(k) or "").strip()
            for k in ("geo_accession", "title", "experiment_accession", "sample_accession")
        }
        if wanted in ids:
            found |= ids
    return {i for i in found if i}


def _record_for(column: str, records: list[dict]) -> dict | None:
    return next(
        (r for r in records or [] if column in {str(r.get("geo_accession") or ""), str(r.get("title") or "")}),
        None,
    )


def deterministic_choice(previews: list[dict], *, contrast: dict, sample_records: list[dict]) -> dict | None:
    """The one candidate matrix whose columns resolve both arms by exact identifiers, or None."""
    arms = {"test": contrast.get("test_samples") or [], "reference": contrast.get("reference_samples") or []}
    if not arms["test"] or not arms["reference"]:
        return None
    resolving = []
    for preview in previews:
        columns = list(preview.get("header") or [])[1:]
        mapping = []
        for column in columns:
            arm = next(
                (a for a, picks in arms.items() if any(column in _identifiers(p, sample_records) for p in picks)), None
            )
            record = _record_for(column, sample_records)
            unit = (record or {}).get("geo_accession") or column
            mapping.append(
                {
                    "column": column,
                    "arm": arm or "excluded",
                    "biological_unit": unit if arm else None,
                    "biological_sample": unit if arm else None,
                    "technical_group": None,
                    "time_point": None,
                    "evidence": [{"source": "exact_identifier", "quote": column}],
                    "assumption": ("each deposited sample record is read as its own biological unit" if arm else None),
                }
            )
        if any(r["arm"] == "test" for r in mapping) and any(r["arm"] == "reference" for r in mapping):
            resolving.append((preview, mapping))
    if len(resolving) != 1:
        return None
    preview, mapping = resolving[0]
    return {
        "primary_matrix": preview["filename"],
        "mapping": mapping,
        "decided_by": "deterministic",
        "reason": "the only candidate matrix whose columns name both arms' samples exactly",
        "confidence": 1.0,
    }


def _normalized(text: str) -> str:
    return " ".join(str(text or "").lower().split())


# plan_8_3 stage 3: ONE representation of a source record, used to show it and to check a citation of
# it. The prompt printed `accession | title | characteristics` and the validator searched a corpus
# built as `accession title characteristics`; lowercasing and whitespace normalization do not remove
# the pipes, so every one of study 50's 21 quotations was rejected for citing evidence it had been
# given. A separator is presentation, not evidence.
RECORD_FIELDS = ("geo_accession", "title", "condition")
RECORD_SEPARATOR = " | "
# The separators a record might be shown or quoted with. A citation is matched on its FIELDS, so a
# quote that uses any of them names the same record.
_PRESENTATION_SEPARATORS = ("|", "\t", ";")


def record_reference(record: dict) -> str:
    """One source record as both the prompt and a citation of it spell it."""
    return RECORD_SEPARATOR.join(str(record.get(field) or "-").strip() for field in RECORD_FIELDS)


def _citation_fields(quote: str) -> list[str]:
    """The fields a citation names, however it separated them. The placeholder a record shows for a
    field it does not state carries no information and is not one of them."""
    text = str(quote or "")
    for separator in _PRESENTATION_SEPARATORS:
        text = text.replace(separator, RECORD_SEPARATOR)
    parts = (_normalized(part) for part in text.split(RECORD_SEPARATOR))
    return [part for part in parts if part and part != "-"]


# plan_8_3 stage 5: the words the prompt offers as EXAMPLES of an independent unit. They name what
# kind of thing a unit is. Study 50's answer, `gingival fibroblast culture`, is one of them with a
# tissue in front of it, and `design_from_mapping` read it as a unique identity: six columns became
# one biological sample, and both arms were rejected as unsupported technical replication. Allowed
# through, the same string would have collapsed the pairing instead.
UNIT_TYPE_WORDS = frozenset(
    {
        "animal",
        "mouse",
        "rat",
        "subject",
        "patient",
        "donor",
        "participant",
        "clone",
        "culture",
        "cell line",
        "cell culture",
        "organoid",
        "biological replicate",
        "replicate",
        "sample",
        "embryo",
        "isolate",
        "strain",
        "well",
        "dish",
        "flask",
        "passage",
    }
)
# What tells a type apart from an identity: an identity carries something that distinguishes THIS one
# from the others, a number, a letter, an accession. A type is the category alone, with or without
# words describing the material or the treatment.
_HAS_DISTINGUISHER = re.compile(r"\d|\b[A-Z]{2,}\b")

UNIT_KIND_TYPE = "type"
UNIT_KIND_IDENTITY = "identity"
UNIT_KIND_UNSTATED = "unstated"


def unit_identity(unit: str | None) -> dict:
    """Whether ``unit`` names a PARTICULAR biological unit or only what kind of unit it is.

    ``{"kind": "identity" | "type" | "unstated", "value": str, "type_word": str | None}``. A type is
    never an identity: it cannot tell one of a paper's donors from another, and code that groups
    samples by unit identity must never be handed one.
    """
    value = str(unit or "").strip()
    if not value:
        return {"kind": UNIT_KIND_UNSTATED, "value": "", "type_word": None}
    lowered = _normalized(value)
    word = next(
        (w for w in sorted(UNIT_TYPE_WORDS, key=len, reverse=True) if re.search(rf"\b{re.escape(w)}\b", lowered)),
        None,
    )
    if word is None:
        return {"kind": UNIT_KIND_IDENTITY, "value": value, "type_word": None}
    # The type word is there. What is left over decides: a number or an accession-like token makes it
    # a particular one ("donor 3", "clone Cl16"); descriptive words do not ("gingival fibroblast
    # culture treated with Mucoderm" is still every one of them).
    remainder = re.sub(rf"\b{re.escape(word)}\b", " ", value, flags=re.I)
    if _HAS_DISTINGUISHER.search(remainder):
        return {"kind": UNIT_KIND_IDENTITY, "value": value, "type_word": word}
    return {"kind": UNIT_KIND_TYPE, "value": value, "type_word": word}


ASSISTANCE_UNIT_IDENTITY = "unit_identity_confirmed"


def _squashed(text: str) -> str:
    """Letters and digits only, lowercased, so punctuation and spacing cannot hide a match. Deposits
    write the same identity as `culture: WT1`, `WT-1` and `WT 1`."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# An alphanumeric run that distinguishes one unit from another: it carries a digit, or two or more
# capitals. `1` in `donor 1` is one; `the` is not.
_DISTINGUISHING_TOKEN = re.compile(r"[A-Za-z0-9]*(?:\d[A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9]*)")


def _identity_is_stated(unit: str, row: dict, cited_records: list[dict], corpus: str) -> bool:
    """Whether something the row cites actually states this unit identity.

    A unit identity is a fact about the experiment, and the only places it can come from are the
    records the row cites, the text the decision was given, and a recorded confirmation. It is never
    read off a column's order, its trailing digits or a repeating filename pattern.

    The words are the depositor's, so a match is on SUBSTANCE, not spelling: the sources must name
    what kind of unit it is where the identity says so ("clone Cl16" needs the sources to talk about
    clones), and every token that tells this unit from another ("Cl16", "WT1", "3"). That is what
    refuses `donor 1` for a sample whose records name a tissue, a cell type and a treatment and no
    donor at all, while accepting `WT culture 1` for one whose records say `culture: WT1`.
    """
    identity = unit_identity(unit)
    if identity["kind"] != UNIT_KIND_IDENTITY:
        return False
    stated = " ".join(
        [
            *(
                _squashed(" ".join([*record["fields"].values(), *sorted(record["identifiers"])]))
                for record in cited_records
            ),
            _squashed(corpus),
            *(
                _squashed(item.get("quote"))
                for item in row.get("evidence") or []
                if isinstance(item, dict) and str(item.get("source") or "") == "confirmation"
            ),
        ]
    )
    if not stated.strip():
        return False
    word = identity["type_word"]
    if word and _squashed(word) not in stated:
        return False
    remainder = re.sub(rf"\b{re.escape(word)}\b", " ", unit, flags=re.I) if word else unit
    tokens = [t for t in _DISTINGUISHING_TOKEN.findall(remainder) if t]
    if not tokens:
        return _squashed(unit) in stated
    return all(_squashed(token) in stated for token in tokens)


def source_records(records: list[dict]) -> list[dict]:
    """The repository's sample records as the decision sees them: the record, its reference, and the
    fields a citation of it must match."""
    shown = []
    for record in records or []:
        shown.append(
            {
                "record": record,
                "reference": record_reference(record),
                "fields": {field: _normalized(record.get(field)) for field in RECORD_FIELDS},
                "identifiers": {
                    _normalized(record.get(key))
                    for key in ("title", "geo_accession", "run_accession", "sample_accession", "experiment_accession")
                    if str(record.get(key) or "").strip()
                },
            }
        )
    return shown


def _cited_record(quote: str, shown: list[dict]) -> tuple[dict | None, str | None]:
    """``(record, problem)`` for a citation of a sample record.

    A citation names a record when the record's own identifier is among its fields. Whether the REST
    of the citation matches that record is the second question, and it has its own answer: a real
    record quoted with a treatment it does not state is a changed quotation, not a missing record.
    """
    fields = _citation_fields(quote)
    if not fields:
        return None, "cites nothing"
    # A citation names a record when one of that record's own identifiers is among its fields, or
    # inside one: a quote whose separators were dropped is one long field naming the same record.
    named = [
        entry for entry in shown if any(identifier in field for field in fields for identifier in entry["identifiers"])
    ]
    if not named:
        return None, "names no sample record this deposit holds"
    for entry in named:
        stated = _normalized(" ".join([*entry["fields"].values(), *sorted(entry["identifiers"])]))
        if all(field in stated for field in fields):
            return entry, None
    return named[0], "does not match what that record states"


def validate_mapping(
    mapping: list[dict],
    *,
    columns: list[str],
    sample_records: list[dict],
    texts: list[str] | None = None,
    contrast: dict | None = None,
    confirmed_units: dict[str, str] | None = None,
) -> dict:
    """Accept a proposed mapping only when the evidence supports every assignment.

    plan_8_3 stage 3: a citation is checked against the RECORD it names, not against a corpus of
    every record's words run together. Three questions are answered separately, because they have
    three different remedies: does the cited record exist, does it state what the citation says, and
    does it describe the column it was attached to. Treatment compatibility with the contrast is a
    fourth, checked where the contrast is given.

    Returns ``{"status": "accepted" | "unresolved", "reasons": [...], "mapping": rows}``."""
    reasons: list[str] = []
    shown = source_records(sample_records)
    corpus = _normalized(" ".join(texts or []))
    column_names = {_normalized(c) for c in columns}
    every_identifier = {identifier for entry in shown for identifier in entry["identifiers"]}
    assistance: str | None = None
    rows = [r for r in mapping or [] if isinstance(r, dict)]
    seen: dict[str, int] = {}
    for row in rows:
        seen[str(row.get("column"))] = seen.get(str(row.get("column")), 0) + 1
    for column, n in seen.items():
        if n > 1:
            reasons.append(f"column {column} is assigned more than once")
    for column in columns:
        if column not in seen:
            reasons.append(f"column {column} is neither assigned to an arm nor excluded explicitly")
    for column in seen:
        if column not in columns:
            reasons.append(f"the proposal names {column}, which the matrix does not hold")

    groups: dict[str, list[dict]] = {}
    for row in rows:
        column, arm = str(row.get("column")), row.get("arm")
        if arm not in ARMS:
            reasons.append(f"column {column} has no arm the contrast defines")
            continue
        evidence = [e for e in row.get("evidence") or [] if isinstance(e, dict) and str(e.get("quote") or "").strip()]
        if not evidence:
            reasons.append(f"column {column} cites no evidence")
            continue
        cited_records = []
        for item in evidence:
            source = str(item.get("source") or "").strip()
            quote = str(item["quote"])
            if source == "sample_record":
                record, problem = _cited_record(quote, shown)
                if problem:
                    reasons.append(f'column {column} cites "{quote}", which {problem}')
                    continue
                cited_records.append(record)
                # A matrix names its own columns, and they often are not the repository's titles, so
                # a citation is the decision's stated identity for a column bioAF cannot link by
                # name. What it may NOT do is attach a record to a column that is another record's:
                # that is a quote from the wrong sample, and it is refused.
                if _normalized(column) in every_identifier and _normalized(column) not in record["identifiers"]:
                    reasons.append(
                        f'column {column} cites "{quote}", which describes {record["record"].get("title") or "another sample"}, '
                        f"not column {column}"
                    )
            elif source == "column_name":
                if _normalized(quote) != _normalized(column):
                    reasons.append(
                        f'column {column} cites the column name "{quote}", which is not the name of column {column}'
                    )
            elif source == "exact_identifier":
                if _normalized(quote) not in column_names:
                    reasons.append(f'column {column} cites the identifier "{quote}", which the matrix does not hold')
            elif source == "confirmation":
                # plan_8_3 stage 5: a recorded confirmation is checked against what was recorded, not
                # against the paper. A citation of one bioAF holds no record of establishes nothing.
                recorded = _normalized((confirmed_units or {}).get(column))
                if not recorded or recorded not in _normalized(quote):
                    reasons.append(f'column {column} cites a confirmation, "{quote}", that bioAF holds no record of')
            elif _normalized(quote) not in corpus:
                reasons.append(f'column {column} cites "{quote}", which is not in the text given to the decision')

        if arm in ("test", "reference") and contrast:
            # plan_8_3 section 3.1: the arm's condition is compared to the ATTRIBUTES the record
            # states, not to its prose. A paper writes "SAMD1 KO mouse ES cells" and the repository
            # writes "genotype: SAMD1KO", so asking whether the phrase occurs in the record's text
            # refused every row of study 56's correct mapping, the reference arm included.
            wanted = contrast.get("test_condition") if arm == "test" else contrast.get("reference_condition")
            for record in cited_records:
                found = condition_match(wanted, record["record"], sample_records)
                if found["status"] == CONTRADICTED:
                    reasons.append(
                        f"column {column} is put in the {arm} arm, whose condition is \"{wanted}\", and the "
                        f'{found["attribute"]} its sample record states is "{found["stated"]}"'
                    )
                elif found["status"] == ATTRIBUTE_UNRESOLVED:
                    reasons.append(
                        f"column {column} is put in the {arm} arm, and bioAF cannot establish that its sample "
                        f'record states the arm\'s condition ("{wanted}"): '
                        + (
                            f'it states no {found["attribute"]}'
                            if found["attribute"]
                            else "no attribute its records state carries that condition"
                        )
                        + f" (the records state {', '.join(found['available'])})"
                    )
        if arm == "excluded":
            continue
        unit = str(row.get("biological_unit") or "").strip()
        sources = {str(e.get("source") or "") for e in evidence}
        group = row.get("technical_group")
        if group:
            # Registered before the unit checks, so a group that rests on a name pattern is reported
            # whether or not the same row's unit identity also failed.
            groups.setdefault(str(group), []).append({**row, "_sources": sources})
        if not unit:
            reasons.append(f"the evidence does not state the biological unit of column {column}")
            continue
        # The most specific rule first: a trailing number taken off the column's own name is the one
        # inference that has its own sentence, and it is never replicate identity.
        trailing = _TRAILING_NUMBER.search(column)
        if sources <= {"column_name"} and trailing and unit in (trailing.group(1), column):
            reasons.append(
                f"column {column}: a trailing number is a candidate token, never replicate identity on its own"
            )
            continue
        # plan_8_3 stage 5: a unit TYPE is not a unit identity. It is the answer to "what kind of
        # thing is this", and the design code groups samples by unit identity.
        identity = unit_identity(unit)
        if identity["kind"] == UNIT_KIND_TYPE:
            reasons.append(
                f'column {column} states its biological unit as "{unit}", which is the kind of unit it is, not '
                f"which {identity['type_word']} it is; every column carrying the same words would be one unit"
            )
            continue
        # An identity has to come from somewhere. A confirmation supplies one the sources do not, and
        # is recorded as assistance; otherwise the cited evidence has to state it.
        confirmed = str((confirmed_units or {}).get(column) or "").strip()
        if confirmed and _normalized(confirmed) == _normalized(unit):
            assistance = ASSISTANCE_UNIT_IDENTITY
        elif not _identity_is_stated(unit, row, cited_records, corpus):
            reasons.append(
                f'column {column} states its biological unit as "{unit}", which nothing it cites states; a unit '
                "identity is never read off a sample's order, its trailing digits or a repeating filename pattern"
            )
            continue

    for group, members in groups.items():
        if any(m["_sources"] <= {"column_name"} for m in members):
            reasons.append(
                f"technical group {group} rests on a name pattern, which never confirms a technical replicate"
            )
        for field, words in (("arm", "arms"), ("biological_unit", "biological units"), ("time_point", "time points")):
            if len({str(m.get(field) or "") for m in members}) > 1:
                reasons.append(f"technical group {group} spans two {words}")

    test = [r for r in rows if r.get("arm") == "test"]
    reference = [r for r in rows if r.get("arm") == "reference"]
    if not test or not reference:
        reasons.append("the proposal leaves an arm with no column")
    return {
        "status": "unresolved" if reasons else "accepted",
        "reasons": sorted(set(reasons)),
        "mapping": rows,
        # plan_8_3 stage 5: what a person supplied that the published sources did not. A result carrying
        # this is assisted, and is never reported as unattended validation.
        "assistance": None if reasons else assistance,
    }


def build_input_prompt(
    *,
    claim: dict,
    predicate_words: str | None,
    contrast: dict,
    experiment: dict | None,
    sample_records: list[dict],
    previews: list[dict],
    tables: list[str],
) -> tuple[str, str]:
    system = (
        "You are choosing which deposited file reproduces ONE of a paper's claims, which of its columns are "
        "the contrast's two arms, and which deposited file is the authors' own result table for the same "
        "contrast. You are given the claim, its statistical definition, the contrast, the experiment, the "
        "repository's sample records and a preview (header and first rows) of each candidate file.\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"primary_matrix": "exact filename", "author_table": "exact filename or null", '
        '"mapping": [{"column": "exact column name", "arm": "test | reference | excluded", '
        '"biological_unit": "WHICH independent unit this column measured, named as the sources name it '
        '(donor D2, animal 7, clone Cl16, culture WT1) - never what KIND of unit it is", '
        '"biological_sample": "the unit under one condition at one time point", '
        '"technical_group": "a label shared by repeated measurements of one biological sample, or null", '
        '"time_point": "as the evidence states it, or null", '
        '"evidence": [{"source": "sample_record | column_name | metadata | methods", "quote": "exact words from the inputs"}]}], '
        '"reason": "one sentence", "confidence": 0.0 to 1.0}\n\n'
        "Rules:\n"
        "- Choose the matrix measured on the contrast's own conditions and time point. Use filenames EXACTLY "
        "as listed; never invent one, and never choose a result table as the matrix.\n"
        "- Assign EVERY column of the chosen matrix except its identifier column: to an arm, or excluded "
        "when it belongs to a condition or time point outside the contrast.\n"
        "- Every assignment quotes its evidence exactly from the inputs. To cite a sample record, "
        "quote its whole line as shown, separators and all.\n"
        "- A clone is not a replicate, and a trailing number in a column name is never replicate identity on "
        "its own. State the biological unit only as the evidence defines it.\n"
        "- biological_unit must say WHICH one, not what kind. A phrase that would be true of every column "
        "(a cell type, a tissue, a culture) identifies nothing, and bioAF will refuse it. Where the sources "
        "do not say which unit a column came from, say so in the reason rather than inventing one, and never "
        "read it off a column's order, its trailing digits or a repeating name pattern.\n"
        "- A technical group is only repeated measurements of ONE biological sample under one condition "
        "and one time point that the evidence names as such; a name pattern never confirms one."
    )
    # plan_8_3 stage 3: the canonical record representation. What is shown here is exactly what a
    # citation of it is checked against.
    records = "\n".join(f"  {entry['reference']}" for entry in source_records(sample_records)[:60])
    shown = []
    for preview in previews:
        rows = "\n".join("    " + "\t".join(row) for row in preview.get("rows") or [])
        shown.append(
            f"  {preview['filename']}\n    " + "\t".join(preview.get("header") or []) + ("\n" + rows if rows else "")
        )
    payload = (
        f"The paper's claim: {claim.get('claim_text')}\n"
        f"Its statistical definition: {predicate_words or 'not stated'}\n"
        f"The contrast: {contrast.get('name')} ({contrast.get('test_condition') or '?'} versus "
        f"{contrast.get('reference_condition') or '?'})\n"
        f"The experiment: {(experiment or {}).get('id')}: {(experiment or {}).get('assay') or 'not stated'}\n\n"
        f"The repository's sample records (accession | title | characteristics):\n{records or '  none'}\n\n"
        "Candidate files, previewed:\n" + "\n".join(shown) + "\n\n"
        f"Result tables listed: {', '.join(tables) or 'none'}"
    )
    return system, payload


async def choose_input(
    entries,
    *,
    stream,
    claim: dict,
    predicate_words: str | None,
    contrast: dict,
    experiment: dict | None,
    sample_records: list[dict],
    client,
    model: str,
    api_key: str | None,
    on_issue=None,
) -> dict:
    """The input decision: ``{primary_matrix, matrix_files, author_table, mapping, mapping_validation,
    previews, decided_by, reason, confidence, model}``. ``primary_matrix`` is None when nothing was
    chosen, with the reason."""
    found = input_candidates(entries)
    matrices, tables = found["matrices"], found["tables"]
    previewed = (matrices + tables)[:PREVIEW_FILES]
    previews = [await preview_file(e.url, e.filename, stream=stream) for e in previewed]
    by_name = {p["filename"]: p for p in previews}
    base = {
        "primary_matrix": None,
        "matrix_files": [],
        "metadata_file": None,
        "value_type": "unknown",
        "author_table": None,
        "mapping": [],
        "mapping_validation": None,
        "previews": previews,
        "decided_by": None,
        "reason": "",
        "confidence": None,
        "model": None,
        "declined": False,
    }
    if not matrices:
        return {**base, "reason": "the deposit lists no matrix a reanalysis could read"}

    chosen = deterministic_choice(
        [by_name[e.filename] for e in matrices if e.filename in by_name],
        contrast=contrast,
        sample_records=sample_records,
    )
    author_table = tables[0].filename if len(tables) == 1 else None
    if chosen is None:
        system, payload = build_input_prompt(
            claim=claim,
            predicate_words=predicate_words,
            contrast=contrast,
            experiment=experiment,
            sample_records=sample_records,
            previews=previews,
            tables=[e.filename for e in tables],
        )
        decision = await decide_with_recovery(
            intent=INPUT_CHOICE_INTENT,
            system=system,
            payload=payload,
            client=client,
            model=model,
            api_key=api_key,
            purpose=budgets.INPUT_CHOICE,
        )
        if not decision.ok:
            if on_issue:
                on_issue(decision.as_issue(impact="degraded"))
            return {**base, "reason": "the input decision could not be read"}
        data = fenced_json(decision.text) or {}
        matrix = str(data.get("primary_matrix") or "").strip()
        if matrix not in {e.filename for e in matrices}:
            return {
                **base,
                "reason": f"the model chose {matrix or 'nothing'}, which is not a matrix this deposit lists",
                "decided_by": "model",
                "model": model,
            }
        named_table = str(data.get("author_table") or "").strip()
        author_table = named_table if named_table in {e.filename for e in tables} else None
        chosen = {
            "primary_matrix": matrix,
            "mapping": [r for r in data.get("mapping") or [] if isinstance(r, dict)],
            "decided_by": "model",
            "reason": str(data.get("reason") or "").strip(),
            "confidence": confidence_of(data.get("confidence")),
        }
    columns = list((by_name.get(chosen["primary_matrix"]) or {}).get("header") or [])[1:]
    validation = validate_mapping(
        chosen["mapping"],
        columns=columns,
        sample_records=sample_records,
        texts=[claim.get("claim_text") or "", (experiment or {}).get("assay") or ""],
    )
    return {
        **base,
        **chosen,
        "matrix_files": [chosen["primary_matrix"]],
        "author_table": author_table,
        "mapping_validation": validation,
        "model": model if chosen["decided_by"] == "model" else None,
    }


def design_from_mapping(
    design: dict, mapping: list[dict], *, contrast_index: int | None
) -> tuple[dict, str, str | None]:
    """Rewrite the SELECTED contrast from a validated mapping: its arms, each column's biological unit
    and technical group, and a declared pairing carried by unit. ``(design, status, reason)`` with
    status ``ok``, ``unsupported`` (the design cannot be modelled as the evidence describes it) or
    ``pairing_lost`` (the declared pairing does not survive the rewrite).

    7.4 section 2.3: collapse only within a technical group whose columns share unit, arm and time
    point; several biological samples of one unit in one arm are not technical replicates and hold; a
    declared pairing takes its labels from the unit and is checked after the rewrite.
    """
    from app.services.reproduction_plan_service import validate_paired_designs

    contrasts = list((design or {}).get("contrasts") or [])
    if not isinstance(contrast_index, int) or not 0 <= contrast_index < len(contrasts):
        return design or {}, "ok", None
    rows = [r for r in mapping or [] if isinstance(r, dict) and r.get("arm") in ("test", "reference")]
    contrast = dict(contrasts[contrast_index])
    test = [str(r["column"]) for r in rows if r["arm"] == "test"]
    reference = [str(r["column"]) for r in rows if r["arm"] == "reference"]
    if not test or not reference:
        return design or {}, "unsupported", "Held before running: the mapping leaves an arm with no column."

    # plan_8_3 stage 5: a generic unit TYPE never reaches the code that groups samples by unit
    # identity. It used to, and the arms then looked like repeated measurements of one sample, which
    # was reported as an unsupported experimental design rather than as the missing fact it is.
    generic = [
        (str(r["column"]), unit_identity(r.get("biological_unit")))
        for r in rows
        if unit_identity(r.get("biological_unit"))["kind"] != UNIT_KIND_IDENTITY
    ]
    if generic:
        columns = ", ".join(column for column, _ in generic)
        kinds = sorted({identity["type_word"] or "unit" for _, identity in generic})
        return (
            design or {},
            "unresolved_identity",
            "Held before running: the biological unit of "
            f"{columns} is stated as what kind of unit it is, not which {' or '.join(kinds)} it is. "
            "bioAF cannot tell the paper's units apart from that, and it does not infer them from the "
            "samples' order or their names.",
        )

    problems: list[str] = []
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("technical_group"):
            groups.setdefault(str(row["technical_group"]), []).append(row)
    for group, members in groups.items():
        for field, words in (
            ("arm", "conditions"),
            ("biological_unit", "biological units"),
            ("time_point", "time points"),
        ):
            if len({str(m.get(field) or "") for m in members}) > 1:
                problems.append(f"technical group {group} spans two {words}")

    # Within an arm, one unit may contribute one biological sample; its columns must be one technical group.
    by_unit: dict[tuple[str, str], dict[str, set]] = {}
    for row in rows:
        key = (row["arm"], str(row.get("biological_unit")))
        sample = str(
            row.get("biological_sample") or f"{row.get('biological_unit')}|{row['arm']}|{row.get('time_point')}"
        )
        entry = by_unit.setdefault(key, {"samples": set(), "ungrouped": set()})
        entry["samples"].add(sample)
        if not row.get("technical_group"):
            entry["ungrouped"].add(str(row["column"]))
    for (arm, unit), entry in by_unit.items():
        if len(entry["samples"]) > 1:
            problems.append(
                f"the {arm} arm holds {len(entry['samples'])} biological samples of one unit ({unit}); they are not "
                "technical replicates, and the templates cannot model replication within a unit"
            )
        elif len(entry["ungrouped"]) > 1:
            problems.append(
                f"columns {', '.join(sorted(entry['ungrouped']))} measure one biological sample of {unit}, and the "
                "evidence does not confirm them as technical replicates"
            )
    if problems:
        return design or {}, "unsupported", "Held before running: " + "; ".join(problems) + "."

    units = {str(r["column"]): str(r.get("biological_unit")) for r in rows}
    contrast.update(
        test_samples=test,
        reference_samples=reference,
        units=units,
        # plan_8_3 stage 5: what the unit identities ARE. A repository sample accession establishes
        # repository identity, not donor identity, and the difference decides whether a paired model
        # is available at all.
        unit_basis=_unit_basis(units, rows),
    )
    technical = {str(r["column"]): str(r["technical_group"]) for r in rows if r.get("technical_group")}
    if technical:
        contrast["technical_groups"] = technical
    else:
        contrast.pop("technical_groups", None)
    if contrast.get("subjects"):
        contrast["subjects"] = {str(r["column"]): str(r.get("biological_unit")) for r in rows}
    contrasts[contrast_index] = contrast
    rewritten = {**(design or {}), "contrasts": contrasts}
    if contrast.get("subjects"):
        errors = validate_paired_designs({"contrasts": [contrast]})
        if errors:
            return (
                rewritten,
                "pairing_lost",
                "Held before running: the contrast is a paired design, and the pairing does not hold on the mapped "
                f"columns ({' '.join(errors)}). A paired design is never run unpaired.",
            )
    return rewritten, "ok", None


async def propose_mapping(
    matrix: str,
    columns: list[str],
    *,
    claim: dict,
    predicate_words: str | None,
    contrast: dict,
    experiment: dict | None,
    sample_records: list[dict],
    client,
    model: str,
    api_key: str | None,
    on_issue=None,
) -> dict:
    """Re-enter mapping for an input already chosen and acquired ("Review and resume" after an
    unresolved mapping). The same decision, with one candidate: the acquired matrix's own columns."""
    system, payload = build_input_prompt(
        claim=claim,
        predicate_words=predicate_words,
        contrast=contrast,
        experiment=experiment,
        sample_records=sample_records,
        previews=[{"filename": matrix, "header": ["(identifier)", *columns], "rows": []}],
        tables=[],
    )
    decision = await decide_with_recovery(
        intent=INPUT_CHOICE_INTENT,
        system=system,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
        purpose=budgets.MAPPING_PROPOSAL,
    )
    if not decision.ok:
        if on_issue:
            on_issue(decision.as_issue(impact="degraded"))
        return {
            "mapping": [],
            "mapping_validation": {
                "status": "unresolved",
                "reasons": ["the mapping decision could not be read"],
                "mapping": [],
            },
        }
    data = fenced_json(decision.text) or {}
    mapping = [r for r in data.get("mapping") or [] if isinstance(r, dict)]
    validation = validate_mapping(
        mapping, columns=columns, sample_records=sample_records, texts=[claim.get("claim_text") or ""]
    )
    return {
        "mapping": mapping,
        "mapping_validation": validation,
        "decided_by": "model",
        "model": model,
        "reason": str(data.get("reason") or "").strip(),
        "confidence": confidence_of(data.get("confidence")),
    }


def scoped_records(records: list[dict], experiment: dict | None) -> list[dict]:
    """The repository's sample records scoped to the experiment: those whose declared library
    strategy its workflow consumes, when the records declare one; all of them otherwise."""
    from app.services.pipeline_mapper import route_for_library_strategy

    workflow = (experiment or {}).get("workflow")
    declared = [r for r in records or [] if str(r.get("library_strategy") or "").strip()]
    if not workflow or not declared:
        return list(records or [])
    kept = []
    for record in records or []:
        route = route_for_library_strategy(record.get("library_strategy"))
        if route is None or workflow in route.compatible:
            kept.append(record)
    return kept or list(records or [])


def associations_from_mapping(mapping: list[dict], contrast: dict) -> list[dict]:
    """The validated mapping as association rows, each with its arm, identities and evidence."""
    conditions = {"test": contrast.get("test_condition"), "reference": contrast.get("reference_condition")}
    rows = []
    for row in mapping or []:
        evidence = "; ".join(
            f'{e.get("source")}: "{e.get("quote")}"' for e in row.get("evidence") or [] if isinstance(e, dict)
        )
        rows.append(
            {
                "column": row.get("column"),
                "sample_accession": None,
                "arm": row.get("arm"),
                "condition": conditions.get(row.get("arm")),
                "biological_unit": row.get("biological_unit"),
                "biological_sample": row.get("biological_sample"),
                "technical_group": row.get("technical_group"),
                "time_point": row.get("time_point"),
                "replicate": None,
                "batch": None,
                "source": "input_choice",
                "reason": evidence or "assigned by the input decision",
                "confidence": 1.0,
            }
        )
    return rows


# What a set of unit identities rests on. Recorded on the contrast so no later reader can mistake one
# for the other: three distinct repository samples per arm are three samples, not three donors.
UNIT_BASIS_REPOSITORY = "repository_sample"
UNIT_BASIS_SOURCE = "source_stated"
UNIT_BASIS_CONFIRMED = "confirmed"

_ACCESSION = re.compile(r"^(?:GSM|SRR|ERR|DRR|SRX|ERX|SAMN|SAMEA|SAMD|EGAF|EGAN)\d+$", re.I)


def _unit_basis(units: dict[str, str], rows: list[dict]) -> str:
    """Whether these unit identities are repository sample records, the sources' own words, or a
    person's recorded confirmation."""
    if any(
        str(item.get("source") or "") == "confirmation"
        for row in rows
        for item in row.get("evidence") or []
        if isinstance(item, dict)
    ):
        return UNIT_BASIS_CONFIRMED
    values = [v for v in units.values() if v]
    if values and all(_ACCESSION.match(v) for v in values):
        return UNIT_BASIS_REPOSITORY
    return UNIT_BASIS_SOURCE
