"""Read-only assessment of environment-cascade versions that lost their QC definition.

Before the fix, cascade versions were created without qc_template, qc_config_json or
each variable's reference_category. The assessment walks each cascade version's
audited provenance to its nearest user-created ancestor and reports what was lost,
which runs used it, and which dashboards would need a separate regeneration review.
It never writes.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.cli.assess_cascade_qc import assess_cascade_versions, format_report
from app.models.audit_log import AuditLog
from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.qc_dashboard import QCDashboard

QC_CONFIG = {"sections": [{"id": "main", "metrics": ["reads"]}], "metrics": {"reads": {"label": "Reads"}}}


class Org:
    """One organization's environment, environment versions, and a pipeline."""

    def __init__(self, session, user, env, env_versions, pipeline):
        self.session = session
        self.user = user
        self.env = env
        self.env_versions = env_versions
        self.pipeline = pipeline

    async def version(
        self,
        number: int,
        *,
        trigger: str,
        env_index: int = 0,
        qc_template: str | None = None,
        qc_config_json: dict | None = None,
        variables: list[tuple[str, str | None]] = (),
        pipeline: CustomPipeline | None = None,
        env_version_id: int | None = None,
    ) -> int:
        v = CustomPipelineVersion(
            custom_pipeline_id=(pipeline or self.pipeline).id,
            version_number=number,
            code_source_type="inline",
            code_content="print('x')",
            entrypoint_command="python x.py",
            environment_version_id=env_version_id or self.env_versions[env_index].id,
            version_trigger=trigger,
            status="active",
            created_by_user_id=self.user.id,
            qc_template=qc_template,
            qc_config_json=qc_config_json,
        )
        self.session.add(v)
        await self.session.flush()
        for name, category in variables:
            self.session.add(
                CustomPipelineVariable(
                    custom_pipeline_version_id=v.id,
                    variable_name=name,
                    variable_type="reference" if category else "string",
                    reference_category=category,
                    is_required=False,
                )
            )
        await self.session.flush()
        return v.id

    async def audit(self, version_id: int, source_id: int | None, env_version_id: int | None = None) -> None:
        details: dict = {"custom_pipeline_id": self.pipeline.id, "environment_version_id": env_version_id}
        if source_id is not None:
            details["source_version_id"] = source_id
        self.session.add(
            AuditLog(
                user_id=self.user.id,
                entity_type="custom_pipeline_version",
                entity_id=version_id,
                action="cascade_create",
                details_json=details,
            )
        )
        await self.session.flush()

    async def run(self, version_id: int, *, live_files: int = 0, deleted_files: int = 0) -> int:
        r = PipelineRun(
            organization_id=self.user.organization_id,
            pipeline_name=self.pipeline.pipeline_key,
            custom_pipeline_version_id=version_id,
            status="completed",
        )
        self.session.add(r)
        await self.session.flush()
        for i in range(live_files + deleted_files):
            self.session.add(
                File(
                    organization_id=self.user.organization_id,
                    gcs_uri=f"gs://results/runs/{r.id}/out{i}.csv",
                    filename=f"out{i}.csv",
                    file_type="csv",
                    source_pipeline_run_id=r.id,
                    source_type="pipeline_output",
                    deleted_at=func.now() if i >= live_files else None,
                )
            )
        await self.session.flush()
        return r.id

    async def dashboard(self, run_id: int, snapshot: dict | None) -> int:
        d = QCDashboard(
            organization_id=self.user.organization_id,
            pipeline_run_id=run_id,
            metrics_json={},
            qc_config_json=snapshot,
            status="ready",
        )
        self.session.add(d)
        await self.session.flush()
        return d.id


