"""plan_8_2 section 1.1: the passages that cite a supplement are kept with it, verbatim, when the article is read.

A binding can be established by a verbatim passage linking a file to a contrast (owner decision 1). The
article's paragraphs are in hand only while its JATS is parsed, and the full text is never persisted, so
the paragraphs citing each supplement, and its legend, are recorded on the supplement's row then, with
the checksum of the document they came from.
"""

import hashlib
import pathlib

from app.services.supplement_inventory import merge_resource_identity, parse_jats_supplements

_JATS = (pathlib.Path(__file__).parent / "fixtures" / "groff" / "fulltext_jats.xml").read_text()
_S3 = "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt"


def _row(rows, filename):
    return next(r for r in rows if r.get("filename") == filename)


def test_the_paragraph_citing_a_supplement_is_kept_on_its_row():
    rows = parse_jats_supplements(_JATS)
    passages = _row(rows, _S3)["citing_passages"]
    texts = [p["text"] for p in passages]
    assert any("We include the results of this analysis as Supplemental File S3." in t for t in texts)
    assert any("between WEs with XX and XY karyotypes" in t for t in texts)
    assert {p["source"] for p in passages} == {"paper_text"}
    assert passages[0]["source_checksum"] == hashlib.sha256(_JATS.encode("utf-8")).hexdigest()


def test_a_paragraph_that_does_not_cite_the_supplement_is_not_kept():
    rows = parse_jats_supplements(_JATS)
    texts = [p["text"] for p in _row(rows, _S3)["citing_passages"]]
    assert not any("Sequencing reads were trimmed" in t for t in texts)


def test_a_supplements_legend_is_kept_as_a_legend():
    jats = (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><p>Nothing cites it here.</p>'
        '<supplementary-material id="S1"><label>Supplementary Table S1</label><caption><p>Genes differentially '
        'expressed between KO and WT cells.</p></caption><media xlink:href="table_s1.txt" mimetype="text" '
        'mime-subtype="plain"/></supplementary-material></body></article>'
    )
    rows = parse_jats_supplements(jats)
    passages = _row(rows, "table_s1.txt")["citing_passages"]
    assert passages == [
        {
            "text": "Genes differentially expressed between KO and WT cells.",
            "source": "legend",
            "source_checksum": hashlib.sha256(jats.encode("utf-8")).hexdigest(),
        }
    ]


def test_merging_one_files_rows_keeps_its_passages():
    rows = parse_jats_supplements(_JATS)
    resolved = [{**r, "resolved": True} for r in rows if r.get("filename") == _S3]
    merged = merge_resource_identity(resolved + [{**resolved[0], "citing_passages": []}])
    assert merged[0]["citing_passages"] == resolved[0]["citing_passages"]
