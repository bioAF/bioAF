from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService


def _make_sample(sample_id: int, external_id: str):
    """Create a mock sample object."""
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    return sample


def test_generate_scrnaseq_sheet():
    """Generate nf-core/scrnaseq sample sheet."""
    samples = [_make_sample(1, "SAMPLE_A"), _make_sample(2, "SAMPLE_B")]
    parameters = {
        "input_paths": {
            "1": ["/data/raw/SAMPLE_A_R1.fastq.gz", "/data/raw/SAMPLE_A_R2.fastq.gz"],
            "2": ["/data/raw/SAMPLE_B_R1.fastq.gz", "/data/raw/SAMPLE_B_R2.fastq.gz"],
        },
        "expected_cells": 5000,
    }
    result = SampleSheetService.generate_scrnaseq_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2,expected_cells"
    assert "SAMPLE_A" in lines[1]
    assert "/data/raw/SAMPLE_A_R1.fastq.gz" in lines[1]
    assert "5000" in lines[1]
    assert "SAMPLE_B" in lines[2]


def test_generate_rnaseq_sheet():
    """Generate nf-core/rnaseq sample sheet."""
    samples = [_make_sample(1, "RNA_1")]
    parameters = {
        "input_paths": {"1": ["/data/raw/RNA_1_R1.fastq.gz", "/data/raw/RNA_1_R2.fastq.gz"]},
        "strandedness": "reverse",
    }
    result = SampleSheetService.generate_rnaseq_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2,strandedness"
    assert "RNA_1" in lines[1]
    assert "reverse" in lines[1]


def test_generate_generic_sheet():
    """Generate generic sample sheet."""
    samples = [_make_sample(1, "GEN_1")]
    parameters = {"input_paths": {"1": ["/data/raw/GEN_1_R1.fastq.gz"]}}
    result = SampleSheetService.generate_generic_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2"
    assert "GEN_1" in lines[1]


def test_sheet_with_no_linked_files():
    """Handle samples with no linked files: empty paths."""
    samples = [_make_sample(1, "NO_FILES")]
    parameters = {}
    result = SampleSheetService.generate_scrnaseq_sheet(samples, parameters)

    lines = [line.strip() for line in result.strip().splitlines()]
    assert "NO_FILES" in lines[1]
    # Should have empty file paths
    parts = lines[1].split(",")
    assert parts[1] == ""  # fastq_1 empty
    assert parts[2] == ""  # fastq_2 empty


def test_sheet_with_manual_path_fallback():
    """Manual paths provided in parameters work as fallback."""
    samples = [_make_sample(42, "MANUAL")]
    parameters = {
        "input_paths": {"42": ["/manual/path/R1.fq.gz", "/manual/path/R2.fq.gz"]},
    }
    result = SampleSheetService.generate_scrnaseq_sheet(samples, parameters)
    assert "/manual/path/R1.fq.gz" in result


def test_generate_sheet_routes_correctly():
    """The generate_sheet method routes to correct generator."""
    samples = [_make_sample(1, "TEST")]
    parameters = {"input_paths": {"1": ["/data/R1.fq.gz"]}}

    scrnaseq_result = SampleSheetService.generate_sheet("nf-core/scrnaseq", samples, parameters)
    assert "expected_cells" in scrnaseq_result

    rnaseq_result = SampleSheetService.generate_sheet("nf-core/rnaseq", samples, parameters)
    assert "strandedness" in rnaseq_result

    generic_result = SampleSheetService.generate_sheet("custom-pipeline", samples, parameters)
    assert "sample,fastq_1,fastq_2" in generic_result


def test_sample_without_external_id():
    """Sample without external ID uses fallback naming."""
    sample = _make_sample(5, None)
    result = SampleSheetService.generate_scrnaseq_sheet([sample], {})
    assert "sample_5" in result


# ---- nf-core/chipseq sheet (lit_validation Phase 4) ----


def _chip_sample(sample_id: int, external_id: str, prep_notes: str = ""):
    """A sample with explicit external_id + prep_notes (MagicMock auto-vivifies prep_notes to a
    truthy mock otherwise, whose repr contains 'mock' and would false-trigger control detection)."""
    s = MagicMock()
    s.id = sample_id
    s.external_id = external_id
    s.prep_notes = prep_notes
    return s


def _rows_by_sample(csv_text: str) -> dict[str, list[str]]:
    lines = [line.strip() for line in csv_text.strip().splitlines()]
    return {line.split(",")[0]: line.split(",") for line in lines[1:]}


