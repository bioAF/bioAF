"""A sheet for a pipeline that publishes no contract at all.

Seventeen pipelines in the catalog publish no ``schema_input.json``. For those,
``generate_generic_sheet`` emitted a fixed ``sample,fastq_1,fastq_2`` header and
ignored everything a scientist stated: the same request with and without values
produced a byte-identical sheet. Nothing they could do reached the file.

Decision 1 of 2026-08-19: the scientist declares the columns themselves, in the
same ``{"fields": [{name, type, required}]}`` shape the experiment field editor
already uses, and each column gains the one thing an intake field does not have,
a BINDING that says where its value comes from:

    read          the row's mate-1 or mate-2 FASTQ
    sample_field  a field on the Sample
    file_type     one of the sample's files, by type
    custom_field  one of the sample's custom fields, by name
    literal       a constant, the same in every row
    (none)        ask the scientist, per sample, in the entry grid

Declaring nothing keeps today's behaviour exactly. A pipeline with no contract
and no declaration still gets ``sample,fastq_1,fastq_2``, because "no schema"
means "we do not know" and never a refusal.

These assert the emitted sheet and the launch decision.
"""

from unittest.mock import MagicMock

import pytest

from app.exceptions import DomainError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_declaration import DEFAULT_DECLARATION, parse_declaration


def _make_file(filename: str, file_type: str = "fastq", read_type: str | None = None):
    f = MagicMock()
    f.filename = filename
    f.storage_uri = f"gs://bucket/{filename}"
    f.file_type = file_type
    f.tags_json = []
    for column in ("lane", "flowcell_id", "index_sequence", "source_run_accession"):
        setattr(f, column, None)
    f.read_type = read_type
    return f


def _custom(name: str, value: str):
    field = MagicMock()
    field.field_name = name
    field.field_value = value
    return field


def _make_sample(sample_id: int, external_id: str, files=None, customs=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex", "assay"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    sample.custom_fields = list(customs or [])
    return sample


def _reads(name: str):
    return [
        _make_file(f"{name}_R1_001.fastq.gz", read_type="R1"),
        _make_file(f"{name}_R2_001.fastq.gz", read_type="R2"),
    ]


def _field(name: str, source: str | None = None, key: str = "", required: bool = False, type_: str = "string"):
    field: dict = {"name": name, "type": type_, "required": required}
    if source:
        field["binding"] = {"source": source, "key": key}
    return field


def _declaration(*fields: dict) -> dict:
    return {"fields": list(fields)}


def _rows(csv_text: str) -> list[list[str]]:
    return [line.split(",") for line in csv_text.strip().splitlines()]


class TestTheDeclarationIsTheSheet:
    def test_the_header_is_the_declared_columns_in_the_declared_order(self):
        """The scientist's own order, not bioAF's. For a pipeline with no
        contract there is nothing else to go on, and reordering a sheet somebody
        wrote by hand would be bioAF overruling the only source of truth."""
        contract = parse_declaration(
            _declaration(
                _field("id", "sample_field", "external_id", required=True),
                _field("R1", "read", "1", required=True, type_="file"),
                _field("R2", "read", "2", type_="file"),
                _field("condition", required=True),
            )
        )
        sample = _make_sample(1, "SAMPLE_A", files=_reads("SAMPLE_A"))

        csv_text = SampleSheetService.generate_from_contract(contract, [sample], {}, {"1": {"condition": "treated"}})

        assert _rows(csv_text)[0] == ["id", "R1", "R2", "condition"]

    def test_each_binding_source_resolves(self):
        contract = parse_declaration(
            _declaration(
                _field("id", "sample_field", "external_id", required=True),
                _field("R1", "read", "1", type_="file"),
                _field("assembly", "file_type", "fasta", type_="file"),
                _field("batch", "custom_field", "batch_code"),
                _field("protocol", "literal", "10XV3"),
            )
        )
        sample = _make_sample(
            1,
            "SAMPLE_A",
            files=_reads("SAMPLE_A") + [_make_file("SAMPLE_A.fasta", file_type="fasta")],
            customs=[_custom("batch_code", "B7")],
        )

        row = _rows(SampleSheetService.generate_from_contract(contract, [sample], {}))[1]

        assert row == [
            "SAMPLE_A",
            "gs://bucket/SAMPLE_A_R1_001.fastq.gz",
            "gs://bucket/SAMPLE_A.fasta",
            "B7",
            "10XV3",
        ]

    def test_an_unbound_column_is_filled_from_the_grid(self):
        """No binding means "ask me", which is how a design column (a
        co-assembly grouping, a differential contrast) gets stated."""
        contract = parse_declaration(
            _declaration(_field("id", "sample_field", "external_id"), _field("group", required=True))
        )
        samples = [_make_sample(1, "A", files=_reads("A")), _make_sample(2, "B", files=_reads("B"))]

        csv_text = SampleSheetService.generate_from_contract(
            contract, samples, {}, {"1": {"group": "gut"}, "2": {"group": "skin"}}
        )

        assert [r[1] for r in _rows(csv_text)[1:]] == ["gut", "skin"]

    def test_a_stated_value_outranks_a_binding(self):
        """The scientist correcting a cell in the review step is the backstop
        against a binding that resolved to the wrong thing."""
        contract = parse_declaration(_declaration(_field("id", "sample_field", "external_id")))
        sample = _make_sample(1, "SAMPLE_A")

        csv_text = SampleSheetService.generate_from_contract(contract, [sample], {}, {"1": {"id": "OTHER"}})

        assert _rows(csv_text)[1] == ["OTHER"]

    def test_one_row_per_sequencing_unit(self):
        """A declared sheet is still a sheet: a sample sequenced twice gets two
        rows, exactly as a schema-driven one does."""
        contract = parse_declaration(
            _declaration(_field("id", "sample_field", "external_id"), _field("R1", "read", "1", type_="file"))
        )
        files = _reads("A_S1_L001") + _reads("A_S1_L002")
        for f in files:
            f.lane = 1 if "L001" in f.filename else 2
        sample = _make_sample(1, "A", files=files)

        csv_text = SampleSheetService.generate_from_contract(contract, [sample], {})

        assert len(_rows(csv_text)) == 3


class TestABindingNeverGuesses:
    def test_two_files_of_the_bound_type_block_rather_than_pick(self):
        """The project's governing rule, at the one place a declared sheet could
        break it: choosing the wrong BAM is a wrong mapping, and a wrong mapping
        is worse than a missing one."""
        contract = parse_declaration(_declaration(_field("alignment", "file_type", "bam", required=True, type_="file")))
        sample = _make_sample(
            1,
            "A",
            files=[_make_file("first.bam", file_type="bam"), _make_file("second.bam", file_type="bam")],
        )

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {})

        assert raised.value.details["missing_columns"]["alignment"]["reason"] == "ambiguous"

    def test_a_required_column_nothing_can_fill_blocks_and_names_the_samples(self):
        contract = parse_declaration(_declaration(_field("case_id", required=True)))
        samples = [_make_sample(1, "A", files=_reads("A")), _make_sample(2, "B", files=_reads("B"))]

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, samples, {}, {"1": {"case_id": "C1"}})

        gap = raised.value.details["missing_columns"]["case_id"]
        assert [s["external_id"] for s in gap["samples"]] == ["B"]

    def test_the_grid_asks_for_exactly_what_the_block_names(self):
        contract = parse_declaration(_declaration(_field("case_id", required=True)))
        sample = _make_sample(1, "A", files=_reads("A"))

        specs = SampleSheetService.per_sample_inputs(contract, [sample], {}, {})

        assert [spec["name"] for spec in specs] == ["case_id"]

    def test_an_optional_column_that_resolves_to_nothing_is_emitted_empty(self):
        """Emitted, not dropped. bioAF knows nothing about this pipeline, so
        removing a header the scientist declared would be bioAF deciding the
        shape of a sheet it does not understand."""
        contract = parse_declaration(
            _declaration(_field("id", "sample_field", "external_id"), _field("notes", "custom_field", "absent"))
        )
        sample = _make_sample(1, "A")

        csv_text = SampleSheetService.generate_from_contract(contract, [sample], {})

        assert _rows(csv_text)[0] == ["id", "notes"]
        assert _rows(csv_text)[1] == ["A", ""]


