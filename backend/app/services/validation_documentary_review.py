"""plan_8_5 section 3.6: the production caller for the rubric's documentary judgments.

`validation_judgment` declared what bioAF may ask a model about one obligation and what it does with
the answer, and nothing ever called it. So every experimental obligation and most of the
computational ones stayed declared capability limits: an implemented response schema is not an
implemented assessor.

This is the caller. It holds to the rules the contract was written for:

- **one obligation per request**, with the paper's own passages in front of the assessor and its own
  words as the question. No holistic rating, no number, no rubric in the paper-reading response;
- **a failure affects its own obligation only.** A provider that is unreachable for one question
  leaves that one leaf grey with its next action, and the other answers stand;
- **nothing is asked without evidence.** No passages, no provider, no judgment, and the reason says
  which it was;
- **a bounded budget**, declared in the decision audit like every other model call bioAF makes.

It asks. What an answer establishes is `validation_judgment`'s to decide, and what it is worth is
the rubric's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import OUTCOME_OK, decide_with_recovery
from app.services.validation_judgment import (
    CONTRACT_VERSION,
    MODEL_ASSISTED,
    JudgmentRefused,
    build_request,
    judgment_from,
)
from app.services.validation_rubric_v3 import UNDETERMINED

logger = logging.getLogger("bioaf.validation_documentary_review")

# The obligations a bounded model review can establish from the paper's own text. Everything else in
# the rubric is measured, or needs a run. C5 is here because "does this operation suit this input"
# is a judgment about documented behaviour; C1 to C4 are read by a parser and never asked.
JUDGED_LEAVES = (
    "S2.A",
    "S2.B",
    "S5.A",
    "E1.A",
    "E1.B",
    "E2.A",
    "E2.B",
    "E3.A",
    "E3.B",
    "M1.A",
    "M1.B",
    "M3.A",
    "M3.B",
    "M5.A",
    "M5.B",
    "C5.A",
    "C5.B",
)

# plan_8_6 sections 3, 6 and 8: one packet per obligation, the request carrying its section and
# scope, and a negative kept where its coverage supports it. All three change what an accepted
# judgment means, so a held review made under the old version is asked again.
REVIEW_VERSION = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# How much of one piece of evidence reaches the assessor. A passage is a sentence or a record, not
# a document: the contract is that every citation resolves to one thing a reader can check. A piece
# longer than this becomes SEVERAL passages; it is never cut short.
MAX_PASSAGE_CHARS = 900

# What bioAF carries into selection, and the last stop against a pathological input. These are NOT
# selection: `validation_evidence_packets` ranks the rows against the obligation being asked and
# reports what its own budget deferred. A cap applied here, before any obligation has ranked
# anything, is evidence bioAF holds and never offers, and every one of these that binds is recorded
# as an omission the obligations that depend on that source can see (section 3, item 3).
#
# Measured on study 65, 2026-09-21: its two supplied sources are 14,357 characters and its manifest
# 2,433, so nothing here binds on it. What bound was the eight-excerpt rule these replace.
MAX_SOURCE_CHARS = 120_000
MAX_CODE_CHARS = 600_000
MIN_SOURCE_SHARE = 4_000
MAX_SAMPLE_RECORDS = 400
MAX_SUPPLEMENTS = 200


def carried_evidence(*, evidence: dict | None, plan: dict | None) -> dict:
    """``{"rows", "omissions"}``: the evidence that is not the article's running text, and what of it did not fit.

    plan_8_6 section 3: `passages_for` handed every obligation one flat list, so a deposit record
    was in front of a preprocessing question and the supplied code was in front of nothing. These
    carry a ``kind``, and `validation_evidence_packets` decides which obligations each kind answers.

    The bounds here are the study's, not the obligation's, and an omission is a
    ``{"needs", "reason"}`` row in the shape `limitations_for` returns: a source bioAF held and
    could not carry whole reaches the packet's coverage the same way a source it never fetched does,
    so section 8 refuses an absence finding over the part nobody read.
    """
    from app.services.validation_evidence_packets import CODE, DEPOSIT, SUPPLEMENT, TOOLS

    evidence, plan = evidence or {}, plan or {}
    rows: list[dict] = []
    omissions: list[dict] = []
    # plan_8_6 section 7: the design facts a comparison rests on, read from the paper's own words.
    # E2.B was verified from a list of comparators because nothing else about the design was ever
    # put in front of it.
    from app.services.validation_design_summary import design_facts

    rows += design_facts(_index_of(evidence))
    held = evidence.get("sample_records") if isinstance(evidence.get("sample_records"), dict) else {}
    for deposit in (held or {}).get("deposits") or []:
        samples = deposit.get("samples") or []
        for sample in samples[:MAX_SAMPLE_RECORDS]:
            described = "; ".join(
                part
                for part in [
                    str(sample.get("title") or ""),
                    str(sample.get("organism") or ""),
                    str(sample.get("source_name") or ""),
                    *[f"{k}: {v}" for k, v in (sample.get("characteristics") or {}).items()],
                    *[str(u) for u in sample.get("unkeyed") or []],
                ]
                if part
            )
            # A record longer than one passage becomes several: a characteristic bioAF cut off is a
            # field of the deposit the assessor was never shown.
            for part, piece in enumerate(_pieces(described), start=1):
                rows.append(
                    {
                        "id": f"{deposit.get('accession')}/{sample.get('accession')}"
                        + (f"#{part}" if part > 1 else ""),
                        "kind": DEPOSIT,
                        "source": f"the deposited record of {deposit.get('accession')}",
                        "text": piece,
                    }
                )
        if len(samples) > MAX_SAMPLE_RECORDS:
            omissions.append(
                {
                    "needs": "deposit",
                    "reason": (
                        f"{len(samples) - MAX_SAMPLE_RECORDS} of {deposit.get('accession')}'s "
                        f"{len(samples)} sample records were not put in front of this obligation"
                    ),
                }
            )
    # plan_8_6 section 4: the code bioAF holds, shown to the obligations that reason about it. M5.B
    # asks whether the inspected methods, configuration and supplied code agree on the consequential
    # parameters, and it cannot be answered by anyone who was not shown the code.
    inspection = evidence.get("code_inspection") if isinstance(evidence.get("code_inspection"), dict) else {}
    files = [
        (str(f.get("path") or f"source {i}"), str(f.get("text") or ""), f)
        for i, f in enumerate(
            ((inspection or {}).get("sources") or []) + ((inspection or {}).get("manifests") or []), 1
        )
        if isinstance(f, dict)
    ]
    # One file does not take the whole study's budget: each is carried up to its share, so the last
    # script is offered as well as the first. Nothing is dropped without a word either way.
    share = max(MIN_SOURCE_SHARE, MAX_CODE_CHARS // len(files)) if files else 0
    packages: list[str] = []
    spent = 0
    for path, text, source in files:
        if str(source.get("language") or "").lower() == "r":
            from app.services.r_parser import read_r

            found = read_r(text)
            packages += found["packages"] + found["namespaced"]
        limit = min(MAX_SOURCE_CHARS, share, max(0, MAX_CODE_CHARS - spent))
        excerpts, left = _excerpts(text, limit=limit)
        spent += sum(len(body) for _, _, body in excerpts)
        for part, (first, last, body) in enumerate(excerpts, start=1):
            rows.append(
                {
                    "id": f"code:{path}#{part}",
                    "kind": CODE,
                    "source": f"the supplied {path}",
                    "location": f"{path} lines {first}-{last}",
                    "text": f"lines {first}-{last}:\n{body}",
                }
            )
        if left:
            omissions.append(
                {
                    "needs": "code",
                    "reason": (
                        f"{left} of {path}'s {len(text)} characters were not put in front of this obligation, "
                        "so what the rest of that file does is not established"
                    ),
                }
            )
    for supplement in (evidence.get("supplements") or [])[:MAX_SUPPLEMENTS]:
        if not isinstance(supplement, dict):
            continue
        for passage in supplement.get("citing_passages") or []:
            text = str((passage or {}).get("text") or "").strip()
            for piece in _pieces(text):
                rows.append(
                    {
                        "id": f"supp:{supplement.get('identity')}#{len(rows)}",
                        "kind": SUPPLEMENT,
                        "source": f"the paper on {supplement.get('label') or supplement.get('identity')}",
                        "text": piece,
                    }
                )
    if len(evidence.get("supplements") or []) > MAX_SUPPLEMENTS:
        omissions.append(
            {
                "needs": "supplements",
                "reason": (
                    f"{len(evidence['supplements']) - MAX_SUPPLEMENTS} of this paper's "
                    f"{len(evidence['supplements'])} supplement records were not put in front of this obligation"
                ),
            }
        )
    tools = [str(t).strip() for t in (plan.get("method") or {}).get("tools") or [] if str(t).strip()]
    declared = sorted(set(tools) | set(packages))
    if declared:
        rows.append(
            {
                "id": "t1",
                "kind": TOOLS,
                "source": "the tools this analysis declares",
                "text": "The analysis declares: " + ", ".join(declared),
            }
        )
    return {"rows": rows, "omissions": omissions}


def extras_for(*, evidence: dict | None, plan: dict | None) -> list[dict]:
    """The rows `carried_evidence` carries, for a caller that does not need its omissions."""
    return carried_evidence(evidence=evidence, plan=plan)["rows"]


def _excerpts(text: str, *, limit: int) -> tuple[list[tuple[int, int, str]], int]:
    """``([(first line, last line, body)], characters left out)``: one file, cut at line boundaries.

    A source file is not one passage: it is carried as excerpts a citation can resolve to a place a
    reader can find. The whole file is carried unless ``limit`` stops it, and what ``limit`` stopped
    is returned rather than dropped quietly.
    """
    lines = (text or "").splitlines()
    if limit <= 0:
        return [], len(text or "")
    found: list[tuple[int, int, str]] = []
    current: list[str] = []
    spent = 0
    carried = 0
    start = 1
    for number, line in enumerate(lines, start=1):
        if spent + len(line) > MAX_PASSAGE_CHARS and current:
            body = "\n".join(current)
            found.append((start, number - 1, body))
            carried += len(body) + 1
            current, spent, start = [], 0, number
            if carried >= limit:
                return found, sum(len(rest) + 1 for rest in lines[number - 1 :])
        current.append(line)
        spent += len(line) + 1
    if current:
        found.append((start, len(lines), "\n".join(current)))
    return found, 0


def _pieces(text: str) -> list[str]:
    """One record as passages of at most `MAX_PASSAGE_CHARS`, split between its fields, not inside one."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= MAX_PASSAGE_CHARS:
        return [text]
    found: list[str] = []
    current = ""
    for field in text.split("; "):
        while len(field) > MAX_PASSAGE_CHARS:
            if current:
                found.append(current)
                current = ""
            found.append(field[:MAX_PASSAGE_CHARS])
            field = field[MAX_PASSAGE_CHARS:]
        if current and len(current) + len(field) + 2 > MAX_PASSAGE_CHARS:
            found.append(current)
            current = ""
        current = f"{current}; {field}" if current else field
    if current:
        found.append(current)
    return found


