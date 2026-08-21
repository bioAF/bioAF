"""Filling a file's sequencing identity from the bytes rather than the name.

Migration 119 gave a file typed columns for its flow cell and lane and filled
them from the FILENAME, which is all bioAF had: it enforces no naming standard,
so a name is a hint and a file renamed after ingest loses even that.

Decision 4 of 2026-08-19: extract on ingest, from the FASTQ header, which carries
these facts regardless of naming discipline. Flow cell and lane only, never the
barcode.

Three properties, all of them about not making anything worse:

**It never fails an ingest.** This is an optional enrichment. Storage bioAF
cannot reach, a file it cannot parse, an adapter that is not configured: each
leaves the columns as they were, and everything unknown collapses to one implicit
sequencing unit, which is the pre-merged-FASTQ case that must stay untouched.

**It is monotonic.** The header adds a fact or it adds nothing. A lane already
read from the filename is never cleared by a header that says nothing.

**It reads a prefix.** The answer is in the first record of a file that may be
hundreds of GB.
"""

import logging

from app.services import fastq_header

logger = logging.getLogger("bioaf.sequencing_enrichment")

# The file types whose first record answers this question. Reading a BAM as a
# FASTQ is a category error, and spending a storage round trip to find that out
# is worse than not asking.
READABLE_TYPES = ("fastq", "fq")


async def enrich_from_header(file, *, adapter=None) -> dict:
    """Read one FASTQ's header and fill what it says. Returns what it wrote.

    ``adapter`` is injectable so this can be exercised without storage; the
    default resolves the configured one through the BAL registry.
    """
    if (getattr(file, "file_type", "") or "").lower() not in READABLE_TYPES:
        return {}
    uri = getattr(file, "storage_uri", None)
    if not uri:
        return {}

    if adapter is None:
        try:
            from app.adapters.registry import get_storage_adapter

            adapter = get_storage_adapter()
        except Exception:
            # No adapter configured for this install, or none initialised in
            # this process. Not an error: the columns simply stay as they are.
            logger.debug("No storage adapter available, skipping FASTQ header read for %s", uri)
            return {}

    try:
        prefix = await adapter.read_prefix(uri, fastq_header.PREFIX_BYTES)
    except Exception as exc:
        # Deliberately broad. Every failure here means the same thing to the
        # caller (bioAF could not look), and an upload must never fail because
        # an optional enrichment could not run.
        logger.info("Could not read the FASTQ header of %s: %s", uri, exc)
        return {}

    read = fastq_header.parse(prefix)
    if not read:
        return {}

    for column, value in read.items():
        setattr(file, column, value)
    logger.info("Read sequencing identity from the header of %s: %s", uri, read)
    return read
