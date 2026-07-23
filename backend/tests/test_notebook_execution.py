"""G1 headless notebook executor (lit_validation Level-3, C2).

The executor launches a curated (builtin) template notebook headless, with parameters
injected into its `parameters`-tagged cell and pipeline-output File rows mounted as
inputs, tracked on a ComputeSession(session_type="headless"). poll_execution advances
pending/running -> completed/failed and registers outputs on success. The k8s adapter is
mocked; this pins the service contract, not the pod mechanics.
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.models import ServiceState, SessionInfo, SessionStatus, StoredObject, TerminationResult
from app.exceptions import ValidationError
from app.models.file import File
from app.models.template_notebook import TemplateNotebook
from app.services.notebook_execution_service import NotebookExecutionService
from app.services.quota_service import QuotaService
from app.services.template_notebook_service import TemplateNotebookService

# a minimal notebook with a parameters-tagged cell (the injection target) + a body cell
_NB = {
    "cells": [
        {"cell_type": "code", "metadata": {"tags": ["parameters"]}, "source": ['contrast = "PLACEHOLDER"\n']},
        {"cell_type": "code", "metadata": {}, "source": ["print('body')\n"]},
    ],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def _patch_common(monkeypatch):
    async def _ok_quota(*a, **k):
        return True, ""

    async def _content(org_id, template):
        return json.dumps(_NB)

    monkeypatch.setattr(QuotaService, "check_quota", _ok_quota)
    monkeypatch.setattr(TemplateNotebookService, "get_template_content", _content)


def _adapter(*, launch=None, status=None, terminate=None):
    a = MagicMock()
    a.launch_session = AsyncMock(
        return_value=launch
        or SessionInfo(
            session_id="s1",
            status=ServiceState.RUNNING,
            provider_details={"pod_name": "pod-1", "namespace": "bioaf-notebooks"},
        )
    )
    a.get_session_status = AsyncMock(return_value=status or SessionStatus(session_id="s1", status=ServiceState.RUNNING))
    a.terminate_session = AsyncMock(return_value=terminate or TerminationResult())
    return a


async def _template(session, org_id, is_builtin=True):
    nb = TemplateNotebook(
        organization_id=org_id,
        name="Bulk DE",
        description="DESeq2 differential expression",
        category="differential_expression",
        notebook_path="notebooks/de_bulk.ipynb",
        parameters_json={"contrast": "A_vs_B", "padj": 0.05},
        compatible_with="nf-core/rnaseq",
        sort_order=1,
        is_builtin=is_builtin,
    )
    session.add(nb)
    await session.flush()
    return nb


@pytest.mark.asyncio
async def test_execute_template_launches_headless_session(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id)
    adapter = _adapter()
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        cs = await NotebookExecutionService.execute_template(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            template_id=tmpl.id,
            parameters={"contrast": "A_vs_B"},
        )
    assert cs.session_type == "headless"
    assert cs.status == "running"
    assert cs.compute_job_ref == "pod-1"
    spec = adapter.launch_session.await_args.args[0]
    assert spec["session_type"] == "headless"
    assert "notebook_json" in spec


@pytest.mark.asyncio
async def test_execute_template_rejects_non_curated_template(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id, is_builtin=False)
    adapter = _adapter()
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        with pytest.raises(ValidationError):
            await NotebookExecutionService.execute_template(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                template_id=tmpl.id,
                parameters={},
            )
    adapter.launch_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_template_injects_parameters(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id)
    adapter = _adapter()
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        await NotebookExecutionService.execute_template(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            template_id=tmpl.id,
            parameters={"contrast": "KO_vs_WT", "padj": 0.01},
        )
    spec = adapter.launch_session.await_args.args[0]
    src = "".join(spec["notebook_json"]["cells"][0]["source"])
    assert 'contrast = "KO_vs_WT"' in src
    assert "padj = 0.01" in src


@pytest.mark.asyncio
async def test_execute_template_mounts_input_files(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id)
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/deg/counts.tsv",
        filename="counts.tsv",
        file_type="count_matrix",
    )
    session.add(f)
    await session.flush()
    adapter = _adapter()
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        await NotebookExecutionService.execute_template(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            template_id=tmpl.id,
            parameters={},
            input_file_ids=[f.id],
        )
    spec = adapter.launch_session.await_args.args[0]
    assert spec["input_files"][0]["gcs_uri"] == "gs://bucket/deg/counts.tsv"


@pytest.mark.asyncio
async def test_poll_execution_running_stays_running(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id)
    adapter = _adapter(status=SessionStatus(session_id="s1", status=ServiceState.RUNNING))
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        cs = await NotebookExecutionService.execute_template(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, template_id=tmpl.id, parameters={}
        )
        cs = await NotebookExecutionService.poll_execution(session, cs)
    assert cs.status == "running"


@pytest.mark.asyncio
async def test_poll_execution_error_marks_failed(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id)
    adapter = _adapter(
        status=SessionStatus(session_id="s1", status=ServiceState.ERROR, provider_details={"message": "boom"})
    )
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        cs = await NotebookExecutionService.execute_template(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, template_id=tmpl.id, parameters={}
        )
        cs = await NotebookExecutionService.poll_execution(session, cs)
    assert cs.status == "failed"


@pytest.mark.asyncio
async def test_poll_execution_success_registers_outputs_and_completes(session, admin_user, monkeypatch):
    _patch_common(monkeypatch)
    tmpl = await _template(session, admin_user.organization_id)
    term = TerminationResult(
        status=ServiceState.STOPPED,
        output_files=[
            StoredObject(filename="deg_results.csv", storage_uri="gs://bucket/x/deg_results.csv", size_bytes=10)
        ],
        output_prefix="gs://bucket/x",
    )
    adapter = _adapter(status=SessionStatus(session_id="s1", status=ServiceState.STOPPED), terminate=term)
    with patch("app.services.notebook_execution_service.get_notebook_adapter", return_value=adapter):
        cs = await NotebookExecutionService.execute_template(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, template_id=tmpl.id, parameters={}
        )
        cs = await NotebookExecutionService.poll_execution(session, cs)
    assert cs.status == "completed"
    assert cs.gcs_output_prefix == "gs://bucket/x"
    adapter.terminate_session.assert_awaited()
