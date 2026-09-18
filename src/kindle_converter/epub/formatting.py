"""Kindle-oriented formatting profile and stylesheet generation (M4.5).

This module is the **internal** formatting layer of the EPUB builder. It owns
the single, centralized profile of Kindle-oriented values the generated
stylesheet is built from, so the builder's XHTML rendering and the packaged
``style.css`` can never drift apart and no CSS literal is scattered through
the rendering code.

Scope (M4.5)
------------
M4.5 refines the *output* of the existing ``Book -> EPUB`` boundary. Nothing
here inspects a PDF, runs OCR, reconstructs structure, or decides reading
order; the profile only styles the semantic content the M2/M3 reconstruction
already produced:

* reflowable typography in relative units (``em``/``rem``/``%``) only, so the
  reader's own font and font-size controls keep working;
* conservative paragraph, heading, chapter, blockquote, list, image, and
  page-break rules;
* one deterministic stylesheet -- no random identifiers, no timestamps, no
  per-element generated classes.

What the stylesheet deliberately never contains: fixed page or viewport
dimensions, absolute/fixed positioning, grid/flex/column layout, JavaScript,
animations, transitions, ``url()`` dependencies, external or remote fonts
(``@font-face``), ``@import``, CSS ``@media`` device profiles, and any attempt
to reproduce PDF geometry. A Kindle ebook is reflowable; the formatting is
**Kindle-friendly, not Kindle-fixed**.

The profile is internal on purpose: the Kindle-oriented formatting is the
default output behavior, not a user-selectable theme (see the M4.5 scope
notes in the README). :data:`DEFAULT_FORMATTING` is the only profile the
builder uses, and :func:`build_stylesheet` is a pure function of it, so a
given profile always produces byte-identical CSS.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template

__all__ = [
    "DEFAULT_FORMATTING",
    "KindleFormattingProfile",
    "build_stylesheet",
]


@dataclass(frozen=True, slots=True)
class KindleFormattingProfile:
    """Centralized Kindle-oriented formatting values (M4.5).

    Every field is a CSS value string in a reflowable unit (``em``, ``rem``,
    ``%``) or a unitless keyword. The dataclass is frozen: the profile is a
    constant, immutable description of the formatting contract, never a
    mutable runtime configuration object.

    ``heading_sizes`` holds ``h1``..``h6`` in order; consecutive equal sizes
    are grouped into one selector when the stylesheet is rendered, so the
    hierarchy stays visible without repeated declarations.
    """

    #: Conservative font stack: the reading system picks an installed font.
    #: No device font is forced and no font is embedded (M4.5 embeds none).
    body_font_family: str = 'serif, Georgia, "Times New Roman", serif'
    body_font_size: str = "1em"
    body_line_height: str = "1.5"
    body_color: str = "#000"

    #: Paragraphs: modest bottom margin, no indentation, no top margin.
    paragraph_margin: str = "0 0 1em 0"
    paragraph_line_height: str = "1.5"

    #: Headings: spacing before and after, tighter leading than body text.
    heading_line_height: str = "1.3"
    heading_margin: str = "1.2em 0 0.5em 0"
    heading_sizes: tuple[str, ...] = (
        "1.6em",  # h1
        "1.4em",  # h2
        "1.25em",  # h3
        "1.1em",  # h4
        "1.1em",  # h5
        "1.1em",  # h6
    )

    #: Images: scale down to the reading width, centered, aspect preserved.
    image_margin: str = "0.5em auto"

    #: Blockquotes: indented on both sides, no decorative quotation marks.
    blockquote_margin: str = "1em 1.5em"
    blockquote_line_height: str = "1.5"

    #: Lists: indented, readable leading, no manually generated numbering.
    list_margin: str = "0 0 1em 1.5em"
    list_item_margin: str = "0 0 0.25em 0"
    list_line_height: str = "1.5"

    #: Page breaks: a reflowable break marker, never a fixed PDF page.
    page_break_after: str = "always"


#: The one profile the EPUB builder renders. Deliberately not exposed as a
#: public, user-selectable configuration (M4.5 scope).
DEFAULT_FORMATTING = KindleFormattingProfile()


def _heading_size_rules(sizes: tuple[str, ...]) -> str:
    """Render ``h1``..``h6`` font sizes, grouping equal adjacent sizes.

    Deterministic: the same ``sizes`` tuple always produces the same lines,
    and each rule stays a single readable line (``h1 { font-size: 1.6em; }``).
    """
    rules: list[str] = []
    start = 0
    for index in range(1, len(sizes) + 1):
        if index == len(sizes) or sizes[index] != sizes[start]:
            levels = ", ".join(
                f"h{level}" for level in range(start + 1, index + 1)
            )
            rules.append(f"{levels} {{ font-size: {sizes[start]}; }}")
            start = index
    return "\n".join(rules)


#: The single stylesheet template. Sections are ordered base/body, headings,
#: chapters, paragraphs, images (with the cover note), blockquotes, lists,
#: page breaks -- the M4.5 CSS architecture. One stylesheet is packaged; no
#: per-chapter or per-device stylesheets are generated.
_CSS_TEMPLATE = Template(
    """\
