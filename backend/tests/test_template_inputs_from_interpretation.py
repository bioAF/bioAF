"""plan_8_3 stage 4: what bioAF hands the analysis template selects the right columns of the real file.

Excluding study 50's two annotation columns from the sample mapping would still have sent `GeneType`,
a gene biotype, into the template as the feature identifier, because the template was told the
identifier by NAME and fell back to the matrix's first column whenever the name did not match. The
parameters now carry the identifier column bioAF established AND its index, and the template has no
first-column fallback left: an identifier it cannot resolve stops the run.

These tests read the parameters the product emits and apply them to the fixture file, so a parameter
set that selects a biotype or drops the counts fails here. Checking the inspector's dictionary alone
would not have caught it.
"""

import json
import pathlib

import pytest
import pytest_asyncio

from app.models.file import File
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.deposit_inspection import inspect_matrix
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_level3_service import resolve_level3_from_deposit

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "panahipour"
_TEMPLATES = pathlib.Path(__file__).parent.parent / "app" / "services" / "notebook_templates"


def _matrix() -> str:
    return (_FIXTURE / "raw_counts_excerpt.tsv").read_text(encoding="utf-8")


def _records() -> list[dict]:
    return json.loads((_FIXTURE / "sample_records.json").read_text(encoding="utf-8"))


def _test_arm() -> list[str]:
    return [r["title"] for r in _records() if "Mucoderm® + HA" in r["condition"]]


def _reference_arm() -> list[str]:
    return [r["title"] for r in _records() if r["condition"].endswith("treated with Mucoderm®")]


def _select(text: str, parameters: dict) -> tuple[list[str], list[list[int]]]:
    """The template's own selection, applied to the file: the identifier column bioAF named at the
    index bioAF named, and the named sample columns as integers."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split("\t")
    index = int(parameters["id_column_index"]) - 1
    assert header[index] == parameters["id_column"]
    wanted = [
        header.index(c) for c in (parameters["test_samples"].split(",") + parameters["reference_samples"].split(","))
    ]
    ids, counts = [], []
    for line in lines[1:]:
        cells = line.split("\t")
        ids.append(cells[index])
        counts.append([int(cells[i]) for i in wanted])
    return ids, counts


@pytest_asyncio.fixture
async def deposited_file(session, admin_user):
    f = File(
        organization_id=admin_user.organization_id,
        filename="raw_counts.tsv",
        storage_uri="s3://x/raw_counts.tsv",
        file_type="table",
        source_type="external_deposit",
        artifact_type="deposited_matrix",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    return f


@pytest_asyncio.fixture
async def deseq2_template(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="Differential Expression (DESeq2, headless)",
        category="differential_expression",
        notebook_path="notebooks/de_bulk_deseq2.ipynb",
        parameters_json={"id_column": "gene_id", "id_column_index": ""},
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


@pytest_asyncio.fixture
async def study(session, admin_user, deseq2_template, deposited_file):
    design = {
        "selected_contrast": {"contrast_index": 0, "decided_by": "only_contrast"},
        "contrasts": [
            {
                "name": "membrane with HA versus membrane",
                "test_condition": "membrane with HA",
                "reference_condition": "membrane",
                "test_samples": _test_arm(),
                "reference_samples": _reference_arm(),
            }
        ],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
    }
    s = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        state="reproducing",
        evidence_json={
            "route": "deposit",
            "sample_manifest": _records(),
            "deposit": {
                "files": [
                    {
                        "file_id": deposited_file.id,
                        "filename": "raw_counts.tsv",
                        "storage_uri": "s3://x/raw_counts.tsv",
                        "artifact_type": "deposited_matrix",
                    }
                ]
            },
            "deposit_inspection": inspect_matrix(_matrix(), sample_records=_records()),
        },
    )
    session.add(s)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, s, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design
    )
    plan.finding_claim_json = {
        "kind": "gene",
        "namespace": "ensembl_gene",
        "confirmed": True,
        "finding_set": {"kind": "gene", "namespace": "ensembl_gene", "entities": [], "n_sig": 0},
    }
    await session.flush()
    return s


class TestTheParametersBioafEmits:
    @pytest.mark.asyncio
    async def test_they_name_the_identifier_column_and_its_index(self, session, study, admin_user):
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        parameters = (await resolve_level3_from_deposit(session, study, plan)).inputs["parameters"]
        assert parameters["id_column"] == "ENSG"
        assert parameters["id_column_index"] == 3

    @pytest.mark.asyncio
    async def test_no_annotation_column_reaches_either_arm(self, session, study, admin_user):
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        parameters = (await resolve_level3_from_deposit(session, study, plan)).inputs["parameters"]
        arms = parameters["test_samples"].split(",") + parameters["reference_samples"].split(",")
        assert "GeneType" not in arms
        assert "GeneSymbol" not in arms
        assert "ENSG" not in arms
        assert len(arms) == 6

    @pytest.mark.asyncio
    async def test_applied_to_the_real_file_they_select_gene_ids_and_integer_counts(self, session, study, admin_user):
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        parameters = (await resolve_level3_from_deposit(session, study, plan)).inputs["parameters"]
        ids, counts = _select(_matrix(), parameters)
        assert all(i.startswith("ENSG") for i in ids)
        assert len(ids) == 12
        assert all(len(row) == 6 for row in counts)
        assert any(any(v > 0 for v in row) for row in counts)


class TestTheTemplateHasNoFirstColumnFallback:
    @pytest.mark.parametrize("notebook", ["de_bulk_deseq2.ipynb", "de_normalized_limma.ipynb"])
    def test_it_never_reads_the_first_column_because_a_name_did_not_match(self, notebook):
        source = "".join("".join(cell["source"]) for cell in json.loads((_TEMPLATES / notebook).read_text())["cells"])
        assert "colnames(mat)[1]" not in source

    @pytest.mark.parametrize("notebook", ["de_bulk_deseq2.ipynb", "de_normalized_limma.ipynb"])
    def test_it_resolves_the_identifier_by_the_index_bioaf_established(self, notebook):
        source = "".join("".join(cell["source"]) for cell in json.loads((_TEMPLATES / notebook).read_text())["cells"])
        assert "id_column_index" in source
        assert "stop(" in source

    @pytest.mark.parametrize("notebook", ["de_bulk_deseq2.ipynb", "de_normalized_limma.ipynb"])
    def test_its_parameters_cell_declares_the_index_so_the_injector_keeps_it(self, notebook):
        cells = json.loads((_TEMPLATES / notebook).read_text())["cells"]
        parameters = next(c for c in cells if "parameters" in (c.get("metadata") or {}).get("tags", []))
        assert any("id_column_index" in line for line in parameters["source"])

    def test_the_builtin_registration_declares_the_index_too(self):
        from app.services.template_notebook_service import BUILTIN_TEMPLATES

        for tmpl in BUILTIN_TEMPLATES:
            if "id_column" in (tmpl["parameters"] or {}):
                assert "id_column_index" in tmpl["parameters"], tmpl["notebook_path"]
