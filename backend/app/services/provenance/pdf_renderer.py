"""Renders a Markdown provenance report as PDF via weasyprint."""

from __future__ import annotations

from typing import Any

from app.services.provenance.markdown_renderer import MarkdownRenderer

_CSS = """\
@page {
    size: A4;
    margin: 2cm 1.5cm;
    @bottom-center {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 9px;
        color: #666;
    }
    @top-right {
        content: "bioAF Provenance Report";
        font-size: 9px;
        color: #666;
    }
}
body {
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 11px;
    line-height: 1.5;
    color: #222;
}
h1 {
    font-size: 22px;
    border-bottom: 2px solid #333;
    padding-bottom: 6px;
    page-break-after: avoid;
}
h2 {
    font-size: 16px;
    margin-top: 1.5em;
    border-bottom: 1px solid #aaa;
    padding-bottom: 4px;
    page-break-after: avoid;
}
h3 { font-size: 13px; page-break-after: avoid; }
h4 { font-size: 11px; page-break-after: avoid; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.5em 0 1em 0;
    font-size: 10px;
}
th, td {
    border: 1px solid #ccc;
    padding: 4px 8px;
    text-align: left;
}
th {
    background: #f0f0f0;
    font-weight: 600;
}
tr:nth-child(even) td { background: #fafafa; }
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 1em 0;
}
strong { font-weight: 600; }
code { font-family: "Courier New", monospace; font-size: 10px; }
"""


class PdfRenderer:
    @staticmethod
    def render(entity_type: str, json_report: dict[str, Any]) -> bytes:
        import markdown as md_lib
        import weasyprint

        md_text = MarkdownRenderer.render(entity_type, json_report)
        html_body = md_lib.markdown(md_text, extensions=["tables"])
        full_html = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>{html_body}</body>
</html>"""
        return weasyprint.HTML(string=full_html).write_pdf()
