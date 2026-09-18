"""plan_8_3 section 0.2: replay a study's SAVED evidence through bioAF's production callers.

The first implementation of plan_8_3 was settled by unit tests over constructed inputs, and every
stage it reported built left no eligible paper able to reach a score. Two of its new checks refuse
correct evidence, and neither was caught, because nothing stood between a unit test and a live paper
read: a read spends two extraction calls, consumes provider credit and yields a different paper state
every time.

This harness restores a captured study (``tests/fixtures/*/study_<id>_persisted.json``) into the test
database with its own ids, and then drives the SAME functions the driver, the queue and the API call:

- ``replay_mapping``   -> ``ValidationDriverService._map_from_input_choice``
- ``replay_consistency`` -> ``validation_consistency_checks.enqueue`` + ``run_pending``
- ``replay_report``    -> ``validation_report_summary.report_summary_for``

Nothing here reads a paper, calls a model or launches compute. A table a check needs is served from
the committed fixture bytes the live run actually read, matched by filename, so a replayed check reads
the same bytes under the same checksum.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime

from sqlalchemy import text as sa_text

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# The three live regression studies of plan_8_3's current baseline, and where each was captured.
SAVED_STUDIES = {
    55: FIXTURES / "groff" / "study_55_persisted.json",
    56: FIXTURES / "samd1" / "study_56_persisted.json",
    57: FIXTURES / "substrate_stiffness" / "study_57_persisted.json",
}

# The bytes a replayed check may read, by the tail of the filename it was recorded against. Each is a
# committed fixture whose sha256 is the one the live run recorded.
SAVED_TABLES = {
    "Supplemental_File_3_XX-v-XY_siggenes.txt": FIXTURES / "groff" / "supplemental_file_3_siggenes.txt",
    "GSE144396_RNA-Seq_DeSeq2.txt.gz": FIXTURES / "samd1" / "GSE144396_RNA-Seq_DeSeq2.txt.gz",
}


def load(study_id: int) -> dict:
    """One captured study, as it was persisted."""
    return json.loads(SAVED_STUDIES[study_id].read_text())


def _at(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


_TIMESTAMPS = frozenset(
    {
        "requested_at",
        "approved_at",
        "created_at",
        "updated_at",
        "superseded_at",
        "next_attempt_at",
        "last_attempted_at",
        "occurred_at",
    }
)


def _columns(row: dict, model) -> dict:
    """The saved row's values for the columns the model actually has, timestamps parsed."""
    known = {c.key for c in model.__table__.columns}
    return {k: (_at(v) if k in _TIMESTAMPS else v) for k, v in row.items() if k in known}


class Restored:
    """A captured study as rows in the database: the ids are the captured ones, so a check record's
    ``check_id`` still names its own plan and target."""

    def __init__(self, bundle: dict, study, plans: list, targets: list, checks: list, issues: list):
        self.bundle = bundle
        self.study = study
        self.plan = plans[0] if plans else None
        self.plans = plans
        self.targets = targets
        self.checks = checks
        self.issues = issues

    @property
    def evidence(self) -> dict:
        return self.study.evidence_json or {}


async def restore(session, study_id: int, *, organization_id: int, user_id: int) -> Restored:
    """Write a captured study's rows into the database under this organization, ids intact."""
    from app.models.comparison_target import ComparisonTarget
    from app.models.reproduction_plan import ReproductionPlan
    from app.models.validation_check_record import ValidationCheckRecord
    from app.models.validation_study import ValidationStudy
    from app.models.validation_study_issue import ValidationStudyIssue

    bundle = load(study_id)
    study = ValidationStudy(
        **_columns(bundle["study"], ValidationStudy),
        organization_id=organization_id,
        requested_by_user_id=user_id,
    )
    session.add(study)
    await session.flush()

    plans = []
    for row in bundle["reproduction_plans"]:
        plan = ReproductionPlan(**_columns(row, ReproductionPlan))
        session.add(plan)
        plans.append(plan)
    targets = []
    for row in bundle["comparison_targets"]:
        target = ComparisonTarget(**_columns(row, ComparisonTarget))
        session.add(target)
        targets.append(target)
    await session.flush()

    if plans:
        study.reproduction_plan_id = plans[0].id
    checks = []
    for row in bundle["check_records"]:
        record = ValidationCheckRecord(**_columns(row, ValidationCheckRecord))
        session.add(record)
        checks.append(record)
    issues = []
    for row in bundle["issues"]:
        issue = ValidationStudyIssue(**_columns(row, ValidationStudyIssue))
        session.add(issue)
        issues.append(issue)
    await session.flush()

    # The captured ids were inserted explicitly, so every sequence is behind them. A later insert
    # through the sequence (a new check record, say) would collide.
    for table in (
        "validation_studies",
        "reproduction_plans",
        "comparison_targets",
        "validation_check_records",
        "validation_study_issues",
    ):
        await session.execute(
            sa_text(
                f"select setval(pg_get_serial_sequence('{table}', 'id'), coalesce((select max(id) from {table}), 1))"
            )
        )
    return Restored(bundle, study, plans, targets, checks, issues)


def saved_table_fetcher():
    """A fetcher that serves the committed bytes of the tables these studies' checks read.

    A URL it holds no bytes for raises, so a replay can never quietly reach the network.
    """

    async def fetch(url: str) -> bytes:
        for tail, path in SAVED_TABLES.items():
            if str(url).endswith(tail):
                return path.read_bytes()
        raise AssertionError(f"the replay has no saved bytes for {url}")

    return fetch


