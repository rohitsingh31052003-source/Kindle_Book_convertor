#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf import (
    analyze_pdf,
    extract_page_layout,
    reconstruct_layout,
)

doc = pymupdf.open()
doc.set_metadata({"title": "t"})
p = doc.new_page(width=595, height=842)
p.insert_textbox(
    pymupdf.Rect(72, 100, 500, 700),
    "Body for page 1 here in the document.",
    fontname="helv",
    fontsize=12,
)
p2 = doc.new_page(width=595, height=842)
p2.insert_textbox(
    pymupdf.Rect(72, 100, 500, 700),
    "Body for page 2 here in the document.",
    fontname="helv",
    fontsize=12,
)
path = "tmp_api.pdf"
doc.save(path)
doc.close()

analysis = analyze_pdf(path)
print("type", analysis.document_type, "density", analysis.text_density, "pages", analysis.page_count)
layout = extract_page_layout(path)
print("layout pages", len(layout.pages))
rec = reconstruct_layout(layout)
print("rec pages", len(rec.pages))
for page in rec.pages:
    print("page", page.page_number, [e.text for e in page.elements])

os.remove(path)
