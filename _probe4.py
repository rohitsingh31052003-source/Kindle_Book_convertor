#!/usr/bin/env python3
"""Reproduce the public-api helper behavior outside of pytest."""
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf import analyze_pdf, extract_page_layout, PDFType

def make_text_pdf(path, pages=2):
    doc = pymupdf.open()
    doc.set_metadata({"title": "API Test Book"})
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"Body text for page {n + 1} here in the document.",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path

path = make_text_pdf("tmp_probe.pdf", pages=3)
a = analyze_pdf(path)
print("document_type", a.document_type, "density", a.text_density, "pages", a.page_count)
layout = extract_page_layout(path)
print("layout pages", len(layout.pages))
for page in layout.pages:
    print("  p", page.page_number, "blocks", len(page.blocks), "text_sample", page.blocks[0].text if page.blocks else None)
os.remove(path)
