"""GEO checksum manifest generator: MD5 manifest in GEO format."""

import logging

logger = logging.getLogger("bioaf.geo.checksum")


def generate_checksum_manifest(files_data: dict | None) -> tuple[str, list[str]]:
    """Generate MD5 checksum manifest in GEO format.

    Returns:
        Tuple of (manifest_text, missing_checksum_filenames).
        Format: "<md5>  <filename>" per line.
    """
    if not files_data:
        return "", []

    lines: list[str] = []
    missing: list[str] = []

    all_files = files_data.get("raw_files", []) + files_data.get("processed_files", [])

    for f in all_files:
        filename = f.get("filename", "unknown")
        checksum = f.get("md5_checksum")

        if checksum:
            lines.append(f"{checksum}  {filename}")
        else:
            missing.append(filename)

    return "\n".join(lines) + "\n" if lines else "", missing
