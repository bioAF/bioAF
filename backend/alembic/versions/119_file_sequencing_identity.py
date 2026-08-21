"""Sequencing identity on File: lane, read type, flow cell, index, source run.

Revision ID: 119
Revises: 118
Create Date: 2026-08-18

bioAF's model is sample -> files with nothing in between, so a sample sequenced
over several lanes had nowhere to record which unit a file came from. Three
services improvised around that: upload_service wrote `lane:`/`read:` strings
into tags_json, sample_sheet_service re-parsed the same convention out of
filenames, and fetchngs_ingest_service fabricated lane numbers so a sample's
sibling runs stayed on separate sheet rows.

Untyped strings in a JSONB array are why two spellings of one lane could coexist:
upload_service stored int("001") so its tag read `lane:1`, fetchngs stored
f"{n:03d}" so its tag read `lane:001`, and both were dict keys when the sheet
builder paired mates. One physical lane became two units, and a sample whose
mates arrived by different ingest paths emitted two rows each carrying one mate
and an empty partner. So this is a correctness change, not tidying.

All five columns are additive and nullable, and NULL means "not known" rather
than a sentinel: a lab receiving pre-merged FASTQs from a CRO, or pulling from a
public archive, has no lane at all and must be wholly unaffected.

`source_run_accession` is separate from `lane` deliberately. A fetched FASTQ's
archive run accession tells sequencing units apart the way a lane does, but it is
not a lane, and writing it into the lane column promoted a fiction into a typed
axis.

`flowcell_id` and `index_sequence` are declared but populated by nothing yet.
The read-group axis is (flowcell, lane), because L001 on two flow cells is two
different lanes, so the column has to exist for that grouping to be sound. Both
values live in the FASTQ header rather than in the filename, and whether bioAF
reads that header is an open decision for the owner.

The backfill reads the existing tags for the read type and the FILENAME for the
lane, and the asymmetry is deliberate. Both writers spelled the read tag the same
way, so it is trustworthy; only one of them ever meant a lane. See statement 2:
the rule was measured against the demo's own rows rather than reasoned about, and
reading the lane tag would have written a lane number onto 37 files that were
never sequenced in one.

It fills holes only: a value a writer has already stated is never overwritten, so
re-running it is a no-op.
"""

import sqlalchemy as sa
from alembic import op

revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


# Kept as module-level text so the tests can execute the statements this
# migration will actually run. The suite builds its tables from Base.metadata and
# never runs the chain, so a backfill asserted anywhere else would be a claim
# about SQL nobody had executed.
BACKFILL_STATEMENTS: tuple[str, ...] = (
    # 1. Read type from the legacy `read:` tag. Both writers spelled this one the
    #    same way, so the tag is trustworthy.
    #
    #    The CASE guard matters: tags_json is JSONB with an array default but
    #    nothing constrains it to one, and jsonb_array_elements_text raises on an
    #    object. A migration that dies on one odd row takes the whole deploy with
    #    it.
    """
    UPDATE files
    SET read_type = tagged.read_type
    FROM (
        SELECT f.id,
               (regexp_match(tag, '^read:(R[12]|I[12])$'))[1] AS read_type
        FROM files f
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(f.tags_json) = 'array' THEN f.tags_json ELSE '[]'::jsonb END
        ) AS tag
        WHERE tag LIKE 'read:%'
    ) AS tagged
    WHERE files.id = tagged.id
      AND tagged.read_type IS NOT NULL
      AND files.read_type IS NULL
    """,
    # 2. Lane from the FILENAME, and deliberately never from the `lane:` tag.
    #
    #    Two writers produced lane tags and only one of them meant a lane.
    #    upload_service tagged a real lane, and only ever when the Illumina
    #    filename matched, so a genuine lane tag ALWAYS co-occurs with `_LNNN_`
    #    in the name. fetchngs fabricated a per-source-run ordinal and wrote it
    #    under the same key, on files whose names carry no lane at all.
    #
    #    Measured on the demo before writing this: 41 files carry a lane tag, 37
    #    of them fetchngs fabrications with no `_LNNN_` filename, and ZERO files
    #    have a real lane tag without one. So reading the filename alone loses
    #    nothing real and admits no fiction, which reading the tag would: it
    #    would have written lane = 1..21 onto 37 files that were never sequenced
    #    in those lanes.
    """
    UPDATE files
    SET lane = ((regexp_match(filename, '_L0*([1-9][0-9]*)_'))[1])::int
    WHERE lane IS NULL
      AND filename ~ '_L0*[1-9][0-9]*_'
      AND (
        filename LIKE '%.fastq.gz' OR filename LIKE '%.fq.gz'
        OR filename LIKE '%.fastq' OR filename LIKE '%.fq'
      )
    """,
    # 3. Read type from the filename for anything the tags did not answer. The
    #    Illumina convention is a FASTQ convention, and a name is a hint rather
    #    than a source of truth, so a name that does not carry it leaves the
    #    column NULL.
    """
    UPDATE files
    SET read_type = (regexp_match(filename, '_(R[12]|I[12])_'))[1]
    WHERE read_type IS NULL
      AND filename ~ '_(R[12]|I[12])_'
      AND (
        filename LIKE '%.fastq.gz' OR filename LIKE '%.fq.gz'
        OR filename LIKE '%.fastq' OR filename LIKE '%.fq'
      )
    """,
    # 4. The source run accession, recovered from the name of a fetched FASTQ.
    #
    #    This is what those 37 files were really distinguished by, and it is the
    #    value the fabricated lane stood in for. Without it they would all
    #    collapse into one implicit sequencing unit, and a sample holding two
    #    sibling runs would emit one row instead of two, dropping a file with no
    #    error. Every one of the 37 carries a parseable accession
    #    (SRX25642458_SRR30176122_1.fastq.gz -> SRR30176122), so none is lost.
    """
    UPDATE files
    SET source_run_accession = (regexp_match(filename, '(?:^|_)([SED]RR[0-9]+)'))[1]
    WHERE source_run_accession IS NULL
      AND filename ~ '(^|_)[SED]RR[0-9]+'
      AND (
        filename LIKE '%.fastq.gz' OR filename LIKE '%.fq.gz'
        OR filename LIKE '%.fastq' OR filename LIKE '%.fq'
      )
    """,
)


def upgrade() -> None:
    op.add_column("files", sa.Column("lane", sa.Integer(), nullable=True))
    op.add_column("files", sa.Column("read_type", sa.String(length=10), nullable=True))
    op.add_column("files", sa.Column("flowcell_id", sa.String(length=64), nullable=True))
    op.add_column("files", sa.Column("index_sequence", sa.String(length=64), nullable=True))
    op.add_column("files", sa.Column("source_run_accession", sa.String(length=64), nullable=True))

    conn = op.get_bind()
    for statement in BACKFILL_STATEMENTS:
        conn.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_column("files", "source_run_accession")
    op.drop_column("files", "index_sequence")
    op.drop_column("files", "flowcell_id")
    op.drop_column("files", "read_type")
    op.drop_column("files", "lane")