async def _make_org(session, name: str, user=None) -> Org:
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    if user is None:
        org = Organization(name=name, setup_complete=True)
        session.add(org)
        await session.flush()
        role_map = await seed_builtin_roles(session, org.id)
        user = User(
            email=f"{name.lower().replace(' ', '-')}@test.com",
            password_hash=AuthService.hash_password("assesspass"),
            role_id=role_map["admin"],
            organization_id=org.id,
            status="active",
        )
        session.add(user)
        await session.flush()
    env = Environment(
        name=f"{name} env",
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()
    env_versions = []
    for n in (1, 2, 3, 4):
        ev = EnvironmentVersion(
            environment_id=env.id,
            version_number=n,
            status="ready",
            definition_format="conda",
            definition_content="name: x\n",
            created_by_user_id=user.id,
        )
        session.add(ev)
        env_versions.append(ev)
    await session.flush()
    pipeline = CustomPipeline(
        organization_id=user.organization_id,
        name=f"{name} pipeline",
        pipeline_key=f"{name.lower().replace(' ', '-')}-pipeline",
        created_by_user_id=user.id,
    )
    session.add(pipeline)
    await session.flush()
    return Org(session, user, env, env_versions, pipeline)


@pytest_asyncio.fixture
async def org(session, admin_user) -> Org:
    return await _make_org(session, "Home", user=admin_user)


async def _assess(session, **kwargs):
    """Run the assessment inside a read-only transaction: any write raises."""
    await session.commit()
    await session.execute(text("SET TRANSACTION READ ONLY"))
    try:
        return await assess_cascade_versions(session, **kwargs)
    finally:
        await session.rollback()


def _by_version(assessment) -> dict:
    return {f.version_id: f for f in assessment.findings}


@pytest.mark.asyncio
async def test_direct_cascade_reports_every_lost_field_and_its_runs_and_dashboards(session, org):
    root = await org.version(
        1,
        trigger="user",
        qc_template="bulk_rnaseq",
        qc_config_json=QC_CONFIG,
        variables=[("genome", "genome"), ("gtf", "annotation"), ("mode", None)],
    )
    cascade = await org.version(
        2, trigger="environment_cascade", env_index=1, variables=[("genome", None), ("gtf", None), ("mode", None)]
    )
    await org.audit(cascade, root, org.env_versions[1].id)
    run_with_snapshot = await org.run(cascade, live_files=2, deleted_files=1)
    snapshot_dashboard = await org.dashboard(run_with_snapshot, {"template": "custom", "sections": []})
    run_without_snapshot = await org.run(cascade)
    empty_dashboard = await org.dashboard(run_without_snapshot, None)
    run_without_dashboard = await org.run(cascade)

    assessment = await _assess(session, org_id=org.user.organization_id)

    (finding,) = assessment.findings
    assert finding.version_id == cascade
    assert finding.outcome == "affected"
    assert finding.root_version_id == root
    assert finding.chain == [cascade, root]
    assert finding.lost_fields == [
        "qc_template",
        "qc_config_json",
        "reference_category:genome",
        "reference_category:gtf",
    ]
    assert finding.qc_render_changed is True
    runs = {r.run_id: r for r in finding.runs}
    assert set(runs) == {run_with_snapshot, run_without_snapshot, run_without_dashboard}
    assert runs[run_with_snapshot].live_output_files == 2
    assert runs[run_with_snapshot].deleted_output_files == 1
    assert [(d.dashboard_id, d.has_snapshot) for d in runs[run_with_snapshot].dashboards] == [
        (snapshot_dashboard, True)
    ]
    assert [(d.dashboard_id, d.has_snapshot) for d in runs[run_without_snapshot].dashboards] == [
        (empty_dashboard, False)
    ]
    assert runs[run_without_dashboard].dashboards == []
    assert assessment.next_after_id is None


@pytest.mark.asyncio
async def test_chain_of_cascades_resolves_to_the_nearest_user_version(session, org):
    root = await org.version(1, trigger="user", qc_template="scrnaseq", qc_config_json=QC_CONFIG)
    middle = await org.version(2, trigger="environment_cascade", env_index=1)
    latest = await org.version(3, trigger="environment_cascade", env_index=2)
    await org.audit(middle, root, org.env_versions[1].id)
    await org.audit(latest, middle, org.env_versions[2].id)

    findings = _by_version(await _assess(session))

    assert findings[latest].chain == [latest, middle, root]
    assert findings[latest].root_version_id == root
    assert findings[latest].outcome == "affected"
    assert findings[middle].chain == [middle, root]
    assert findings[middle].outcome == "affected"


@pytest.mark.asyncio
async def test_a_later_user_version_is_the_root_for_cascades_after_it(session, org):
    first = await org.version(1, trigger="user", qc_template="scrnaseq")
    cascade_a = await org.version(2, trigger="environment_cascade", env_index=1)
    second_user = await org.version(3, trigger="user", env_index=1)
    cascade_b = await org.version(4, trigger="environment_cascade", env_index=2)
    await org.audit(cascade_a, first, org.env_versions[1].id)
    await org.audit(cascade_b, second_user, org.env_versions[2].id)

    findings = _by_version(await _assess(session))

    assert findings[cascade_a].outcome == "affected"
    assert findings[cascade_b].root_version_id == second_user
    assert findings[cascade_b].outcome == "unaffected"


@pytest.mark.asyncio
async def test_null_ancestor_is_unaffected(session, org):
    root = await org.version(1, trigger="user", variables=[("x", None)])
    cascade = await org.version(2, trigger="environment_cascade", env_index=1, variables=[("x", None)])
    await org.audit(cascade, root, org.env_versions[1].id)

    (finding,) = (await _assess(session)).findings

    assert finding.outcome == "unaffected"
    assert finding.lost_fields == []
    assert finding.qc_render_changed is False


@pytest.mark.asyncio
async def test_empty_ancestor_config_is_reported_without_a_render_change(session, org):
    root = await org.version(1, trigger="user", qc_template="custom", qc_config_json={})
    cascade = await org.version(2, trigger="environment_cascade", env_index=1)
    await org.audit(cascade, root, org.env_versions[1].id)

    (finding,) = (await _assess(session)).findings

    assert finding.outcome == "affected"
    assert finding.lost_fields == ["qc_template", "qc_config_json"]
    assert finding.qc_render_changed is False


@pytest.mark.asyncio
async def test_already_repaired_cascade_is_unaffected(session, org):
    root = await org.version(
        1, trigger="user", qc_template="custom", qc_config_json=QC_CONFIG, variables=[("g", "genome")]
    )
    cascade = await org.version(
        2,
        trigger="environment_cascade",
        env_index=1,
        qc_template="custom",
        qc_config_json=QC_CONFIG,
        variables=[("g", "genome")],
    )
    await org.audit(cascade, root, org.env_versions[1].id)

    (finding,) = (await _assess(session)).findings

    assert finding.outcome == "unaffected"


@pytest.mark.asyncio
async def test_conflicting_values_are_flagged_not_assumed_lost(session, org):
    root = await org.version(1, trigger="user", qc_template="scrnaseq", variables=[("g", "genome")])
    middle = await org.version(
        2, trigger="environment_cascade", env_index=1, qc_template="bulk_rnaseq", variables=[("g", None)]
    )
    latest = await org.version(3, trigger="environment_cascade", env_index=2, variables=[("g", None), ("extra", None)])
    await org.audit(middle, root, org.env_versions[1].id)
    await org.audit(latest, middle, org.env_versions[2].id)

    findings = _by_version(await _assess(session))

    assert findings[middle].outcome == "conflicting"
    assert "value_differs:qc_template" in findings[middle].reasons
    assert findings[latest].outcome == "conflicting"
    assert "intermediate_differs:qc_template" in findings[latest].reasons
    assert "variable_set_mismatch" in findings[latest].reasons


@pytest.mark.asyncio
async def test_broken_provenance_is_unresolved_with_reason_codes(session, org):
    root = await org.version(1, trigger="user", qc_template="scrnaseq")
    no_audit = await org.version(2, trigger="environment_cascade", env_index=1)
    conflicting = await org.version(3, trigger="environment_cascade", env_index=1)
    await org.audit(conflicting, root, org.env_versions[1].id)
    await org.audit(conflicting, no_audit, org.env_versions[1].id)
    no_source = await org.version(4, trigger="environment_cascade", env_index=1)
    await org.audit(no_source, None, org.env_versions[1].id)
    missing_ancestor = await org.version(5, trigger="environment_cascade", env_index=1)
    await org.audit(missing_ancestor, 999_999, org.env_versions[1].id)
    out_of_order = await org.version(6, trigger="environment_cascade", env_index=1)
    later = await org.version(7, trigger="user", env_index=1)
    await org.audit(out_of_order, later, org.env_versions[1].id)
    wrong_env = await org.version(8, trigger="environment_cascade", env_index=2)
    await org.audit(wrong_env, root, org.env_versions[1].id)

    findings = _by_version(await _assess(session))

    assert findings[no_audit].outcome == "unresolved"
    assert findings[no_audit].reasons == ["missing_audit"]
    assert findings[conflicting].reasons == ["conflicting_audit"]
    assert findings[no_source].reasons == ["malformed_audit"]
    assert findings[missing_ancestor].reasons == ["missing_ancestor"]
    assert findings[out_of_order].reasons == ["version_order"]
    assert findings[wrong_env].reasons == ["audit_environment_mismatch"]
    for vid in (no_audit, conflicting, no_source, missing_ancestor, out_of_order, wrong_env):
        assert findings[vid].root_version_id is None
        assert findings[vid].lost_fields == []


@pytest.mark.asyncio
async def test_cycles_and_depth_exhaustion_are_unresolved(session, org):
    a = await org.version(1, trigger="environment_cascade")
    b = await org.version(2, trigger="environment_cascade", env_index=1)
    await org.audit(a, b, org.env_versions[0].id)
    await org.audit(b, a, org.env_versions[1].id)

    root = await org.version(3, trigger="user", env_index=1)
    chain = [root]
    for n in range(4, 8):
        vid = await org.version(n, trigger="environment_cascade", env_index=1)
        await org.audit(vid, chain[-1], org.env_versions[1].id)
        chain.append(vid)

    findings = _by_version(await _assess(session, max_depth=2))

    # Version ordering catches a two-version cycle first; a cycle the ordering check
    # cannot see is still reported and never loops.
    assert findings[a].outcome == "unresolved"
    assert findings[b].outcome == "unresolved"
    assert findings[chain[1]].root_version_id == root
    assert findings[chain[2]].root_version_id == root
    assert findings[chain[2]].outcome == "unaffected"
    assert findings[chain[3]].reasons == ["depth_exceeded"]
    assert findings[chain[4]].reasons == ["depth_exceeded"]


@pytest.mark.asyncio
async def test_cycle_with_consistent_ordering_is_reported(session, org):
    a = await org.version(1, trigger="environment_cascade")
    await org.audit(a, a, org.env_versions[0].id)

    (finding,) = (await _assess(session)).findings

    assert finding.reasons == ["cycle"]


@pytest.mark.asyncio
async def test_never_follows_provenance_into_another_organization(session, org):
    foreign = await _make_org(session, "Foreign")
    foreign_root = await foreign.version(1, trigger="user", qc_template="scrnaseq")
    # A cascade in the home pipeline whose audit names a foreign version.
    cascade = await org.version(1, trigger="environment_cascade", env_index=1)
    await org.audit(cascade, foreign_root, org.env_versions[1].id)
    # A home-pipeline ancestor pinned to a foreign environment.
    pinned = await org.version(2, trigger="user", env_version_id=foreign.env_versions[0].id)
    after_pinned = await org.version(3, trigger="environment_cascade", env_index=1)
    await org.audit(after_pinned, pinned, org.env_versions[1].id)

    home = _by_version(await _assess(session, org_id=org.user.organization_id))

    assert home[cascade].reasons == ["pipeline_mismatch"]
    assert home[after_pinned].reasons == ["organization_mismatch"]
    assert foreign_root not in home


@pytest.mark.asyncio
async def test_organization_filter_limits_scope(session, org):
    other = await _make_org(session, "Other")
    home_root = await org.version(1, trigger="user")
    home_cascade = await org.version(2, trigger="environment_cascade", env_index=1)
    await org.audit(home_cascade, home_root, org.env_versions[1].id)
    other_root = await other.version(1, trigger="user")
    other_cascade = await other.version(2, trigger="environment_cascade", env_index=1)
    await other.audit(other_cascade, other_root, other.env_versions[1].id)

    home = await _assess(session, org_id=org.user.organization_id)
    everything = await _assess(session)

    assert [f.version_id for f in home.findings] == [home_cascade]
    assert {f.version_id for f in everything.findings} == {home_cascade, other_cascade}


@pytest.mark.asyncio
async def test_limit_and_continuation_cover_every_candidate_once(session, org):
    root = await org.version(1, trigger="user")
    cascades = []
    for n in range(2, 7):
        vid = await org.version(n, trigger="environment_cascade", env_index=1)
        await org.audit(vid, root, org.env_versions[1].id)
        cascades.append(vid)

    seen: list[int] = []
    after_id = 0
    pages = 0
    while True:
        page = await _assess(session, after_id=after_id, limit=2)
        pages += 1
        assert len(page.findings) <= 2
        seen.extend(f.version_id for f in page.findings)
        if page.next_after_id is None:
            break
        after_id = page.next_after_id

    assert seen == cascades
    assert pages == 3


@pytest.mark.asyncio
async def test_runs_per_version_are_bounded_and_truncation_is_reported(session, org):
    root = await org.version(1, trigger="user", qc_template="scrnaseq")
    cascade = await org.version(2, trigger="environment_cascade", env_index=1)
    await org.audit(cascade, root, org.env_versions[1].id)
    run_ids = [await org.run(cascade) for _ in range(3)]

    (finding,) = (await _assess(session, max_runs=2)).findings

    assert [r.run_id for r in finding.runs] == run_ids[:2]
    assert finding.runs_truncated is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 0}, {"limit": 1001}, {"max_depth": 0}, {"max_depth": 101}, {"max_runs": 0}, {"max_runs": 1001}],
)
async def test_limits_are_validated(session, kwargs):
    with pytest.raises(ValueError):
        await assess_cascade_versions(session, **kwargs)


