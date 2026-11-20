"""Read a GEO export package the way a submitter would: workbook rows, checksums, reports, README."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO

from openpyxl import load_workbook

from app.services.geo.excel_generator import PLACEHOLDER


def _split(cell: str | None) -> list[str]:
    """A comma-joined reference cell; the required-field placeholder means no references."""
    if cell == PLACEHOLDER:
        return []
    return [p.strip() for p in (cell or "").split(",") if p.strip()]


@dataclass
class GeoPackage:
    samples: dict[str, dict[str, list[str]]]
    """Per Sample_name row: its raw_file and processed_data_file references."""
    supplementary: list[str]
    """SERIES Series_supplementary_file storage URIs."""
    checksums: list[str]
    """Filenames listed in md5_checksums.txt (in order, duplicates kept)."""
    manifest: list[str]
    """Filenames in validation_report.json's file manifest (in order, duplicates kept)."""
    readme: list[str]
    """Filenames listed as data files in README.txt (in order, duplicates kept)."""


def read_package(zip_bytes: bytes) -> GeoPackage:
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    names = zf.namelist()
    wb = load_workbook(BytesIO(zf.read(next(n for n in names if n.endswith(".xlsx")))))

    ws = wb["SAMPLES"]
    header = [c.value for c in ws[1]]
    samples = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = row[header.index("Sample_name")]
        if name:
            samples[name] = {
                "raw": _split(row[header.index("raw_file")]),
                "processed": _split(row[header.index("processed_data_file")]),
            }

    series = wb["SERIES"]
    series_header = [c.value for c in series[1]]
    supplementary = _split(series.cell(row=2, column=series_header.index("Series_supplementary_file") + 1).value)

    checksums = [line.split("  ", 1)[1] for line in zf.read("md5_checksums.txt").decode().splitlines() if line]
    report = json.loads(zf.read("validation_report.json"))
    readme = [
        line.split(":", 1)[1].strip()
        for line in zf.read("README.txt").decode().splitlines()
        if line.startswith("  RAW: ") or line.startswith("  PROCESSED: ")
    ]
    return GeoPackage(
        samples=samples,
        supplementary=supplementary,
        checksums=checksums,
        manifest=[f["filename"] for f in report["file_manifest"]["files"]],
        readme=readme,
    )
