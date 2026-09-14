"""plan_8_1 section 3.3: consistency with the authors' results, for every claim with an identified table.

Author-table consistency ran only after approval and acquisition, and only for claims on the selected
contrast: a deposit listing four DESeq2 result tables had one of them checked. Change_7.5 decided on
consistency for every claim with an identified table (open question 3); this completes it.

- **Which table.** For each claim, the candidates are the result tables its experiment's deposits list,
  and the paper's retrieved results supplements. plan_8_2 section 1.1: each is bound to the claim's
  contrast through ``validation_table_binding`` (its columns, a verified passage, a recorded confirmation);
  a name, being the only table, or the input choice's identification makes a table a candidate only, read
  for its columns before any value is compared. A table whose metadata names another contrast is rejected.
  Anything else is unresolved, with the candidates listed, never a guess.
- **One record per claim** (``AUTHOR_RESULTS``), never shared: two claims on one contrast keep their own
  predicate and outcome. Their table is downloaded once and read once.
- **Within bioAF's limits for checks before approval** (D4): total downloaded bytes, decompressed size,
  execution time and model spend, per study. A check past a limit is unresolved and says so.
- A failed fetch leaves that record unresolved (uncertainty, never an absence); every other record stands.

The result is consistency with the authors' results. It is never presented as reproduction.
"""

from __future__ import annotations

import logging
import time

from app.services import validation_check_queue as queue
from app.services.table_decoding import DECODER_VERSION, TOO_LARGE, decode_table, safe_excerpt
from app.services.validation_table_binding import BINDING_VERSION

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
# plan_8_2 labels, pending the owner's sign-off.
RETRIEVAL_EXHAUSTED_REASON = (
    f"the authors' table could not be retrieved in {queue.MAX_TRANSPORT_ATTEMPTS} attempts, so what it holds is "
    "not established"
)
ACCESS_REFUSED_REASON = "the archive refused bioAF's request for the authors' table"
PENDING_RE_EVALUATION_REASON = (
    "this comparison was made before bioAF established which contrast the table reports, so it is pending "
    "re-evaluation and is not current evidence"
)


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
                    # plan_8_2 section 1.1: what can bind it to a contrast before its bytes are read.
                    "labels": [
                        label for label in [*(supplement.get("references") or []), supplement.get("label")] if label
                    ],
                    "passages": list(supplement.get("citing_passages") or []),
                }
            )
    return found


def competitors_for(table: dict, position: int | None, contrasts: list[dict], resources: list[dict]) -> list[dict]:
    """plan_8_2 section 1.1: every other contrast the table's source serves, across experiments. A paper's
    supplement serves every experiment; a deposit serves the experiments its resource is linked to, or
    all of them when the paper links none."""
    served: set = set()
    if table.get("source") == "deposit":
        key = str(table.get("accession") or "").upper()
        for resource in resources or []:
            if str(resource.get("identifier") or "").upper() == key:
                served.update(resource.get("reported_experiment_ids") or [])
    return [
        c
        for i, c in enumerate(contrasts)
        if i != position
        and isinstance(c, dict)
        and (not served or c.get("reported_experiment_id") in served or not c.get("reported_experiment_id"))
    ]


def confirmation_for(evidence: dict, table: dict, contrast: dict | None) -> dict | None:
    """A person's recorded confirmation that this table reports this contrast, if one was recorded."""
    for entry in (evidence or {}).get("table_confirmations") or []:
        if (
            isinstance(entry, dict)
            and entry.get("table") == table.get("name")
            and entry.get("contrast") == (contrast or {}).get("name")
        ):
            return entry
    return None


