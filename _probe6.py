#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf.analyzer import (
    _text_lines,
    _non_ws_count,
    _all_lines_in_bands,
    _analyze_all_pages,
    _open_document,
    build_analysis,
    classify,
)

doc = pymupdf.open()
doc.set_metadata({"title": "t"})
for _ in range(2):
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 200), "Wide block A spanning most of the page width", fontname="helv", fontsize=12)
    p.insert_text((110, 280), "Wide block B also spanning most of the page", fontname="helv", fontsize=12)
    p.insert_text((60, 360), "Wide block C again spanning most of the page", fontname="helv", fontsize=12)
    p.insert_text((72, 800), "Ambiguous Book", fontname="helv", fontsize=9)
path = "z.pdf"
doc.save(path)
doc.close()

d = pymupdf.open(path)
for i in range(2):
    pg = d.load_page(i)
    lines = _text_lines(pg)
    print(i, [(t[:30], _non_ws_count(t), _all_lines_in_bands(pg, lines)) for t, _ in lines])
doc2 = _open_document(path)
rows, valid = _analyze_all_pages(doc2)
analysis = build_analysis(rows)
print("type", analysis.document_type, "density", analysis.text_density, "pages", analysis.page_count, "text_pages", analysis.text_page_count)
doc2.close()
d.close()
os.remove(path)
