#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf import extract_page_layout, reconstruct_layout

doc = pymupdf.open()
doc.set_metadata({"title": "t"})
for _ in range(2):
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 200), "Wide block A spanning most of the page width", fontname="helv", fontsize=12)
    p.insert_text((110, 280), "Wide block B also spanning most of the page", fontname="helv", fontsize=12)
    p.insert_text((60, 360), "Wide block C again spanning most of the page", fontname="helv", fontsize=12)
    p.insert_text((72, 800), "Ambiguous Book", fontname="helv", fontsize=9)
path = os.path.join(os.path.dirname(__file__) or ".", "z.pdf")
doc.save(path)
doc.close()

layout = extract_page_layout(path)
for pg in layout.pages:
    print("layout page", pg.page_number, [(b.text, b.bbox) for b in pg.blocks])
rec, _, _, _ = reconstruct_layout(layout)
for pg in rec.pages:
    print("rec page", pg.page_number, [e.text for e in pg.elements])

os.remove(path)