def choose_table(
    contrast: dict | None,
    position: int | None,
    candidates: list[dict],
    *,
    contrasts: list[dict],
    resources: list[dict],
    evidence: dict,
    identified: str | None = None,
) -> tuple[dict | None, dict | None, str | None, str, list[dict]]:
    """``(table, binding, reason, identified_by, bindings)`` for one claim.

    plan_8_2 section 1.1: each candidate is bound to the claim's contrast from what is known before its
    bytes are read (its name, the passages citing it, a confirmation). One established binding is the
    table. Otherwise a candidate the binding did not reject may be read for its columns: the one the input
    choice identified, the only one, or the only one whose name names the arms. Rejected tables never are."""
    from app.services import validation_table_binding as binding

    if contrast is None:
        return None, None, "the claim reports on no contrast", "none", []
    bound = []
    for candidate in candidates:
        result = binding.bind(
            candidate,
            contrast,
            competitors=competitors_for(candidate, position, contrasts, resources),
            confirmation=confirmation_for(evidence, candidate, contrast),
        )
        bound.append((candidate, result))
    summary = [{"name": c["name"], "status": b["status"], "reason": b["reason"]} for c, b in bound]
    established = [(c, b) for c, b in bound if b["status"] == binding.ESTABLISHED]
    if len(established) == 1:
        return established[0][0], established[0][1], None, "binding", summary
    if len(established) > 1:
        return (
            None,
            None,
            f"{len(established)} tables are each linked to this contrast, and {UNIDENTIFIED_REASON}",
            "none",
            summary,
        )
    open_ = [(c, b) for c, b in bound if b["status"] != binding.REJECTED]
    if identified:
        chosen = next(((c, b) for c, b in open_ if c["name"] == identified), None)
        if chosen is not None:
            return chosen[0], chosen[1], None, "input_choice", summary
    if len(open_) == 1:
        return open_[0][0], open_[0][1], None, "only_table", summary
    named = [(c, b) for c, b in open_ if binding.names_arms(str(c.get("name") or ""), contrast)]
    if len(named) > 1:
        # Several names name the arms: the one that also names what distinguishes this contrast from the
        # contrasts sharing its arms leads. It is still only a candidate until its columns bind it.
        rivals = [c for c in contrasts if c is not contrast and binding.shares_arms(c, contrast)]
        named = [
            (c, b)
            for c, b in named
            if all(
                any(binding.mentions(str(c.get("name") or ""), w) for w in binding.own_words(contrast, rival))
                for rival in rivals
                if binding.own_words(contrast, rival)
            )
        ] or named
    if len(named) == 1:
        return named[0][0], named[0][1], None, "table_name", summary
    if not open_:
        why = "; ".join(f"{c['name']}: {b['reason']}" for c, b in bound)
        return None, None, f"{UNIDENTIFIED_REASON}: no listed table reports it ({why})", "none", summary
    return None, None, f"{len(open_)} result tables are candidates, and {UNIDENTIFIED_REASON}", "none", summary


async def enqueue(session, study, plan, *, skip: set | None = None, reason: str | None = None) -> list:
    """A pending consistency record for every claim with an identified candidate table. Idempotent: a
    claim whose dependencies did not change keeps its record and its outcome. plan_8_2 section 2.1:
    ``skip`` leaves those claims' records untouched (a recovery re-evaluates only what it names), and
    ``reason`` is recorded with every revision this supersedes."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_author_consistency import claim_predicates
    from app.services.validation_predicate import predicate_identity, predicate_words

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
        if skip and target.id in skip:
            continue
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
        table, bound, unidentified, identified_by, bindings = choose_table(
            item["contrast"],
            position,
            candidates,
            contrasts=contrasts,
            resources=resources,
            evidence=evidence,
            identified=input_table if position == selected else None,
        )
        dependencies = {
            "predicate": predicate_words(item["predicate"]) if item.get("predicate") else None,
            "predicate_fingerprint": queue.fingerprint(predicate_identity(item["predicate"])),
            "contrast": (item["contrast"] or {}).get("name"),
            "table": (
                {k: table.get(k) for k in ("name", "source", "url", "supplement_index", "accession")} if table else None
            ),
            "candidates": sorted(c["name"] for c in candidates),
            "identified_by": identified_by,
            "unidentified_reason": unidentified,
            # plan_8_2 section 1.1: the binding the record depends on, and the versions that decided it.
            "binding": (
                {k: bound.get(k) for k in ("status", "reason", "version")}
                | {"evidence": [e.get("kind") for e in bound.get("evidence") or []]}
                if bound
                else None
            ),
            "bindings": bindings,
            "binding_version": BINDING_VERSION,
            "decoder_version": DECODER_VERSION,
        }
        records.append(
            await queue.ensure_record(session, study, plan, target, queue.AUTHOR_RESULTS, dependencies, reason=reason)
        )
    return records


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


class _Run:
    """What one pass over a study's pending records shares: the claims, the limits and each table read."""

    def __init__(self, session, study, plan, *, fetch, limits: dict, records: list, targets: list, predicates: dict):
        self.session = session
        self.study = study
        self.plan = plan
        self.fetch = fetch
        self.limits = limits
        self.records = records
        self.by_target = {t.id: i for i, t in enumerate(targets)}
        self.predicates = predicates
        # One read per table and pass: a decoded text, or the terminal outcome every sharer receives.
        self.texts: dict[str, str] = {}
        self.decodings: dict[str, dict] = {}
        self.failed: dict[str, dict] = {}
        self.retrying: dict[str, str] = {}


