"""plan_8_2 section 3.1 (and owner decision 1): a person's recorded confirmation of how a table reads.

When bioAF's own evidence (a header, a legend, a README, the methods) does not establish a table's
interpretation, a person may record it: that the table reports a contrast (a binding, decision 1), which
column holds the identifier, the fold change, the P value and the adjusted P value, the effect scale, the
ratio orientation, and that the file is the claim's complete selected list. Each confirmation states the
evidence it rests on, and is kept in ``evidence.table_confirmations`` with its version, apart from any
comparison. The latest for a table and contrast applies; earlier ones stay.

A confirmation is never an assumption bioAF makes. Values can still reject it (a column read as a P value
that holds values above 1), and recording one re-evaluates only the checks of its contrast.
"""

from __future__ import annotations

VERSION = 1
ROLES = ("id", "lfc", "pvalue", "padj")
SCALES = ("log2", "linear")
ORIENTATIONS = ("test_over_reference", "reference_over_test")


class ConfirmationRefused(ValueError):
    """The confirmation cannot be recorded; the words say why."""


def confirmation_entry(
    *,
    table: str,
    contrast: str,
    reports_contrast: bool = False,
    columns: dict | None = None,
    effect_scale: str | None = None,
    orientation: str | None = None,
    selected_list: bool = False,
    filter_semantics: dict | None = None,
    note: str,
    confirmed_by: str | None,
    at: str,
) -> dict:
    """One recorded confirmation, validated. Raises ``ConfirmationRefused``."""
    note = (note or "").strip()
    if not note:
        raise ConfirmationRefused("a confirmation records the evidence it rests on; say what establishes it")
    if filter_semantics is not None:
        # plan_8_3 section 1.2: the control for a refinement whose wording does not say whether its
        # cutoff is on the magnitude of the effect or on its signed value. It settles that and nothing
        # else about the filter: the cutoff, its operator and its scale stay the paper's own words.
        unknown = set(filter_semantics) - {"magnitude", "note"}
        if unknown:
            raise ConfirmationRefused(
                f"a filter confirmation states the magnitude reading and nothing else ({', '.join(sorted(unknown))})"
            )
        if not isinstance(filter_semantics.get("magnitude"), bool):
            raise ConfirmationRefused(
                "say whether the refinement's cutoff is on the magnitude of the fold change (true) or on its "
                "signed value (false)"
            )
    if columns:
        for role, index in columns.items():
            if role not in ROLES:
                raise ConfirmationRefused(f"{role} is not a column role bioAF reads ({', '.join(ROLES)})")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise ConfirmationRefused(f"the column for {role} must be a column number, counting from 0")
        if len(set(columns.values())) != len(columns):
            raise ConfirmationRefused("two roles name the same column")
    if effect_scale is not None and effect_scale not in SCALES:
        raise ConfirmationRefused("the effect scale must be log2 or linear")
    if orientation is not None and orientation not in ORIENTATIONS:
        raise ConfirmationRefused("the orientation must be test over reference or reference over test")
    if not (reports_contrast or columns or effect_scale or orientation or selected_list or filter_semantics):
        raise ConfirmationRefused("the confirmation says nothing about the table")
    return {
        "version": VERSION,
        "table": table,
        "contrast": contrast,
        "reports_contrast": bool(reports_contrast),
        "columns": dict(columns) if columns else None,
        "effect_scale": effect_scale,
        "orientation": orientation,
        "selected_list": bool(selected_list),
        # plan_8_3 section 1.2: which reading of a documented refinement's cutoff the paper meant.
        "filter_semantics": dict(filter_semantics) if filter_semantics else None,
        "note": note,
        "confirmed_by": confirmed_by,
        "at": at,
    }


def latest(evidence: dict | None, table_name: str | None, contrast_name: str | None) -> dict | None:
    """The confirmation that applies to this table and contrast: the latest recorded."""
    found = None
    for entry in (evidence or {}).get("table_confirmations") or []:
        if isinstance(entry, dict) and entry.get("table") == table_name and entry.get("contrast") == contrast_name:
            found = entry
    return found


def interpretation_of(confirmation: dict | None) -> dict | None:
    """What a confirmation says about reading the table, in the shape ``check_claim`` applies, or None."""
    if not isinstance(confirmation, dict):
        return None
    if not (confirmation.get("columns") or confirmation.get("effect_scale") or confirmation.get("orientation")):
        return None
    return {
        "columns": confirmation.get("columns"),
        "effect_scale": confirmation.get("effect_scale"),
        "orientation": confirmation.get("orientation"),
        "confirmed_by": confirmation.get("confirmed_by"),
        "at": confirmation.get("at"),
        "note": confirmation.get("note"),
        "version": confirmation.get("version"),
    }


def fingerprint_of(confirmation: dict | None) -> str | None:
    from app.services.validation_check_queue import fingerprint

    return fingerprint(confirmation) if isinstance(confirmation, dict) else None


async def record_confirmation(session, study, plan, entry: dict, *, reason: str) -> list:
    """Keep ``entry`` on the study and re-evaluate the checks of its contrast, and only those."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services import validation_check_queue as queue
    from app.services.validation_claim_capabilities import refresh_claim_capabilities
    from app.services.validation_consistency_checks import enqueue

    evidence = dict(study.evidence_json or {})
    evidence["table_confirmations"] = [*(evidence.get("table_confirmations") or []), entry]
    study.evidence_json = evidence
    await session.flush()

    contrasts = [c for c in (plan.differential_design_json or {}).get("contrasts") or [] if isinstance(c, dict)]
    position = next((i for i, c in enumerate(contrasts) if c.get("name") == entry.get("contrast")), None)
    targets = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    dependent = {t.id for t in targets if t.contrast_index == position}
    recorded = {
        r.comparison_target_id for r in await queue.records_for(session, study.id) if r.kind == queue.AUTHOR_RESULTS
    }
    records = await enqueue(session, study, plan, reason=reason, skip=recorded - dependent)
    await refresh_claim_capabilities(session, study, plan)
    return records
