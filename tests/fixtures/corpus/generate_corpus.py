"""Deterministic generator for the M6.1 representative PDF corpus.

This module produces every corpus fixture PDF (and the corpus manifest) from
project-authored content using PyMuPDF. It is the **single source of truth**
for both artifacts:

* Each fixture is generated from a deterministic recipe (fixed page size,
  fixed coordinates, fixed fonts, fixed text) with **no timestamps**, no
  randomness, and no external files.
* A deterministic trailer ``/ID`` normalization step makes regeneration
  byte-for-byte identical for a given PyMuPDF edition (see
  :func:`_normalize_id`).
* :func:`fixture_metadata` exposes the machine-readable metadata that is
  serialized into ``manifest.json``, so the manifest can never drift from the
  generator.

The generator deliberately avoids importing ``kindle_converter`` or the
``tests`` package. It only needs ``pymupdf``.

Regenerating the corpus::

    # From the repository root (package layout)
    python -m tests.fixtures.corpus.generate_corpus

    # Regenerate a single fixture into a scratch directory
    python -m tests.fixtures.corpus.generate_corpus --only scanned_book --out <dir>

Determinism caveat (documented in the corpus README): content, layout,
classification, and structure are deterministic across PyMuPDF editions;
**byte**-identical output is guaranteed for the PyMuPDF edition the committed
fixtures were generated with (``garbage=4``, ``deflate=True``, pinned metadata,
normalized ``/ID``).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Callable

import pymupdf

# --------------------------------------------------------------------------- #
# Geometry constants (595 x 842 pt pages, matching the existing test modules)
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

#: Empirically measured for PyMuPDF ``insert_textbox``: a 10 pt font lays
#: lines ~14.05 pt apart, i.e. ~1.405 x fontsize. Used to size paragraph
#: boxes from wrap counts so body text is never clipped.
_LINE_HEIGHT_FACTOR = 1.405

DEFAULT_OUT_DIR = pathlib.Path(__file__).resolve().parent / "pdfs"
DEFAULT_MANIFEST_PATH = pathlib.Path(__file__).resolve().parent / "manifest.json"

_CORPUS_VERSION = "1.0.0"

#: Fixed document-ID pair written into every generated PDF trailer. A PDF's
#: ``/ID`` is normally a random nonce generated at save time; replacing it
#: with a deterministic constant is the only transformation needed to make
#: PyMuPDF output byte-for-byte reproducible.
_FIXED_PDF_ID = b"<4D36314B30B17D3A302097DEF0A82C31>"
_ID_PATTERN = re.compile(rb"/ID\s*\[(?:<[0-9A-Fa-f]*>|\([^)]*\)|\s){2}\]")


# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #


def _new_doc(*, title: str, author: str) -> pymupdf.Document:
    """Create a deterministic A4-ish document with pinned metadata."""
    doc = pymupdf.open()
    doc.set_metadata({"title": title, "author": author})
    return doc


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _save(doc: pymupdf.Document, path: pathlib.Path) -> pathlib.Path:
    """Save ``doc`` deterministically and normalize the trailer ``/ID``."""
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return _normalize_id(path)


def _normalize_id(path: pathlib.Path) -> pathlib.Path:
    """Replace the random PDF trailer ``/ID`` with a fixed deterministic one.

    Everything else PyMuPDF emits (object graph, streams, xref table, pinned
    metadata) is already deterministic for a given edition, so this single
    replacement makes regeneration byte-for-byte stable.
    """
    raw = path.read_bytes()
    replaced = _ID_PATTERN.subn(
        lambda _match: b"/ID[" + _FIXED_PDF_ID + _FIXED_PDF_ID + b"]", raw
    )
    if replaced[1] != 1:
        raise RuntimeError(
            f"expected exactly one PDF /ID trailer entry in {path}, "
            f"found {replaced[1]}"
        )
    path.write_bytes(replaced[0])
    return path


def _line(
    page: pymupdf.Page,
    text: str,
    x0: float,
    y: float,
    *,
    fontname: str = "helv",
    fontsize: float = 12.0,
) -> None:
    """Insert one text line at baseline ``(x0, y)``."""
    page.insert_text((x0, y), text, fontname=fontname, fontsize=fontsize)


def _wrap_count(
    text: str,
    *,
    width: float,
    fontname: str = "helv",
    fontsize: float = 12.0,
) -> int:
    """Return the number of wrapped lines ``text`` occupies in ``width``."""
    font = pymupdf.Font(fontname)
    lines = 1
    line_width = 0.0
    for word in text.split():
        word_width = font.text_length(word + " ", fontsize)
        if line_width + word_width <= width or line_width == 0:
            line_width += word_width
        else:
            lines += 1
            line_width = word_width
    return lines


def _textbox(
    page: pymupdf.Page,
    rect: tuple[float, float, float, float],
    text: str,
    *,
    fontname: str = "helv",
    fontsize: float = 12.0,
    align: int = 0,
) -> None:
    """Insert ``text`` into ``rect`` (deterministic PyMuPDF word wrapping)."""
    page.insert_textbox(
        pymupdf.Rect(*rect),
        text,
        fontname=fontname,
        fontsize=fontsize,
        align=align,
    )


def _raster_text_page(lines: tuple[str, ...], *, dpi: int = 120) -> pymupdf.Pixmap:
    """Rasterize ``lines`` of large text into a deterministic page image.

    Used for the scanned-book and image-only fixture pages, so the embedded
    image genuinely contains recognizable prose (useful for the later
    OCR-quality milestone) instead of being a solid block.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    y = 120.0
    for text in lines:
        page.insert_text((72, y), text, fontname="helv", fontsize=16)
        y += 44.0
    pix = page.get_pixmap(dpi=dpi)
    doc.close()
    return pix