async def run_pending(
    session,
    study,
    plan,
    *,
    fetcher=None,
    limits: dict | None = None,
    max_checks: int | None = None,
    checkpoint=None,
) -> int:
    """Run the study's due consistency records within the limits. Returns how many concluded.

    plan_8_2 section 1.3: each record runs behind its own savepoint, and ``checkpoint`` (the queue's
    commit under its lease) follows each one, so one record's failure never costs another's result. A
    record whose result could not be stored gets a minimal safe record instead. ``max_checks`` bounds the
    records one pass takes, oldest attempt first."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_author_consistency import claim_predicates

    limits = {**PRELIMINARY_LIMITS, **(limits or {})}
    records = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
    now = queue._utcnow()
    pending = sorted(
        (r for r in records if r.reproduction_plan_id == plan.id and queue.due(r, now)),
        key=lambda r: (r.last_attempted_at is not None, r.last_attempted_at or now, r.id),
    )
    if max_checks is not None:
        pending = pending[: max(0, max_checks)]
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
    run = _Run(
        session,
        study,
        plan,
        fetch=fetcher or _default_fetch,
        limits=limits,
        records=records,
        targets=targets,
        predicates={item["claim_index"]: item for item in claim_predicates(targets, plan)},
    )
    concluded = 0
    for record in pending:
        record_id = record.id
        try:
            async with session.begin_nested():
                concluded += await _run_one(run, record)
        except Exception:  # noqa: BLE001 - one record's failure is recorded on that record alone
            logger.exception("check record %s: its result could not be recorded", record_id)
            concluded += await _record_safe_failure(session, record_id)
        if checkpoint is not None:
            await checkpoint()
    return concluded


async def _record_safe_failure(session, record_id: int) -> int:
    """The minimal record a check gets when its own result could not be stored: unresolved, why, and the
    source it was reading. Written in a fresh savepoint after the failed one rolled back."""
    from app.models.validation_check_record import ValidationCheckRecord

    try:
        async with session.begin_nested():
            record = await session.get(ValidationCheckRecord, record_id, populate_existing=True)
            if record is None:
                return 0
            table = (record.dependencies_json or {}).get("table") or {}
            await queue.finish(
                session,
                record,
                state=queue.UNRESOLVED,
                outcome={
                    "outcome": "unresolved",
                    "reason": queue.PERSISTENCE_REASON,
                    "source": {k: table.get(k) for k in ("name", "source", "url", "supplement_index")},
                },
                terminal_reason=queue.PERSISTENCE_FAILED,
            )
        return 1
    except Exception:  # noqa: BLE001 - the queue carries on; the log holds both failures
        logger.exception("check record %s: not even a safe failure could be recorded", record_id)
        return 0


async def _conclude(run: _Run, record, *, state: str, outcome: dict, terminal_reason: str | None, error=None) -> int:
    await queue.finish(run.session, record, state=state, outcome=outcome, error=error, terminal_reason=terminal_reason)
    return 1


async def _run_one(run: _Run, record) -> int:
    """One record, from its table to its outcome. Returns 1 when it concluded, 0 when it waits."""
    from app.services.validation_acquisition_outcome import ACCESS_REFUSED, RESOURCE_LIMIT, retrieval_cause
    from app.services import validation_table_binding as binding
    from app.services.validation_author_consistency import check_claim, unbound_record

    deps = record.dependencies_json or {}
    table = deps.get("table")
    item = run.predicates.get(run.by_target.get(record.comparison_target_id, -1))
    if table is None or item is None:
        return await _conclude(
            run,
            record,
            state=queue.UNRESOLVED,
            outcome={
                "outcome": "unresolved",
                "reason": deps.get("unidentified_reason") or UNIDENTIFIED_REASON,
                "candidates": deps.get("candidates") or [],
                "bindings": deps.get("bindings") or [],
            },
            terminal_reason=queue.BINDING,
        )
    if table.get("source") == "supplement":
        # The assessment already checked every claim against a retrieved results supplement while its
        # bytes were in hand (change_7.5 section 4.1): that record is this check's outcome, when the
        # assessment bound the table to this claim's contrast (plan_8_2 section 1.1).
        supplement_index = table.get("supplement_index")
        supplements = [x for x in (run.study.evidence_json or {}).get("supplements") or [] if isinstance(x, dict)]
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
        if held is not None and held.get("predicate_fingerprint") not in (None, deps.get("predicate_fingerprint")):
            # Compared at a predicate the claim no longer has: not this check's outcome.
            held = None
        if held is not None:
            if binding.established(held.get("binding")):
                return await _conclude(
                    run,
                    record,
                    state=queue.DONE,
                    outcome={**held, "identified_by": deps.get("identified_by")},
                    terminal_reason=None,
                )
            if isinstance(held.get("binding"), dict):
                return await _conclude(
                    run,
                    record,
                    state=queue.UNRESOLVED,
                    outcome={
                        **unbound_record(held.get("table"), "supplement", held["binding"]),
                        "outcome": "unresolved",
                    },
                    terminal_reason=queue.BINDING,
                )
            # A comparison made before bindings existed is never reused: it is kept, and it waits for
            # re-evaluation under the binding contract (plan_8_2 sections 1.1 and 2.1).
            return await _conclude(
                run,
                record,
                state=queue.UNRESOLVED,
                outcome={
                    "outcome": "unresolved",
                    "reason": PENDING_RE_EVALUATION_REASON,
                    "table": held.get("table"),
                    "source": "supplement",
                    "superseded": held,
                },
                terminal_reason=queue.BINDING,
            )
        stored = (
            supplements[supplement_index].get("storage_uri")
            if isinstance(supplement_index, int) and 0 <= supplement_index < len(supplements)
            else None
        )
        if stored and not table.get("url"):
            table = {**table, "url": stored}
    url = table.get("url") or ""
    if not url:
        return await _conclude(
            run,
            record,
            state=queue.UNRESOLVED,
            outcome={"outcome": "unresolved", "reason": "bioAF holds no copy of this table to check the claim against"},
            terminal_reason=queue.UNAVAILABLE,
        )
    if url in run.failed:
        # Another record sharing this table already found why it cannot be read in this pass.
        failure = run.failed[url]
        return await _conclude(run, record, **failure)
    if url in run.retrying:
        concluded = await queue.retry_later(
            run.session, record, error=run.retrying[url], exhausted_reason=RETRIEVAL_EXHAUSTED_REASON
        )
        return 1 if concluded else 0
    spent_bytes, spent_seconds = _spent(run.records)
    started = time.monotonic()
    if url not in run.texts:
        if spent_seconds >= run.limits["seconds"] or spent_bytes >= run.limits["total_bytes"]:
            return await _conclude(
                run,
                record,
                state=queue.UNRESOLVED,
                outcome={"outcome": "unresolved", "reason": LIMIT_REASON},
                terminal_reason=queue.LIMIT,
            )
        await queue.start(run.session, record, url=url)
        try:
            blob = await run.fetch(url)
        except Exception as exc:  # noqa: BLE001 - a failed fetch is uncertainty about this record alone
            error = safe_excerpt(str(exc), limit=300)
            cause = retrieval_cause(exc)
            if cause == ACCESS_REFUSED:
                failure = {
                    "state": queue.UNRESOLVED,
                    "outcome": {"outcome": "unresolved", "reason": ACCESS_REFUSED_REASON},
                    "terminal_reason": queue.ACCESS_REFUSED,
                    "error": error,
                }
                run.failed[url] = failure
                return await _conclude(run, record, **failure)
            if cause == RESOURCE_LIMIT:
                failure = {
                    "state": queue.UNRESOLVED,
                    "outcome": {"outcome": "unresolved", "reason": LIMIT_REASON},
                    "terminal_reason": queue.LIMIT,
                    "error": error,
                }
                run.failed[url] = failure
                return await _conclude(run, record, **failure)
            # A transport failure: tried again later, within the bound the record carries.
            run.retrying[url] = error
            concluded = await queue.retry_later(
                run.session, record, error=error, exhausted_reason=RETRIEVAL_EXHAUSTED_REASON
            )
            return 1 if concluded else 0
        _note_attempt(record, bytes=len(blob))
        if spent_bytes + len(blob) > run.limits["total_bytes"]:
            return await _conclude(
                run,
                record,
                state=queue.UNRESOLVED,
                outcome={"outcome": "unresolved", "reason": LIMIT_REASON},
                terminal_reason=queue.LIMIT,
            )
        # plan_8_2 section 1.2: the decoder acquisition and supplement inspection use. A table that
        # arrived and could not be interpreted is unresolved and says so; nothing is replaced.
        decoded = decode_table(blob, table.get("name") or "", max_decompressed_bytes=run.limits["decompressed_bytes"])
        _note_attempt(record, decoding=decoded.provenance())
        if not decoded.ok:
            too_large = decoded.reason_kind == TOO_LARGE
            failure = {
                "state": queue.UNRESOLVED,
                "outcome": {
                    "outcome": "unresolved",
                    "reason": LIMIT_REASON if too_large else decoded.reason,
                    "decoding": decoded.provenance(),
                },
                "terminal_reason": queue.LIMIT if too_large else queue.INTERPRETATION,
            }
            run.failed[url] = failure
            return await _conclude(run, record, **failure)
        run.texts[url] = decoded.text
        run.decodings[url] = decoded.provenance()
    else:
        await queue.start(run.session, record, url=url, shared_download=True, decoding=run.decodings.get(url))
    # plan_8_2 section 1.1: the table's columns are in hand; its binding to this claim's contrast is
    # completed from them before any predicate is applied.
    # The binding names the exact bytes it was made from.
    table = {**table, "checksum": (run.decodings.get(url) or {}).get("source_checksum")}
    bound = _bind_with_columns(run, table, item, run.texts[url])
    if not binding.established(bound):
        _note_attempt(record, seconds=round(time.monotonic() - started, 3))
        return await _conclude(
            run,
            record,
            state=queue.UNRESOLVED,
            outcome={
                **unbound_record(table.get("name"), table.get("source"), bound),
                "identified_by": deps.get("identified_by"),
            },
            terminal_reason=queue.BINDING,
        )
    candidate = candidate_for(run.study.evidence_json or {}, table)
    result = check_claim(
        {},
        item["predicate"],
        {"name": table.get("name"), "text": run.texts[url], "source": table.get("source")},
        contrast=item["contrast"],
        selector=bound.get("selector"),
        list_evidence=binding.list_evidence(
            candidate,
            item["predicate"],
            confirmation=confirmation_for(run.study.evidence_json or {}, candidate, item["contrast"]),
        ),
    )
    result.pop("predicate", None)
    result["identified_by"] = deps.get("identified_by")
    result["binding"] = bound
    _note_attempt(record, seconds=round(time.monotonic() - started, 3))
    return await _conclude(run, record, state=queue.DONE, outcome=result, terminal_reason=None)


def _bind_with_columns(run: _Run, table: dict, item: dict, text: str) -> dict:
    """The claim's binding to a table whose bytes are in hand: its name, the passages citing it, a
    confirmation, and now its columns."""
    return bind_table_text(run.plan, run.study.evidence_json or {}, table, item.get("contrast_index"), text)


def candidate_for(evidence: dict, table: dict) -> dict:
    """A chosen table as a binding candidate: a supplement carries its labels and the passages citing it."""
    candidate = dict(table)
    index = table.get("supplement_index")
    supplements = [s for s in (evidence or {}).get("supplements") or [] if isinstance(s, dict)]
    if table.get("source") == "supplement" and isinstance(index, int) and 0 <= index < len(supplements):
        row = supplements[index]
        candidate["labels"] = [label for label in [*(row.get("references") or []), row.get("label")] if label]
        candidate["passages"] = list(row.get("citing_passages") or [])
    return candidate


def bind_table_text(plan, evidence: dict, table: dict, contrast_index: int | None, text: str) -> dict:
    """plan_8_2 section 1.1: one table, with its text in hand, bound to the contrast at ``contrast_index``.
    The one entry point for the queued check, the acquired authors' table and the reanalysis's ground truth."""
    from app.services import validation_table_binding as binding

    contrasts = [c for c in (plan.differential_design_json or {}).get("contrasts") or [] if isinstance(c, dict)]
    contrast = (
        contrasts[contrast_index] if isinstance(contrast_index, int) and 0 <= contrast_index < len(contrasts) else {}
    )
    resources = [r for r in plan.resources_json or [] if isinstance(r, dict)]
    candidate = candidate_for(evidence, table)
    return binding.bind(
        candidate,
        contrast,
        competitors=competitors_for(candidate, contrast_index, contrasts, resources),
        header=binding.header_of(text),
        confirmation=confirmation_for(evidence, candidate, contrast),
    )


async def _default_fetch(url: str) -> bytes:
    """A deposit's table over HTTP; a retrieved supplement from bioAF's own storage."""
    if not url.startswith(("http://", "https://")):
        from app.adapters.registry import get_storage_adapter

        return await get_storage_adapter().read_bytes(url)
    from app.services.validation_assessment import deposit_bytes_fetcher

    return await deposit_bytes_fetcher(url)
