"""What a samplesheet is built from: immutable snapshots of samples and their eligible files.

The sheet builder used to read loaded ORM samples and a transient ``_input_files``
list the launcher hung on each one. When that list was absent the builder
silently used every linked file instead, deleted files and earlier runs' outputs
included. These records replace both: the launcher decides which files are
eligible (organization, deletion, derived opt-in), and passes them in. There is
no default; an empty collection means no files.

Only the fields the sheet computation reads are carried. Each one, and who reads it:

==================== =========================================================
``id``, ``uuid``      row attribution, stated values, identity snapshot
``external_id``       sample name columns, ChIP-seq control detection, messages
``prep_notes``        ChIP-seq control and antibody detection (fetched titles)
``donor_source``,     automatic column fill (``_COLUMN_TO_SAMPLE_FIELD``) and
``organism``,         sample-field write-back decisions; ``organism`` and
``tissue_type``,      ``chemistry_version`` also drive parameter resolution
``sex``
the rest of           declared ``sample_field`` bindings
``BINDABLE_SAMPLE_FIELDS``
``custom_fields``     declared ``custom_field`` bindings and the column editor
``input_files``       reads, file columns, missing-file decisions, provenance
==================== =========================================================

The records are fully typed. ``from_file``, ``from_sample`` and ``require_sheet_samples`` take
dynamic ORM or caller objects (``Any``) and are the only conversion points.

Building one does no I/O. ``from_sample`` reads attributes the caller has
already loaded, and never the sample's own ``files`` relationship.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SheetFileInput:
    """One eligible input file, as the sheet sees it."""

    id: int | None
    filename: str
    storage_uri: str | None
    file_type: str | None
    # The legacy ``lane:``/``read:`` tags, read by the sequencing identity rule
    # after the typed columns. Named as the stored column is, so one rule reads both.
    tags_json: tuple[str, ...]
    lane: int | None
    read_type: str | None
    flowcell_id: str | None
    source_run_accession: str | None

    @classmethod
    def from_file(cls, f: Any) -> SheetFileInput:
        tags = getattr(f, "tags_json", None)
        return cls(
            id=getattr(f, "id", None),
            filename=getattr(f, "filename", None) or "",
            storage_uri=getattr(f, "storage_uri", None),
            file_type=getattr(f, "file_type", None),
            tags_json=tuple(t for t in tags if isinstance(t, str)) if isinstance(tags, (list, tuple)) else (),
            lane=getattr(f, "lane", None),
            read_type=getattr(f, "read_type", None),
            flowcell_id=getattr(f, "flowcell_id", None),
            source_run_accession=getattr(f, "source_run_accession", None),
        )


@dataclass(frozen=True)
class SheetCustomField:
    field_name: str
    field_value: str | None


@dataclass(frozen=True)
class SheetSampleInput:
    """One selected sample, with the files it may feed this run, in the order given."""

    id: int
    # Read only as identity text (str() of it); never narrowed here.
    uuid: object
    external_id: str | None
    prep_notes: str | None
    donor_source: str | None
    organism: str | None
    tissue_type: str | None
    sex: str | None
    treatment_condition: str | None
    chemistry_version: str | None
    molecule_type: str | None
    library_prep_method: str | None
    assay: str | None
    library_layout: str | None
    custom_fields: tuple[SheetCustomField, ...]
    input_files: tuple[SheetFileInput, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.input_files, tuple):
            raise TypeError("SheetSampleInput.input_files must be a tuple of SheetFileInput; pass () for none")

    @classmethod
    def from_sample(cls, sample: Any, eligible_files: Iterable[Any]) -> SheetSampleInput:
        """Snapshot a loaded sample with the files the caller decided it may use.

        ``eligible_files`` is required and has no default: ``None`` is refused, so
        a caller that forgot to decide cannot fall back to every linked file.
        """
        if eligible_files is None:
            raise TypeError("eligible_files is required; pass an empty collection for a sample with no files")
        return cls(
            id=sample.id,
            uuid=getattr(sample, "uuid", None),
            external_id=getattr(sample, "external_id", None),
            prep_notes=getattr(sample, "prep_notes", None),
            donor_source=getattr(sample, "donor_source", None),
            organism=getattr(sample, "organism", None),
            tissue_type=getattr(sample, "tissue_type", None),
            sex=getattr(sample, "sex", None),
            treatment_condition=getattr(sample, "treatment_condition", None),
            chemistry_version=getattr(sample, "chemistry_version", None),
            molecule_type=getattr(sample, "molecule_type", None),
            library_prep_method=getattr(sample, "library_prep_method", None),
            assay=getattr(sample, "assay", None),
            library_layout=getattr(sample, "library_layout", None),
            custom_fields=tuple(
                SheetCustomField(
                    field_name=getattr(c, "field_name", "") or "", field_value=getattr(c, "field_value", None)
                )
                for c in (getattr(sample, "custom_fields", None) or [])
            ),
            input_files=tuple(SheetFileInput.from_file(f) for f in eligible_files),
        )


def require_sheet_samples(samples: Iterable[Any]) -> list[SheetSampleInput]:
    """The samples, having checked each is a typed input. Anything else is a programming error."""
    checked = list(samples)
    for sample in checked:
        if not isinstance(sample, SheetSampleInput):
            raise TypeError(
                f"samplesheets are built from SheetSampleInput records, not {type(sample).__name__}; "
                "build one with SheetSampleInput.from_sample(sample, eligible_files)"
            )
    return checked
