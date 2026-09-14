"""Diagnose the full pipeline including deduplicate_layout."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import pymupdf
from kindle_converter.pdf import (
    extract_page_layout,
    reconstruct_layout,
)
from kindle_converter.pdf.reconstruction import deduplicate_layout

BODY_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen and the weather was cold across the country side here."
)

# Scenario 1: Full pipeline
doc = pymupdf.open()
doc.set_metadata({"title": "Test"})
for _ in range(3):
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        pymupdf.Rect(72, 200, 500, 760),
        f"{BODY_LINE}\n\n{BODY_LINE}",
        fontname="helv",
        fontsize=12,
    )
    page.insert_text((72, 810), "Single Column Book", fontname="helv", fontsize=9)
tmp = os.path.join(tempfile.gettempdir(), "diag_full.pdf")
doc.save(tmp)
doc.close()

layout = extract_page_layout(tmp)
print(f"Before deduplicate: type={type(layout).__name__}, block_count={layout.block_count}")

deduped = deduplicate_layout(layout)
print(f"After deduplicate: type={type(deduped).__name__ if deduped else 'None'}")
if deduped is not None:
    print(f"  block_count={deduped.block_count}")
    print(f"  pages={len(deduped.pages) if deduped.pages else 'None'}")
else:
    print("  deduplicate_layout returned None!")

# Check if the original layout was mutated
print(f"\nOriginal layout after deduplicate: type={type(layout).__name__}, block_count={layout.block_count}")