def _insert_pixmap(
    page: pymupdf.Page,
    pix: pymupdf.Pixmap,
    rect: tuple[float, float, float, float],
) -> None:
    """Insert ``pix`` scaled into ``rect`` (image-area ratio preserved)."""
    page.insert_image(pymupdf.Rect(*rect), pixmap=pix)


def _solid_pixmap(
    width: int, height: int, color: tuple[int, int, int]
) -> pymupdf.Pixmap:
    """Create a deterministic solid-color pixmap."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pix.set_rect(pix.irect, color)
    return pix


# --------------------------------------------------------------------------- #
# Project-authored corpus content
# --------------------------------------------------------------------------- #

_NOVEL_TITLE = "The Lantern Keeper"
_NOVEL_AUTHOR = "Ada Grant"
_NOVEL_SUBTITLE = "A novel of the harbor"

_NOVEL_CHAPTER_1 = (
    "The wind had a habit of changing after sunset, and Elias knew it the "
    "way other men knew the turn of the tide. He climbed the iron stairs of "
    "the old tower with a lantern in one hand and a coil of rope over his "
    "shoulder, counting each step as he had counted it since boyhood.",
    "Below him the harbor lay quiet, its water dark and close, and the small "
    "houses along the shore appeared as a row of amber sparks. Somewhere a "
    "dog barked once and then fell silent, as if the night itself had asked "
    "for quiet.",
    "He had made this climb ten thousand times, more or less, and every time "
    "the tower gave the same answer: one room, one lamp, one wheel of glass, "
    "and the sea waiting below like a patient audience.",
    "The keeper's book lay open on the table by the window, its pages stiff "
    "with salt. He wrote the date, the weather, the number of ships sighted, "
    "and the hour when the light would next be turned.",
)

_NOVEL_CHAPTER_2 = (
    "Mira had grown up at the foot of the tower, and she knew the harbor "
    "better than anyone who merely sailed it. Her father kept the ledger of "
    "the tides, and she kept the stories that went with the names in it.",
    "On the morning of the storm she walked the breakwater in her yellow "
    "coat, testing the wind with her face, and every boathouse door she "
    "passed was already shut against the weather.",
    "Elias watched her from the gallery rail and thought of all the mornings "
    "he had watched her grow small, then large, then small again in the "
    "curve of the bay.",
)

_NOVEL_CHAPTER_3 = (
    "The north wind arrived before its clouds. It moved over the water in a "
    "cold flat line, flattening the swells and driving the gulls inland, and "
    "the whole town knew what it meant.",
    "They battened the boats and the shutters and the windows while the light "
    "of the tower turned against the darkening sky, and no one spoke of the "
    "sea, as if naming it would make it louder.",
    "When the lamp came round to the west, Elias saw a green hull riding low "
    "beyond the harbor mouth, and he knew the season had changed for good.",
    "He wrote the sighting in the ledger with a steady hand, and then he went "
    "down the stairs to wake the one person in the town who would know what "
    "to do with it.",
)

_TEXTBOOK_TITLE = "Introduction to Coastal Hydrography"
_TEXTBOOK_AUTHOR = "The Harbor Survey Office"

_TEXTBOOK_SECTIONS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "1. Introduction",
        "1.1 Background",
        (
            "Coastal hydrography records the shape and movement of the sea "
            "within sight of land. Soundings, shore features, currents, and "
            "tidal ranges are assembled into charts that describe a region "
            "of water as faithfully as a map describes a region of ground.",
            "The discipline has changed slowly and deliberately. Methods that "
            "produce an honest, repeatable measurement have outlived many "
            "that promised more than they could deliver at sea.",
        ),
    ),
    (
        "1. Introduction",
        "1.2 Scope of this work",
        (
            "This volume collects the observations made along a single "
            "stretch of coastline over one full year. The procedures are "
            "deliberately simple and repeatable, so every figure can be "
            "checked against the raw notes.",
            "No claim is made that the coast measured here represents every "
            "coast. It is a documented example, not a universal result.",
        ),
    ),
    (
        "2. Methods",
        "2.1 Instrumentation",
        (
            "Soundings were taken with a weighted line and recorded on paper "
            "in a fixed table. Positions were fixed by bearing to two "
            "landmarks whose distances from each other are known to the "
            "nearest meter.",
            "All observations share one working rule: a reading that cannot "
            "be repeated is not recorded as fact. Marginal measurements are "
            "kept in a separate column of the same table.",
        ),
    ),
    (
        "2. Methods",
        "2.2 Tidal corrections",
        (
            "Every sounding was reduced to a common datum using the local "
            "tide curve. Correcting for the stage of the tide removes the "
            "largest source of error in a shallow-water survey.",
            "The tide curve itself was rebuilt from the hourly observations "
            "of the previous twelve months, not borrowed from a distant "
            "port.",
        ),
    ),
    (
        "3. Results",
        "3.1 The inner channel",
        (
            "The inner channel deepens steadily from the outer bar to the "
            "mooring ground. Depths ranged between two and nine meters, with "
            "the steepest gradient found a hundred meters west of the beacon.",
            "The measured line agrees with the harbor master's log wherever "
            "the two records overlap, which gives confidence in both.",
        ),
    ),
    (
        "3. Results",
        "3.2 The northern shoal",
        (
            "The northern shoal proved shallower than the earlier survey "
            "suggested. The two-meter line lies three hundred meters further "
            "seaward than the chart of 1927 shows.",
            "The difference is too large to blame on either survey alone. The "
            "seabed here is mobile, and the shoal has shifted within living "
            "memory.",
        ),
    ),
    (
        "4. Discussion",
        "4.1 Comparison with older surveys",
        (
            "Earlier charts exaggerate the depth close to the harbor mouth. "
            "The discrepancy is consistent with a slow filling of the outer "
            "basin over the last eighty years.",
            "Comparisons with the archive are limited by the older surveys' "
            "coarser position fixes, which scatter by up to twenty meters.",
        ),
    ),
    (
        "4. Discussion",
        "4.2 Recommendations",
        (
            "The results argue for a revised chart and a sounding program "
            "repeated at the same season every year. A harbor changes, and a "
            "fixed survey is only true for the moment it was made.",
            "It is also recommended that the tide pole be moved to the outer "
            "quay, where it is visible from the full length of the channel.",
        ),
    ),
    (
        "5. Field practice",
        "5.1 Equipment list",
        (
            "The field kit is deliberately small: a graduated line, a "
            "stopwatch, two bearing compasses, a log book, and a water-"
            "tight case for the tide table. Nothing electric is required.",
            "The line is checked against a steel rule at the start of every "
            "week, because a stretched line is a silent error that appears in "
            "every sounding taken with it.",
        ),
    ),
    (
        "5. Field practice",
        "5.2 Routine",
        (
            "Soundings run from the first light to mid-morning, when wind "
            "and glare make the landmarks hard to read. The boat follows a "
            "set of transects marked by two poles on the shore.",
            "Each transect is sounded twice in opposite directions. If the "
            "two readings disagree by more than the table limit, the line is "
            "run a third time and the majority is kept.",
        ),
    ),
    (
        "6. Conclusion",
        "6.1 Limitations",
        (
            "This survey covers a single year and a single coast. The tide "
            "curve, though carefully rebuilt, rests on hourly observations "
            "from one station near the harbor mouth.",
            "Positions depend on two shore landmarks whose coordinates were "
            "accepted from the harbor plan without fresh triangulation.",
        ),
    ),
    (
        "6. Conclusion",
        "6.2 Closing remarks",
        (
            "The value of this work lies in its being repeatable. Any later "
            "survey party can recover the same lines, the same datum, and "
            "the same landmarks, and so compare like with like.",
            "A chart is a statement about one moment of the sea. The method "
            "is a way of keeping that statement honest, and that is the "
            "whole of the surveyor's craft.",
        ),
    ),
)

_GAZETTE_TITLE = "The Harbor Gazette"
_GAZETTE_COLUMNS = (
    # (left_column_text, right_column_text)
    (
        "L1 The harbor committee met on Tuesday and approved a new dredging schedule.",
        "R1 A lecture on coastal navigation begins at the town hall at seven.",
    ),
    (
        "L2 The schedule divides the season into three working windows of ten days each.",
        "R2 The speaker will review the charts that a spring survey produced.",
    ),
    (
        "L3 Mooring lines at the east quay will be replaced before the autumn gales.",
        "R3 Questions from the public are welcome, and the talk is free to all.",
    ),
    (
        "L4 The committee asked the surveyors to mark the outer bar with new buoys.",
        "R4 Refreshments are provided by the harbor guild after the discussion.",
    ),
)
_GAZETTE_FOOTNOTE = (
    "The Gazette welcomes letters on harbor matters before noon on Fridays."
)

_SCANNED_PAGES: tuple[tuple[str, ...], ...] = (
    (
        "Chapter Three",
        "The north wind arrived before its clouds. It moved over the water in a",
        "cold flat line, flattening the swells and driving the gulls inland, and",
        "the whole town knew what it meant.",
        "They battened the boats and the shutters and the windows while the light",
        "of the tower turned against the darkening sky, and no one spoke of the",
        "sea, as if naming it would make it louder.",
    ),
    (
        "Chapter Four",
        "When the lamp came round to the west, Elias saw a green hull riding low",
        "beyond the harbor mouth, and he knew the season had changed for good.",
        "He wrote the sighting in the ledger with a steady hand, and then he went",
        "down the stairs to wake the one person in the town who would know what",
        "to do with it.",
    ),
    (
        "Chapter Five",
        "The fishing fleet put out before dawn in a long ragged line, each boat",
        "following the one ahead until the harbor was only a pale notch behind",
        "them. The season that followed would be remembered for its northern",
        "winds and for the green hull that rode them home.",
    ),
    (
        "Chapter Six",
        "By midsummer the tower had a new coat of white paint and a new keeper's",
        "book with the old one's first line copied out at the top of the first",
        "page. Some things are worth keeping when they are handed over.",
    ),
    (
        "Chapter Seven",
        "Mira kept her own log at the foot of the tower, of comings and goings",
        "and of the weather as it arrived and passed. In time the book at the top",
        "of the stairs and the book at the bottom of the stairs began to tell one",
        "story together.",
    ),
)

_IMAGE_FIGURES: tuple[tuple[str, str, tuple[int, int, int]], ...] = (
    ("Figure 1: The survey boat at anchor off the outer bar.", "fig_one", (40, 90, 160)),
    ("Figure 2: Cross-section of the inner channel at the beacon line.", "fig_two", (160, 120, 40)),
    ("Figure 3: The tide curve recorded over a single spring day.", "fig_three", (60, 60, 160)),
)
_IMAGE_REPORT_TITLE = "Survey Notes at a Glance"

_UNUSUAL_BODY = (
    "The geometry of light on moving water is not an easy subject, and it "
    "deserves an honest introduction. Every reflection below was observed "
    "from the same bench at the same hour, and each was drawn as it "
    "appeared, without embellishment.",
    "What looks like a simple pattern of ripples is, on close reading, a "
    "family of curves. The eye learns to separate the wave that carries a "
    "line of light from the wave that merely carries the shadow of the next.",
)
_UNUSUAL_QUOTATION = (
    '"Detail is the friend of the patient observer. Begin with one line, '
    "own that line, and only then allow yourself the next.'"
)
_UNUSUAL_TITLE = "On the Behavior of Light"
_UNUSUAL_SUBTITLE = "A private study, recorded at the harbor bench"
_UNUSUAL_DEDICATION = "For the tides, which keep no one's time but their own."

_MIXED_TEXT_PAGE = (
    "The harbor offices keep a single working rule that has survived three "
    "directors and a fire: write the sea down as it is, not as you wish it "
    "were. Every chart, every log, and every line of the bulletin below "
    "follows that rule.",
    "The plate accompanying this page is a reduced copy of the surveyor's "
    "weathered original, kept for the record because the paper will not last "
    "another winter.",
)
_MIXED_DOC_TITLE = "Harbor Bulletin, First Quarter"
_MIXED_SCANNED_PAGE = (
    "The original chart, reduced for the bulletin.",
    "Datum line: mean low water. Soundings in meters.",
    "The plate has been in storage since the spring survey.",
)

_EDGE_BLANK_TEXT = (
    "This page intentionally carries nothing but a single short sentence, "
    "so the converter must handle a page that has no meaningful content at "
    "all and no images either."
)

_SHORT_REPORT_BODY = (
    "This short report exists to give the corpus a deliberately minimal "
    "text document: two pages, one heading, and a handful of paragraphs. "
    "It exercises chapter and structure detection on a document that has "
    "no repeated furniture and no images.",
    "The second paragraph repeats the same observation from a different "
    "angle, because a corpus fixture should be long enough to survive "
    "layout reconstruction without being so long that it slows the suite "
    "that must regenerate it.",
)
_SHORT_REPORT_TITLE = "Minimum Viable Document"
_SHORT_REPORT_SECTION = "Closing note"


# --------------------------------------------------------------------------- #
# Fixture recipes
# --------------------------------------------------------------------------- #


def _generate_novel_basic(path: pathlib.Path) -> pathlib.Path:
    """A 6-page single-column novel (TEXT): title page, 3 chapters."""
    doc = _new_doc(title=_NOVEL_TITLE, author=_NOVEL_AUTHOR)
    page = _new_page(doc)
    _textbox(page, (72, 140, 523, 190), _NOVEL_TITLE, fontsize=22, align=1)
    _textbox(page, (72, 195, 523, 220), _NOVEL_SUBTITLE, fontsize=12, align=1)
    _line(page, f"by {_NOVEL_AUTHOR}", x0=260, y=250)
    _line(page, "Chapter 1: The Harbor Lights", x0=72, y=320, fontname="hebo", fontsize=16)
    _textbox(page, (72, 350, 500, 520), _NOVEL_CHAPTER_1[0], fontsize=12)
    _textbox(page, (72, 540, 500, 700), _NOVEL_CHAPTER_1[1], fontsize=12)

    page = _new_page(doc)
    _textbox(page, (72, 150, 500, 330), _NOVEL_CHAPTER_1[2], fontsize=12)
    _textbox(page, (72, 360, 500, 560), _NOVEL_CHAPTER_1[3], fontsize=12)
    _line(page, "Chapter 2: The Keeper's Daughter", x0=72, y=620, fontname="hebo", fontsize=16)

    page = _new_page(doc)
    _textbox(page, (72, 170, 500, 350), _NOVEL_CHAPTER_2[0], fontsize=12)
    _textbox(page, (72, 380, 500, 560), _NOVEL_CHAPTER_2[1], fontsize=12)

    page = _new_page(doc)
    _textbox(page, (72, 150, 500, 330), _NOVEL_CHAPTER_2[2], fontsize=12)
    _line(page, "Chapter 3: North Wind", x0=72, y=400, fontname="hebo", fontsize=16)
    _textbox(page, (72, 430, 500, 620), _NOVEL_CHAPTER_3[0], fontsize=12)

    page = _new_page(doc)
    _textbox(page, (72, 150, 500, 340), _NOVEL_CHAPTER_3[1], fontsize=12)
    _textbox(page, (72, 370, 500, 540), _NOVEL_CHAPTER_3[2], fontsize=12)

    page = _new_page(doc)
    _textbox(page, (72, 150, 500, 360), _NOVEL_CHAPTER_3[3], fontsize=12)
    return _save(doc, path)


def _generate_textbook_dense(path: pathlib.Path) -> pathlib.Path:
    """A dense structured textbook (TEXT): section/subsection headings.

    Body text is 10 pt Times-Roman; headings are 16 pt / 12 pt Times-Bold.
    Paragraph boxes are sized from measured wrap counts so no text is ever
    clipped, and page breaks fall cleanly between logical blocks.
    """
    doc = _new_doc(title=_TEXTBOOK_TITLE, author=_TEXTBOOK_AUTHOR)
    page = _new_page(doc)
    _textbox(page, (72, 120, 523, 160), _TEXTBOOK_TITLE, fontsize=18, align=1)
    _line(page, _TEXTBOOK_AUTHOR, x0=230, y=180, fontsize=11)

    max_y = 715.0
    body_width = 500.0 - 72.0
    line_height = 10.0 * _LINE_HEIGHT_FACTOR

    def _body_box(p_text: str) -> float:
        return _wrap_count(
            p_text, width=body_width, fontname="tiro", fontsize=10.0
        ) * line_height + 6.0

    def _new_body_page() -> pymupdf.Page:
        block_page = _new_page(doc)
        return block_page

    current_section: str | None = None
    first_section = True
    y = 230.0
    for section, subsection, paragraphs in _TEXTBOOK_SECTIONS:
        if section != current_section:
            current_section = section
            if not first_section or y > max_y:
                page = _new_body_page()
                y = 150.0
            first_section = False
            _line(page, section, x0=72, y=y, fontname="tibo", fontsize=16)
            y += 54.0
        if y > max_y - 30.0:
            page = _new_body_page()
            y = 150.0
        _line(page, subsection, x0=72, y=y, fontname="tibo", fontsize=12)
        y += 34.0
        for paragraph in paragraphs:
            height = _body_box(paragraph)
            if y + height > max_y:
                page = _new_body_page()
                y = 150.0
            _textbox(
                page,
                (72, y, 500, y + height),
                paragraph,
                fontname="tiro",
                fontsize=10,
            )
            y += height + 16.0
        if y > max_y - 30.0:
            page = _new_body_page()
            y = 150.0
    return _save(doc, path)


def _generate_twocolumn_article(path: pathlib.Path) -> pathlib.Path:
    """A 3-page two-column newspaper article (TEXT)", 4 blocks per column."""
    doc = _new_doc(title=_GAZETTE_TITLE, author="Harbor Gazette Press")
    for _ in range(3):
        page = _new_page(doc)
        _textbox(page, (60, 130, 535, 170), _GAZETTE_TITLE, fontsize=18, align=1)
        y = 230.0
        for left, right in _GAZETTE_COLUMNS:
            _line(page, left, x0=72, y=y, fontsize=12)
            _line(page, right, x0=330, y=y + 6, fontsize=12)
            y += 70.0
        _line(page, _GAZETTE_FOOTNOTE, x0=72, y=696, fontsize=11)
    return _save(doc, path)


