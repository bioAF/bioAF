"""plan_8_2 section 4.2 (approved by the owner 2026-09-14): a compact scorecard and four report sections.

The report was an all-or-nothing read. It now opens on the scorecard and a strip of decisions, then four
sections, each with a short summary and counts: Findings, Data and code, Checks performed, Run diagnostics.
This module derives those summaries and counts from the projection's other parts, so the page, the JSON and
the markdown export say the same thing, and nothing is hidden: the detail stays in the sections.

- Findings are grouped by the experiment that reports them. A reason several findings share is stated once,
  with the findings it affects; each of those findings points to it. A valid discrepancy opens by default.
- Resources bioAF has no adapter for are one compact group, with links; sample records another.
- The scorecard names its units: findings conclusive and inconclusive, and checks by what they did.

The words are the ones the owner approved in the mock.
"""

from __future__ import annotations

EXCLUDED_LABEL = "Not scored: technical prerequisites and descriptive checks"
NO_EXPERIMENT_LABEL = "Not linked to an experiment"


def _n(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _unit(key: str, count: int, singular: str, plural: str) -> dict:
    return {"key": key, "count": count, "label": singular if count == 1 else plural}


def units(card: dict, *, claims: list[dict], applicability: dict | None) -> list[dict]:
    """What the scorecard counts, each in its own unit: findings are never counted as checks."""
    if (applicability or {}).get("status") == "not_applicable":
        outside = sum(1 for e in applicability.get("experiments") or [] if not e.get("supported"))
        return [
            _unit(
                "experiments_outside_methods",
                outside,
                "experiment outside bioAF's methods",
                "experiments outside bioAF's methods",
            )
        ]
    found: list[dict] = []
    if card.get("total_count") and card.get("assessed_count") is not None:
        assessed, total = card["assessed_count"], card["total_count"]
        found.append(_unit("findings_conclusive", assessed, "finding conclusive", "findings conclusive"))
        found.append(_unit("findings_inconclusive", total - assessed, "finding inconclusive", "findings inconclusive"))
    counts = ((card.get("activity") or {}).get("counts")) or {}
    for key, count, singular, plural in (
        # plan_8_4 defect 4: what the checks CONCLUDED, not what the queue finished. A comparison
        # recorded `done` with an outcome of `unresolved` is completed work and no conclusion.
        ("checks_concluded", counts.get("concluded", 0), "check concluded", "checks concluded"),
        (
            "checks_without_conclusion",
            counts.get("unresolved", 0) + counts.get("blocked", 0) + counts.get("finished_without_conclusion", 0),
            "check completed without a conclusion",
            "checks completed without a conclusion",
        ),
        (
            "checks_under_way",
            counts.get("pending", 0) + counts.get("retrying", 0) + counts.get("running", 0),
            "check under way",
            "checks under way",
        ),
        (
            "checks_pending_re_evaluation",
            sum(1 for c in claims if (c.get("consistency") or {}).get("outcome") == "pending_re_evaluation"),
            "check pending re-evaluation",
            "checks pending re-evaluation",
        ),
    ):
        if count:
            found.append(_unit(key, count, singular, plural))
    return found


# ---- findings ------------------------------------------------------------------------------------------


def _experiment_of(item: dict, claims: list[dict]) -> str | None:
    ids = item.get("experiment_ids") or []
    if ids:
        return ids[0]
    for index in item.get("claim_indices") or []:
        if 0 <= index < len(claims):
            experiment = (claims[index] or {}).get("experiment") or {}
            if experiment.get("id"):
                return experiment["id"]
    return None


def _group_label(experiment: dict | None, experiment_id: str | None) -> str:
    if experiment_id is None:
        return NO_EXPERIMENT_LABEL
    experiment = experiment or {}
    label = f"Experiment {experiment_id}: {experiment.get('assay') or 'assay not stated'}"
    return f"{label} ({experiment['workflow']})" if experiment.get("workflow") else label


def _findings(card: dict, claims: list[dict], experiments: list[dict], applicability: dict | None) -> dict:
    scoreable = [*card.get("assessed_items", []), *card.get("unassessed_items", [])]
    excluded = list(card.get("excluded_items") or [])
    by_id = {e.get("id"): e for e in experiments if isinstance(e, dict)}
    order = [e.get("id") for e in experiments if isinstance(e, dict)]

    groups: dict[str | None, list[str]] = {}
    for item in scoreable:
        groups.setdefault(_experiment_of(item, claims), []).append(item["finding_id"])
    ordered = sorted(groups, key=lambda key: (key is None, order.index(key) if key in order else len(order)))
    group_rows = [
        {"key": key or "none", "label": _group_label(by_id.get(key), key), "finding_ids": groups[key]}
        for key in ordered
    ]
    if excluded:
        group_rows.append(
            {"key": "excluded", "label": EXCLUDED_LABEL, "finding_ids": [i["finding_id"] for i in excluded]}
        )

    # A reason several findings share, stated once. Each of those findings points to it.
    by_reason: dict[str, list[dict]] = {}
    for item in card.get("unassessed_items") or []:
        if item.get("reason"):
            by_reason.setdefault(item["reason"], []).append(item)
    shared = []
    for text, items in by_reason.items():
        if len(items) < 2:
            continue
        reason_id = f"R{len(shared) + 1}"
        for item in items:
            item["shared_reason"] = reason_id
        shared.append(
            {
                "id": reason_id,
                "text": text,
                "cause_label": items[0].get("cause_label"),
                "finding_ids": [i["finding_id"] for i in items],
            }
        )

    referenced = {i for item in [*scoreable, *excluded] for i in item.get("claim_indices") or []}
    counts: list[dict] = []
    for category, label in (("primary", "primary"), ("supporting", "supporting")):
        count = sum(1 for i in scoreable if i.get("category") == category)
        if count:
            counts.append({"label": f"{count} {label}", "tone": None})
    unestablished = sum(1 for i in scoreable if i.get("category") is None)
    if unestablished:
        counts.append({"label": f"{unestablished} importance not established", "tone": "warn"})
    for status, words, tone in (
        ("supported", "supported", "ok"),
        ("discrepancy", "discrepant", "bad"),
        ("unresolved", "unresolved", "warn"),
        ("blocked", "blocked", None),
        ("inconclusive", "inconclusive", None),
        ("not_attempted", "not attempted", None),
    ):
        count = sum(1 for i in scoreable if i.get("status") == status)
        if count:
            counts.append({"label": f"{count} {words}", "tone": tone})
    if excluded:
        counts.append({"label": f"{len(excluded)} technical, not scored", "tone": None})

    if (applicability or {}).get("status") == "not_applicable":
        summary = " ".join(p for p in (applicability.get("limitation"), applicability.get("statement")) if p)
    elif card.get("total_count") is None:
        # The scorecard above says why; the section does not repeat it.
        summary = "The findings are being established." if card.get("status") == "pending" else "The findings are not established."
    else:
        total, assessed = card["total_count"], card.get("assessed_count") or 0
        summary = f"{_n(total, 'finding')} scored, {assessed} assessed."
        if assessed:
            summary += f" {card.get('supported_count') or 0} supported, {card.get('discrepant_count') or 0} discrepant."
        if shared:
            summary += (
                f" {_n(len(shared), 'reason')} shared by several findings "
                f"{'is' if len(shared) == 1 else 'are'} stated once."
            )
    return {
        "summary": summary,
        "counts": counts,
        "groups": group_rows,
        "shared_reasons": shared,
        "open": [i["finding_id"] for i in scoreable if i.get("status") == "discrepancy"],
        "ungrouped_claims": [i for i in range(len(claims)) if i not in referenced],
    }


# ---- data and code ---------------------------------------------------------------------------------------


def _resource_kind(row: dict) -> str:
    support = row.get("support") or {}
    if row.get("level") == "sample":
        return "sample"
    if row.get("archive") == "journal_supplement" or row.get("type") == "supplementary_file":
        return "supplement"
    if row.get("type") == "sequencing_data" or support.get("download_supported") == "yes":
        return "deposit"
    return "unsupported"


def _compact(row: dict) -> dict:
    return {k: row.get(k) for k in ("identifier", "type", "archive", "link", "limitation")}


def _data(summary: dict) -> dict:
    resources = [r for r in summary.get("resources") or [] if isinstance(r, dict)]
    kinds = {
        k: [r for r in resources if _resource_kind(r) == k] for k in ("deposit", "unsupported", "sample", "supplement")
    }
    readable = sum(1 for r in kinds["deposit"] if (r.get("support") or {}).get("download_supported") == "yes")
    artifacts = [a for a in summary.get("artifacts") or [] if isinstance(a, dict)]
    retrieved = sum(1 for a in artifacts if (a.get("retrieval") or {}).get("status") == "retrieved")
    code = [c for c in summary.get("code_sources") or [] if isinstance(c, dict)]
    counts = []
    if kinds["deposit"]:
        counts.append({"label": _n(len(kinds["deposit"]), "deposit"), "tone": None})
        counts.append({"label": f"{readable} readable", "tone": "ok" if readable else None})
    if kinds["unsupported"]:
        counts.append({"label": f"{len(kinds['unsupported'])} without an adapter", "tone": None})
    if kinds["sample"]:
        counts.append({"label": _n(len(kinds["sample"]), "sample record"), "tone": None})
    if artifacts:
        counts.append({"label": f"{retrieved} of {len(artifacts)} attachments retrieved", "tone": None})
    counts.append({"label": _n(len(code), "code source") if code else "no code", "tone": None})

    if not resources and not artifacts:
        sentence = "The paper names no deposit, supplement or code."
    else:
        parts = []
        if kinds["deposit"]:
            parts.append(f"{_n(len(kinds['deposit']), 'deposit')} ({readable} bioAF reads)")
        if kinds["unsupported"]:
            parts.append(f"{len(kinds['unsupported'])} bioAF has no adapter for")
        if kinds["sample"]:
            parts.append(_n(len(kinds["sample"]), "sample record"))
        if kinds["supplement"]:
            parts.append(_n(len(kinds["supplement"]), "supplement"))
        sentence = f"{_n(len(resources), 'resource')}: {', '.join(parts)}." if resources else ""
        if artifacts:
            sentence += f" {retrieved} of {len(artifacts)} attachments retrieved."
        sentence += f" {_n(len(code), 'code source')}." if code else " No code is linked."
    return {
        "summary": sentence.strip(),
        "counts": counts,
        "unsupported": {
            "resources": [_compact(r) for r in kinds["unsupported"]],
            "note": "bioAF has no adapter for these, and no check reads them; they are listed, not held against the paper.",
        },
        "sample_records": [_compact(r) for r in kinds["sample"]],
    }


# ---- checks performed -----------------------------------------------------------------------------------


def _checks(summary: dict) -> dict:
    from app.services.validation_report_summary import CLAIM_CHECK_LABELS

    claims = [c for c in summary.get("claims") or [] if isinstance(c, dict)]
    rows = []
    for key, label in CLAIM_CHECK_LABELS.items():
        statuses = [
            row.get("status")
            for c in claims
            for row in c.get("checks") or []
            if isinstance(row, dict) and row.get("key") == key
        ]
        if not statuses:
            continue
        rows.append(
            {
                "key": key,
                "label": label,
                "claims": len(statuses),
                "available": statuses.count("available"),
                "unresolved": statuses.count("unresolved"),
                "unavailable": statuses.count("unavailable"),
            }
        )
    outcomes: dict[str, int] = {}
    for claim in claims:
        outcome = (claim.get("consistency") or {}).get("outcome")
        if outcome:
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
    consistency_total = sum(outcomes.values())
    selection = summary.get("selection") or {}
    if selection.get("check_label"):
        sentence = f"This run checks claim {(selection.get('claim_index') or 0) + 1} by {selection['check_label']}"
        sentence += f" on {selection['workflow']}." if selection.get("workflow") else "."
    elif selection.get("reason"):
        sentence = f"No claim is checked by this run: {selection['reason']}."
    else:
        sentence = ""
    words = [
        (outcomes.get(k, 0), w)
        for k, w in (
            ("agree", "agree"),
            ("disagree", "differ"),
            ("unresolved", "unresolved"),
            ("not_checkable", "not checkable"),
            ("pending_re_evaluation", "pending re-evaluation"),
        )
    ]
    if consistency_total:
        detail = ", ".join(f"{n} {w}" for n, w in words if n)
        sentence += f" {_n(consistency_total, 'consistency check')} against the authors' results: {detail}."
    counts = []
    if consistency_total:
        counts.append({"label": _n(consistency_total, "consistency check"), "tone": None})
        for n, w in words:
            if n:
                tone = {"agree": "ok", "differ": "bad", "unresolved": "warn"}.get(w)
                counts.append({"label": f"{n} {w}", "tone": tone})
    return {"summary": sentence.strip() or "No check has run.", "counts": counts, "rows": rows}


# ---- run diagnostics ------------------------------------------------------------------------------------

_ERROR_REASONS = ("error", "persistence_failed")


def _diagnostics(summary: dict, checks: list[dict], issues: list[dict]) -> dict:
    from app.services.validation_check_queue import activity_of

    rows = [
        {
            "check_id": c.get("check_id"),
            "kind": c.get("kind"),
            "state": c.get("state"),
            "activity": activity_of(c),
            "revision": c.get("revision"),
            "retry_count": c.get("retry_count") or 0,
            "terminal_reason": c.get("terminal_reason"),
            "outcome_revision": c.get("outcome_revision"),
        }
        for c in checks
        if isinstance(c, dict)
    ]
    retrying = sum(1 for r in rows if r["activity"] == "retrying")
    errors = sum(1 for r in rows if r["terminal_reason"] in _ERROR_REASONS)
    revised = sum(1 for r in rows if (r["revision"] or 1) > 1)
    limitations = len(summary.get("limitations") or [])
    counts = [{"label": _n(len(rows), "check record"), "tone": None}]
    if retrying:
        counts.append({"label": f"{retrying} retrying", "tone": "warn"})
    counts.append({"label": _n(errors, "error"), "tone": "bad" if errors else None})
    if limitations:
        counts.append({"label": _n(limitations, "limitation"), "tone": None})
    counts.append({"label": _n(len(issues), "issue"), "tone": None})
    sentence = f"{_n(len(rows), 'check record')}"
    if revised:
        sentence += f", {revised} revised"
    sentence += f"; {_n(errors, 'error')}"
    if retrying:
        sentence += f"; {retrying} retrying"
    sentence += f"; {_n(len(issues), 'issue')} recorded."
    return {"summary": sentence, "counts": counts, "checks": rows}


def sections(summary: dict, *, checks: list[dict] | None, issues: list[dict] | None) -> dict:
    """The four sections' summaries and counts, and the findings' grouping, from the projection itself."""
    card = summary.get("scorecard") or {}
    claims = [c for c in summary.get("claims") or [] if isinstance(c, dict)]
    return {
        "findings": _findings(card, claims, summary.get("experiments") or [], summary.get("applicability")),
        "data": _data(summary),
        "checks": _checks(summary),
        "diagnostics": _diagnostics(summary, checks or [], issues or []),
    }
