"""change_7.2 section 4: the public assessment, as a stage both entrances can run.

Everything change_7.1 built (supplement resolution, retrieval propagation, reconciliation,
evidence-based completion) was attached to `_finish_without_execution`, which only a refused route
ever reached. The first study approved by hand walked past all of it, and the improvements were
therefore delivered to exactly one path.

This module is that work, lifted out of the driver so the gate can call it too. It needs no pipeline
execution and no controlled-data credentials: a missing deposit says nothing about whether the
journal published a usable attachment.

**Assessment completion is not execution completion.** The stage always produces an assessment
record. Where execution is possible, a terminal classification follows execution; where it is not,
the assessment record and its stated limitation ARE the terminal outcome. A study that can still run
must not be classified by this stage.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_provider_config_service
from app.services.llm_feature_models import FEATURE_LITERATURE_VALIDATION
from app.services.llm_provider_clients import get_client
from app.services.validation_completion import completion_for
from app.services.validation_issue_service import ValidationIssueService

logger = logging.getLogger("bioaf.validation_assessment")


async def deposit_bytes_fetcher(url: str, max_bytes: int | None = None) -> bytes:
    """Default byte fetcher for a deposited file. Bytes, not text: the format is decided from magic
    bytes and a text decode would destroy a spreadsheet before it could be recognised.

    plan_8_6 section 5: with ``max_bytes`` the response is STREAMED and stopped once it passes the
    cap, rather than downloaded in full and then refused. Study 65's supplementary bundle is 243 MiB
    against a 200 MiB limit, and every byte of it was transferred before being thrown away.
    """
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), follow_redirects=True) as client:
        if max_bytes is None:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
        chunks: list[bytes] = []
        spent = 0
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                spent += len(chunk)
                if spent > max_bytes:
                    # One byte past the cap is enough for the caller to record `too_large`, and the
                    # connection is closed rather than draining the rest of a file we will refuse.
                    break
        return b"".join(chunks)


def propagate_retrieval(capabilities: dict, supplements: list[dict]) -> dict:
    """Carry retrieval outcomes from the inventory onto the capability answers.

    change_7.1 section 7: a resource cannot be recorded as retrieved and as accessibility-not-
    attempted at the same time, and a consumer that never hears about the retrieval keeps
    reporting the stale answer beside the new fact.
    """
    from app.services.supplement_inventory import apply_retrieval_to_code_sources

    updated = dict(capabilities)
    updated["code_sources"] = apply_retrieval_to_code_sources(capabilities.get("code_sources") or [], supplements)
    return updated


def independent_checks_outstanding(evidence: dict) -> bool:
    """Whether anything can still be established without compute or credentials.

    change_7.1 section 4: a blocked execution route does not end the assessment. Reading the sample
    metadata, inspecting the code the authors supplied and checking their results table need no
    cluster and no data access agreement, and the assessment is what merges, not the reproduction.

    change_7.3 section 1: an artifact the bundle turned out not to contain is settled for that
    bundle; only one never attempted, or whose retrieval failed, is worth another fetch.
    """
    from app.services.supplement_inventory import RETRIEVAL_FAILED, RETRIEVAL_NOT_ATTEMPTED, establish_identity

    supplements = [s for s in (evidence or {}).get("supplements") or [] if isinstance(s, dict)]
    return any(
        not s.get("resolved") and s["retrieval"]["status"] in (RETRIEVAL_NOT_ATTEMPTED, RETRIEVAL_FAILED)
        for s in establish_identity(supplements)
    )


def _bundle_never_requested(evidence: dict) -> bool:
    """An article whose JATS listed no attachments, and whose supplementary bundle nobody asked for.

    change_7.5 section 1.5: an empty manifest never requested the bundle, so study 38 inspected
    nothing and reported "none is a results table". The bundle is the paper's own attachments,
    whatever the JATS happened to list, and it is asked for once.
    """
    return (
        bool((evidence.get("pmcid") or "").strip())
        and not evidence.get("supplements")
        and not any(isinstance(entry, dict) for entry in evidence.get("retrieval_ledger") or [])
    )


def manifest_known(evidence: dict) -> bool:
    """Whether bioAF holds a list of the paper's attachments.

    change_7.5 section 1.5: a PMCID alone made the manifest "known", so an article whose JATS listed
    nothing read as one that attaches nothing. A list is known when the JATS named attachments, or
    when the supplementary bundle was retrieved.
    """
    if evidence.get("supplements"):
        return True
    return any(
        isinstance(entry, dict) and entry.get("outcome") == "retrieved"
        for entry in evidence.get("retrieval_ledger") or []
    )


RETRIEVAL_STEP = "retrieving the paper's supplementary files"


def retrieval_issue(entries: list[dict], supplements: list[dict]) -> dict | None:
    """One issue for a retrieval source whose attempts all failed, naming what it affected.

    change_7.3 section 9: one per failed SOURCE, never one per artifact. Study 34 showed one bundle
    failure about sixteen times. The sentence is plain; the URL, status, error class, attempts and
    times travel as technical detail.
    """
    if not entries or entries[-1].get("outcome") == "retrieved":
        return None
    last = entries[-1]
    affected = [
        str(s.get("label"))
        for s in supplements
        if isinstance(s, dict)
        and (s.get("retrieval") or {}).get("ledger") == last.get("id")
        and s.get("kind") in ("attachment", "reference")
    ]
    message = "bioAF could not download the paper's supplementary files in this attempt"
    message += f", so these were not inspected: {', '.join(affected)}." if affected else "."
    return {
        "step": RETRIEVAL_STEP,
        "outcome": "retrieval_failed",
        "impact": "degraded",
        "message": message,
        "model": None,
        "technical_detail": {
            "source": last.get("source_label"),
            "url": last.get("url"),
            "http_status": last.get("http_status"),
            "error_class": last.get("error_class"),
            "outcome": last.get("outcome"),
            "attempts": len(entries),
            "first_at": entries[0].get("at"),
            "last_at": last.get("at"),
            "ledger": [e.get("id") for e in entries],
        },
    }


async def active_plan(session: AsyncSession, study):
    """The study's one active reproduction plan.

    change_7.2 section 2: every recent study carried two plans, both back-linked, and a query on the
    back-link read targets from both of them. The forward pointer names the active one; the
    superseded flag is what makes a back-link query safe when it is missing.
    """
    from app.models.reproduction_plan import ReproductionPlan

    if study.reproduction_plan_id:
        plan = (
            await session.execute(select(ReproductionPlan).where(ReproductionPlan.id == study.reproduction_plan_id))
        ).scalar_one_or_none()
        if plan is not None:
            return plan
    return (
        (
            await session.execute(
                select(ReproductionPlan)
                .where(
                    ReproductionPlan.validation_study_id == study.id,
                    ReproductionPlan.superseded_at.is_(None),
                )
                .order_by(ReproductionPlan.id.desc())
            )
        )
        .scalars()
        .first()
    )


async def claimed_predicates(session: AsyncSession, study) -> list[dict]:
    """change_7.5 section 4.1: each claim's predicate, for checking a results table against it."""
    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_author_consistency import claim_predicates

    plan = await active_plan(session, study)
    if plan is None:
        return []
    rows = (
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
    return claim_predicates(list(rows), plan, evidence=study.evidence_json or {})


async def claimed_thresholds(session: AsyncSession, study) -> list[float]:
    """The fold-change cutoffs this paper's claims actually name.

    change_7.1 section 7: measuring a results table at fixed cutoffs of 1 and 2 is Groff's rule in
    production code. A paper claiming a 1.5-fold cutoff needs 1.5 measured.

    change_7.2 section 2: read from the study's ACTIVE plan, so the query cannot depend on a cleanup
    having happened. Study 33 carried plans 33 and 34 and this read the cutoffs off both.
    """
    from app.models.comparison_target import ComparisonTarget

    plan = await active_plan(session, study)
    if plan is None:
        return []
    rows = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    wanted = {
        log2
        for t in rows
        if t.threshold is not None and (log2 := _as_log2_cutoff(t.threshold_kind, t.threshold)) is not None
    }
    # change_7.3 section 7: a claim's cutoffs are structure now, and a two-part cutoff carries its
    # fold change there rather than in the one scalar.
    for t in rows:
        for cutoff in t.cutoffs or []:
            if (
                isinstance(cutoff, dict)
                and (log2 := _as_log2_cutoff(cutoff.get("kind"), cutoff.get("value"))) is not None
            ):
                wanted.add(log2)
    return sorted(wanted)


def _as_log2_cutoff(kind, value) -> float | None:
    """A fold-change cutoff on the |log2FC| scale the table is measured on, or None for anything else.

    change_7.5 section 1.2: a linear fold change was read as log2, so "twofold" was measured as
    |log2FC| > 2, which is fourfold. A linear fold change of x is |log2FC| > log2(x).
    """
    import math

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    kind = str(kind or "").lower()
    if kind in ("abs_log2fc", "log2fc"):
        return number
    if kind == "fold_change" and number > 1:
        return math.log2(number)
    return None


def _article_urls(study, pmcid: str) -> list[str]:
    """Where this article's own pages are, so its attachments can be located one at a time.

    plan_8_6 section 5. The DOI resolves to the publisher's page, which links the files it hosts;
    Europe PMC's article page is the fallback for a study bioAF has no DOI for.
    """
    found: list[str] = []
    doi = str(getattr(study, "source_doi", "") or "").strip()
    if doi:
        found.append(f"https://doi.org/{doi.removeprefix('https://doi.org/')}")
    if pmcid:
        found.append(f"https://europepmc.org/articles/{pmcid}")
    return found


async def resolve_study_supplements(session: AsyncSession, study, evidence: dict, *, allowance=None) -> list[dict]:
    """Download the paper's attachments and establish what each one is. Never raises.

    This is the point where the read-time manifest of NAMES becomes an inventory of FILES with
    roles. An article with no PMC id has nothing to download, which is a limitation of the run and
    leaves the references exactly as they were.

    change_7.3 section 1: every attempt lands in ``evidence["retrieval_ledger"]``, which this
    updates in place, and one failed source becomes one issue.
    """
    from app.services.supplement_inventory import merge_resource_identity, recorded_failures, resolve_supplements

    references = evidence.get("supplements") or []
    pmcid = (evidence.get("pmcid") or "").strip()
    if not pmcid:
        return references
    ledger = list(evidence.get("retrieval_ledger") or [])
    if not ledger:
        # A study recorded before the ledger carries its failure only as a copy on each row, and the
        # attempt below clears those copies. The failure is written into the ledger first, so a
        # resumed study shows the original failure beside the new attempt.
        ledger.extend(recorded_failures(references, pmcid=pmcid, at=(evidence.get("assessment") or {}).get("at")))
    start = len(ledger)
    # plan_8_4 section 6.2: the paper's code and its environment specifications, while their bytes are
    # in hand. The bundle has one address and its members have none, so nothing can read them later.
    code_bytes: dict[str, bytes] = {}
    try:
        resolved = await resolve_supplements(
            pmcid,
            references,
            fetcher=deposit_bytes_fetcher,
            # plan_8_6 section 5: where the publisher's own copies of the attachments can be looked
            # up when the combined bundle is over bioAF's cap. The article record, not a URL pattern
            # hard-coded per journal.
            article_urls=_article_urls(study, pmcid),
            thresholds=await claimed_thresholds(session, study),
            ledger=ledger,
            predicates=await claimed_predicates(session, study),
            code_bytes=code_bytes,
            # plan_8_6 section 11: ONE transfer allowance for this attempt, shared with the
            # repository fetch below. A budget spent separately in each place is three budgets.
            allowance=allowance,
        )
    except Exception as exc:  # noqa: BLE001 - an inventory failure degrades the report, never fails the study
        logger.warning("supplement resolution failed for study %s: %s", study.id, exc)
        return references
    evidence["retrieval_ledger"] = ledger
    await ValidationIssueService.record(session, study, [retrieval_issue(ledger[start:], resolved)])
    # One file is one resource. The prose reference and the manifest entry resolve to the same
    # bytes, and study 32 listed each of S1, S2 and S3 twice as a result.
    merged = merge_resource_identity(resolved)
    # plan_8_4 section 6.2: what the paper supplied AS SOURCE, with its file boundaries and its
    # provenance, and what bioAF could not read back into files. An inspection of nothing is still
    # recorded: "bioAF never read it" and "bioAF read it and found nothing" are different statements.
    try:
        from app.services.validation_code_inspection import inspect_code

        inspected = inspect_code(merged, bytes_for=code_bytes)
        held = dict(evidence.get("code_inspection") or {})
        # plan_8_6 section 4: the source read from the paper's REPOSITORY is not the supplements'
        # to replace. Re-reading the attachments used to wipe it, so a study whose code bioAF had
        # fetched and parsed came out of this stage holding none.
        repository = [
            source
            for source in held.get("sources") or []
            if isinstance(source, dict) and (source.get("provenance") or {}).get("from") == "repository"
        ]
        repository_manifests = [
            entry
            for entry in held.get("manifests") or []
            if isinstance(entry, dict) and (entry.get("provenance") or {}).get("from") == "repository"
        ]
        evidence["code_inspection"] = {
            **inspected,
            "sources": [*repository, *inspected["sources"]],
            "manifests": [*repository_manifests, *inspected["manifests"]],
            # An earlier inspection's reviews and approved runs are evidence in their own right and
            # are not discarded because the source was read again.
            "reviews": held.get("reviews") or [],
            "execution": held.get("execution") or {},
        }
    except Exception as exc:  # noqa: BLE001 - reading the code never fails the assessment
        logger.warning("code inspection failed for study %s: %s", study.id, exc)
    return merged


def _metered(fetcher, allowance):
    """``fetcher``, spending ``allowance`` as it transfers. The fetcher itself when there is none."""
    if allowance is None:
        return fetcher
    from app.services.supplement_inventory import _fetch_within

    async def metered(url, max_bytes=None):
        from app.services.code_fetch_service import _MAX_BYTES

        return await _fetch_within(fetcher, url, max_bytes or _MAX_BYTES, allowance)

    return metered


async def refresh_paper_index(session: AsyncSession, study, *, fetch_text=None) -> dict | None:
    """plan_8_6 section 3: give a study recorded before the index one, without re-extracting it.

    The index is built at read time. A study read before it existed holds only the capped passages
    of the old selector, which is how study 65 came to be judged on forty sentences of cell-culture
    protocol. Its methods, legends and availability statement are still free to fetch from the
    SOURCE ITS READ RECORDED, and none of that is an extraction: no model is asked, no claim is
    re-read, and what the study already holds is untouched when the source cannot be reached.

    Never raises: a text bioAF cannot re-fetch leaves the study exactly as it was.
    """
    from app.services import validation_paper_text as paper_text
    from app.services.validation_evidence_index import build_index

    evidence = dict(study.evidence_json or {})
    held = evidence.get("paper_index")
    if isinstance(held, dict) and held.get("passages"):
        # The index is what it was, and the READING of it may not be: `methods_cutoffs` was recorded
        # before the analysis-scoped statements existed, so a study whose index is already in hand
        # held a record with none. The record is refreshed from the held index, which asks nobody
        # and fetches nothing.
        _record_methods_reading(evidence, held, source=(evidence.get("methods_cutoffs") or {}).get("source"))
        study.evidence_json = evidence
        if session is not None:
            await session.flush()
        return held
    source = (evidence.get("paper_text_acquisition") or {}).get("source")
    fetch = fetch_text or paper_text.again
    try:
        text = await fetch(session, study, source)
    except Exception as exc:  # noqa: BLE001 - a text bioAF cannot re-fetch is a limitation of the run
        logger.info("study %s: the paper's text could not be re-read for the index: %s", study.id, exc)
        return None
    if text is None or not (text.text or "").strip():
        return None
    index = build_index(text.text, sections=text.sections, source=text.source)
    evidence["paper_index"] = index
    # Section 3 item 4: the deterministic cutoff reader gets the methods this recovered too.
    _record_methods_reading(evidence, index, source=text.source)
    study.evidence_json = evidence
    if session is not None:
        await session.flush()
    return index


def _record_methods_reading(evidence: dict, index: dict, *, source) -> None:
    """Keep bioAF's normalized reading of the methods beside the index it was read from.

    plan_8_6 section 3 item 4. The record is what a reader inspects to see what bioAF resolved out
    of the paper: the sentences that define a differential test's cutoff, and the ones that state a
    criterion for a named analysis operation. Both are recomputed from the held index, so the stored
    record never says the paper stated nothing when the reader would find two thresholds in it.
    """
    from app.services.validation_evidence_index import methods_paragraphs
    from app.services.validation_methods_cutoffs import record as record_methods

    paragraphs = methods_paragraphs(index)
    if not paragraphs:
        return
    held = evidence.get("methods_cutoffs") if isinstance(evidence.get("methods_cutoffs"), dict) else {}
    evidence["methods_cutoffs"] = record_methods(paragraphs, source=source or held.get("source"))


async def refresh_code_inspection(session: AsyncSession, study, *, sources=None, fetcher=None, allowance=None) -> dict:
    """plan_8_6 section 4: fetch the code this paper published, and READ it. Never raises.

    Study 65 identified a repository and a cited Software Heritage revision and recorded
    ``retrieval: not_attempted``, because the only caller of `code_fetch_service` was
    `_handle_reproducing`, which is post-approval and which this study never reached. So no C
    obligation could be assessed, and M5.B, which asks whether the inspected methods and the
    supplied code agree on the consequential parameters, was put to an assessor that had never been
    shown the code.

    The revision the paper ARCHIVED is what is fetched; its members become source with their own
    paths and checksums. Nothing is installed and nothing is executed: an archive is opened, a
    notebook is parsed as JSON, and bytes are decoded as text. The run-only obligations keep every
    approval prerequisite they had.

    Cached on what it was built from, so a second assessment of unchanged evidence costs no fetch.
    """
    from app.services.code_fetch_service import RESOLVED, cited_revisions, resolve_code

    evidence = dict(study.evidence_json or {})
    if sources is None:
        plan = await active_plan(session, study)
        sources = (getattr(plan, "code_availability_json", None) or []) if plan is not None else []
    rows = [s for s in sources or [] if isinstance(s, dict)]
    inventory = (evidence.get("deposit_inventory") or {}).get("entries") or []
    entries = [
        SimpleNamespace(
            filename=e.get("filename"), url=e.get("url"), classification=e.get("classification"), level=e.get("level")
        )
        for e in inventory
        if isinstance(e, dict)
    ]
    revisions = cited_revisions(_availability_text(evidence))
    identity = {
        "sources": [(s.get("kind"), s.get("url"), s.get("identifier")) for s in rows],
        "deposit_code": [e.url for e in entries if e.classification == "code"],
        "revisions": [r["value"] for r in revisions],
    }
    held = evidence.get("code_resolution") if isinstance(evidence.get("code_resolution"), dict) else None
    if held is not None and held.get("inputs") == identity:
        return held
    if not rows and not entries:
        return held or {}

    try:
        resolution = await resolve_code(
            sources=rows,
            deposit_entries=entries,
            # plan_8_6 section 11: the repository's archive is transferred out of the SAME allowance
            # the supplements spend. It was counted nowhere, so an assessment could move far more
            # than its budget by spending it in two places.
            fetcher=_metered(fetcher or deposit_bytes_fetcher, allowance),
            revisions=revisions,
        )
    except Exception as exc:  # noqa: BLE001 - reading the code cannot fail the stage that earned the rest
        logger.warning("study %s: the authors' code could not be resolved: %s", study.id, exc)
        return held or {}

    # `staged: False` says this record holds no bytes: the execution arm re-resolves for them when
    # it needs to stage a run, and only then.
    record = {**resolution.record(), "inputs": identity, "at": _now_iso(), "staged": False}
    evidence["code_resolution"] = record
    from app.services.validation_code_inspection import inspect_archive

    if resolution.outcome == RESOLVED and resolution.archive:
        inspected = inspect_archive(resolution.archive, origin=resolution.url)
    else:
        # The report has to say bioAF did not read it, rather than implying there was nothing to
        # read. A location that was never retrieved is one unreadable entry with its reason.
        inspected = {
            "sources": [],
            "manifests": [],
            "unreadable": [{"path": resolution.url or "", "reason": resolution.reason}],
        }
    previous = dict(evidence.get("code_inspection") or {})
    held_paths = {str(s.get("path")) for s in previous.get("sources") or []}
    held_manifests = {str(m.get("path")) for m in previous.get("manifests") or []}
    evidence["code_inspection"] = {
        "sources": [
            *(previous.get("sources") or []),
            *[s for s in inspected["sources"] if s["path"] not in held_paths],
        ],
        "manifests": [
            *(previous.get("manifests") or []),
            *[m for m in inspected["manifests"] if m["path"] not in held_manifests],
        ],
        "unreadable": inspected["unreadable"],
        # An earlier inspection's reviews, approved runs and environment check are evidence in
        # their own right and are not discarded because more source arrived.
        "reviews": previous.get("reviews") or [],
        "execution": previous.get("execution") or {},
        **({"environment_check": previous["environment_check"]} if previous.get("environment_check") else {}),
    }
    # Step 13 recorded `accessible: not_attempted` on each source. This is the attempt.
    capabilities = dict(evidence.get("capabilities") or {})
    if capabilities.get("code_sources"):
        capabilities["code_sources"] = [
            {**src, **_accessibility_from(resolution.accessibility.get(src.get("url") or ""))}
            for src in capabilities["code_sources"]
        ]
        evidence["capabilities"] = capabilities
    study.evidence_json = evidence
    if session is not None:
        await session.flush()
    return record


def _availability_text(evidence: dict) -> str:
    """Where a paper states its code's location and the revision it archived.

    The whole index, because a paper that names its repository in the methods rather than under a
    data-availability heading has still named it.
    """
    return " ".join(str(p.get("text") or "") for p in (evidence.get("paper_index") or {}).get("passages") or [])


def _accessibility_from(answer: dict | None) -> dict:
    if not answer:
        return {}
    return {"accessible": answer.get("accessible"), "accessible_reason": answer.get("reason")}


async def reconcile_plan(session: AsyncSession, study, supplements: list[dict]) -> None:
    """Re-interpret the plan against what the supplements turned out to hold. Never raises."""
    from app.services.validation_reconciliation import not_performed, reconcile

    plan = await active_plan(session, study)
    if plan is None:
        return not_performed("this study has no reproduction plan to reconcile")
    cfg = await llm_provider_config_service.get_active(session, study.organization_id)
    if cfg is None:
        logger.info("study %s: no active provider, so nothing can reconcile the plan", study.id)
        return not_performed(
            "no language model is configured for this organization, so nothing could reconcile the plan"
        )
    # change_7.3 section 9: this caller dropped `on_issue`, so a model failing inside reconciliation
    # reached nothing but a log line.
    issues: list[dict] = []
    try:
        result = await reconcile(
            session,
            study,
            plan,
            supplements=supplements,
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
            on_issue=issues.append,
        )
    except Exception as exc:  # noqa: BLE001 - a reconciliation failure degrades the report only
        logger.warning("reconciliation failed for study %s: %s", study.id, exc)
        result = not_performed("bioAF hit an internal error while reconciling the plan", error_class=type(exc).__name__)
    await ValidationIssueService.record(session, study, issues)
    result["model_issue"] = any(issues)
    return result


RECONCILIATION_STEP = "reconciling the plan against the paper's evidence"
CONSISTENCY_STEP = "checking this assessment's statements for contradictions"


def _not_performed_issue(step: str, reason: str, **detail) -> dict:
    """change_7.3 section 9: a stage that could not run is an issue, stated plainly, with any
    technical detail beside the sentence rather than in it."""
    return {
        "step": step,
        "outcome": "not_performed",
        "impact": "degraded",
        "message": f"This step was not performed: {reason}.",
        "model": None,
        "technical_detail": {k: v for k, v in detail.items() if v is not None} or None,
    }


def refresh_checks(study, plan) -> None:
    """Re-settle the read-time checks against what the assessment established. Never raises.

    change_7.3 section 6: the driver said reconciliation re-ran the pre-compute checks once
    supplements resolved, and nothing did. Study 34 kept a read-time "samples described: ok" whose
    reasoning cited supplementary metadata nobody had inspected. A verdict that rests on the paper's
    text stays marked as such until something inspected settles it.
    """
    from app.services.validation_precompute_checks import (
        BASIS_INSPECTED,
        CHECK_SAMPLE_DATA,
        CHECK_SAMPLES_DESCRIBED,
        OK,
        UNKNOWN,
        basis_of,
        check_sample_data,
    )

    evidence = dict(study.evidence_json or {})
    checks = {k: dict(v) if isinstance(v, dict) else v for k, v in (evidence.get("precompute_checks") or {}).items()}
    supplements = [s for s in evidence.get("supplements") or [] if isinstance(s, dict)]
    deposits = [d for d in ((evidence.get("capabilities") or {}).get("deposits") or []) if isinstance(d, dict)]

    table = next(
        (
            s
            for s in supplements
            if s.get("resolved") and s.get("role") == "sample_metadata" and (s.get("row_count") or 0) > 0
        ),
        None,
    )
    described = checks.get(CHECK_SAMPLES_DESCRIBED)
    if table is not None and isinstance(described, dict):
        columns = ", ".join(str(c) for c in (table.get("columns") or [])[:6])
        described.update(
            verdict=OK,
            detail=(
                f"{table.get('label')} was retrieved and lists {table.get('row_count')} samples"
                + (f" with {columns} for each" if columns else "")
                + ", so which sample belongs to which condition is recoverable from it"
            ),
            decided_by="measurement",
            basis=BASIS_INSPECTED,
        )

    sample_data = checks.get(CHECK_SAMPLE_DATA)
    paper_count = ((plan.sample_sheet_json if plan else None) or {}).get("sample_count")
    if isinstance(sample_data, dict) and sample_data.get("verdict") == UNKNOWN:
        rerun = check_sample_data(
            paper_sample_count=paper_count if isinstance(paper_count, int) else None,
            entries=[],
            supplements=supplements,
            deposits=deposits,
        )
        if rerun.get("verdict") != UNKNOWN or "no deposited files were listed" in str(sample_data.get("detail")):
            checks[CHECK_SAMPLE_DATA] = rerun

    for check in checks.values():
        if isinstance(check, dict) and not check.get("basis"):
            check["basis"] = basis_of(check)
    if checks:
        evidence["precompute_checks"] = checks
        study.evidence_json = evidence


async def run_assessment(session: AsyncSession, study, *, fetcher=None) -> dict:
    """The public assessment stage. Never raises; always produces a record.

    Runs on every authorized study on both routes, after authorization and before the final
    execution-feasibility decision. It concludes nothing about the study's state: that is the
    caller's, and it differs between a route that can still run and one that cannot.

    change_7.3 section 6: **the record says what reconciliation and the contradiction pass covered,
    and whether they ran at all.** ``reconciled: false`` stood for five different causes, and an
    empty contradiction list read as consistency established when the pass had not run.
    """
    import time

    from app.services.validation_provenance import record_stage

    started = time.monotonic()
    record_stage(study, "assessment")
    evidence = dict(study.evidence_json or {})
    # plan_8_6 section 11: one transfer allowance for this attempt, shared by the supplement bundle,
    # the publisher's member files and the repository archive, and recorded with what it spent.
    from app.services.supplement_inventory import TransferAllowance

    allowance = TransferAllowance()
    if independent_checks_outstanding(evidence) or _bundle_never_requested(evidence):
        evidence["supplements"] = await resolve_study_supplements(session, study, evidence, allowance=allowance)
        # What retrieval established has to reach the rows that answer for it. Study 32 recorded
        # Supplemental File S2 as "not attempted" in the same bundle that had downloaded and read it.
        evidence["capabilities"] = propagate_retrieval(evidence.get("capabilities") or {}, evidence["supplements"])
        study.evidence_json = evidence
        await session.flush()

    # plan_8_5 sections 3.1 and 3.4: the deposit's own sample records, for every deposit this study
    # resolved. They answer documentary obligations about species, material, arms, counts and units,
    # so they are held here, before any input is acquired and before anyone approves compute.
    await refresh_sample_records(session, study, fetcher=fetcher)

    # plan_8_6 section 3: a study read before the evidence index existed gets one now, from the
    # source its read recorded. No model is asked and no claim is re-read.
    await refresh_paper_index(session, study)

    # plan_8_6 section 4: the code this paper published, fetched at the revision the paper archived
    # and READ. Study 65 resolved a repository and a cited revision and recorded `not_attempted`,
    # because the only caller was post-approval. Static inspection only: nothing is installed and
    # nothing is executed, and the run-only obligations keep every approval prerequisite they had.
    await refresh_code_inspection(session, study, allowance=allowance)
    evidence = dict(study.evidence_json or {})
    evidence["transfer_allowance"] = allowance.record()
    study.evidence_json = evidence
    await session.flush()

    plan = await active_plan(session, study)
    refresh_checks(study, plan)
    await session.flush()

    # change_7.1 section 6: the evidence is in hand, so the provisional reading gets revised before
    # anything is concluded from it. Retrieval on its own changed no decision.
    reconciliation = await reconcile_plan(session, study, (study.evidence_json or {}).get("supplements") or [])
    if reconciliation["status"] != "performed" and not reconciliation.get("model_issue"):
        await ValidationIssueService.record(
            session,
            study,
            [
                _not_performed_issue(
                    RECONCILIATION_STEP, reconciliation["reason"], error_class=reconciliation.get("error_class")
                )
            ],
        )

    # change_7.3 section 7: a count equal to the collected inventory and tagged post-QC is marked
    # unresolved. The deposit's registered count is established evidence reaching a consumer.
    await guard_population_counts(session, study, plan)

    # change_7.2 section 4: reconciliation settles its DEPENDENTS. Reaching this stage is not the
    # same as the report being consistent, and study 33 shipped a check asserting that per-sample
    # assignments are recoverable beside a blocker asserting they cannot be reconstructed.
    contradiction_pass = await settle_dependents(session, study)
    contradictions = contradiction_pass["findings"]

    evidence = dict(study.evidence_json or {})
    supplements = [
        s for s in evidence.get("supplements") or [] if isinstance(s, dict) and s.get("kind") not in ("figure", "index")
    ]
    resolved = [s for s in supplements if s.get("resolved")]
    performed = reconciliation["status"] == "performed"
    record = {
        "contradictions": contradictions,
        "unresolved_contradictions": [c for c in contradictions if c.get("status") == "unresolved"],
        "contradiction_pass": {
            "checked": contradiction_pass["checked"],
            "pairs": contradiction_pass["pairs"],
            "reason": contradiction_pass.get("reason"),
        },
        "at": _now_iso(),
        "supplements_inspected": len(resolved),
        "supplements_unresolved": len(supplements) - len(resolved),
        "reconciliation": {
            "status": reconciliation["status"],
            "reason": reconciliation.get("reason"),
            "basis": reconciliation.get("basis"),
        },
        # What every finding in this assessment rests on until something inspected settles it.
        "basis": reconciliation.get("basis") if performed else "paper_text",
        "reconciled": performed,
        "revisions": len(reconciliation.get("revisions") or []),
    }
    evidence["assessment"] = record
    study.evidence_json = evidence
    await session.flush()

    # plan_8_5 section 3.6: the documentary obligations a bounded model review can settle, judged on
    # the passages this study holds. It is cached on those passages, so the stage running again costs
    # nothing, and a provider failure leaves only the obligations it was asked about grey.
    cfg = await llm_provider_config_service.get_for_feature(
        session, study.organization_id, FEATURE_LITERATURE_VALIDATION
    )
    reviewed = await refresh_documentary_review(
        session,
        study,
        client=get_client(cfg.provider) if cfg else None,
        model=cfg.model if cfg else None,
        api_key=cfg.api_key if cfg else None,
    )
    # plan_8_6 section 11: what this assessment actually cost, recorded rather than estimated. A
    # cheaper configuration is only acceptable once the accuracy gates pass, and neither can be
    # argued from numbers nobody kept.
    evidence = dict(study.evidence_json or {})
    record["measured"] = _measured(evidence, reviewed, seconds=time.monotonic() - started)
    evidence["assessment"] = record
    study.evidence_json = evidence
    await session.flush()

    # plan_8_5 sections 3.1 and 3.2: settle the rubric's obligations from what this study now holds,
    # and publish them. The score follows the evidence as it settles; it does not wait for approval,
    # for an acquired input or for a finding inventory, and nothing is judged at render time.
    await publish_assessment(session, study, reason="the assessment stage ran")
    return record


def _measured(evidence: dict, reviewed: dict | None, *, seconds: float) -> dict:
    """plan_8_6 section 11: bytes moved, model calls made, and obligations settled, for this attempt."""
    ledger = [e for e in evidence.get("retrieval_ledger") or [] if isinstance(e, dict)]
    judgments = ((reviewed or {}).get("judgments") or {}) if isinstance(reviewed, dict) else {}
    settled = [j for j in judgments.values() if isinstance(j, dict) and j.get("outcome") in ("verified", "failed")]
    code = evidence.get("code_resolution") if isinstance(evidence.get("code_resolution"), dict) else {}
    return {
        "seconds": round(seconds, 2),
        "supplement_bytes": sum(int(e.get("bytes_transferred") or 0) for e in ledger),
        "supplement_attempts": len(ledger),
        "code_bytes": sum(int(f.get("size_bytes") or 0) for f in (code or {}).get("files") or []),
        "code_sources_read": len((evidence.get("code_inspection") or {}).get("sources") or []),
        "model_requests": ((reviewed or {}).get("asked") or {}).get("requests", 0),
        "model_expansions": ((reviewed or {}).get("asked") or {}).get("expansions", 0),
        "skipped_without_evidence": ((reviewed or {}).get("asked") or {}).get("skipped_without_evidence", 0),
        "evidence_chars": (reviewed or {}).get("evidence_chars", 0),
        "obligations_judged": len(judgments),
        "obligations_settled": len(settled),
    }


async def publish_assessment(session: AsyncSession, study, *, reason: str) -> None:
    """Record this study's rubric assessment and the score snapshot that cites it. Never raises.

    ``record_scorecard`` settles the obligations first, so this is the one call a production path
    makes when its evidence has moved: the assessment and the snapshot that cites it are published
    together, and neither can be left behind by the other.
    """
    from app.services.validation_report_summary import record_scorecard

    try:
        await record_scorecard(session, study, reason=reason, force=False)
    except Exception as exc:  # noqa: BLE001 - publishing a score cannot fail the stage that earned it
        logger.warning("study %s: the rubric assessment could not be published: %s", study.id, exc)


def _deposit_identity(deposits: list[dict]) -> list[str]:
    """What a held set of sample records was built from, so an unchanged set is not fetched again."""
    return sorted({str(d.get("accession") or "").strip().upper() for d in deposits or [] if isinstance(d, dict)} - {""})


async def refresh_sample_records(session: AsyncSession, study, *, fetcher=None) -> dict:
    """plan_8_5 section 3.4: hold the sample records of every deposit this study resolved.

    Keyed on the accessions themselves, so re-running the stage over the same deposits costs no
    request, and a deposit the reading discovered later is fetched when it appears. Never raises:
    a retrieval failure is recorded as bioAF's limitation beside the records it did read.
    """
    from app.services.validation_sample_records import collect_sample_records

    evidence = dict(study.evidence_json or {})
    deposits = [d for d in ((evidence.get("capabilities") or {}).get("deposits") or []) if isinstance(d, dict)]
    requested = str(getattr(study, "source_accession", "") or "").strip()
    if requested and not any(str(d.get("accession") or "").strip().upper() == requested.upper() for d in deposits):
        # The requested accession is authoritative for what a run fetches, whether or not discovery
        # has described it yet, so it is opened here too.
        deposits = [{"accession": requested, "provenance": "requested", "scoped": True}, *deposits]
    wanted = _deposit_identity(deposits)
    held = evidence.get("sample_records") if isinstance(evidence.get("sample_records"), dict) else None
    from app.services.validation_sample_records import retrievable_again

    # plan_8_5 section 3.4: unchanged deposits cost no request, but a failure to RETRIEVE is not an
    # answer about the deposit, so it is tried again rather than kept.
    if held is not None and held.get("deposits_seen") == wanted and not retrievable_again(held):
        return held
    if not wanted:
        return held or {"deposits": [], "limitations": []}
    try:
        collected = await collect_sample_records(deposits=deposits, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001 - the records inform the score; they cannot fail the stage
        logger.warning("sample records could not be collected for study %s: %s", study.id, exc)
        return held or {"deposits": [], "limitations": []}
    record = {**collected, "deposits_seen": wanted, "at": _now_iso()}
    evidence["sample_records"] = record
    study.evidence_json = evidence
    # The read-time caller holds no session of its own; it persists with the rest of its evidence.
    if session is not None:
        await session.flush()
    return record


async def refresh_documentary_review(session: AsyncSession, study, *, client=None, model=None, api_key=None) -> dict:
    """plan_8_5 section 3.6: judge the documentary obligations from the passages this study holds.

    Cached on the evidence itself: the passages supplied, the contract they were judged under and the
    model that judged them. Unchanged inputs cost no call, so a refresh, a recovery and a second
    assessment do not reroll a paper's score. Never raises: a provider failure leaves the obligations
    it was asked about grey, with what would settle them, and everything already established stands.
    """
    from app.services.validation_documentary_review import JUDGED_LEAVES, packets_for, review_documents

    evidence = dict(study.evidence_json or {})
    plan = await active_plan(session, study)
    plan_dict = {}
    if plan is not None:
        from app.services.validation_report_summary import plan_projection

        plan_dict = plan_projection(plan)
    # plan_8_6 section 3: one packet per obligation, built from this study's section-aware index,
    # so a computational question is not answered from the culture protocol and an absence finding
    # rests on what the packet actually covered.
    packets = packets_for(evidence=evidence, plan=plan_dict, leaves=JUDGED_LEAVES)
    fingerprints = {leaf: _packet_fingerprint(packet) for leaf, packet in packets.items()}
    identity = {
        "packets": {leaf: [p["id"] for p in packet["passages"]] for leaf, packet in packets.items()},
        "fingerprints": fingerprints,
        "model": model,
    }
    held = evidence.get("rubric_judgments") if isinstance(evidence.get("rubric_judgments"), dict) else None
    if held is not None and held.get("inputs") == identity:
        return held
    # plan_8_6 section 11, and the owner's review 2026-09-21: "One fingerprint covers every packet.
    # Any change invokes the full documentary review again." An obligation whose packet, model and
    # versions are unchanged keeps the judgment it already has; only the ones that moved are asked.
    settled = _unchanged_judgments(held, fingerprints, model)
    try:
        reviewed = await review_documents(
            packets=packets, client=client, model=model or "", api_key=api_key, settled=settled
        )
    except Exception as exc:  # noqa: BLE001 - a review cannot fail the stage that earned the rest
        logger.warning("study %s: the documentary review could not run: %s", study.id, exc)
        return held or {"judgments": {}, "failures": [], "reason": str(exc)}
    record = {**reviewed, "inputs": identity}
    evidence["rubric_judgments"] = record
    study.evidence_json = evidence
    await session.flush()
    return record


def _packet_fingerprint(packet: dict) -> str:
    """What ONE obligation was shown, as one hash, so a changed packet is judged again and no other.

    plan_8_6 section 11, keyed per obligation: one changed source reruns the checks that depend on
    it rather than every judgment this study has ever made. The contract, the review, the overlap
    and the packet versions are in it, because a corrected question is a different question and a
    changed reconciliation is a different answer.
    """
    import hashlib
    import json as _json

    from app.services.validation_documentary_review import REVIEW_VERSION
    from app.services.validation_evidence_packets import PACKET_VERSION
    from app.services.validation_finding_overlap import OVERLAP_VERSION
    from app.services.validation_judgment import CONTRACT_VERSION

    payload = _json.dumps(
        {
            "passages": (packet or {}).get("passages"),
            "coverage": (packet or {}).get("coverage"),
            "expansion": [p.get("id") for p in (packet or {}).get("expansion") or []],
            "contract": CONTRACT_VERSION,
            "review": REVIEW_VERSION,
            "packet": PACKET_VERSION,
            "overlap": OVERLAP_VERSION,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unchanged_judgments(held: dict | None, fingerprints: dict[str, str], model: str | None) -> dict[str, dict]:
    """The judgments whose packet is exactly what it was when they were made.

    A judgment the reconciliation pass demoted is NOT reused: what it is depends on the other
    obligations, and those are about to be judged again. Its own answer is held under ``withheld``
    and the pass reruns over the whole set, so nothing is lost by asking for it again.
    """
    if not isinstance(held, dict):
        return {}
    inputs = held.get("inputs") if isinstance(held.get("inputs"), dict) else {}
    if inputs.get("model") != model:
        return {}
    before = inputs.get("fingerprints") if isinstance(inputs.get("fingerprints"), dict) else {}
    judgments = held.get("judgments") if isinstance(held.get("judgments"), dict) else {}
    return {
        leaf: judgment
        for leaf, judgment in judgments.items()
        if isinstance(judgment, dict)
        and before.get(leaf)
        and before.get(leaf) == fingerprints.get(leaf)
        and not judgment.get("same_finding_as")
        and not judgment.get("contradicted_by")
        and not judgment.get("tension_with")
    }


async def guard_population_counts(session: AsyncSession, study, plan) -> list[int]:
    """Mark a post-QC count that equals a deposit's registered sample count as unresolved.

    change_7.3 section 7. Study 34 recorded "54 samples, post-QC" beside a paper that excludes three
    TE biopsies after quality control, leaving 51 analysed, and an EGA record that registers 54. The
    count is the collected inventory; the analysed population is a different number. The claim is
    never rewritten: the registered count is evidence, the paper's number is the paper's, and which
    population the sentence meant is exactly what is unresolved.

    Returns the ids of the targets it marked.
    """
    from app.models.comparison_target import ComparisonTarget

    if plan is None:
        return []
    evidence = study.evidence_json or {}
    registered = [
        (str(d.get("accession")), d.get("registered_samples"))
        for d in ((evidence.get("capabilities") or {}).get("deposits") or [])
        if isinstance(d, dict) and isinstance(d.get("registered_samples"), int)
    ]
    if not registered:
        return []
    excluded = bool(((evidence.get("paper_passages") or {}).get("statements") or []))
    targets = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    marked: list[int] = []
    for target in targets:
        stage = (target.qc_stage or "").lower().replace("-", "").replace(" ", "")
        # "as analysed" says the same thing as post-QC once the paper states that samples were
        # excluded: the deployed resume of study 34 had reconciliation relabel the 54 that way.
        # Without a stated exclusion an analysed count equal to the inventory is plausible, and left.
        analysed = "postqc" in stage or ("analys" in stage or "analyz" in stage) and excluded
        if not analysed or target.claimed_value is None or not _is_sample_count(target):
            continue
        match = next((acc for acc, count in registered if float(count) == float(target.claimed_value)), None)
        if match is None:
            continue
        target.unresolved_reason = (
            f"This count equals the {int(target.claimed_value)} samples {match} registers, which is the collected "
            f"inventory, but the claim describes it as {target.qc_stage}."
            + (
                " The paper states that samples were excluded after quality control, so fewer were analysed."
                if excluded
                else ""
            )
            + " Which population it counts is unresolved; the number is not rewritten."
        )
        marked.append(target.id)
    if marked:
        await session.flush()
    return marked


def _is_sample_count(target) -> bool:
    text = " ".join(str(v or "") for v in (target.metric_key, target.unit, target.claim_text)).lower()
    return (target.output_type or "count") == "count" and ("sample" in text or "biops" in text or "embryo" in text)


def record_refusal(study, route: str, decision, reason: str | None = None) -> dict:
    """Land a refused route on the study, identically from either entrance.

    Both the notice a reader sees and the typed action a report names come from here, so a study's
    evidence cannot depend on which door it came through.
    """
    evidence = dict(study.evidence_json or {})
    evidence["route_decision"] = decision.as_record()
    evidence["route_blocked"] = {
        "chosen": route,
        "reason": reason or decision.reason,
        "action": decision.action,
        "at": _now_iso(),
    }
    study.evidence_json = evidence
    return evidence


async def settle_dependents(session: AsyncSession, study) -> dict:
    """Express the assessment's dependent statements against the reconciled evidence.

    Returns ``{"checked", "pairs", "findings", "reason"}``: whether the pass ran, the pairs it
    compared, and every contradiction found, each either resolved by what was inspected or reported
    as unresolved. Never raises: a consistency pass that fails must not fail the study, and
    change_7.3 section 6 records its failure rather than returning ``[]``, which read as agreement.
    """
    from app.services import validation_consistency as consistency

    evidence = dict(study.evidence_json or {})
    plan = await active_plan(session, study)
    blockers = list((plan.blockers_json if plan else None) or [])

    try:
        findings = consistency.reconcile_contradictions(
            precompute_checks=evidence.get("precompute_checks"),
            blockers=blockers,
            supplements=evidence.get("supplements") or [],
            blocker_kinds=(plan.blocker_kinds_json if plan else None) or [],
        )
    except Exception as exc:  # noqa: BLE001 - a consistency failure degrades the report only
        logger.warning("consistency pass failed for study %s: %s", study.id, exc)
        reason = "bioAF hit an internal error while comparing this assessment's statements"
        await ValidationIssueService.record(
            session, study, [_not_performed_issue(CONSISTENCY_STEP, reason, error_class=type(exc).__name__)]
        )
        return {"checked": False, "pairs": [], "findings": [], "reason": reason}

    result = {"checked": True, "pairs": list(consistency.CHECKED_PAIRS), "findings": findings, "reason": None}
    if not findings:
        return result

    checks, remaining = consistency.apply_resolutions(
        precompute_checks=evidence.get("precompute_checks"), blockers=blockers, findings=findings
    )
    evidence["precompute_checks"] = checks
    study.evidence_json = evidence
    if plan is not None and remaining != blockers:
        plan.blockers_json = remaining
    await session.flush()
    return result


async def conclude_without_execution(
    session: AsyncSession, study, reason: str, *, limitation: dict | None = None
) -> None:
    """Assess everything that needs no compute, then state the outcome. Never raises.

    The merge requirement is an accurate, completed assessment, not a successful reproduction.
    Reading the authors' sample metadata, inspecting the code they published and checking their
    results table are all public work, and none of it needs the data bioAF cannot obtain.
    """
    from app.services.validation_study_service import ValidationStudyService

    await run_assessment(session, study)

    evidence = dict(study.evidence_json or {})
    outcome = completion_for(
        route=study.intended_route or (evidence.get("route") or "deposit"),
        capabilities=evidence.get("capabilities") or {},
        supplements=evidence.get("supplements") or [],
        extra_limitations=[limitation] if limitation else None,
        # A paper read from a pasted body carries no manifest, so an empty inventory there means
        # nobody listed its attachments, not that it has none.
        manifest_known=manifest_known(evidence),
        # change_7.4 section 1.3: the acquisition record, so an acquired input is never reported as
        # none acquired.
        acquisition=evidence,
        data_run_id=study.data_run_id,
        fetched_samples=await _fetched_samples(session, study),
    )
    # The assessment reason is a dependent too: an unresolved contradiction has to reach the reader
    # of the outcome, not only the reader of the evidence bundle.
    unresolved = (evidence.get("assessment") or {}).get("unresolved_contradictions") or []
    if unresolved:
        outcome["limitations"] = [
            *outcome.get("limitations", []),
            {
                "kind": "failed_discovery",
                "resource": "this study's own statements",
                "operation": study.intended_route or "deposit",
                "detail": unresolved[0].get("outcome") or "two statements in this assessment disagree",
            },
        ]
        outcome["reason"] = " ".join(limitation["detail"] for limitation in outcome["limitations"])

    evidence["completion"] = outcome
    study.evidence_json = evidence
    study.failure_reason = outcome["reason"] or reason
    await session.flush()

    await ValidationStudyService.transition(
        session,
        study.id,
        study.organization_id,
        study.requested_by_user_id,
        "classified",
        classification=outcome["classification"],
    )


async def _fetched_samples(session: AsyncSession, study) -> int | None:
    """How many of the study's samples carry a fetched sequencing file, or None with no data run."""
    if not study.data_run_id or study.experiment_id is None:
        return None
    from sqlalchemy import func

    from app.models.sample import Sample, sample_files

    return int(
        (
            await session.execute(
                select(func.count(func.distinct(Sample.id)))
                .join(sample_files, Sample.id == sample_files.c.sample_id)
                .where(Sample.experiment_id == study.experiment_id)
            )
        ).scalar()
        or 0
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def deposit_head_fetcher(url: str, max_bytes: int) -> bytes:
    """change_7.5 section 3.2: at most ``max_bytes`` of a deposited file's first bytes, streamed. A
    preview never downloads the whole file."""
    import httpx

    chunks: list[bytes] = []
    size = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_raw():
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    break
    return b"".join(chunks)[:max_bytes]
