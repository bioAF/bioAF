"""Parse one PDF in its own process: PDF bytes on stdin, extracted metadata as JSON on stdout.

PyMuPDF does not support multithreaded use, so the API process never parses an acquired copy
itself; fulltext_service.parse_pdf runs this module once per PDF and kills it on overrun."""

import json
import os
import sys

from app.services.literature.extraction import extract_pdf_metadata

if __name__ == "__main__":
    # PyMuPDF prints notices to stdout; only the result may go there.
    result_out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    json.dump(extract_pdf_metadata(sys.stdin.buffer.read()), result_out, default=str)
    result_out.close()
