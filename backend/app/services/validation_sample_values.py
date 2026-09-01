"""Answer a pipeline's design columns from the contrast a scientist already ratified.

``sample_values`` is fully built on the manual launch path: a pipeline's schema declares the column,
the launch grid collects it per sample, ``check_contract_satisfiable`` refuses the launch without
it, and the preview shows exactly what will be submitted. The lit_validation driver launches with no
form at all and passed none of it, so a validation study was refused for every column that describes
EXPERIMENTAL DESIGN rather than the sample itself: cutandrun's ``group``, atacseq's ``replicate``,
sarek's ``patient``.

Those columns are deliberately absent from ``_COLUMN_TO_SAMPLE_FIELD`` and must stay absent. The
comment there is right: mag's ``group`` controls co-assembly and rnasplice's ``condition`` defines
the differential contrast, so guessing one yields a scientifically wrong result that still runs
green. This is not a guess. The plan's differential design IS a statement of arm membership, made by
the scientist at the C1 gate and already resolved to real fetched samples by
``_resolve_sample_design``. It arrives through ``sample_values``, the same channel a scientist's own
answers use, rather than through bioAF's automatic maps.

Tier 1 only, and deliberately narrow: arm membership, position within the arm, and the subject a
sample came from. Anything a design does not state (cutandrun's ``control``, ampliseq's primers,
bacass's genome size) stays unanswered, and the launch still refuses and names it.
"""

from __future__ import annotations

from app.services.sample_sheet_service import acceptable_spelling

# Which of a pipeline's columns each part of a design answers. Exact and explicit, the same
# discipline `_COLUMN_TO_SAMPLE_FIELD` states: a column is answered only if it is named here, never
# by reflecting a design key onto a same-named column.
#
# Every value is checked against the column's own regex and its own enum before it is emitted, which
# is what keeps a shared NAME from being read as a shared MEANING. rnasplice's `condition` is the
# differential contrast and rnastructurome's is an rf-norm chemistry (treated/untreated/denatured);
# the second declares an enum and the first does not, so an arm name reaches one and not the other.
_ARM_COLUMNS: tuple[str, ...] = ("group", "condition", "type")
_REPLICATE_COLUMNS: tuple[str, ...] = ("replicate",)
_SUBJECT_COLUMNS: tuple[str, ...] = ("patient", "case_id")


def _primary_contrast(design) -> dict | None:
    """The contrast this study reproduces, which is the first one, as Level-3 also reads it."""
    contrasts = (design or {}).get("contrasts") or []
    return contrasts[0] if isinstance(contrasts, list) and contrasts and isinstance(contrasts[0], dict) else None


def _answer(row: dict[str, str], contract, columns: tuple[str, ...], value: str) -> None:
    """Fill whichever of ``columns`` this pipeline declares, in a spelling it will accept.

    Silence on failure is the point. A column whose pattern nothing can satisfy, or whose enum does
    not list this value, is left empty so ``check_contract_satisfiable`` blocks and names it. A value
    that merely looks plausible would produce a sheet that passes bioAF's own checks and then runs.
    """
    for column in columns:
        if column not in getattr(contract, "columns", ()):
            continue
        spelled = acceptable_spelling(value, (getattr(contract, "patterns", None) or {}).get(column))
        if not spelled:
            continue
        allowed = contract.enum_for(column)
        if allowed and spelled not in allowed:
            continue
        row[column] = spelled


def sample_values_from_design(design, samples: list, contract) -> dict[str, dict[str, str]]:
    """Per-sample samplesheet answers derived from the ratified design, keyed by sample id.

    Keyed by ``str(Sample.id)`` because that is what ``_supplied`` matches on, and it matches on the
    id rather than on position for the reason that module gives: a positional match off by one
    assigns every value to the wrong sample and the run still completes green.

    Empty when there is nothing to say: a QC-only study with no contrast, a pipeline whose schema
    bioAF could not read (an empty contract means "we do not know", never "it wants these"), or a
    sample the design does not place in either arm.
    """
    if contract is None or getattr(contract, "is_empty", True):
        return {}
    contrast = _primary_contrast(design)
    if contrast is None:
        return {}

    test_label = str(contrast.get("test_condition") or "").strip() or "test"
    reference_label = str(contrast.get("reference_condition") or "").strip() or "reference"
    subjects = contrast.get("subjects") or {}

    # A replicate is the nth sample OF ITS ARM. Numbering across the sheet would tell cutandrun that
    # the reference arm holds replicates 4, 5 and 6 of a group with three members.
    placement: dict[str, tuple[str, int]] = {}
    for key, label in (("test_samples", test_label), ("reference_samples", reference_label)):
        for index, external_id in enumerate(contrast.get(key) or []):
            token = str(external_id).strip()
            if token and token not in placement:
                placement[token] = (label, index + 1)

    out: dict[str, dict[str, str]] = {}
    for sample in samples:
        placed = placement.get(str(getattr(sample, "external_id", "") or "").strip())
        if placed is None:
            continue
        label, replicate = placed
        row: dict[str, str] = {}
        _answer(row, contract, _ARM_COLUMNS, label)
        _answer(row, contract, _REPLICATE_COLUMNS, str(replicate))
        subject = str(subjects.get(sample.external_id) or "").strip()
        if subject:
            _answer(row, contract, _SUBJECT_COLUMNS, subject)
        if row:
            out[str(sample.id)] = row
    return out
