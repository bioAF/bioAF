"""fetchngs accessions run-path (ai_pipeline_run point 1b).

Import-by-accession launches nf-core/fetchngs, which pulls its data from a list of database
accessions rather than per-sample FASTQ files. bioAF carries those accessions in
parameters["accessions"] (the assistant's launch_run folds them there). For an actual run, the
accessions must become fetchngs's --input ids file, and must NOT leak through as a bogus
--accessions nextflow flag (nf-core schema validation is strict). These tests pin both halves.
"""

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.services.sample_sheet_service import SampleSheetService


def test_generate_fetchngs_sheet_emits_ids_from_accessions():
    """fetchngs --input is an ids file: one accession per line, no CSV header."""
    result = SampleSheetService.generate_sheet("nf-core/fetchngs", [], {"accessions": ["SRR1", "GSE2"]})
    lines = [ln for ln in result.splitlines() if ln.strip()]
    assert lines == ["SRR1", "GSE2"]


def _fetchngs_job():
    return {
        "pipeline_source": "https://github.com/nf-core/fetchngs",
        "pipeline_version": "1.12.0",
        "parameters": {"outdir": "gs://bioaf-results/x", "accessions": ["SRR1", "SRR2"]},
        "sample_sheet": "SRR1\nSRR2\n",
    }


def test_accessions_not_passed_as_a_nextflow_param():
    cmd = KubernetesComputeProvider._build_nextflow_command(_fetchngs_job())[-1]
    # accessions are the bioAF-internal carrier for the ids file, not a real nextflow flag.
    assert "--accessions" not in cmd


def test_fetchngs_ids_file_wired_in_via_input():
    cmd = KubernetesComputeProvider._build_nextflow_command(_fetchngs_job())[-1]
    assert "--input /data/samplesheet.csv" in cmd
