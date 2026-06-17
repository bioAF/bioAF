"""AWS runs default iGenomes off so schema validation does not 403 on the public
ngi-igenomes bucket (signed IRSA reads are scoped to bioaf-*). GCP is unchanged.
"""

from app.adapters.compute.kubernetes import KubernetesComputeProvider


def _job():
    return {
        "pipeline_source": "https://github.com/nf-core/demo",
        "pipeline_version": "1.0.1",
        "parameters": {"outdir": "s3://bioaf-results/x"},
        "sample_sheet": "sample,fastq_1\nS1,s3://b/r1.fq\n",
    }


def test_igenomes_ignore_added_on_aws():
    cmd = KubernetesComputeProvider._build_nextflow_command(_job(), igenomes_ignore=True)[-1]
    assert "--igenomes_ignore true" in cmd


def test_igenomes_ignore_absent_on_gcp():
    cmd = KubernetesComputeProvider._build_nextflow_command(_job(), igenomes_ignore=False)[-1]
    assert "igenomes_ignore" not in cmd


def test_explicit_igenomes_param_is_not_duplicated():
    job = _job()
    job["parameters"]["igenomes_ignore"] = "false"
    cmd = KubernetesComputeProvider._build_nextflow_command(job, igenomes_ignore=True)[-1]
    # The operator's explicit value wins; the auto default does not double-emit.
    assert cmd.count("--igenomes_ignore") == 1
    assert "--igenomes_ignore false" in cmd


# --- nextflow.config: skip nf-schema validation of igenomes_base on AWS --------
# nf-schema's directory-path format check does a live S3 read of the public
# igenomes_base default, which IRSA-signed creds 403 on. ignoreParams skips it.


def test_config_ignores_igenomes_base_on_aws():
    config = KubernetesComputeProvider._build_nextflow_k8s_config(
        namespace="bioaf-pipelines",
        has_gcs_secret=False,
        gcs_work_dir="s3://bioaf-raw-x/nextflow-work",
        ignore_igenomes_base=True,
    )
    assert "validation.ignoreParams = ['igenomes_base']" in config


def test_config_does_not_ignore_igenomes_base_on_gcp():
    config = KubernetesComputeProvider._build_nextflow_k8s_config(
        namespace="bioaf-pipelines",
        has_gcs_secret=True,
        gcs_work_dir="gs://bioaf-raw-x/nextflow-work",
    )
    assert "ignoreParams" not in config