async def replay_mapping(session, restored: Restored) -> dict:
    """Drive the driver's own mapping validation over the saved proposal. Returns the validation.

    The study is put back into ``inspecting_deposit``, the state it was in when the mapping ran: a
    refused mapping HOLDS the study there, and a captured study that has since reached a terminal
    classification cannot be held again.
    """
    from app.services.validation_driver_service import ValidationDriverService

    restored.study.state = "inspecting_deposit"
    restored.study.classification = None
    await session.flush()
    evidence = dict(restored.evidence)
    choice = dict(evidence.get("input_choice") or {})
    inspection = evidence.get("deposit_inspection") or {}
    matrices = [
        f
        for f in (evidence.get("deposit") or {}).get("files") or []
        if f.get("filename") == choice.get("primary_matrix")
    ]
    matrix = matrices[0] if matrices else {"filename": choice.get("primary_matrix")}
    await ValidationDriverService._map_from_input_choice(
        session,
        restored.study,
        evidence,
        restored.plan,
        restored.plan.differential_design_json or {},
        inspection,
        matrix,
        choice,
    )
    restored.study.evidence_json = evidence
    await session.flush()
    return (evidence.get("input_choice") or {}).get("mapping_validation") or {}


async def replay_consistency(session, restored: Restored, *, fetcher=None) -> dict:
    """Re-run the study's author-result checks from pending over the saved evidence.

    Returns ``{claim_index: outcome}``. The records are reset first: a replay runs the check again,
    against the same saved table bytes and the same predicates, and must reach the same outcome.
    """
    from app.services import validation_check_queue as queue
    from app.services import validation_consistency_checks as consistency

    for record in restored.checks:
        if record.kind != queue.AUTHOR_RESULTS:
            continue
        record.state = queue.PENDING
        record.outcome_json = None
        record.terminal_reason = None
        record.retry_count = 0
        record.next_attempt_at = None
        record.last_attempted_at = None
    await session.flush()
    await consistency.enqueue(session, restored.study, restored.plan)
    await consistency.run_pending(session, restored.study, restored.plan, fetcher=fetcher or saved_table_fetcher())
    outcomes = {}
    for record in await queue.records_for(session, restored.study.id, kind=queue.AUTHOR_RESULTS):
        outcome = record.outcome_json or {}
        index = outcome.get("claim_index")
        if index is None:
            index = next((a.get("claim_index") for a in record.attempts_json or [] if a.get("claim_index")), None)
        outcomes[index if index is not None else record.check_id] = {
            **outcome,
            "state": record.state,
            "terminal_reason": record.terminal_reason,
        }
    return outcomes


def saved_supplement_bundle(*files: pathlib.Path) -> bytes:
    """The paper's supplement bundle, built from committed fixture files under their own names."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in files:
            archive.writestr(path.name, path.read_bytes())
    return buffer.getvalue()


# The Groff supplements as the paper publishes them, under the filenames the study recorded.
GROFF_BUNDLE = {
    "supp_gr.252981.119_Supplemental_File_1_embryo_metadata.txt": (
        FIXTURES / "groff" / "supplemental_file_1_embryo_metadata.txt"
    ),
    "supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx": (
        FIXTURES / "groff" / "supplemental_file_2_allrcode.docx"
    ),
    "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt": (
        FIXTURES / "groff" / "supplemental_file_3_siggenes.txt"
    ),
}


def groff_bundle_fetcher():
    """The bundle the assessment stage downloads, from the committed supplements."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, path in GROFF_BUNDLE.items():
            archive.writestr(name, path.read_bytes())
    blob = buffer.getvalue()

    async def fetch(_url):
        return blob

    return fetch


async def replay_recovery(session, restored: Restored, *, fetcher, monkeypatch) -> dict:
    """Run the study's own recovery over its saved evidence, with its supplements served from committed
    bytes. Returns the recovery's result. No model call, no compute, and no workflow is launched."""
    from app.services.validation_recovery import run_recovery

    monkeypatch.setattr("app.services.validation_assessment.deposit_bytes_fetcher", fetcher)
    return await run_recovery(session, restored.study, user_id=None)


async def replay_assessment(session, restored: Restored, *, fetcher, monkeypatch) -> dict:
    """Re-run the assessment stage over the saved evidence, with the paper's supplements served from
    committed bytes.

    This is the production caller that COMPUTES author-result consistency while a table's bytes are in
    hand; the queued check keeps and reuses what it recorded. A repair to what a check establishes
    therefore reaches an existing study through this stage, which the study's own recovery runs, and
    never by rewriting a record in place.
    """
    from app.services.validation_assessment import run_assessment

    monkeypatch.setattr("app.services.validation_assessment.deposit_bytes_fetcher", fetcher)
    restored.study.state = "acquiring_processed"
    restored.study.classification = None
    evidence = dict(restored.evidence)
    evidence.pop("assessment", None)
    restored.study.evidence_json = evidence
    await session.flush()
    await run_assessment(session, restored.study)
    await session.flush()
    return restored.study.evidence_json or {}


def consistency_of(evidence: dict, filename: str) -> list[dict]:
    """The consistency records the assessment stage wrote for one supplement."""
    for row in evidence.get("supplements") or []:
        if isinstance(row, dict) and row.get("filename") == filename:
            return list(row.get("consistency") or [])
    return []


async def replay_report(session, restored: Restored) -> dict:
    """Project the saved study exactly as the report API does."""
    from app.services.validation_report_summary import report_summary_for

    return await report_summary_for(session, restored.study, restored.study.organization_id)
