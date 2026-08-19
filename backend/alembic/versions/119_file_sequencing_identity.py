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

The backfill reads the existing tags first, then falls back to the filename for
FASTQs that carry the Illumina convention. It fills holes only: a value a writer
has already stated is never overwritten, so re-running it is a no-op.
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
    # 1. Read type from the legacy `read:` tag.
    #
    # The CASE guard matters: tags_json is JSONB with an array default but
    # nothing constrains it to one, and jsonb_array_elements_text raises on an
    # object. A migration that dies on one odd row takes the whole deploy with
    # it.
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
    # 2. Lane from the legacy `lane:` tag, accepting BOTH spellings the two
    #    writers produced and landing on one integer. `0*[1-9][0-9]*` is what
    #    makes "1" and "001" the same lane while refusing "000": that was
    #    _get_lane's "I do not know" default, and read as a number it becomes
    #    lane 0, a lane no sequencer has.
    """
    UPDATE files
    SET lane = tagged.lane
    FROM (
        SELECT f.id,
               ((regexp_match(tag, '^lane:0*([1-9][0-9]*)$'))[1])::int AS lane
        FROM files f
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(f.tags_json) = 'array' THEN f.tags_json ELSE '[]'::jsonb END
        ) AS tag
        WHERE tag LIKE 'lane:%'
    ) AS tagged
    WHERE files.id = tagged.id
      AND tagged.lane IS NOT NULL
      AND files.lane IS NULL
    """,
    # 3. Whatever the tags did not answer, from the filename, for FASTQs only:
    #    the Illumina convention is a FASTQ convention, and a name is a hint
    #    rather than a source of truth, so a name that does not carry the
    #    convention leaves the columns NULL.
    """
    UPDATE files
    SET read_type = COALESCE(read_type, (regexp_match(filename, '_(R[12]|I[12])_'))[1]),
        lane = COALESCE(lane, ((regexp_match(filename, '_L0*([1-9][0-9]*)_'))[1])::int)
    WHERE (read_type IS NULL OR lane IS NULL)
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
