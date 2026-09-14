#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf.analyzer import (
    _text_lines,
    _non_ws_count,
    _all_lines_in_bands,
    MEANINGFUL_TEXT_CHAR_THRESHOLD,
    HEADER_FOOTER_BAND_FRACTION,
)

doc = pymupdf.open()
doc.set_metadata({"title": "t"})
p = doc.new_page(width=595, height=842)
p.insert_textbox(
    pymupdf.Rect(72, 200, 500, 700),
    "Body for page one here in the document.",
    fontname="helv",
    fontsize=12,
)
path = "z.pdf"
doc.save(path)
doc.close()

d = pymupdf.open(path)
pg = d.load_page(0)
lines = _text_lines(pg)
print("lines", lines)
print("longest", max((_non_ws_count(t) for t, _ in lines), default=0))
print("all_in_bands", _all_lines_in_bands(pg, lines))
print("threshold", MEANINGFUL_TEXT_CHAR_THRESHOLD, "band", HEADER_FOOTER_BAND_FRACTION)
d.close()
os.remove(path)
