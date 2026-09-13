"""Deterministic reading-order reconstruction (Milestones 2.2 + 2.6).

This module consumes the Milestone 2.1 layout-aware representation
(:class:`~kindle_converter.pdf.layout.PageLayout` /
:class:`~kindle_converter.pdf.layout.LayoutPage`) and produces a *new*
ordering of each page's text blocks in a human reading order, using
bounding-box geometry rather than trusting PyMuPDF's ``"dict"`` extraction
order.

The reading-order contract
--------------------------
* **Reconstructed order** answers "in what order should a human normally
  read these blocks?", which is a different question from the M2.1 source
  order ("in what order did the PDF library return these blocks?"). The
  original extraction order is never overwritten: every reconstructed
  block keeps its ``source_order`` and the source layout objects are never
  mutated.
* **Coordinate assumptions.** Geometry follows the layout module's
  convention: top-left origin, y grows downwards, units are PDF points,
  bounding boxes are inclusive (``width == x1 - x0``). Reading order is
  therefore top-to-bottom (smaller y first) and left-to-right within a row
  (smaller x first).
* **Per-page scope.** Reconstruction is performed independently per page.
  Pages are never reordered; only the order of blocks *within* each page
  changes.
* **What is NOT solved.** This milestone deliberately does *not* do
  paragraph reconstruction, line merging, heading detection,
  header/footer detection, font-based semantic classification, table
  reconstruction, OCR, or image placement. M2.6 refines the column
  hypothesis below but still recognizes only geometrically obvious
  columns: full-width blocks are excluded from column clustering instead
  of merging the page, and weak column evidence still falls back to the
  exact M2.2 legacy ordering rather than guessing.

Tolerance strategy
------------------
PDF coordinates carry small floating-point differences, so no code here
uses exact float equality to decide whether blocks share a row. Three
named constants centralize every tolerance:

* ``ROW_OVERLAP_TOLERANCE_PT`` -- the minimum vertical interval overlap
  (in points) two blocks must share before they can be considered as
  being on the same row. Absorbs sub-point noise on identical baselines.
* ``ROW_TOP_TOLERANCE_PT`` -- the maximum allowed difference between two
  blocks' top edges for a same-row match. Prevents a tall block from
  dragging unrelated lower blocks into its row.
* ``COLUMN_OVERLAP_RATIO`` -- two blocks belong to the same column when
  their horizontal intervals overlap by at least this fraction of the
  *smaller* block's width.
* ``COLUMN_MIN_BLOCKS`` -- minimum blocks per column cluster; rejects
  columns built from isolated objects.
* ``COLUMN_GAP_TOLERANCE_PT`` -- minimum edge-to-edge gutter between
  accepted column clusters.
* ``COLUMN_MAX_OVERLAP_RATIO`` -- maximum tolerated x-overlap between
  accepted column clusters.
* ``COLUMN_FULL_WIDTH_FRACTION`` / ``COLUMN_FULL_WIDTH_MIN_PT`` --
  relative and absolute gates for the full-width (bridge) rule.
* ``COLUMN_MIN_COVERAGE_FRACTION`` -- minimum share of eligible blocks
  owned by accepted columns.
* ``COLUMN_MAX_COUNT`` -- maximum accepted column clusters per page.

Algorithm
---------
For each page the reconstruction proceeds in deterministic phases:

1. **M2.6 multi-column hypothesis.** Exclude full-width blocks, cluster
   the rest by the M2.2 x-overlap rule, and accept a multi-column split
   only when every evidence gate above passes. The page is then emitted
   as vertical bands: full-width separators in place, column regions
   left-to-right with top-to-bottom blocks inside each column.
2. **Legacy grouping (M2.2 fallback).** When the hypothesis is rejected,
   build connected components of blocks whose x-intervals overlap by at
   least ``COLUMN_OVERLAP_RATIO`` of the smaller width (a full-width
   block bridges the components, which is the documented conservative
   fallback).
2. **Row grouping.** Within each component/column, blocks are sorted by
   ``(y0, x0, source_order)`` and greedily grouped into rows: a block
   joins the current row only if it vertically overlaps the previous
   block by more than ``ROW_OVERLAP_TOLERANCE_PT`` *and* its top edge is
   within ``ROW_TOP_TOLERANCE_PT`` of it. Each row is then ordered
   left-to-right by ``x0``.
3. **Assembly.** The components/columns are output left-to-right (by
   their leftmost ``x0``), and inside each one the rows are output
   top-to-bottom. Multi-column pages insert full-width separators at
   their vertical positions. Every comparison falls back to the
   original extraction ``order`` as a final tie-breaker, so identical
   geometry always yields identical output.

Result objects
--------------
The reconstructed order is returned as a *view* over the source layout:
:class:`OrderedPage` / :class:`OrderedBlock` hold references to the
original immutable M2.1 dataclasses and add only ordering information, so
no layout data is duplicated. :func:`reconstruct_page_order` returns the
plain block tuple for callers that only need the ordering itself.

Images are not ordered. The reconstruction only ever emits text blocks
(``LayoutPage.blocks``); a page's ``image_block_count`` is carried over
unchanged and the presence of images never affects the text ordering.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import LayoutBlock, LayoutPage, PageLayout

# --------------------------------------------------------------------------- #
# Tolerance and threshold constants (centralized, deterministic, documented)
# --------------------------------------------------------------------------- #

#: Minimum vertical interval overlap (in points) for two blocks to be
#: considered as sharing a row. A positive epsilon absorbs floating-point
#: noise on blocks that were typeset on the same baseline while still
#: rejecting rows separated by a real gap.
ROW_OVERLAP_TOLERANCE_PT = 2.0

#: Maximum allowed difference between the top edges of two blocks that are
#: to be joined into one row (points). Same-row blocks sit on a shared
#: baseline, so their top edges agree within a line-height fraction; a tall
#: block (e.g. a drop-cap) that merely *partially* overlaps a lower block
#: must not drag the lower block into its row.
ROW_TOP_TOLERANCE_PT = 8.0

#: Minimum horizontal interval overlap, as a fraction of the *smaller*
#: block's width, for two blocks to be considered part of the same column
#: component. Blocks in obviously separate columns do not overlap at all;
#: a ratio of 0.5 keeps the rule conservative.
COLUMN_OVERLAP_RATIO = 0.5

#: Minimum number of column-eligible blocks each surviving column cluster
#: must contain. Rejects "columns" built from a lone page number, heading,
#: or decorative object.
COLUMN_MIN_BLOCKS = 2

#: Minimum horizontal gutter (in points) between two accepted column
#: clusters, measured edge-to-edge. Small positive gaps still count;
#: touching clusters do not.
COLUMN_GAP_TOLERANCE_PT = 4.0

#: Maximum allowed horizontal overlap between two accepted column clusters,
#: as a fraction of the narrower cluster's width. Substantially overlapping
#: clusters are one ragged single column, not two columns.
COLUMN_MAX_OVERLAP_RATIO = 0.25

#: Minimum fraction of the page's text width a block must span to count as
#: full-width (bridge) content. Such blocks stay outside every column and
#: split the page vertically instead of merging columns.
COLUMN_FULL_WIDTH_FRACTION = 0.85

#: Minimum absolute width (in points) for the full-width rule, so narrow
#: pages or tiny blocks can never be labelled full-width on fraction alone.
COLUMN_FULL_WIDTH_MIN_PT = 200.0

#: Fraction of the column-eligible blocks that accepted column clusters
#: must jointly cover. Prevents inventing columns from scattered content.
COLUMN_MIN_COVERAGE_FRACTION = 0.5

#: Maximum number of column clusters ever accepted on one page. Bounds the
#: search; genuine layouts beyond this use the legacy ordering.
COLUMN_MAX_COUNT = 4


@dataclass(frozen=True, slots=True)
class OrderedBlock:
    """One text block in reconstructed reading order.

    ``block`` is a reference to the original immutable
    :class:`~kindle_converter.pdf.layout.LayoutBlock` -- no layout data is
    copied. ``page_order`` is the dense 0-based reading index of the block
    within its page; ``source_order`` is the block's preserved M2.1
    extraction order (``LayoutBlock.order``).
    """

    block: LayoutBlock
    page_order: int
    source_order: int

    @property
    def text(self) -> str:
        return self.block.text

    @property
    def x0(self) -> float:
        return self.block.x0

    @property
    def y0(self) -> float:
        return self.block.y0

    @property
    def x1(self) -> float:
        return self.block.x1

    @property
    def y1(self) -> float:
        return self.block.y1

    @property
    def width(self) -> float:
        return self.block.width

    @property
    def height(self) -> float:
        return self.block.height

    @property
    def order(self) -> int:
        """Alias for :attr:`source_order` (the preserved M2.1 provenance)."""
        return self.source_order


@dataclass(frozen=True, slots=True)
class OrderedPage:
    """One page's blocks in reconstructed reading order.

    ``page`` references the source :class:`~kindle_converter.pdf.layout.LayoutPage`,
    whose unmodified ``blocks`` tuple remains the page's extraction-order
    provenance; ``blocks`` here are the :class:`OrderedBlock` views in
    reconstructed reading order.
    """

    page: LayoutPage
    blocks: tuple[OrderedBlock, ...]

    @property
    def page_number(self) -> int:
        return self.page.page_number

    @property
    def page_width(self) -> float:
        return self.page.page_width

    @property
    def page_height(self) -> float:
        return self.page.page_height

    @property
    def image_block_count(self) -> int:
        return self.page.image_block_count

    @property
    def text(self) -> str:
        """All block text on the page, in reconstructed order, blank-joined."""
        return "\n".join(block.text for block in self.blocks)

    def source_order(self, block: LayoutBlock) -> int:
        """The preserved extraction order of ``block`` on this page.

        The identity-based lookup never reads the reconstructed order, so
        the M2.1 provenance stays meaningful no matter how the blocks are
        re-sorted.
        """
        for ordered in self.blocks:
            if ordered.block is block:
                return ordered.source_order
        raise ValueError("block is not a text block of this page")


@dataclass(frozen=True, slots=True)
class OrderedLayout:
    """The whole document's blocks in per-page reconstructed reading order.

    ``pages`` are :class:`OrderedPage` objects in the original page order;
    pages themselves are never reordered.
    """

    pages: tuple[OrderedPage, ...]

    @property
    def block_count(self) -> int:
        return sum(len(page.blocks) for page in self.pages)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def reconstruct_page_order(page: LayoutPage) -> tuple[LayoutBlock, ...]:
    """Return ``page``'s text blocks in reconstructed reading order.

    This is the pure-geometry core: a deterministic, explainable ordering
    based only on bounding boxes and the M2.1 source order (used solely as
    a tie-breaker). The source layout is not modified.

    An empty or single-block page returns its blocks unchanged. Multi
    column pages use the M2.6 bridge-robust refinement; pages without
    sufficient column evidence use the exact M2.2 legacy ordering.
    """
    if len(page.blocks) <= 1:
        return page.blocks

    refined = _multicolumn_order(page)
    if refined is not None:
        return refined
    return _legacy_order(page.blocks)


def reconstruct_read_order(
    source: LayoutPage | PageLayout,
) -> OrderedPage | OrderedLayout:
    """Reconstruct the reading order of one page or a whole document.

    Returns an :class:`OrderedPage` when ``source`` is a
    :class:`LayoutPage` and an :class:`OrderedLayout` when it is a
    :class:`PageLayout`. The source objects are never mutated; the returned
    views reference them.
    """
    if isinstance(source, PageLayout):
        return OrderedLayout(pages=tuple(_ordered_page(p) for p in source.pages))
    return _ordered_page(source)


# --------------------------------------------------------------------------- #
# Legacy ordering (M2.2, preserved bit-for-bit as the fallback)
# --------------------------------------------------------------------------- #


def _legacy_order(blocks: tuple[LayoutBlock, ...]) -> tuple[LayoutBlock, ...]:
    """Emit the exact M2.2 ordering for ``blocks``."""
    ordered: list[LayoutBlock] = []
    for group in sorted(_column_groups(blocks), key=_component_key):
        for row in _rows(group):
            ordered.extend(sorted(row, key=lambda b: (b.x0, b.order)))
    return tuple(ordered)


def _column_groups(blocks: tuple[LayoutBlock, ...]) -> list[list[LayoutBlock]]:
    """Partition ``blocks`` into connected components by horizontal overlap.

    Two blocks belong to the same component when their x-intervals overlap
    by at least ``COLUMN_OVERLAP_RATIO`` of the smaller block's width.
    Components therefore model *obvious* columns: blocks in clearly
    separated columns never overlap horizontally, while a full-width block
    bridges every component and collapses the page into a single component
    (the documented conservative fallback).
    """
    groups: list[list[LayoutBlock]] = []
    for block in blocks:
        joined: list[list[LayoutBlock]] = []
        remaining: list[list[LayoutBlock]] = []
        for group in groups:
            if any(_same_column(block, other) for other in group):
                joined.append(group)
            else:
                remaining.append(group)
        merged = [block]
        for group in joined:
            merged.extend(group)
        groups = remaining + [merged]
    return groups


def _same_column(a: LayoutBlock, b: LayoutBlock) -> bool:
    """Return whether ``a`` and ``b`` belong to the same column component."""
    overlap = _x_overlap(a, b)
    if overlap <= 0:
        return False
    return overlap >= COLUMN_OVERLAP_RATIO * min(a.width, b.width)


def _x_overlap(a: LayoutBlock, b: LayoutBlock) -> float:
    """The length of the horizontal interval shared by ``a`` and ``b``."""
    return min(a.x1, b.x1) - max(a.x0, b.x0)


def _component_key(group: list[LayoutBlock]) -> tuple[float, float, int]:
    """Deterministic left-to-right ordering key for a component."""
    return (
        min(b.x0 for b in group),
        min(b.y0 for b in group),
        min(b.order for b in group),
    )


# --------------------------------------------------------------------------- #
# Multi-column refinement (M2.6: bridge-robust column detection)
# --------------------------------------------------------------------------- #


def _multicolumn_order(page: LayoutPage) -> tuple[LayoutBlock, ...] | None:
    """Return the M2.6 column-major order, or ``None`` when evidence is weak.

    ``None`` means the caller must use :func:`_legacy_order` unchanged, so
    ordinary single-column pages keep their exact M2.2 ordering. The
    refinement excludes full-width (bridge) blocks from column clustering,
    accepts a multi-column split only when every evidence threshold passes,
    then emits vertical bands (full-width separators in place, column
    regions left-to-right with top-to-bottom blocks inside each column).
    """
    blocks = page.blocks
    span_width = max(b.x1 for b in blocks) - min(b.x0 for b in blocks)
    full_ids = {id(b) for b in blocks if _is_full_width(b, span_width)}
    eligible = tuple(b for b in blocks if id(b) not in full_ids)
    if len(eligible) < 2 * COLUMN_MIN_BLOCKS:
        return None
    columns = sorted(
        (
            cluster
            for cluster in _column_groups(eligible)
            if len(cluster) >= COLUMN_MIN_BLOCKS
            and not any(_is_full_width(b, span_width) for b in cluster)
            and not _cluster_is_singleton_x_outlier(cluster, eligible)
        ),
        key=_component_key,
    )
    if len(columns) < 2 or len(columns) > COLUMN_MAX_COUNT:
        return None
    if sum(len(c) for c in columns) < COLUMN_MIN_COVERAGE_FRACTION * len(
        eligible
    ):
        return None
    extents = [_extent(c) for c in columns]
    for (lx0, lx1, ly0, ly1), (rx0, rx1, ry0, ry1) in zip(
        extents, extents[1:]
    ):
        overlap = min(lx1, rx1) - max(lx0, rx0)
        narrower = min(lx1 - lx0, rx1 - rx0)
        if narrower <= 0:
            return None
        if overlap > COLUMN_MAX_OVERLAP_RATIO * narrower:
            return None
        if min(ly1, ry1) - max(ly0, ry0) <= 0:
            return None
        if rx0 - lx1 < COLUMN_GAP_TOLERANCE_PT:
            # No real gutter between wide neighbours: keep legacy order.
            return None
    owned_ids = {id(b) for c in columns for b in c}
    separators = [b for b in blocks if id(b) not in owned_ids]
    return _assemble_bands(columns, separators)


def _is_full_width(block: LayoutBlock, span_width: float) -> bool:
    """Return whether ``block`` spans most of the page's text width.

    Full-width blocks (chapter headings, intro paragraphs, bridge blocks)
    are kept out of every column so a single wide block cannot collapse the
    column structure. Both a relative fraction and an absolute floor must
    pass, so narrow pages or tiny blocks never qualify on fraction alone.
    """
    if span_width <= 0:
        return False
    return (
        block.width >= COLUMN_FULL_WIDTH_FRACTION * span_width
        and block.width >= COLUMN_FULL_WIDTH_MIN_PT
    )


def _extent(cluster: list[LayoutBlock]) -> tuple[float, float, float, float]:
    """Return the ``(x0, x1, y0, y1)`` bounding extent of ``cluster``."""
    return (
        min(b.x0 for b in cluster),
        max(b.x1 for b in cluster),
        min(b.y0 for b in cluster),
        max(b.y1 for b in cluster),
    )


def _cluster_is_singleton_x_outlier(
    cluster: list[LayoutBlock], eligible: tuple[LayoutBlock, ...]
) -> bool:
    """Return whether a 2-block cluster is only joined via one bridge block.

    Two same-x blocks linked by a single block overlapping both (an A-B-C
    chain where A and C never overlap each other) are weaker evidence than
    a genuine column. Reject such chains so isolated centered headings do
    not glue one side of the page into a false column split.
    """
    if len(cluster) != COLUMN_MIN_BLOCKS:
        return False
    first, second = cluster
    if _intervals_overlap(first, second):
        return False
    bridges = [
        b
        for b in eligible
        if b not in cluster
        and _intervals_overlap(b, first)
        and _intervals_overlap(b, second)
    ]
    return len(bridges) <= 1


def _intervals_overlap(a: LayoutBlock, b: LayoutBlock) -> bool:
    """Return whether ``a`` and ``b`` satisfy the column x-overlap rule."""
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    if overlap <= 0:
        return False
    return overlap >= COLUMN_OVERLAP_RATIO * min(a.width, b.width)


def _assemble_bands(
    columns: list[list[LayoutBlock]], separators: list[LayoutBlock]
) -> tuple[LayoutBlock, ...]:
    """Merge column clusters and full-width separators into reading order.

    Separators entirely above (below) the column region are emitted before
    (after) it; separators vertically inside the region act as cuts that
    split each column into above/below segments, so a heading between
    column rows keeps its vertical position without merging the columns.
    Inside each band, columns read left-to-right and blocks read
    top-to-bottom via the M2.2 row grouping.
    """
    owned = [b for column in columns for b in column]
    top = min(b.y0 for b in owned)
    bottom = max(b.y1 for b in owned)
    key = lambda b: (b.y0, b.x0, b.order)
    before = sorted((s for s in separators if s.y1 <= top), key=key)
    after = sorted((s for s in separators if s.y0 >= bottom), key=key)
    before_ids = {id(s) for s in before}
    after_ids = {id(s) for s in after}
    cuts = sorted(
        (s for s in separators if id(s) not in before_ids | after_ids),
        key=key,
    )
    ordered: list[LayoutBlock] = list(before)
    if not cuts:
        for column in columns:
            ordered.extend(_column_emit(column))
    else:
        centers = [(_y_center(s)) for s in cuts]

        def band(block: LayoutBlock) -> int:
            center = _y_center(block)
            index = 0
            for cut_center in centers:
                if cut_center <= center:
                    index += 1
            return index

        for index in range(len(cuts) + 1):
            for column in columns:
                ordered.extend(
                    _column_emit([b for b in column if band(b) == index])
                )
            if index < len(cuts):
                ordered.append(cuts[index])
    ordered.extend(after)
    return tuple(ordered)


def _column_emit(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    """Order one column (segment) top-to-bottom via M2.2 row grouping."""
    ordered: list[LayoutBlock] = []
    for row in _rows(list(blocks)):
        ordered.extend(sorted(row, key=lambda b: (b.x0, b.order)))
    return ordered


def _y_center(block: LayoutBlock) -> float:
    """Return the vertical midpoint of ``block``."""
    return (block.y0 + block.y1) / 2.0


# --------------------------------------------------------------------------- #
# Row grouping within a component
# --------------------------------------------------------------------------- #


def _rows(blocks: list[LayoutBlock]) -> list[list[LayoutBlock]]:
    """Greedily split ``blocks`` (primary-sorted) into reading rows.

    A block starts a new row unless it both vertically overlaps the
    previous block by more than ``ROW_OVERLAP_TOLERANCE_PT`` and keeps its
    top edge within ``ROW_TOP_TOLERANCE_PT`` of it. The two-part guard
    keeps same-baseline blocks together while tall blocks cannot drag
    unrelated lower blocks into their row.
    """
    rows: list[list[LayoutBlock]] = []
    for block in sorted(blocks, key=_primary_key):
        if rows and _shares_row(rows[-1][-1], block):
            rows[-1].append(block)
        else:
            rows.append([block])
    return rows


def _primary_key(block: LayoutBlock) -> tuple[float, float, int]:
    """Deterministic top-down scanning key for a block."""
    return (block.y0, block.x0, block.order)


def _shares_row(upper: LayoutBlock, lower: LayoutBlock) -> bool:
    """Return whether ``lower`` sits on the same visual row as ``upper``.

    Both geometric guards use tolerance constants rather than exact
    equality, so sub-point floating-point noise on identical baselines is
    absorbed while genuinely distinct rows stay separated.
    """
    if _y_overlap(upper, lower) <= ROW_OVERLAP_TOLERANCE_PT:
        return False
    return abs(lower.y0 - upper.y0) <= ROW_TOP_TOLERANCE_PT


def _y_overlap(upper: LayoutBlock, lower: LayoutBlock) -> float:
    """The length of the vertical interval shared by two blocks."""
    return min(upper.y1, lower.y1) - max(upper.y0, lower.y0)


# --------------------------------------------------------------------------- #
# Ordered views
# --------------------------------------------------------------------------- #


def _ordered_page(page: LayoutPage) -> OrderedPage:
    """Build the :class:`OrderedPage` view for ``page``."""
    ordered_blocks = tuple(
        OrderedBlock(
            block=block,
            page_order=page_order,
            source_order=block.order,
        )
        for page_order, block in enumerate(reconstruct_page_order(page))
    )
    return OrderedPage(page=page, blocks=ordered_blocks)