def limitations_for(evidence: dict | None) -> list[dict]:
    """The sources bioAF tried to get and did not, as the packets' coverage reads them.

    plan_8_6 section 8: an absence finding is a claim about what was looked at. A supplement whose
    bytes never arrived, or a repository nobody fetched, is a source the claim would be about, and
    one that is missing leaves the obligations that need it untested rather than negative.
    """
    evidence = evidence or {}
    found: list[dict] = []
    if not (evidence.get("paper_index") or (evidence.get("paper_passages") or {}).get("methods")):
        found.append({"needs": "text", "reason": "bioAF holds no methods text for this paper"})
    # plan_8_6 section 5: only an ATTACHMENT bioAF did not retrieve. A prose reference is another
    # name for one of them, and counting unresolved aliases as missing files left every obligation's
    # coverage insufficient on a paper whose attachments had all arrived.
    unresolved = [
        s
        for s in evidence.get("supplements") or []
        if isinstance(s, dict) and s.get("kind") == "attachment" and not s.get("resolved")
    ]
    if unresolved:
        ledger = [e for e in evidence.get("retrieval_ledger") or [] if isinstance(e, dict)]
        why = next(
            (str(e.get("outcome")) for e in reversed(ledger) if e.get("outcome") != "retrieved"), "not retrieved"
        )
        found.append(
            {
                "needs": "supplements",
                "reason": (
                    f"{len(unresolved)} of this paper's attachments were not retrieved ({why}), so what they "
                    "carry was not inspected"
                ),
            }
        )
    resolution = evidence.get("code_resolution") if isinstance(evidence.get("code_resolution"), dict) else None
    inspected = (evidence.get("code_inspection") or {}).get("sources") or []
    if not inspected:
        found.append(
            {
                "needs": "code",
                "reason": (
                    (resolution or {}).get("reason")
                    or "bioAF holds no source code for this paper, so nothing was read from it"
                ),
            }
        )
    if not ((evidence.get("sample_records") or {}).get("deposits") or []):
        found.append({"needs": "deposit", "reason": "bioAF holds no independent sample records for this study"})
    return found