def _generate_scanned_book(path: pathlib.Path) -> pathlib.Path:
    """A 5-page scanned book (SCANNED): full-page raster images, no text."""
    doc = _new_doc(title="Scanned Harbor Novel", author="Unknown")
    for lines in _SCANNED_PAGES:
        page = _new_page(doc)
        pix = _raster_text_page(lines, dpi=120)
        _insert_pixmap(page, pix, (30, 40, 565, 802))
    return _save(doc, path)


def _generate_text_with_images(path: pathlib.Path) -> pathlib.Path:
    """A 4-page text report with meaningful embedded figures (TEXT)."""
    doc = _new_doc(title=_IMAGE_REPORT_TITLE, author="Harbor Survey Office")
    figure_number = 0
    for page_index in range(4):
        page = _new_page(doc)
        if page_index == 0:
            _textbox(page, (72, 140, 523, 180), _IMAGE_REPORT_TITLE, fontsize=18, align=1)
        color = (40, 90, 160)
        if 0 <= figure_number < len(_IMAGE_FIGURES):
            label, _name, color = _IMAGE_FIGURES[figure_number]
        _textbox(
            page,
            (72, 200, 500, 320),
            "The text on this page describes the figure below it in plain "
            "terms, so the embedded image is meaningful rather than "
            "decorative. Captions are placed beneath each image and reference "
            "a labeled drawing that the converter should preserve.",
            fontsize=12,
        )
        pix = _solid_pixmap(200, 130, color)
        _insert_pixmap(page, pix, (72, 330, 420, 520))
        if 0 <= figure_number < len(_IMAGE_FIGURES):
            _line(page, _IMAGE_FIGURES[figure_number][0], x0=72, y=545, fontsize=11)
        _textbox(
            page,
            (72, 580, 500, 700),
            "Following the figure, a short paragraph continues the report so "
            "the page mixes text and image content in the normal reading "
            "order, with the image between two text regions.",
            fontsize=12,
        )
        figure_number += 1
    return _save(doc, path)