/* Kindle-oriented stylesheet for a reflowable book (M4.5). */

/* Base / body */
html {
    font-size: 1em;
}
body {
    font-family: $body_font_family;
    font-size: $body_font_size;
    line-height: $body_line_height;
    margin: 0;
    padding: 0;
    color: $body_color;
    text-align: left;
}

/* Headings */
h1, h2, h3, h4, h5, h6 {
    font-family: inherit;
    font-weight: bold;
    font-style: normal;
    line-height: $heading_line_height;
    margin: $heading_margin;
    padding: 0;
    text-align: left;
    page-break-after: avoid;
    break-after: avoid;
    page-break-inside: avoid;
    break-inside: avoid;
}
$heading_size_rules

/* Chapters: a heading that opens a chapter needs no leading blank space. */
h1:first-child, h2:first-child, h3:first-child, h4:first-child,
h5:first-child, h6:first-child {
    margin-top: 0;
}

/* Paragraphs */
p {
    margin: $paragraph_margin;
    padding: 0;
    text-indent: 0;
    line-height: $paragraph_line_height;
}

/* Images */
img.image {
    display: block;
    max-width: 100%;
    height: auto;
    margin: $image_margin;
    page-break-inside: avoid;
    break-inside: avoid;
}
/* Cover: the M4.4 cover page reuses img.image above, so it needs no
   cover-specific CSS and no fixed cover dimensions. */

/* Blockquotes */
blockquote {
    margin: $blockquote_margin;
    padding: 0;
    line-height: $blockquote_line_height;
}

/* Lists */
ul, ol {
    margin: $list_margin;
    padding: 0;
    line-height: $list_line_height;
}
li {
    margin: $list_item_margin;
    padding: 0;
    line-height: $list_line_height;
    page-break-inside: avoid;
    break-inside: avoid;
}

/* Page breaks */
div.page-break {
    height: 0;
    margin: 0;
    padding: 0;
    page-break-after: $page_break_after;
    break-after: $page_break_after;
}
"""
)


def build_stylesheet(
    profile: KindleFormattingProfile = DEFAULT_FORMATTING,
) -> str:
    """Render the deterministic Kindle-oriented stylesheet for ``profile``.

    The result depends only on ``profile``: no timestamps, no random or
    generated identifiers, no environment inspection, and no element-specific
    classes. Calling this function twice with the same profile returns
    identical text.
    """
    return _CSS_TEMPLATE.substitute(
        body_font_family=profile.body_font_family,
        body_font_size=profile.body_font_size,
        body_line_height=profile.body_line_height,
        body_color=profile.body_color,
        paragraph_margin=profile.paragraph_margin,
        paragraph_line_height=profile.paragraph_line_height,
        heading_line_height=profile.heading_line_height,
        heading_margin=profile.heading_margin,
        heading_size_rules=_heading_size_rules(profile.heading_sizes),
        image_margin=profile.image_margin,
        blockquote_margin=profile.blockquote_margin,
        blockquote_line_height=profile.blockquote_line_height,
        list_margin=profile.list_margin,
        list_item_margin=profile.list_item_margin,
        list_line_height=profile.list_line_height,
        page_break_after=profile.page_break_after,
    )