def packets_for(*, evidence: dict | None, plan: dict | None, leaves: tuple[str, ...]) -> dict[str, dict]:
    """One bounded evidence packet per obligation, with what it covered.

    plan_8_6 section 3. A study recorded before the index still has its held passages read: the
    bounded methods, statements and claim passages become a one-section index rather than being
    left unassessed.
    """
    from app.services.validation_evidence_packets import packet_for

    index = _index_of(evidence or {})
    carried = carried_evidence(evidence=evidence, plan=plan)
    # A source bioAF held and could not carry whole is a limitation of this study's evidence in the
    # same way a source it never fetched is: section 8 will not report an absence over either.
    limitations = limitations_for(evidence) + carried["omissions"]
    return {leaf: packet_for(leaf, index=index, extras=carried["rows"], limitations=limitations) for leaf in leaves}


def _index_of(evidence: dict) -> dict:
    """This study's evidence index, rebuilt from its held passages when it predates the index."""
    index = evidence.get("paper_index")
    if isinstance(index, dict) and index.get("passages"):
        return index
    from app.services.validation_evidence_index import build_index

    passages = evidence.get("paper_passages") or {}
    entries = []
    if passages.get("methods"):
        entries.append({"title": "Methods", "kind": "methods", "paragraphs": [str(m) for m in passages["methods"]]})
    if passages.get("statements"):
        entries.append(
            {"title": "Sample accounting", "kind": "methods", "paragraphs": [str(s) for s in passages["statements"]]}
        )
    claims = [str((c or {}).get("passage") or "") for c in passages.get("claims") or []]
    if any(claims):
        entries.append({"title": "Results", "kind": "results", "paragraphs": [c for c in claims if c]})
    return build_index("", sections={"index": entries}, source="held_passages")