def _generate_headers_footers(path: pathlib.Path) -> pathlib.Path:
    """A 6-page document with a running header, footer, and page numbers."""
    doc = _new_doc(title="The Lantern Keeper", author="Ada Grant")
    for page_index in range(6):
        page = _new_page(doc)
        _line(page, "The Lantern Keeper", x0=72, y=44, fontsize=10)
        _line(page, "Chapter One", x0=72, y=792, fontsize=10)
        first = 7 + page_index
        _line(page, str(first), x0=310, y=816, fontsize=10)
        _textbox(
            page,
            (72, 160, 500, 320),
            "The salt wind off the harbor carried the smell of rope and "
            "paint, and the morning light made the wet quay shine like "
            "polished stone. Every page of this document repeats the same "
            "running header and footer so the converter can learn to remove "
            "them.",
            fontsize=12,
        )
        _textbox(
            page,
            (72, 360, 500, 520),
            "The page number changes on each page, forming a small sequence "
            "at the bottom edge of the page. Removing the repeated furniture "
            "and the page numbers should leave only the body paragraphs "
            "behind.",
            fontsize=12,
        )
        _textbox(
            page,
            (72, 560, 500, 700),
            "A third paragraph gives the page enough body text to keep the "
            "document firmly in the text classification, surrounded on both "
            "sides by content that belongs to the page frame.",
            fontsize=12,
        )
    return _save(doc, path)


