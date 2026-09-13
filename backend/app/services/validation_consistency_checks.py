"""plan_8_1 section 3.3: consistency with the authors' results, for every claim with an identified table.

Author-table consistency ran only after approval and acquisition, and only for claims on the selected
contrast: a deposit listing four DESeq2 result tables had one of them checked. Change_7.5 decided on
consistency for every claim with an identified table (open question 3); this completes it.

- **Which table.** For each claim, the candidates are the result tables its experiment's deposits list,
  and the paper's retrieved results supplements. One candidate is the table. Of several, the one that
  NAMES the claim's contrast is: a table names a contrast when its name carries a word only that contrast
  has (or, when it has none, every word it has) and no word only another contrast has. The input choice's
  identification for the selected contrast is taken as it stands. Anything else is unresolved, with the
  candidates listed, never a guess.
- **One record per claim** (``AUTHOR_RESULTS``), never shared: two claims on one contrast keep their own
  predicate and outcome. Their table is downloaded once and read once.
- **Within bioAF's limits for checks before approval** (D4): total downloaded bytes, decompressed size,
  execution time and model spend, per study. A check past a limit is unresolved and says so.
- A failed fetch leaves that record unresolved (uncertainty, never an absence); every other record stands.

The result is consistency with the authors' results. It is never presented as reproduction.
"""

from __future__ import annotations

import logging
import re
import time
import zlib

from app.services import validation_check_queue as queue

logger = logging.getLogger("bioaf.validation_consistency_checks")

# D4, per study, for every check run before approval. Consistency asks no model, so its model spend is
# zero; a check that would need one does not run here.
PRELIMINARY_LIMITS = {
    "total_bytes": 200 * 1024 * 1024,
    "decompressed_bytes": 800 * 1024 * 1024,
    "seconds": 600.0,
    "model_calls": 0,
}
# plan_8_1 label, pending the owner's sign-off.
LIMIT_REASON = "exceeds bioAF's limit for checks run before approval"
UNIDENTIFIED_REASON = "which table reports this claim's contrast is not established"

_STOP = {"and", "the", "versus", "with", "for", "vs"}


def _words(*texts) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for word in re.findall(r"[a-z0-9]+", str(text or "").lower()):
            if len(word) >= 3 and word not in _STOP:
                found.add(word)
    return found