def test_chipseq_sheet_header_and_pairs_ip_with_input():
    chip = _chip_sample(1, "SRX_CHIP", "via fetchngs. strategy=ChIP-Seq title=MDA-MB-231, H3K4me3, ChIP")
    inp = _chip_sample(2, "SRX_INPUT", "via fetchngs. strategy=ChIP-Seq title=MDA-MB-231, Input")
    params = {
        "input_paths": {
            "1": ["/d/chip_R1.fastq.gz", "/d/chip_R2.fastq.gz"],
            "2": ["/d/input_R1.fastq.gz", "/d/input_R2.fastq.gz"],
        }
    }
    result = SampleSheetService.generate_chipseq_sheet([chip, inp], params)
    lines = [line.strip() for line in result.strip().splitlines()]
    assert lines[0] == "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate"

    rows = _rows_by_sample(result)
    # IP row: histone-mark antibody parsed from the title, control -> the input sample, replicate set.
    chip_row = rows["SRX_CHIP"]
    assert chip_row[4] == "H3K4me3"  # antibody
    assert chip_row[5] == "SRX_INPUT"  # control
    assert chip_row[6] == "1"  # control_replicate
    # Input row: empty antibody/control/control_replicate (it IS the control).
    input_row = rows["SRX_INPUT"]
    assert input_row[4] == "" and input_row[5] == "" and input_row[6] == ""


def test_chipseq_sheet_detects_igg_control():
    chip = _chip_sample(1, "SRX_TF", "strategy=ChIP-Seq title=K562, GATA1 ChIP")
    igg = _chip_sample(2, "SRX_IGG", "strategy=ChIP-Seq title=K562, IgG")
    params = {"input_paths": {"1": ["/d/tf_R1.fastq.gz", ""], "2": ["/d/igg_R1.fastq.gz", ""]}}
    rows = _rows_by_sample(SampleSheetService.generate_chipseq_sheet([chip, igg], params))
    # No histone mark in the title -> antibody falls back to the sanitized sample name; control is the IgG.
    assert rows["SRX_TF"][4] == "SRX_TF"
    assert rows["SRX_TF"][5] == "SRX_IGG"
    assert rows["SRX_IGG"][4] == "" and rows["SRX_IGG"][5] == ""


def test_chipseq_sheet_degrades_to_control_less_when_no_input():
    # No sample looks like a control -> IP samples are emitted WITHOUT antibody/control (still valid;
    # the run completes, those samples just aren't peak-called). Never a launch failure.
    a = _chip_sample(1, "SRX_A", "strategy=ChIP-Seq title=cellX, H3K27ac ChIP")
    b = _chip_sample(2, "SRX_B", "strategy=ChIP-Seq title=cellY, H3K27ac ChIP")
    params = {"input_paths": {"1": ["/d/a_R1.fastq.gz", ""], "2": ["/d/b_R1.fastq.gz", ""]}}
    rows = _rows_by_sample(SampleSheetService.generate_chipseq_sheet([a, b], params))
    for name in ("SRX_A", "SRX_B"):
        assert rows[name][4] == "" and rows[name][5] == "" and rows[name][6] == ""


def test_generate_sheet_routes_chipseq():
    chip = _chip_sample(1, "SRX_CHIP", "title=H3K4me3 ChIP")
    inp = _chip_sample(2, "SRX_INPUT", "title=Input")
    params = {"input_paths": {"1": ["/d/c_R1.fastq.gz", ""], "2": ["/d/i_R1.fastq.gz", ""]}}
    result = SampleSheetService.generate_sheet("nf-core/chipseq", [chip, inp], params)
    assert result.splitlines()[0].strip() == "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate"


# ---- nf-core/atacseq sheet (lit_validation Phase 4) ----


def test_atacseq_sheet_has_required_replicate_and_no_antibody():
    a = _chip_sample(1, "ATAC_A")
    b = _chip_sample(2, "ATAC_B")
    params = {"input_paths": {"1": ["/d/a_R1.fastq.gz", "/d/a_R2.fastq.gz"], "2": ["/d/b_R1.fastq.gz", ""]}}
    result = SampleSheetService.generate_atacseq_sheet([a, b], params)
    lines = [line.strip() for line in result.strip().splitlines()]
    # ATAC schema requires a replicate column; there is no antibody/control (no immunoprecipitation).
    assert lines[0] == "sample,fastq_1,fastq_2,replicate"
    rows = _rows_by_sample(result)
    assert rows["ATAC_A"] == ["ATAC_A", "/d/a_R1.fastq.gz", "/d/a_R2.fastq.gz", "1"]
    assert rows["ATAC_B"][3] == "1"  # replicate present for single-end too