def _generate_unusual_typography(path: pathlib.Path) -> pathlib.Path:
    """A 4-page document with deliberately unusual, deterministic typography."""
    doc = _new_doc(title=_UNUSUAL_TITLE, author="Unknown")
    page = _new_page(doc)
    _textbox(page, (72, 150, 523, 200), _UNUSUAL_TITLE, fontsize=24, align=1)
    _textbox(page, (72, 230, 523, 260), _UNUSUAL_SUBTITLE, fontsize=12, align=1)
    _line(page, _UNUSUAL_DEDICATION, x0=120, y=360, fontsize=14)
    _line(page, "On reflection, first that:", x0=72, y=430, fontsize=11)
    _textbox(
        page,
        (60, 470, 535, 640),
        " ".join(_UNUSUAL_BODY),
        fontname="tiro",
        fontsize=10,
    )
    page = _new_page(doc)
    _line(page, "A quotation in a monospaced face, set apart from the body", x0=72, y=180, fontsize=12)
    _textbox(
        page,
        (120, 230, 480, 340),
        _UNUSUAL_QUOTATION,
        fontname="cour",
        fontsize=11,
    )
    _textbox(
        page,
        (72, 380, 500, 540),
        "The quoted material uses a different font family, a different size, "
        "and generous indentation from both margins, so the reconstruction "
        "must hold it apart while keeping the surrounding paragraph text in "
        "order.",
        fontname="tiro",
        fontsize=10,
    )
    page = _new_page(doc)
    _line(page, "SET IN SMALL CAPS, A LINE OF EMPHASIS", x0=72, y=200, fontsize=13)
    _textbox(
        page,
        (72, 250, 500, 420),
        "The lines that follow use an unusually large leading for their size, "
        "placing each line far from its neighbors. The generous spacing "
        "stresses the layout code's tolerance for documents that do not "
        "follow a tight grid.",
        fontname="tiro",
        fontsize=10,
    )
    _textbox(
        page,
        (72, 470, 500, 660),
        "A second paragraph continues the thought, and a third line completes "
        "the page, so there is enough text to reconstruct regardless of the "
        "unusual spacing above.",
        fontname="tiro",
        fontsize=10,
    )
    page = _new_page(doc)
    _line(page, "An italic closing reflection", x0=72, y=200, fontsize=14)
    _textbox(
        page,
        (72, 250, 500, 420),
        "Italic body text is uncommon in fiction but entirely legal in a PDF, "
        "and it changes the flags and family names the layout code sees. The "
        "converter should preserve the words as body text.",
        fontname="tiit",
        fontsize=10,
    )
    return _save(doc, path)


