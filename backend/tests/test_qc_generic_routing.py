"""Routing: which template an unmapped pipeline type resolves to.

Before the generic engine existed, a catalog entry with no qc_template fell back
to `scrnaseq`, which meant a methylseq or sarek run got the single-cell
extractor, render config, and plot list applied to it. Now it resolves to
`generic`, which reads whatever MultiQC the run actually wrote.

The four tailored types must keep resolving exactly as they did.
"""

import pytest
import pytest_asyncio

from app.services.qc.templates import TEMPLATES, atacseq, bulk_rnaseq, chipseq, generic, get_template, scrnaseq


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="RoutingExp",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()
    return exp


async def _run_for(
    session,
    admin_user,
    experiment,
    pipeline_key: str,
    qc_template: str | None,
    *,
    in_catalog: bool = True,
):
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry
    from app.models.pipeline_run import PipelineRun

    org_id = admin_user.organization_id
    if in_catalog:
        session.add(
            PipelineCatalogEntry(
                organization_id=org_id,
                pipeline_key=pipeline_key,
                name=pipeline_key,
                source_type="nf-core",
                qc_template=qc_template,
            )
        )
        await session.flush()

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name=pipeline_key,
        status="completed",
        work_dir="/tmp/x",
    )
    session.add(run)
    await session.flush()
    return run


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_generic_is_a_registered_template():
    assert TEMPLATES["generic"] is generic


def test_generic_resolves_to_itself_not_scrnaseq():
    assert get_template("generic") is generic


def test_an_unrecognized_template_name_falls_back_to_generic():
    """A neutral MultiQC read beats a single-cell-shaped dashboard for a
    pipeline that is not single-cell."""
    assert get_template("something_nobody_registered") is generic
    assert get_template(None) is generic


def test_the_tailored_templates_still_resolve_to_themselves():
    assert get_template("scrnaseq") is scrnaseq
    assert get_template("bulk_rnaseq") is bulk_rnaseq
    assert get_template("chipseq") is chipseq
    assert get_template("atacseq") is atacseq


def test_registry_installs_default_unknown_pipelines_to_generic():
    from app.services.nf_core_registry_service import QC_TEMPLATE_MAP

    assert QC_TEMPLATE_MAP.get("methylseq", "generic") == "generic"
    assert "generic" in TEMPLATES


# --------------------------------------------------------------------------
# Resolution from a run
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_entry_without_a_qc_template_resolves_to_generic(session, admin_user, experiment):
    from app.services.qc.resolver import resolve_template_for_run

    run = await _run_for(session, admin_user, experiment, "nf-core/methylseq", None)

    name, config = await resolve_template_for_run(session, run)

    assert name == "generic"
    assert config["template"] == "generic"


@pytest.mark.asyncio
async def test_catalog_entry_marked_generic_resolves_to_generic(session, admin_user, experiment):
    from app.services.qc.resolver import resolve_template_for_run

    run = await _run_for(session, admin_user, experiment, "nf-core/sarek", "generic")

    name, _config = await resolve_template_for_run(session, run)

    assert name == "generic"


@pytest.mark.asyncio
async def test_a_run_with_no_catalog_entry_resolves_to_generic(session, admin_user, experiment):
    from app.services.qc.resolver import resolve_template_for_run

    run = await _run_for(session, admin_user, experiment, "nf-core/uninstalled", None, in_catalog=False)

    name, _config = await resolve_template_for_run(session, run)

    assert name == "generic"


@pytest.mark.asyncio
async def test_tailored_types_resolve_unchanged(session, admin_user, experiment):
    from app.services.qc.resolver import resolve_template_for_run

    for pipeline_key, template in (
        ("nf-core/scrnaseq", "scrnaseq"),
        ("nf-core/rnaseq", "bulk_rnaseq"),
        ("nf-core/chipseq", "chipseq"),
        ("nf-core/atacseq", "atacseq"),
    ):
        run = await _run_for(session, admin_user, experiment, pipeline_key, template)

        name, config = await resolve_template_for_run(session, run)

        assert name == template
        assert config["template"] == template


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_template_dispatches_to_the_generic_extractor(session, admin_user, experiment):
    """The whole point: an unmapped pipeline type reaches an extractor that can
    read its MultiQC, rather than yielding {}."""
    from unittest.mock import AsyncMock, patch

    from app.services.qc_dashboard_service import QCDashboardService

    run = await _run_for(session, admin_user, experiment, "nf-core/methylseq", None)

    with (
        patch.object(QCDashboardService, "_get_results_bucket", new=AsyncMock(return_value="bkt")),
        patch.object(generic, "extract", new=AsyncMock(return_value={"total_sequences": 42})) as generic_ex,
        patch.object(scrnaseq, "extract", new=AsyncMock(return_value={"cell_count": 1})) as scrna_ex,
    ):
        metrics = await QCDashboardService._extract_metrics(session, run, template_name="generic")

    assert metrics == {"total_sequences": 42}
    generic_ex.assert_awaited_once()
    scrna_ex.assert_not_called()


def test_generic_advertises_only_pipeline_agnostic_plots():
    """Advertising aligner-specific plots would promise images an arbitrary
    pipeline never produces."""
    plot_names = {name for name, _title, _type in generic.MULTIQC_PLOTS}

    assert not any("star" in name or "macs2" in name or "frip" in name for name in plot_names)
