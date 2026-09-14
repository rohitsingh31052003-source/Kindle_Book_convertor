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
p2 = doc.new_page(width=595, height=842)
p2.insert_text((72, 200), "Part Two", fontname="hebo", fontsize=18)
p2.insert_text((72, 280), "L4 left text block four", fontname="helv", fontsize=12)
p2.insert_text((330, 280), "R4 right text block four", fontname="helv", fontsize=12)
p2.insert_text((72, 340), "L5 left text block five", fontname="helv", fontsize=12)
p2.insert_text((330, 340), "R5 right text block five", fontname="helv", fontsize=12)
p2.insert_text((72, 800), "Mixed Layout Book", fontname="helv", fontsize=9)
path = os.path.abspath("z.pdf")
doc.save(path)
doc.close()

d = pymupdf.open(path)
pg = d.load_page(0)
print("rect", pg.rect.height, pg.rect.width)
print("band fraction", HEADER_FOOTER_BAND_FRACTION, "top", pg.rect.height * HEADER_FOOTER_BAND_FRACTION, "bottom", pg.rect.height * (1 - HEADER_FOOTER_BAND_FRACTION))
lines = _text_lines(pg)
for t, _ in lines:
    print(repr(t), _non_ws_count(t), _all_lines_in_bands(pg, lines), "bbox", [round(x, 2) for x in _])
print("threshold", MEANINGFUL_TEXT_CHAR_THRESHOLD)
d.close()
os.remove(path)