def _generate_mixed_text_image(path: pathlib.Path) -> pathlib.Path:
    """An 8-page MIXED document: text, scanned-style, and one mixed page."""
    doc = _new_doc(title=_MIXED_DOC_TITLE, author="Harbor Office")
    for index in range(8):
        page = _new_page(doc)
        if index in (0, 2, 4, 6):
            _line(page, _MIXED_DOC_TITLE, x0=72, y=150, fontsize=16)
            _textbox(page, (72, 190, 500, 340), _MIXED_TEXT_PAGE[0], fontsize=12)
            _textbox(page, (72, 380, 500, 560), _MIXED_TEXT_PAGE[1], fontsize=12)
        elif index in (1, 3, 5):
            pix = _raster_text_page(_MIXED_SCANNED_PAGE, dpi=120)
            _insert_pixmap(page, pix, (30, 40, 565, 802))
        else:
            pix = _solid_pixmap(200, 130, (30, 90, 150))
            _insert_pixmap(page, pix, (30, 350, 565, 842))
            _textbox(page, (72, 150, 500, 300), _MIXED_TEXT_PAGE[0], fontsize=12)
    return _save(doc, path)


def _generate_chapters_long(path: pathlib.Path) -> pathlib.Path:
    """A chapter-heavy document (TEXT): 8 explicit chapters over 8 pages."""
    titles = (
        "Chapter 1: The Harbor",
        "Chapter 2: The Quay",
        "Chapter 3: The Tides",
        "Chapter 4: The Fleet",
        "Chapter 5: The Beacon",
        "Chapter 6: The Storm",
        "Chapter 7: The Return",
        "Chapter 8: The Light",
    )
    body = (
        "The chapter opens with the harbor at rest. Lines, gear, and "
        "lanterns are put away in their places, and the water lies as still "
        "as the sky it copies.",
        "Here the ordinary work of the chapter begins: small tasks, taken in "
        "order, that together change the shape of the day.",
        "The closing paragraph leaves the harbor as it was found, except for "
        "the one difference the chapter has been about all along.",
    )
    doc = _new_doc(title="Eight Chapters of the Harbor", author="Ada Grant")
    for title in titles:
        page = _new_page(doc)
        _line(page, title, x0=72, y=200, fontname="hebo", fontsize=18)
        y = 268.0
        for paragraph in body:
            _textbox(page, (72, y, 500, y + 160), paragraph, fontsize=12)
            y += 170.0
    return _save(doc, path)


def _generate_edge_interleaved_blank(path: pathlib.Path) -> pathlib.Path:
    """A 4-page document: text, blank, text, blank (document-level MIXED)."""
    doc = _new_doc(title="Interleaved Blank Pages", author="Test Corpus")
    text_page = _new_page(doc)
    _textbox(text_page, (72, 200, 500, 400), _EDGE_BLANK_TEXT, fontsize=12)
    _new_page(doc)
    text_page = _new_page(doc)
    _textbox(text_page, (72, 150, 500, 360), _EDGE_BLANK_TEXT, fontsize=12)
    _new_page(doc)
    return _save(doc, path)


def _generate_edge_short_report(path: pathlib.Path) -> pathlib.Path:
    """A minimal 2-page text document (TEXT) with one heading."""
    doc = _new_doc(title=_SHORT_REPORT_TITLE, author="Test Corpus")
    page = _new_page(doc)
    _line(page, _SHORT_REPORT_TITLE, x0=72, y=180, fontsize=18)
    _textbox(page, (72, 230, 500, 400), _SHORT_REPORT_BODY[0], fontsize=12)
    _textbox(page, (72, 440, 500, 620), _SHORT_REPORT_BODY[1], fontsize=12)
    page = _new_page(doc)
    _line(page, _SHORT_REPORT_SECTION, x0=72, y=180, fontsize=14)
    _textbox(page, (72, 240, 500, 420), _SHORT_REPORT_BODY[0], fontsize=12)
    return _save(doc, path)


