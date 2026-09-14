#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "src")

import pymupdf
from kindle_converter.pdf import analyze_pdf

doc = pymupdf.open()
doc.set_metadata({"title": "t"})
p = doc.new_page(width=595, height=842)
p.insert_text((72, 200), "Wide block A spanning most of the page width", fontname="helv", fontsize=12)
p.insert_text((110, 280), "Wide block B also spanning most of the page", fontname="helv", fontsize=12)
p.insert_text((60, 360), "Wide block C again spanning most of the page", fontname="helv", fontsize=12)
p.insert_text((72, 800), "Ambiguous Book", fontname="helv", fontsize=9)
path = "z.pdf"
doc.save(path)
doc.close()

a = analyze_pdf(path)
print(a.document_type, a.text_density, a.page_count, a.text_page_count)
os.remove(path)
