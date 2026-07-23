"""Level-3 headless differential-analysis templates (lit_validation, ADR-069).

The DE/DA templates are R notebooks (kernelspec 'ir') run by the headless executor. Their
parameters must be str/number only, because the parameters-cell injector emits Python literals
into the (R) cell, and None/True/False are not valid R.
"""

import json

from app.services.template_notebook_service import BUILTIN_TEMPLATES, TEMPLATES_DIR, TemplateNotebookService

_L3 = ["de_bulk_deseq2.ipynb", "da_peaks_deseq2.ipynb"]


def test_level3_template_files_exist_and_valid():
    for f in _L3:
        p = TEMPLATES_DIR / f
        assert p.exists(), f"missing template {p}"
        nb = json.loads(p.read_text())
        assert nb["metadata"]["kernelspec"]["name"] == "ir"
        assert any("parameters" in c.get("metadata", {}).get("tags", []) for c in nb["cells"])


def test_level3_templates_registered_builtin():
    paths = {t["notebook_path"] for t in BUILTIN_TEMPLATES}
    assert "notebooks/de_bulk_deseq2.ipynb" in paths
    assert "notebooks/da_peaks_deseq2.ipynb" in paths


def test_level3_params_inject_to_valid_r():
    for tmpl in BUILTIN_TEMPLATES:
        if not tmpl["notebook_path"].endswith(tuple(_L3)):
            continue
        nb = {"cells": [{"cell_type": "code", "metadata": {"tags": ["parameters"]}, "source": ["x = 1\n"]}]}
        injected = TemplateNotebookService._inject_parameters(nb, tmpl["parameters"])
        src = "".join(injected["cells"][0]["source"])
        # Python-literal injection must not emit R-invalid tokens for these templates.
        assert "None" not in src and "True" not in src and "False" not in src
        assert "padj_threshold = 0.05" in src