# --------------------------------------------------------------------------- #
# Fixture registry and metadata
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One corpus fixture: its recipe and its manifest metadata."""

    id: str
    category: str
    source: str
    license: str
    classification: str
    features: tuple[str, ...]
    description: str
    expectations: dict
    generate: Callable[[pathlib.Path], pathlib.Path]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def metadict(self) -> dict:
        return {
            "id": self.id,
            "path": f"pdfs/{self.id}.pdf",
            "category": self.category,
            "source": self.source,
            "license": self.license,
            "classification": {"expected": self.classification},
            "features": list(self.features),
            "description": self.description,
            "expectations": self.expectations,
            "generator": self.generate.__name__,
        }


FIXTURES: tuple[_Fixture, ...] = (
    _Fixture(
        id="novel_basic",
        category="normal_novel",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("paragraphs", "headings", "chapters", "reading_order"),
        description=(
            "A 6-page single-column novel with a title page and three "
            "explicitly numbered chapters. Exercises ordinary paragraph "
            "reconstruction, native heading and chapter detection, and "
            "normal single-column reading order."
        ),
        expectations={
            "page_count": 6,
            "text_page_count": 6,
            "image_page_count": 0,
        },
        generate=_generate_novel_basic,
        notes=(
            "Chapter headings are bold 16pt Helvetica against 12pt body, "
            "giving the heading detector several independent signals.",
        ),
    ),
    _Fixture(
        id="textbook_dense",
        category="textbook_dense",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("dense_text", "headings", "subheadings", "structured_content"),
        description=(
            "A dense structured textbook: 10pt serif body text, numbered "
            "sections (1.-4.), and lettered subsections in a bold serif face. "
            "Exercises the reconstruction of tightly packed documents with a "
            "heading/subheading hierarchy."
        ),
        expectations={
            "page_count": 6,
            "text_page_count": 6,
            "image_page_count": 0,
        },
        generate=_generate_textbook_dense,
        notes=(
            "Subsections use Times-Bold at 12pt against a 10pt Times body -- "
            "a smaller size contrast than the novel fixture, stress-testing "
            "the typography signals.",
        ),
    ),
    _Fixture(
        id="twocolumn_article",
        category="multi_column",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("two_columns", "reading_order", "full_width_heading"),
        description=(
            "A 3-page two-column newspaper-style article. Blocks are placed "
            "in interleaved column order so a naive row-major reader would "
            "produce an observably wrong reading order; exercises the M2.6 "
            "multi-column reconstruction."
        ),
        expectations={
            "page_count": 3,
            "text_page_count": 3,
            "image_page_count": 0,
            "left_column_marker": "L1",
            "right_column_marker": "R1",
        },
        generate=_generate_twocolumn_article,
        notes=(
            "The full-width title sits above both columns and the closing "
            "note spans the full width below them, so both the bridge rule "
            "and the band assembly are exercised.",
        ),
    ),
    _Fixture(
        id="scanned_book",
        category="scanned_book",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="SCANNED",
        features=("image_only_pages", "ocr_path", "scanned_document"),
        description=(
            "A 5-page image-only book. Each page embeds a full-page raster "
            "of large recognizable prose with no native text at all, so the "
            "document classifies SCANNED and routes every page through the "
            "render -> OCR -> cleanup path."
        ),
        expectations={
            "page_count": 5,
            "text_page_count": 0,
            "image_page_count": 5,
        },
        generate=_generate_scanned_book,
        notes=(
            "The embedded rasters are generated from text rendered at 120 "
            "DPI so later OCR-quality work has readable content to measure.",
        ),
    ),
    _Fixture(
        id="text_with_images",
        category="pdf_with_images",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("embedded_images", "captions", "image_placement"),
        description=(
            "A 4-page report mixing body text with meaningful colored "
            "figures and captions. Images cover well under the 50% page-area "
            "threshold so every page stays TEXT, exercising the M2.13 image "
            "extraction and placement path without changing classification."
        ),
        expectations={
            "page_count": 4,
            "text_page_count": 4,
            "image_page_count": 4,
        },
        generate=_generate_text_with_images,
        notes=(
            "Each figure is a distinct solid color so extracted assets and "
            "placements can be told apart deterministically.",
        ),
    ),
    _Fixture(
        id="headers_footers",
        category="headers_footers",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("repeated_header", "repeated_footer", "page_numbers"),
        description=(
            "A 6-page document with an identical running header, an "
            "identical footer, and a changing bare page number at the bottom "
            "edge of every page. Exercises M2.5 furniture detection and the "
            "M2.10 page-number removal over repeated body paragraphs."
        ),
        expectations={
            "page_count": 6,
            "text_page_count": 6,
            "image_page_count": 0,
            "header_text": "The Lantern Keeper",
            "footer_text": "Chapter One",
        },
        generate=_generate_headers_footers,
        notes=(
            "Header and footer text repeat verbatim on every page at a "
            "consistent position; the page numbers form a monotonic 7..12 "
            "sequence in the footer region.",
        ),
    ),
    _Fixture(
        id="unusual_typography",
        category="unusual_typography",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("unusual_typography", "centered_elements", "monospace_quote"),
        description=(
            "A 4-page document with deliberately unusual but deterministic "
            "typography: a centered dedication on an otherwise sparse page, "
            "a monospaced indented quotation, small-caps emphasis lines, "
            "very generous leading, and an italic serif closing section. "
            "Exercises the layout, heading, and reconstruction logic against "
            "documents that do not follow a tight grid."
        ),
        expectations={
            "page_count": 4,
            "text_page_count": 4,
            "image_page_count": 0,
        },
        generate=_generate_unusual_typography,
        notes=(
            "Classification stays TEXT; the value of this fixture is that the "
            "unusual faces and spacing are legal PDF and are handled "
            "deterministically.",
        ),
    ),
    _Fixture(
        id="mixed_text_image",
        category="mixed_text_image",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="MIXED",
        features=("native_text", "ocr_images", "mixed_routing"),
        description=(
            "An 8-page MIXED document: four native-text pages, three "
            "scanned-style full-page image pages, and one genuinely MIXED "
            "page (native text plus an image covering over 50% of the page). "
            "Exercises the M3.5 per-page routing that keeps native and OCR "
            "text streams separate."
        ),
        expectations={
            "page_count": 8,
            "text_page_count": 5,
            "image_page_count": 4,
        },
        generate=_generate_mixed_text_image,
        notes=(
            "Pages 1, 3, 5, 7 carry meaningful native text only; pages 2, 4, "
            "6 are image-only; page 8 combines native text with an "
            "image-dominated lower half.",
        ),
    ),
    _Fixture(
        id="chapters_long",
        category="chapter_heavy",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("multiple_chapters", "chapter_boundaries", "headings"),
        description=(
            "A chapter-heavy document of eight explicit, numbered chapters, "
            "one per page. Exercises chapter-boundary detection, the "
            "monotonic chapter-number sequence, chapter-based TOC "
            "generation, and EPUB chapter splitting."
        ),
        expectations={
            "page_count": 8,
            "text_page_count": 8,
            "image_page_count": 0,
            "min_chapters": 6,
        },
        generate=_generate_chapters_long,
        notes=(
            "Each page is one chapter: a bold 18pt 'Chapter N:' heading "
            "followed by three body paragraphs.",
        ),
    ),
    _Fixture(
        id="edge_interleaved_blank",
        category="edge_cases",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="MIXED",
        features=("blank_pages", "mixed_classification", "small_document"),
        description=(
            "A tiny 4-page document alternating meaningful text pages with "
            "intentionally blank pages. The blank pages carry no text and no "
            "images, so the document classifies MIXED and exercises how blank "
            "pages flow through classification and reconstruction."
        ),
        expectations={
            "page_count": 4,
            "text_page_count": 2,
            "image_page_count": 0,
        },
        generate=_generate_edge_interleaved_blank,
        notes=(
            "Deliberately small: 4 pages total, no images, no furniture.",
        ),
    ),
    _Fixture(
        id="edge_short_report",
        category="edge_cases",
        source="synthetic",
        license="Proprietary (project-authored synthetic content)",
        classification="TEXT",
        features=("minimal_document", "short_document", "single_heading"),
        description=(
            "A minimal two-page text document: one main heading, a page-2 "
            "section heading, and a few short paragraphs. Exercises structure "
            "and chapter handling on the smallest useful text document."
        ),
        expectations={
            "page_count": 2,
            "text_page_count": 2,
            "image_page_count": 0,
        },
        generate=_generate_edge_short_report,
        notes=(
            "No repeated furniture and no images, so nothing distracts from "
            "the minimal-document path.",
        ),
    ),
)


def fixture_metadata() -> list[dict]:
    """Return the machine-readable metadata for every fixture document.

    This is the single source of truth that :func:`write_manifest` serializes
    into ``manifest.json``.
    """
    return [fixture.metadict() for fixture in FIXTURES]


def _corpus_header() -> dict:
    return {
        "name": "kindle-converter-representative-corpus",
        "version": _CORPUS_VERSION,
        "description": (
            "Deterministic, repository-safe PDF corpus for Milestone 6 "
            "regression, quality, and performance measurement."
        ),
        "generator": "tests.fixtures.corpus.generate_corpus",
        "notes": (
            "All fixtures are synthetic and project-authored; no external or "
            "copyrighted material is committed. Classification and page-count "
            "expectations are guaranteed for the PyMuPDF editions this "
            "repository supports (>=1.24,<2)."
        ),
    }


def write_manifest(path: pathlib.Path) -> pathlib.Path:
    """Serialize the corpus manifest ({metadata, categories, documents})."""
    header = _corpus_header()
    header["categories"] = sorted({f.category for f in FIXTURES})
    payload = {
        "corpus": header,
        "documents": fixture_metadata(),
        "external": [],
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def generate_all(
    out_dir: pathlib.Path | None = None,
    manifest_path: pathlib.Path | None = None,
) -> list[pathlib.Path]:
    """Regenerate every fixture PDF into ``out_dir``.

    The PDFs are regenerated in fixture order; the matching ``manifest.json``
    is written to ``manifest_path`` (defaults to
    ``tests/fixtures/corpus/manifest.json``).
    """
    out_dir = pathlib.Path(out_dir or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []
    for fixture in FIXTURES:
        path = out_dir / f"{fixture.id}.pdf"
        fixture.generate(path)
        written.append(path)
    write_manifest(pathlib.Path(manifest_path or DEFAULT_MANIFEST_PATH))
    return written


def generate_one(
    fixture_id: str,
    out_dir: pathlib.Path | None = None,
) -> list[pathlib.Path]:
    """Regenerate a single fixture by ID into ``out_dir`` (no manifest write)."""
    out_dir = pathlib.Path(out_dir or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    for fixture in FIXTURES:
        if fixture.id == fixture_id:
            path = out_dir / f"{fixture.id}.pdf"
            fixture.generate(path)
            return [path]
    raise KeyError(f"unknown fixture id: {fixture_id!r}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministically regenerate the M6.1 representative PDF corpus "
            "and its manifest."
        )
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_DIR),
        help="output directory for the fixture PDFs",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="output path for the corpus manifest",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="regenerate only this fixture id (no manifest write)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.only:
        written = generate_one(args.only, pathlib.Path(args.out))
    else:
        written = generate_all(pathlib.Path(args.out), pathlib.Path(args.manifest))
    for path in written:
        print(f"generated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())