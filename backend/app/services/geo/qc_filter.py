"""Which Samples a GEO export selects by QC status.

A separate definition from ``Sample.qc_status`` (``QC_STATUSES``: pass, warning, fail; unset is null).
Exclusion omits only ``fail``: warning and unset QC are not failed QC.
"""

from enum import StrEnum

from app.exceptions import ValidationError


class GeoQcFilter(StrEnum):
    EXCLUDE_FAILED = "exclude_failed"
    INCLUDE_ALL = "include_all"


def parse_qc_filter(value: str) -> GeoQcFilter:
    """The filter for a non-HTTP caller; anything unsupported is refused before data is read."""
    try:
        return GeoQcFilter(value)
    except ValueError:
        allowed = ", ".join(f.value for f in GeoQcFilter)
        raise ValidationError(f"Unsupported qc_status_filter {value!r}; use one of: {allowed}") from None