def test_generate_sheet_routes_atacseq():
    a = _chip_sample(1, "ATAC_A")
    params = {"input_paths": {"1": ["/d/a_R1.fastq.gz", ""]}}
    result = SampleSheetService.generate_sheet("nf-core/atacseq", [a], params)
    assert result.splitlines()[0].strip() == "sample,fastq_1,fastq_2,replicate"


# ---- ChIP sheet defects found by staging a real differential-binding study ----


def test_chipseq_antibody_label_survives_an_underscore_after_the_mark():
    """`H3K27ac_ChIP` must yield `H3K27ac`.

    Real titles (GSE287761: `H3K27ac_DMSO4h`, `H3K4me3_DMSO4h`; GSE260807:
    `WT H3K27ac_ChIP_rep1`) put an underscore straight after the mark. `_` is a word character, so
    the trailing `\\b` never fired and the mark was missed, dropping every such sample to the
    per-sample fallback name.
    """
    a = _chip_sample(1, "SRX_A", "strategy=ChIP-Seq title=H3K27ac_DMSO4h")
    b = _chip_sample(2, "SRX_B", "strategy=ChIP-Seq title=H3K4me3_ChIP_rep1")
    c = _chip_sample(3, "SRX_C", "strategy=ChIP-Seq title=Input_DMSO4h")
    params = {"input_paths": {str(i): [f"/d/{i}_R1.fq.gz", ""] for i in (1, 2, 3)}}
    rows = _rows_by_sample(SampleSheetService.generate_chipseq_sheet([a, b, c], params))
    assert rows["SRX_A"][4] == "H3K27ac"
    assert rows["SRX_B"][4] == "H3K4me3"


def test_chipseq_pairs_each_ip_with_the_control_from_its_own_condition():
    """GSE287761 deposits one input PER CONDITION (`Input_DMSO4h`, `Input_dTAG4h`).

    Every IP used to point at `controls[0]`, so the dTAG IP had the DMSO input subtracted from it.
    That biases exactly the comparison a differential-binding study exists to make.
    """
    dmso = _chip_sample(1, "SRX_A", "strategy=ChIP-Seq title=H3K27ac_DMSO4h")
    dtag = _chip_sample(2, "SRX_B", "strategy=ChIP-Seq title=H3K27ac_dTAG4h")
    in_dmso = _chip_sample(3, "SRX_C", "strategy=ChIP-Seq title=RUN1_Input_DMSO4h")
    in_dtag = _chip_sample(4, "SRX_D", "strategy=ChIP-Seq title=RUN1_Input_dTAG4h")
    params = {"input_paths": {str(i): [f"/d/{i}_R1.fq.gz", ""] for i in (1, 2, 3, 4)}}
    rows = _rows_by_sample(SampleSheetService.generate_chipseq_sheet([dmso, dtag, in_dmso, in_dtag], params))

    assert rows["SRX_A"][5] == "SRX_C"  # DMSO IP -> DMSO input
    assert rows["SRX_B"][5] == "SRX_D"  # dTAG IP -> dTAG input
    # Both are still the same antibody group, which is what consensus peak calling needs.
    assert rows["SRX_A"][4] == rows["SRX_B"][4] == "H3K27ac"


def test_chipseq_single_pooled_control_still_serves_every_ip():
    """GSE260807 deposits ONE pooled input for all conditions, and that must keep working."""
    wt = _chip_sample(1, "SRX_A", "strategy=ChIP-Seq title=WT H3K27ac_ChIP_rep1")
    ko = _chip_sample(2, "SRX_B", "strategy=ChIP-Seq title=Irf2KO H3K27ac_ChIP_rep1")
    pooled = _chip_sample(3, "SRX_C", "strategy=ChIP-Seq title=WT Pooled_Input")
    params = {"input_paths": {str(i): [f"/d/{i}_R1.fq.gz", ""] for i in (1, 2, 3)}}
    rows = _rows_by_sample(SampleSheetService.generate_chipseq_sheet([wt, ko, pooled], params))
    assert rows["SRX_A"][5] == rows["SRX_B"][5] == "SRX_C"
    assert rows["SRX_A"][4] == rows["SRX_B"][4] == "H3K27ac"
