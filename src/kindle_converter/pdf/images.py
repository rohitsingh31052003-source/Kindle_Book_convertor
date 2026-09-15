"""Embedded PDF image extraction and placement (Milestone 2.13).

Only embedded PDF image objects (``Page.get_images`` /
``Document.extract_image``) are used. No OCR, vector rasterization, page
rendering, or text-to-image conversion.

Coordinate convention (matches M2.1/M2.2): PyMuPDF top-left origin, x grows
rightwards, y grows downwards, units are PDF points. Page numbers are
1-based physical PDF page indices.

Identifier strategy: asset ids are deterministic extraction indices
``image-001`` ... in order of first appearance (ascending page order, then
per-page ``get_images`` order). Placements keep the global extraction
sequence in ``order``. No id()/UUID/timestamps/paths are used.

Asset/placement separation: one payload stored once (deduplicated by SHA-256
of the extracted bytes); every placement references its asset by id while
keeping its own page/bbox/order/xref provenance.

Ordering: ``placements`` is ascending page number, then per-page listing
order, then per-placement rect index. Rects of one occurrence are sorted by
``(y0, x0, x1, y1)`` for stability. M2.2/M2.6 stays authoritative for text.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import pymupdf

from .analyzer import EmptyPDFError, PDFReadError

PathLike = str | os.PathLike[str]
Source = PathLike | pymupdf.Document


class ImageExtractionError(Exception):
    """Raised when an embedded PDF image cannot be extracted usably."""


_EXT_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "jpe": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "webp": "image/webp",
    "jpx": "image/jpx",
    "jp2": "image/jp2",
}

_MAGIC_BYTES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"RIFF", "image/webp"),
    (b"<svg", "image/svg+xml"),
    (b"<?xml", "image/svg+xml"),
)


def _mime_for(ext: str | None, data: bytes) -> tuple[str, str]:
    """Return ``(extension, mime_type)`` for an extracted payload."""
    normalized = (ext or "").strip().lower().lstrip(".")
    if normalized in _EXT_MIME_TYPES:
        return normalized, _EXT_MIME_TYPES[normalized]
    for magic, media_type in _MAGIC_BYTES:
        if data.startswith(magic):
            fallback = "bin"
            for cand_ext, cand_mime in _EXT_MIME_TYPES.items():
                if cand_mime == media_type:
                    fallback = cand_ext
                    break
            return normalized or fallback, media_type
    return normalized or "bin", "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ImageAsset:
    """One deduplicated embedded-image payload (immutable value object)."""

    asset_id: str
    data: bytes
    mime_type: str
    width: int
    height: int
    extension: str = "bin"
    xref: int = 0
    order: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"data must be bytes-like, got {type(self.data).__name__}"
            )
        object.__setattr__(self, "data", bytes(self.data))

    @property
    def size(self) -> int:
        """Payload size in bytes."""
        return len(self.data)


@dataclass(frozen=True, slots=True)
class ImagePlacement:
    """One anchored occurrence of an asset on a page (immutable)."""

    asset_id: str
    page_number: int
    bbox: tuple[float, float, float, float]
    order: int = 0
    xref: int = 0

    @property
    def x0(self) -> float:
        """Left edge (points)."""
        return self.bbox[0]

    @property
    def y0(self) -> float:
        """Top edge (points)."""
        return self.bbox[1]

    @property
    def width(self) -> float:
        """Placement width in points."""
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        """Placement height in points."""
        return self.bbox[3] - self.bbox[1]


@dataclass(frozen=True, slots=True)
class ImageExtractionResult:
    """Immutable outcome of scanning one PDF for embedded images."""

    assets: tuple[ImageAsset, ...] = ()
    placements: tuple[ImagePlacement, ...] = ()
    page_count: int = 0

    @property
    def asset_count(self) -> int:
        """Number of deduplicated image assets."""
        return len(self.assets)

    @property
    def placement_count(self) -> int:
        """Total number of image placements."""
        return len(self.placements)

    def asset_by_id(self, asset_id: str) -> ImageAsset:
        """Return the asset with ``asset_id`` (``KeyError`` when absent)."""
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise KeyError(f"unknown image asset {asset_id!r}")

    def placements_for_page(
        self, page_number: int
    ) -> tuple[ImagePlacement, ...]:
        """Placements on ``page_number`` in extraction order."""
        return tuple(p for p in self.placements if p.page_number == page_number)


def extract_pdf_images(source: Source) -> ImageExtractionResult:
    """Extract embedded image assets + placements from a PDF."""
    doc = _open_document(source)
    try:
        return _extract_from_document(doc)
    finally:
        _close_if_owned(source, doc)


def _open_document(source: Source) -> pymupdf.Document:
    if isinstance(source, pymupdf.Document):
        return source
    try:
        return pymupdf.open(str(source))
    except Exception as exc:
        raise PDFReadError(f"Failed to open {source!r} as a PDF") from exc


def _close_if_owned(source: Source, doc: pymupdf.Document) -> None:
    if not isinstance(source, pymupdf.Document):
        try:
            doc.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def _extract_from_document(doc: pymupdf.Document) -> ImageExtractionResult:
    """Scan every page of ``doc`` for embedded images."""
    try:
        page_count = doc.page_count
    except Exception as exc:
        raise PDFReadError("Failed to read PDF pages") from exc
    if page_count == 0:
        raise EmptyPDFError("PDF has no pages")
    assets: list[ImageAsset] = []
    asset_by_digest: dict[str, ImageAsset] = {}
    placements: list[ImagePlacement] = []
    order = 0
    for page_index in range(page_count):
        page_number = page_index + 1
        try:
            page = doc[page_index]
        except Exception as exc:
            raise ImageExtractionError(
                f"failed to read page {page_number}"
            ) from exc
        for info in _page_image_infos(page, page_number):
            xref = _info_xref(info, page_number)
            payload = _extract_payload(doc, xref, page_number)
            digest = hashlib.sha256(payload["data"]).hexdigest()
            asset = asset_by_digest.get(digest)
            if asset is None:
                asset = ImageAsset(
                    asset_id=f"image-{len(assets) + 1:03d}",
                    data=payload["data"],
                    mime_type=payload["mime_type"],
                    width=payload["width"],
                    height=payload["height"],
                    extension=payload["extension"],
                    xref=xref,
                    order=len(assets),
                )
                assets.append(asset)
                asset_by_digest[digest] = asset
            for bbox in _placement_rects(page, info, page_number):
                placements.append(
                    ImagePlacement(
                        asset_id=asset.asset_id,
                        page_number=page_number,
                        bbox=bbox,
                        order=order,
                        xref=xref,
                    )
                )
                order += 1
    placements.sort(key=lambda p: (p.page_number, p.order))
    return ImageExtractionResult(
        assets=tuple(assets), placements=tuple(placements), page_count=page_count
    )


def _page_image_infos(page: pymupdf.Page, page_number: int) -> list:
    try:
        infos = page.get_images(full=True)
    except Exception as exc:
        raise ImageExtractionError(
            f"failed to list images on page {page_number}"
        ) from exc
    unique: list = []
    seen: set[int] = set()
    for info in infos or ():
        xref = _safe_info_xref(info)
        if xref is None:
            raise ImageExtractionError(
                f"image entry on page {page_number} has no usable xref"
            )
        if xref in seen:
            continue
        seen.add(xref)
        unique.append(info)
    return unique


def _safe_info_xref(info: object) -> int | None:
    if isinstance(info, dict):
        xref = info.get("xref")
    elif isinstance(info, (tuple, list)) and len(info) > 0:
        xref = info[0]
    else:
        return None
    if isinstance(xref, bool) or not isinstance(xref, int) or xref <= 0:
        return None
    return xref


def _extract_payload(
    doc: pymupdf.Document, xref: int, page_number: int
) -> dict:
    """Extract and label the binary payload of image object ``xref``."""
    try:
        raw = doc.extract_image(xref)
    except Exception as exc:
        raise ImageExtractionError(
            f"failed to extract image xref {xref} on page {page_number}"
        ) from exc
    if not isinstance(raw, dict):
        raise ImageExtractionError(
            f"image xref {xref} on page {page_number} returned no data"
        )
    data = raw.get("image")
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) == 0:
        raise ImageExtractionError(
            f"image xref {xref} on page {page_number} has an empty payload"
        )
    data = bytes(data)
    extension, mime_type = _mime_for(raw.get("ext"), data)
    width = raw.get("width", 0)
    height = raw.get("height", 0)
    if isinstance(width, bool) or not isinstance(width, int):
        width = 0
    if isinstance(height, bool) or not isinstance(height, int):
        height = 0
    return {
        "data": data,
        "extension": extension,
        "mime_type": mime_type,
        "width": width,
        "height": height,
    }


def _placement_rects(
    page: pymupdf.Page, info: object, page_number: int
) -> list[tuple[float, float, float, float]]:
    """Return sorted placement rects of one image on ``page``."""
    try:
        rects = page.get_image_rects(info)  # type: ignore[arg-type]
    except Exception as exc:
        raise ImageExtractionError(
            f"failed to read placement of image on page {page_number}"
        ) from exc
    boxes: list[tuple[float, float, float, float]] = []
    for rect in rects or ():
        try:
            x0, y0, x1, y1 = (
                float(rect.x0),
                float(rect.y0),
                float(rect.x1),
                float(rect.y1),
            )
        except Exception:
            continue
        if not (x1 >= x0 and y1 >= y0):
            continue
        boxes.append((x0, y0, x1, y1))
    boxes.sort(key=lambda b: (b[1], b[0], b[2], b[3]))
    return boxes


def _info_xref(info: object, page_number: int) -> int:
    xref = _safe_info_xref(info)
    if xref is None:
        raise ImageExtractionError(
            f"image entry on page {page_number} has no usable xref"
        )
    return xref

