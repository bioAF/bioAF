"""The parameters a catalog launch decides, tested directly from explicit inputs.

``PipelineRunService.resolve_parameters`` is the launch's parameter policy with no database, no
compute and no run: the pipeline's defaults, what the scientist stated, the reference genome, and
the samples go in, and the parameters the run will be given come out. What the scientist stated
always wins; everything else is a default.
"""

from types import SimpleNamespace

from app.services.pipeline_run_service import PipelineRunService


def _pipeline(key: str, defaults: dict | None = None, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        pipeline_key=key, default_params_json=defaults or {}, schema_json=None, source_type="nf-core", **extra
    )


def _sample(organism: str = "Homo sapiens") -> SimpleNamespace:
    return SimpleNamespace(organism=organism, prep_notes=None, custom_fields=[], files=[], _input_files=[])


def test_what_the_scientist_stated_overrides_the_pipelines_defaults():
    params = PipelineRunService.resolve_parameters(
        _pipeline("nf-core/rnaseq", {"aligner": "star_salmon", "skip_qc": False}), {"aligner": "hisat2"}, None, []
    )
    assert params["aligner"] == "hisat2"
    assert params["skip_qc"] is False


def test_peak_calling_gets_a_mappable_genome_size_from_its_genome():
    params = PipelineRunService.resolve_parameters(_pipeline("nf-core/chipseq"), {"genome": "GRCh38"}, None, [])
    assert params["macs_gsize"] == 2.7e9


def test_a_stated_read_length_suppresses_the_derived_genome_size():
    params = PipelineRunService.resolve_parameters(
        _pipeline("nf-core/atacseq"), {"genome": "GRCh38", "read_length": 50}, None, []
    )
    assert "macs_gsize" not in params


def test_smrnaseq_gets_mirbase_and_a_species_from_its_samples():
    params = PipelineRunService.resolve_parameters(
        _pipeline("nf-core/smrnaseq"), {}, None, [_sample("Mus musculus"), _sample("mus musculus")]
    )
    assert params["mirtrace_species"] == "mmu"
    assert params["three_prime_adapter"] == "auto-detect"
    assert params["mature"].startswith("http") and params["hairpin"].startswith("http")


def test_smrnaseq_with_mixed_organisms_names_no_species():
    params = PipelineRunService.resolve_parameters(
        _pipeline("nf-core/smrnaseq"), {}, None, [_sample("Homo sapiens"), _sample("Mus musculus")]
    )
    assert "mirtrace_species" not in params


def test_a_reference_of_our_own_switches_igenomes_off_unless_stated():
    stated = {"fasta": "gs://refs/a.fa", "gtf": "gs://refs/a.gtf"}
    params = PipelineRunService.resolve_parameters(_pipeline("nf-core/rnaseq"), stated, None, [])
    assert params["igenomes_ignore"] is True

    kept = PipelineRunService.resolve_parameters(
        _pipeline("nf-core/rnaseq"), {**stated, "igenomes_ignore": False}, None, []
    )
    assert kept["igenomes_ignore"] is False


def test_resolving_changes_none_of_its_inputs():
    defaults = {"aligner": "star"}
    stated = {"genome": "GRCh38"}
    pipeline = _pipeline("nf-core/chipseq", defaults)
    PipelineRunService.resolve_parameters(pipeline, stated, None, [])
    assert defaults == {"aligner": "star"}
    assert stated == {"genome": "GRCh38"}
