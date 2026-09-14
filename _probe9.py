#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf import (
    extract_page_layout,
    reconstruct_layout,
    analyze_pdf,
    PDFType,
)
from kindle_converter.pdf.analyzer import (
    _analyze_all_pages,
    _open_document,
    build_analysis,
)

def scenario3_pdf(path):
    doc = pymupdf.open()
    doc.set_metadata({"title": "Mixed Layout Book"})
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 200), "Part One", fontname="hebo", fontsize=18)
    p1.insert_text((72, 280), "L1 left text block one", fontname="helv", fontsize=12)
    p1.insert_text((330, 280), "R1 right text block one", fontname="helv", fontsize=12)
    p1.insert_text((72, 340), "L2 left text block two", fontname="helv", fontsize=12)
    p1.insert_text((330, 340), "R2 right text block two", fontname="helv", fontsize=12)
    p1.insert_text(
        (72, 440),
        "A full width note between the column bands here",
        fontname="helv",
        fontsize=12,
    )
    p1.insert_text((72, 500), "L3 left text block three", fontname="helv", fontsize=12)
    p1.insert_text((330, 500), "R3 right text block three", fontname="helv", fontsize=12)
    p1.insert_text((72, 800), "Mixed Layout Book", fontname="helv", fontsize=9)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 200), "Part Two", fontname="hebo", fontsize=18)
    p2.insert_text((72, 280), "L4 left text block four", fontname="helv", fontsize=12)
    p2.insert_text((330, 280), "R4 right text block four", fontname="helv", fontsize=12)
    p2.insert_text((72, 340), "L5 left text block five", fontname="helv", fontsize=12)
    p2.insert_text((330, 340), "R5 right text block five", fontname="helv", fontsize=12)
    p2.insert_text((72, 800), "Mixed Layout Book", fontname="helv", fontsize=9)
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text((72, 200), "Part Three", fontname="hebo", fontsize=18)
    p3.insert_text((72, 280), "L6 left text block six", fontname="helv", fontsize=12)
    p3.insert_text((330, 280), "R6 right text block six", fontname="helv", fontsize=12)
    p3.insert_text((72, 340), "L7 left text block seven", fontname="helv", fontsize=12)
    p3.insert_text((330, 340), "R7 right text block seven", fontname="helv", fontsize=12)
    p3.insert_text((72, 800), "Mixed Layout Book", fontname="helv", fontsize=9)
    doc.save(str(path))
    doc.close()
    return path

tmp = os.path.join(os.path.dirname(__file__) or ".", "sc3.pdf")
pdf = scenario3_pdf(tmp)
a = analyze_pdf(pdf)
print("type", a.document_type, "density", a.text_density, "pages", a.page_count, "text_pages", a.text_page_count)
doc2 = _open_document(pdf)
rows, valid = _analyze_all_pages(doc2)
for r in rows:
    print("page", r.page_number, "has_meaningful_text", r.has_meaningful_text, "char_count", r.char_count)
doc2.close()
os.remove(tmp)
