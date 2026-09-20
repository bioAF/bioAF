"""plan_8_5 section 3.4: the deposit's own sample records, as the rubric's S obligations read them.

A series matrix states, per sample, what the depositor said it is: its accession, its title, its
organism, the material it came from and the characteristics that name its group, its replicate and
its donor. bioAF held none of that. It read one line of the matrix for a species check and threw
the rest away, so every other sample obligation had nothing to compare the paper against.

These records are evidence, not a judgment: the parser preserves what the deposit says and where it
says it, and the assessment decides what that establishes.
"""

from app.services.literature.accession_manifest_service import parse_sample_records

_MATRIX = "\n".join(
    [
        '!Series_title\t"SAMD1 loss in human cells"',
        '!Sample_title\t"WT rep1"\t"WT rep2"\t"KO rep1"\t"KO rep2"',
        '!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"\t"GSM4"',
        '!Sample_organism_ch1\t"Homo sapiens"\t"Homo sapiens"\t"Homo sapiens"\t"Homo sapiens"',
        '!Sample_source_name_ch1\t"HepG2 cells"\t"HepG2 cells"\t"HepG2 cells"\t"HepG2 cells"',
        '!Sample_characteristics_ch1\t"genotype: WT"\t"genotype: WT"\t"genotype: SAMD1 KO"\t"genotype: SAMD1 KO"',
        '!Sample_characteristics_ch1\t"replicate: 1"\t"replicate: 2"\t"replicate: 1"\t"replicate: 2"',
        '!Sample_molecule_ch1\t"total RNA"\t"total RNA"\t"total RNA"\t"total RNA"',
        '!Sample_library_strategy\t"RNA-Seq"\t"RNA-Seq"\t"RNA-Seq"\t"RNA-Seq"',
        "",
    ]
)


class TestWhatTheDepositSaysAboutEachSample:
    def test_every_sample_column_becomes_one_record_under_its_own_accession(self):
        records = parse_sample_records(_MATRIX)
        assert [r["accession"] for r in records] == ["GSM1", "GSM2", "GSM3", "GSM4"]
        assert records[0]["title"] == "WT rep1"

    def test_each_record_keeps_the_organism_the_depositor_declared(self):
        assert {r["organism"] for r in parse_sample_records(_MATRIX)} == {"Homo sapiens"}

    def test_each_record_keeps_the_material_it_came_from(self):
        assert parse_sample_records(_MATRIX)[0]["source_name"] == "HepG2 cells"
        assert parse_sample_records(_MATRIX)[0]["molecule"] == "total RNA"

    def test_characteristics_are_kept_as_the_depositor_keyed_them(self):
        first = parse_sample_records(_MATRIX)[0]
        assert first["characteristics"] == {"genotype": "WT", "replicate": "1"}
        assert parse_sample_records(_MATRIX)[2]["characteristics"]["genotype"] == "SAMD1 KO"

    def test_a_characteristic_without_a_key_is_kept_verbatim_rather_than_guessed_at(self):
        matrix = '!Sample_geo_accession\t"GSM1"\n!Sample_characteristics_ch1\t"primary fibroblast"\n'
        assert parse_sample_records(matrix)[0]["unkeyed"] == ["primary fibroblast"]

    def test_the_assay_the_deposit_declares_rides_with_the_record(self):
        assert parse_sample_records(_MATRIX)[0]["library_strategy"] == "RNA-Seq"

    def test_a_matrix_with_no_sample_lines_yields_no_records(self):
        assert parse_sample_records('!Series_title\t"nothing"\n') == []

    def test_a_column_the_matrix_omits_a_value_for_is_empty_rather_than_absent(self):
        matrix = '!Sample_geo_accession\t"GSM1"\t"GSM2"\n!Sample_organism_ch1\t"Homo sapiens"\n'
        records = parse_sample_records(matrix)
        assert len(records) == 2
        assert records[1]["organism"] == ""
