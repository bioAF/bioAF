"""Asking for help when the alias list does not recognise a deposited table's columns.

`_pick` only knows the spellings someone enumerated. A real csaw deposit names its coordinate
columns `regions.seqnames` / `regions.start` / `regions.end`, so the whole table parsed to zero
entities and the study reported "could not locate chrom/start/end columns", which reads like the
paper deposited something unusable.

This is the same defect plan_6 fixed for CLAIMS, one layer over: a paper's arbitrary vocabulary
mapped onto ours by a lookup table. The fix is the same seam. The model is shown ONLY the header
row, never the table, and it answers with which column plays which role.
"""

import pytest

from app.services import column_resolution as cr

_CSAW_HEADER = [
    "",
    "regions.seqnames",
    "regions.start",
    "regions.end",
    "regions.width",
    "combined.PValue",
    "combined.FDR",
    "combined.rep.logFC",
    "best.FDR",
    "best.rep.logFC",
]


def _response(**roles):
    import json

    return (
        "```json\n"
        + json.dumps({"columns": roles, "reason": "csaw prefixes its columns", "confidence": 0.96})
        + "\n```"
    )


class TestThePrompt:
    def test_it_shows_the_header_and_never_asks_for_the_table(self):
        system, payload = cr.build_column_prompt(_CSAW_HEADER, kind="interval")
        assert "regions.seqnames" in payload
        assert "combined.rep.logFC" in payload
        # The roles it must fill, for an interval table.
        for role in ("chrom", "start", "end", "lfc", "padj"):
            assert role in system
        assert "row" not in payload.lower() or "header" in payload.lower()

    def test_a_gene_table_asks_for_the_id_column_instead_of_coordinates(self):
        system, _ = cr.build_column_prompt(["res.gene_symbol", "res.log2FoldChange"], kind="gene")
        assert "id" in system
        assert "chrom" not in system

    def test_it_says_to_prefer_the_primary_statistic(self):
        """The csaw table carries combined.* and best.* side by side and they are different tests."""
        system, _ = cr.build_column_prompt(_CSAW_HEADER, kind="interval")
        assert "adjusted" in system.lower()


class TestParsing:
    def test_it_reads_the_mapping(self):
        out = cr.parse_column_resolution(
            _response(
                chrom="regions.seqnames",
                start="regions.start",
                end="regions.end",
                lfc="combined.rep.logFC",
                padj="combined.FDR",
            ),
            header=_CSAW_HEADER,
        )
        assert out["columns"]["chrom"] == "regions.seqnames"
        assert out["columns"]["padj"] == "combined.FDR"
        assert out["confidence"] == 0.96
        assert out["reason"]

    def test_a_column_the_header_does_not_have_is_dropped(self):
        """The model must not be able to invent a column: the map would then silently blank a table."""
        out = cr.parse_column_resolution(
            _response(chrom="regions.seqnames", start="invented_column"), header=_CSAW_HEADER
        )
        assert out["columns"] == {"chrom": "regions.seqnames"}
        assert "invented_column" in out["reason"]

    def test_junk_yields_no_mapping_rather_than_raising(self):
        assert cr.parse_column_resolution("no json", header=_CSAW_HEADER)["columns"] == {}
        assert cr.parse_column_resolution("```json\nnope\n```", header=_CSAW_HEADER)["columns"] == {}


class TestTheCall:
    @pytest.mark.asyncio
    async def test_it_returns_the_mapping(self):
        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return _response(
                    chrom="regions.seqnames",
                    start="regions.start",
                    end="regions.end",
                    lfc="combined.rep.logFC",
                    padj="combined.FDR",
                )

        out = await cr.resolve_columns(_CSAW_HEADER, kind="interval", client=_C(), model="m", api_key=None)
        assert out["columns"]["chrom"] == "regions.seqnames"
        assert out["model"] == "m"

    @pytest.mark.asyncio
    async def test_an_empty_header_makes_no_call(self):
        class _Boom:
            async def submit(self, **kwargs):
                raise AssertionError("must not ask about a table with no header")

        assert await cr.resolve_columns([], kind="interval", client=_Boom(), model="m", api_key=None) is None

    @pytest.mark.asyncio
    async def test_a_provider_failure_is_not_an_error(self):
        """Asking for help is best-effort. A failure leaves the table exactly as unparsed as it was."""

        class _C:
            async def submit(self, **kwargs):
                raise RuntimeError("provider down")

        assert await cr.resolve_columns(_CSAW_HEADER, kind="interval", client=_C(), model="m", api_key=None) is None


# ---- plan_7 step 7: sample metadata is a third kind of table with wrong headers ----


def test_sample_metadata_is_a_resolvable_kind():
    """ "Other times the metadata has it, but the headers may be incorrect" is the stated case, so a
    deposited metadata table reaches the same seam a result table does."""
    from app.services.column_resolution import ROLES

    assert "sample_metadata" in ROLES
    assert set(ROLES["sample_metadata"]) == {"sample_id", "condition", "replicate", "batch"}


def test_the_metadata_prompt_describes_each_role():
    system, payload = cr.build_column_prompt(["Sample", "Group", "Rep"], kind="sample_metadata")
    assert "sample_id" in system
    assert "condition" in system
    for h in ("Sample", "Group", "Rep"):
        assert h in payload


def test_a_metadata_resolution_still_refuses_an_invented_column():
    out = cr.parse_column_resolution(
        '```json\n{"columns": {"sample_id": "Sample", "condition": "NotThere"}, "confidence": 0.9}\n```',
        header=["Sample", "Group"],
    )
    assert out["columns"] == {"sample_id": "Sample"}