def _validate(data: dict) -> list[str]:
    """The caller's own schema, which is what earns one semantic re-ask instead of a wasted call."""
    problems = []
    outcome = str((data or {}).get("outcome") or "").strip().lower()
    if outcome not in ("met", "unmet", "cannot_establish"):
        problems.append("`outcome` must be exactly one of met, unmet, cannot_establish")
    if outcome in ("met", "unmet") and not (data or {}).get("citations"):
        problems.append("an answer of met or unmet must cite the ids of the passages it rests on")
    return problems


async def review_documents(
    *,
    passages: list[dict] | None = None,
    packets: dict[str, dict] | None = None,
    client,
    model: str,
    api_key: str | None,
    leaves: tuple[str, ...] = JUDGED_LEAVES,
    on_failure=None,
) -> dict:
    """``{"judgments", "failures", "at", "model", "reason"}``: one judgment per obligation asked.

    ``packets`` is one evidence packet per obligation, as `packets_for` builds them: the passages
    that answer THAT obligation, what did not fit, and what the packet covered. ``passages`` is the
    older shape, one list for every obligation, and is used where no packet exists for a leaf.

    plan_8_6 section 3: an obligation whose packet holds nothing relevant is untested with the
    reason, and costs no model call. An obligation the first packet could not settle is asked once
    more with its expansion, which shares the existing recovery allowance rather than nesting a
    second retry loop inside it.
    """
    accepted: dict[str, dict] = {}
    failures: list[dict] = []
    # plan_8_6 section 11: what this review actually cost, recorded rather than estimated.
    asked = {"requests": 0, "expansions": 0, "skipped_without_evidence": 0}
    packets = packets or {}
    shared = [p for p in passages or [] if isinstance(p, dict) and p.get("id") and str(p.get("text") or "").strip()]
    if not shared and not any((p or {}).get("passages") for p in packets.values()):
        return _nothing("bioAF holds no passages of this paper to judge these obligations on")
    if client is None or not model:
        return _nothing("no language model is configured for this organisation, so nothing was judged")
    for leaf in leaves:
        packet = packets.get(leaf) or {}
        rows = packet.get("passages") or shared
        coverage = packet.get("coverage")
        if not rows:
            reason = (coverage or {}).get("reason") or "bioAF holds no evidence relevant to this obligation"
            failures.append({"leaf": leaf, "reason": reason})
            accepted[leaf] = _untested(reason, coverage)
            asked["skipped_without_evidence"] += 1
            continue
        asked["requests"] += 1
        judged, failure = await _ask(
            leaf,
            rows,
            coverage=coverage,
            client=client,
            model=model,
            api_key=api_key,
            on_failure=on_failure,
        )
        if failure is not None:
            failures.append(failure)
        if judged is None:
            accepted[leaf] = _untested("this obligation could not be asked with the evidence supplied", coverage)
            continue
        expansion = packet.get("expansion") or []
        if judged["outcome"] == UNDETERMINED and expansion and not judged.get("conflict"):
            # One targeted expansion, within the existing per-paper allowance: the passages the
            # first packet's budget or ranking left out, added once. Section 3 item 3.
            widened = rows + expansion
            asked["requests"] += 1
            asked["expansions"] += 1
            again, failure = await _ask(
                leaf,
                widened,
                coverage=coverage,
                client=client,
                model=model,
                api_key=api_key,
                on_failure=on_failure,
            )
            if failure is not None:
                failures.append(failure)
            if again is not None:
                again["expanded"] = True
                judged = again
                rows = widened
        accepted[leaf] = judged
        accepted[leaf]["evidence_ids"] = [p["id"] for p in rows]
    # The owner, 2026-09-21: one demonstrated failure must not be several deductions, and a positive
    # cannot stand on evidence a negative contradicts. Obligations are judged one per request, so
    # neither can be seen from inside a judgment; this is where they are reconciled.
    from app.services.validation_finding_overlap import reconcile_findings

    accepted = reconcile_findings(accepted)
    return {
        "judgments": accepted,
        "failures": failures,
        "at": _now_iso(),
        "model": model,
        "contract_version": CONTRACT_VERSION,
        "review_version": REVIEW_VERSION,
        "asked": asked,
        "evidence_chars": sum(
            len(str(p.get("text") or "")) for packet in packets.values() for p in packet.get("passages") or []
        )
        or sum(len(str(p.get("text") or "")) for p in shared),
        "reason": None,
    }