class TestTheDeclarationIsRefusedWhenItIsNotOne:
    def test_no_fields_is_not_a_declaration(self):
        assert parse_declaration({"fields": []}).is_empty
        assert parse_declaration(None).is_empty
        assert parse_declaration({}).is_empty

    def test_a_field_with_no_name_is_dropped(self):
        contract = parse_declaration(_declaration({"type": "string"}, _field("keep")))
        assert contract.columns == {"keep"}

    def test_an_unknown_binding_source_is_refused(self):
        """A source bioAF does not implement must not silently become "ask the
        scientist": the column would look answerable and never resolve."""
        with pytest.raises(ValueError):
            parse_declaration(_declaration(_field("x", "sql_query", "select 1")))

    def test_a_sample_field_outside_the_allowlist_is_refused(self):
        """Reflection onto any attribute would let a binding read a password
        hash or an internal id. The allowlist is the same discipline
        _COLUMN_TO_SAMPLE_FIELD applies."""
        with pytest.raises(ValueError):
            parse_declaration(_declaration(_field("x", "sample_field", "organization_id")))

    def test_a_duplicate_column_is_refused(self):
        with pytest.raises(ValueError):
            parse_declaration(_declaration(_field("dup"), _field("dup")))


class TestTheDefaultDeclarationReproducesTodaysSheet:
    """What the editor opens with. A scientist who declares nothing, and one who
    opens the editor and saves it untouched, must get the same file."""

    def test_it_is_the_generic_header(self):
        contract = parse_declaration(DEFAULT_DECLARATION)
        assert list(contract.column_order) == ["sample", "fastq_1", "fastq_2"]

    def test_it_emits_what_the_generic_generator_emits(self):
        sample = _make_sample(1, "SAMPLE_A", files=_reads("SAMPLE_A"))
        contract = parse_declaration(DEFAULT_DECLARATION)

        declared = SampleSheetService.generate_from_contract(contract, [sample], {})
        generic = SampleSheetService.generate_generic_sheet([sample], {})

        assert declared == generic