@pytest.mark.asyncio
async def test_report_names_ids_and_reasons_but_no_qc_contents(session, org):
    root = await org.version(
        1,
        trigger="user",
        qc_template="bulk_rnaseq",
        qc_config_json={"metrics": {"secret_metric_name": {"label": "Secret label"}}},
        variables=[("genome", "genome")],
    )
    cascade = await org.version(2, trigger="environment_cascade", env_index=1, variables=[("genome", None)])
    await org.audit(cascade, root, org.env_versions[1].id)
    run_id = await org.run(cascade)
    dashboard_id = await org.dashboard(run_id, {"metrics": {"secret_metric_name": {}}})
    orphan = await org.version(3, trigger="environment_cascade", env_index=1)

    report = format_report(await _assess(session))

    assert f"version {cascade}" in report
    assert f"run {run_id}" in report
    assert f"dashboard {dashboard_id}" in report
    assert "missing_audit" in report
    assert str(orphan) in report
    assert "secret_metric_name" not in report
    assert "Secret label" not in report
    assert "bulk_rnaseq" not in report


@pytest.mark.asyncio
async def test_assessment_writes_nothing(session, org):
    root = await org.version(1, trigger="user", qc_template="scrnaseq")
    cascade = await org.version(2, trigger="environment_cascade", env_index=1)
    await org.audit(cascade, root, org.env_versions[1].id)
    await org.dashboard(await org.run(cascade), None)
    await session.commit()

    async def counts():
        tables = (
            "custom_pipeline_versions",
            "custom_pipeline_variables",
            "audit_log",
            "qc_dashboards",
            "pipeline_runs",
        )
        return {t: (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() for t in tables}

    before = await counts()
    # _assess runs in a READ ONLY transaction, so a write would raise.
    await _assess(session)
    assert await counts() == before
    snapshot = (await session.execute(select(QCDashboard.qc_config_json))).scalar_one()
    assert snapshot is None