async def _ask(leaf, rows, *, coverage, client, model, api_key, on_failure):
    """One obligation, asked once. Returns (judgment or None, failure or None). Never raises."""
    try:
        request = build_request(leaf, passages=rows)
    except JudgmentRefused as refusal:
        return None, {"leaf": leaf, "reason": str(refusal)}
    decision = await decide_with_recovery(
        intent=f"judging {request['criterion']} {leaf.rpartition('.')[2]}: {request['title']}",
        system=request["system"],
        payload=request["payload"],
        client=client,
        model=model,
        api_key=api_key,
        purpose=budgets.DOCUMENTARY_JUDGMENT,
        validate=_validate,
    )
    failure = None
    if decision.outcome != OUTCOME_OK:
        failure = {"leaf": leaf, "reason": decision.reason or decision.outcome, "outcome": decision.outcome}
        if on_failure is not None:
            on_failure(decision)
    judged = judgment_from(
        leaf,
        decision.data if decision.outcome == OUTCOME_OK else None,
        passages=rows,
        model=model,
        coverage=coverage,
    )
    return judged, failure


def _untested(reason: str, coverage: dict | None) -> dict:
    """An obligation with nothing to judge it on: grey, with what would settle it, and no model call."""
    return {
        "outcome": UNDETERMINED,
        "rationale": reason,
        "scope": "the evidence bioAF holds for this obligation",
        "method": MODEL_ASSISTED,
        "next_action": "supply or retrieve the sources this obligation is about, then assess it again",
        "coverage": coverage,
        "evidence_ids": [],
    }


def _nothing(reason: str) -> dict:
    return {
        "judgments": {},
        "failures": [],
        "at": _now_iso(),
        "model": None,
        "contract_version": CONTRACT_VERSION,
        "review_version": REVIEW_VERSION,
        "reason": reason,
    }