def _squash(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _contrast_words(contrast: dict | None) -> set[str]:
    contrast = contrast or {}
    return _words(contrast.get("name"), contrast.get("test_condition"), contrast.get("reference_condition"))


def names_contrast(table_name: str, contrast: dict, others: list[dict]) -> bool:
    """Whether a table's name names this contrast among the experiment's other contrasts."""
    squashed = _squash(table_name)
    own = _contrast_words(contrast)
    other_words = [_contrast_words(o) for o in others]
    distinguishing = own - set().union(*other_words) if other_words else own
    foreign = set().union(*(words - own for words in other_words)) if other_words else set()
    if any(word in squashed for word in foreign):
        return False
    if distinguishing:
        return any(word in squashed for word in distinguishing)
    return bool(own) and all(word in squashed for word in own)


def _deposit_table_url(accession: str, filename: str) -> str | None:
    from app.services.literature.deposit_inventory_service import series_suppl_url

    base = series_suppl_url(accession)
    return f"{base}{filename}" if base else None


def candidate_tables(
    target: dict, *, experiment: dict | None, resources: list[dict], deposits: list[dict], supplements: list[dict]
) -> list[dict]:
    """The result tables that could report this claim: its experiment's deposits' listed tables and the
    paper's retrieved results supplements."""
    from app.services.validation_checks import _linked_deposits

    found: list[dict] = []
    for deposit in _linked_deposits(experiment, resources, deposits):
        accession = str(deposit.get("accession") or "")
        for name in deposit["_listing"].get("result_tables") or []:
            url = _deposit_table_url(accession, str(name))
            if url:
                found.append({"name": str(name), "source": "deposit", "accession": accession, "url": url})
    for index, supplement in enumerate(supplements or []):
        if supplement.get("role") == "results_table" and supplement.get("resolved"):
            found.append(
                {
                    "name": supplement.get("filename") or supplement.get("label"),
                    "source": "supplement",
                    "supplement_index": index,
                    "url": supplement.get("url"),
                    "storage_uri": supplement.get("storage_uri"),
                }
            )
    return found


def identify_table(
    contrast: dict | None, others: list[dict], candidates: list[dict], *, identified: str | None = None
) -> tuple[dict | None, str | None, str]:
    """``(table, reason, identified_by)`` for one claim's contrast."""
    if identified:
        chosen = next((c for c in candidates if c["name"] == identified), None)
        if chosen is not None:
            return chosen, None, "input_choice"
    if len(candidates) == 1:
        return candidates[0], None, "only_table"
    if contrast is None:
        return None, "the claim reports on no contrast", "none"
    naming = [c for c in candidates if names_contrast(c["name"], contrast, others)]
    if len(naming) == 1:
        return naming[0], None, "table_name"
    return None, f"{len(candidates)} result tables are listed, and {UNIDENTIFIED_REASON}", "none"


async def enqueue(session, study, plan) -> list:
    """A pending consistency record for every claim with an identified candidate table. Idempotent: a
    claim whose dependencies did not change keeps its record and its outcome."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_author_consistency import claim_predicates
    from app.services.validation_predicate import predicate_words

    evidence = study.evidence_json or {}
    targets = list(
        (
            await session.execute(
                select(ComparisonTarget)
                .where(ComparisonTarget.reproduction_plan_id == plan.id)
                .order_by(ComparisonTarget.id)
            )
        )
        .scalars()
        .all()
    )
    design = plan.differential_design_json or {}
    contrasts = [c for c in design.get("contrasts") or [] if isinstance(c, dict)]
    experiments = {e.get("id"): e for e in plan.reported_experiments_json or [] if isinstance(e, dict)}
    resources = [r for r in plan.resources_json or [] if isinstance(r, dict)]
    deposits = [d for d in ((evidence.get("capabilities") or {}).get("deposits") or []) if isinstance(d, dict)]
    supplements = [s for s in evidence.get("supplements") or [] if isinstance(s, dict)]
    selected = ((plan.analysis_selection_json or {}).get("current") or {}).get("contrast_index")
    # change_7.5 section 3.2: the authors' table the input choice identified for the selected contrast.
    input_table = (evidence.get("deposit_selection") or {}).get("author_table")

    records = []
    for item in claim_predicates(targets, plan):
        target = targets[item["claim_index"]]
        experiment = experiments.get(target.reported_experiment_id)
        candidates = candidate_tables(
            {"contrast_index": target.contrast_index},
            experiment=experiment,
            resources=resources,
            deposits=deposits,
            supplements=supplements,
        )
        if not candidates:
            continue
        position = target.contrast_index
        siblings = [
            c
            for i, c in enumerate(contrasts)
            if i != position and (experiment is None or c.get("reported_experiment_id") in (None, experiment.get("id")))
        ]
        table, reason, identified_by = identify_table(
            item["contrast"], siblings, candidates, identified=input_table if position == selected else None
        )
        dependencies = {
            "predicate": predicate_words(item["predicate"]) if item.get("predicate") else None,
            "predicate_fingerprint": queue.fingerprint(item["predicate"]),
            "contrast": (item["contrast"] or {}).get("name"),
            "table": {k: table.get(k) for k in ("name", "source", "url", "supplement_index")} if table else None,
            "candidates": sorted(c["name"] for c in candidates),
            "identified_by": identified_by,
            "unidentified_reason": reason,
        }
        records.append(await queue.ensure_record(session, study, plan, target, queue.AUTHOR_RESULTS, dependencies))
    return records


def _decompress(blob: bytes, name: str, limit: int) -> bytes:
    if blob[:2] == b"\x1f\x8b" or str(name).endswith(".gz"):
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = decompressor.decompress(blob, limit + 1)
        if len(out) > limit or decompressor.unconsumed_tail:
            raise OverflowError("decompressed")
        return out
    return blob


def _spent(records) -> tuple[int, float]:
    """Bytes downloaded and seconds spent by this study's checks before approval, from their attempts."""
    seen: dict[str, int] = {}
    seconds = 0.0
    for record in records:
        for attempt in record.attempts_json or []:
            if attempt.get("url") and attempt.get("bytes"):
                seen[attempt["url"]] = int(attempt["bytes"])
            seconds += float(attempt.get("seconds") or 0.0)
    return sum(seen.values()), seconds


def _note_attempt(record, **values) -> None:
    """Add what this attempt measured to its entry, reassigned so the JSON column records the change."""
    attempts = list(record.attempts_json or [])
    if attempts:
        attempts[-1] = {**attempts[-1], **values}
        record.attempts_json = attempts


async def run_pending(session, study, plan, *, fetcher=None, limits: dict | None = None) -> int:
    """Run the study's pending consistency records within the limits. Returns how many concluded."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_author_consistency import check_claim, claim_predicates

    limits = {**PRELIMINARY_LIMITS, **(limits or {})}
    fetch = fetcher or _default_fetch
    records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
    pending = [r for r in records if r.state == queue.PENDING and r.reproduction_plan_id == plan.id]
    if not pending:
        return 0
    targets = list(
        (
            await session.execute(
                select(ComparisonTarget)
                .where(ComparisonTarget.reproduction_plan_id == plan.id)
                .order_by(ComparisonTarget.id)
            )
        )
        .scalars()
        .all()
    )
    by_target = {t.id: i for i, t in enumerate(targets)}
    predicates = {item["claim_index"]: item for item in claim_predicates(targets, plan)}
    texts: dict[str, str] = {}
    concluded = 0
    for record in pending:
        deps = record.dependencies_json or {}
        table = deps.get("table")
        item = predicates.get(by_target.get(record.comparison_target_id, -1))
        if table is None or item is None:
            await queue.finish(
                session,
                record,
                state=queue.UNRESOLVED,
                outcome={
                    "outcome": "unresolved",
                    "reason": deps.get("unidentified_reason") or UNIDENTIFIED_REASON,
                    "candidates": deps.get("candidates") or [],
                },
            )
            concluded += 1
            continue
        if table.get("source") == "supplement":
            # The assessment already checked every claim against a retrieved results supplement while its
            # bytes were in hand (change_7.5 section 4.1): that record is this check's outcome.
            supplement_index = table.get("supplement_index")
            supplements = [x for x in (study.evidence_json or {}).get("supplements") or [] if isinstance(x, dict)]
            held = next(
                (
                    r
                    for r in (
                        supplements[supplement_index].get("consistency") or []
                        if isinstance(supplement_index, int) and 0 <= supplement_index < len(supplements)
                        else []
                    )
                    if isinstance(r, dict) and r.get("claim_index") == item["claim_index"]
                ),
                None,
            )
            if held is not None:
                await queue.finish(
                    session, record, state=queue.DONE, outcome={**held, "identified_by": deps.get("identified_by")}
                )
                concluded += 1
                continue
            stored = (
                supplements[supplement_index].get("storage_uri")
                if isinstance(supplement_index, int) and 0 <= supplement_index < len(supplements)
                else None
            )
            if stored and not table.get("url"):
                table = {**table, "url": stored}
        url = table.get("url") or ""
        if not url:
            await queue.finish(
                session,
                record,
                state=queue.UNRESOLVED,
                outcome={
                    "outcome": "unresolved",
                    "reason": "bioAF holds no copy of this table to check the claim against",
                },
            )
            concluded += 1
            continue
        spent_bytes, spent_seconds = _spent(records)
        started = time.monotonic()
        if url not in texts:
            if spent_seconds >= limits["seconds"] or spent_bytes >= limits["total_bytes"]:
                await queue.finish(
                    session, record, state=queue.UNRESOLVED, outcome={"outcome": "unresolved", "reason": LIMIT_REASON}
                )
                concluded += 1
                continue
            await queue.start(session, record, url=url)
            try:
                blob = await fetch(url)
            except Exception as exc:  # noqa: BLE001 - a failed fetch is uncertainty about this record alone
                await queue.finish(
                    session,
                    record,
                    state=queue.UNRESOLVED,
                    outcome={
                        "outcome": "unresolved",
                        "reason": "the authors' table could not be retrieved in this attempt",
                    },
                    error=str(exc)[:300],
                )
                concluded += 1
                continue
            _note_attempt(record, bytes=len(blob))
            if spent_bytes + len(blob) > limits["total_bytes"]:
                await queue.finish(
                    session, record, state=queue.UNRESOLVED, outcome={"outcome": "unresolved", "reason": LIMIT_REASON}
                )
                concluded += 1
                continue
            try:
                texts[url] = _decompress(blob, table.get("name") or "", limits["decompressed_bytes"]).decode(
                    "utf-8", errors="replace"
                )
            except OverflowError:
                await queue.finish(
                    session, record, state=queue.UNRESOLVED, outcome={"outcome": "unresolved", "reason": LIMIT_REASON}
                )
                concluded += 1
                continue
            except (OSError, EOFError, zlib.error) as exc:
                await queue.finish(
                    session,
                    record,
                    state=queue.UNRESOLVED,
                    outcome={"outcome": "unresolved", "reason": "the authors' table arrived but could not be opened"},
                    error=str(exc)[:300],
                )
                concluded += 1
                continue
        else:
            await queue.start(session, record, url=url, shared_download=True)
        result = check_claim(
            {},
            item["predicate"],
            {"name": table.get("name"), "text": texts[url], "source": table.get("source")},
            contrast=item["contrast"],
        )
        result.pop("predicate", None)
        result["identified_by"] = deps.get("identified_by")
        _note_attempt(record, seconds=round(time.monotonic() - started, 3))
        await queue.finish(session, record, state=queue.DONE, outcome=result)
        concluded += 1
    return concluded


async def _default_fetch(url: str) -> bytes:
    """A deposit's table over HTTP; a retrieved supplement from bioAF's own storage."""
    if not url.startswith(("http://", "https://")):
        from app.adapters.registry import get_storage_adapter

        return await get_storage_adapter().read_bytes(url)
    from app.services.validation_assessment import deposit_bytes_fetcher

    return await deposit_bytes_fetcher(url)
