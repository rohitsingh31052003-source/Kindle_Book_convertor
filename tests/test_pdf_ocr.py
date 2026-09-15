"""Deterministic OCR tests (Milestone 3.3).

These tests exercise the application-level OCR layer and never invoke a real
OCR engine or external OCR executable: a fake/injected engine returns
predetermined text. Synthetic raster images and PDFs are generated on the fly
with PyMuPDF; no internet, downloads, or fixture files.

The optional layer that talks to the real Tesseract executable lives in
``tests/test_pdf_ocr_tesseract.py``.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    DEFAULT_OCR_LANGUAGE,
    OCREngine,
    OCRError,
    OCREngineUnavailableError,
    OCRResult,
    RenderedPage,
    render_page,
    render_pages,
)
from kindle_converter.pdf.ocr import TesseractEngine, ocr_page, ocr_pages

# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #

WIDTH = 595
HEIGHT = 842


def _pixmap(width: int = 64, height: int = 48) -> pymupdf.Pixmap:
    """A small solid-color RGB pixmap (valid, deterministic raster)."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pixmap.set_rect(pixmap.irect, (40, 60, 80, 255))
    return pixmap


_MISSING = object()


def _rendered(
    page_number: int, image: pymupdf.Pixmap | None = _MISSING
) -> RenderedPage:
    if image is _MISSING:
        image = _pixmap()
    return RenderedPage(page_number=page_number, dpi=300, image=image)


class _RecordingEngine:
    """Duck-typed fake OCREngine: records every image and returns fixed text."""

    def __init__(self, text: str = "recognized page text") -> None:
        self.text = text
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        return self.text


# --------------------------------------------------------------------------- #
# OCRResult representation
# --------------------------------------------------------------------------- #


class TestOCRResult:
    def test_fields_and_values(self) -> None:
        result = OCRResult(page_number=3, text="hello")
        assert result.page_number == 3
        assert result.text == "hello"

    def test_frozen_and_slots(self) -> None:
        result = OCRResult(page_number=1, text="x")
        with pytest.raises(AttributeError):
            result.text = "changed"  # type: ignore[misc]
        assert OCRResult.__slots__ == ("page_number", "text")

    def test_equality(self) -> None:
        assert OCRResult(1, "a") == OCRResult(1, "a")
        assert OCRResult(1, "a") != OCRResult(2, "a")
        assert OCRResult(1, "a") != OCRResult(1, "b")

    def test_repr(self) -> None:
        assert "OCRResult" in repr(OCRResult(2, "text"))


# --------------------------------------------------------------------------- #
# OCR input/output contract
# --------------------------------------------------------------------------- #


class TestOCRContract:
    def test_ocr_page_returns_ocr_result(self) -> None:
        engine = _RecordingEngine()
        result = ocr_page(_rendered(1), engine)
        assert isinstance(result, OCRResult)

    def test_engine_receives_the_rendered_image(self) -> None:
        rendered = _rendered(1)
        engine = _RecordingEngine()
        ocr_page(rendered, engine)
        assert len(engine.calls) == 1
        assert engine.calls[0] is rendered.image

    def test_page_number_is_preserved(self) -> None:
        result = ocr_page(_rendered(17), _RecordingEngine())
        assert result.page_number == 17

    def test_engine_text_returned_verbatim_no_cleanup(self) -> None:
        # M3.3 performs no OCR cleanup: the engine's raw output (whitespace,
        # line breaks, even the trailing newline) must survive untouched.
        raw = "Line one.\n\nline two. \t end\n"
        result = ocr_page(_rendered(1), _RecordingEngine(text=raw))
        assert result.text == raw

    def test_engine_may_obey_the_engine_protocol(self) -> None:
        # Validate the declared Protocol is importable and structurally
        # satisfied by an object that provides recognize(image) -> str.
        class Engine:
            def recognize(self, image: pymupdf.Pixmap) -> str:
                return "protocol text"

        result = ocr_page(_rendered(2), Engine())
        assert result.text == "protocol text"

    def test_engine_typing_is_duck_typed(self) -> None:
        # OCREngine is a Protocol; runtime checks use duck typing, so an
        # object with a recognize method is accepted without isinstance.
        engine = type("Engine", (), {"recognize": lambda self, image: "duck"})()
        assert ocr_page(_rendered(1), engine).text == "duck"


# --------------------------------------------------------------------------- #
# Engine injection
# --------------------------------------------------------------------------- #


class TestEngineInjection:
    def test_engines_are_injected_per_call(self) -> None:
        first = ocr_page(_rendered(1), _RecordingEngine(text="one"))
        second = ocr_page(_rendered(1), _RecordingEngine(text="two"))
        assert first.text == "one"
        assert second.text == "two"

    def test_single_engine_reused_across_pages(self) -> None:
        engine = _RecordingEngine()
        pages = [_rendered(n) for n in (1, 2, 3)]
        results = [ocr_page(page, engine) for page in pages]
        assert [r.page_number for r in results] == [1, 2, 3]
        assert len(engine.calls) == 3
        assert engine.calls[0] is pages[0].image
        assert engine.calls[2] is pages[2].image

    def test_no_hidden_default_engine(self) -> None:
        # The engine argument is required: there is no implicit global engine.
        with pytest.raises(TypeError):
            ocr_page(_rendered(1))  # type: ignore[call-arg]

    def test_missing_engine_rejected(self) -> None:
        with pytest.raises(TypeError):
            ocr_page(_rendered(1), engine=None)  # type: ignore[arg-type]

    def test_non_engine_object_rejected(self) -> None:
        with pytest.raises(TypeError):
            ocr_page(_rendered(1), object())  # type: ignore[arg-type]

    def test_non_callable_recognize_rejected(self) -> None:
        engine = type("Engine", (), {"recognize": "not callable"})()
        with pytest.raises(TypeError):
            ocr_page(_rendered(1), engine)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Multi-page OCR
# --------------------------------------------------------------------------- #


class TestMultiPageOCR:
    def test_preserves_order_and_page_numbers(self) -> None:
        pages = [_rendered(1), _rendered(2), _rendered(3)]
        results = list(ocr_pages(pages, _RecordingEngine()))
        assert [r.page_number for r in results] == [1, 2, 3]

    def test_sequential_processing_in_given_order(self) -> None:
        engine = _RecordingEngine()
        pages = [_rendered(1), _rendered(2), _rendered(3)]
        list(ocr_pages(pages, engine))
        assert engine.calls == [pages[0].image, pages[1].image, pages[2].image]

    def test_accepts_a_generator(self) -> None:
        pages = (_rendered(n) for n in (4, 5))
        results = list(ocr_pages(pages, _RecordingEngine()))
        assert [r.page_number for r in results] == [4, 5]

    def test_is_lazy_generator(self) -> None:
        # ocr_pages returns a generator; nothing is OCR'd before iteration.
        consumed: list[int] = []

        class TrackingEngine(_RecordingEngine):
            def recognize(self, image: pymupdf.Pixmap) -> str:
                consumed.append(image.width)
                return self.text

        stream = ocr_pages([_rendered(1), _rendered(2)], TrackingEngine())
        assert consumed == []  # not consumed yet
        first = next(stream)
        assert first.page_number == 1
        assert consumed == [64]
        list(stream)
        assert consumed == [64, 64]

    def test_per_page_result_text(self) -> None:
        engine = _RecordingEngine(text="per-page")
        results = list(ocr_pages([_rendered(1), _rendered(2)], engine))
        assert [r.text for r in results] == ["per-page", "per-page"]


# --------------------------------------------------------------------------- #
# Renderer/OCR boundary (M3.2 -> M3.3)
# --------------------------------------------------------------------------- #


def _synthetic_pdf(path, pages: int = 2) -> str:
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        page.insert_text(
            (72, 100 + n * 45), f"Page {n + 1} body text line"
        )
    doc.save(str(path))
    doc.close()
    return str(path)


class TestRendererBoundary:
    """A RenderedPage from the M3.2 renderer flows straight into OCR."""

    def test_ocr_page_consumes_render_page_output(self, tmp_path) -> None:
        path = _synthetic_pdf(tmp_path / "book.pdf", pages=1)
        rendered = render_page(path, 1, dpi=144)
        engine = _RecordingEngine(text="boundary")
        result = ocr_page(rendered, engine)
        assert isinstance(result, OCRResult)
        assert result.page_number == 1
        assert result.text == "boundary"
        assert len(engine.calls) == 1
        assert engine.calls[0] is rendered.image
        assert isinstance(rendered.image, pymupdf.Pixmap)

    def test_ocr_pages_consumes_render_pages(self, tmp_path) -> None:
        path = _synthetic_pdf(tmp_path / "book.pdf", pages=3)
        results = list(ocr_pages(render_pages(path, dpi=72), _RecordingEngine()))
        assert [r.page_number for r in results] == [1, 2, 3]
        assert all(r.text == "recognized page text" for r in results)

    def test_ocr_never_reopens_or_closes_the_pdf(self, tmp_path) -> None:
        # Feed an already-open document through render_pages -> ocr_pages.
        # The document must stay owned/open by the caller throughout: OCR
        # consumes rasters, it never touches the PDF.
        path = _synthetic_pdf(tmp_path / "book.pdf", pages=2)
        doc = pymupdf.open(path)
        try:
            page_count_before = doc.page_count
            results = list(ocr_pages(render_pages(doc, dpi=72), _RecordingEngine()))
            assert [r.page_number for r in results] == [1, 2]
            assert doc.page_count == page_count_before
            assert not doc.is_closed
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestValidation:
    @pytest.mark.parametrize("bad", [None, "not a page", 7, object(), [1, 2]])
    def test_non_rendered_page_rejected(self, bad) -> None:
        with pytest.raises(TypeError):
            ocr_page(bad, _RecordingEngine())  # type: ignore[arg-type]

    def test_missing_image_rejected(self) -> None:
        rendered = _rendered(1, image=None)
        with pytest.raises(ValueError, match="no image"):
            ocr_page(rendered, _RecordingEngine())

    def test_non_pixmap_image_rejected(self) -> None:
        rendered = RenderedPage(page_number=1, dpi=300, image=object())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ocr_page(rendered, _RecordingEngine())

    def test_invalid_image_dimensions_rejected(self) -> None:
        for pixmap in (
            _pixmap(width=0, height=10),
            _pixmap(width=10, height=0),
        ):
            with pytest.raises(ValueError, match="dimensions"):
                ocr_page(_rendered(1, image=pixmap), _RecordingEngine())

    def test_engine_returning_non_str_rejected(self) -> None:
        engine = type("Engine", (), {"recognize": lambda self, image: b"bytes"})()
        with pytest.raises(TypeError, match="expected str"):
            ocr_page(_rendered(1), engine)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [None, 7, "iterable of pages"])
    def test_ocr_pages_non_iterable_rejected(self, bad) -> None:
        with pytest.raises(TypeError):
            list(ocr_pages(bad, _RecordingEngine()))  # type: ignore[arg-type]

    def test_ocr_pages_bad_element_rejected(self) -> None:
        with pytest.raises(TypeError):
            list(ocr_pages([object()], _RecordingEngine()))  # type: ignore[list-item]

    def test_ocr_pages_missing_engine_rejected_eagerly(self) -> None:
        with pytest.raises(TypeError):
            list(ocr_pages([_rendered(1)], None))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Error propagation
# --------------------------------------------------------------------------- #


class _RaisingEngine:
    def __init__(self, exc: Exception, fail_on_call: int = 1) -> None:
        self.exc = exc
        self.fail_on_call = fail_on_call
        self.calls = 0

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls += 1
        if self.calls >= self.fail_on_call:
            raise self.exc
        return "ok"


class TestErrorPropagation:
    def test_engine_failure_gets_page_context(self) -> None:
        engine = _RaisingEngine(OCRError("engine boom"))
        with pytest.raises(OCRError, match="page 3") as excinfo:
            ocr_page(_rendered(3), engine)
        assert "engine boom" in str(excinfo.value)

    def test_unavailable_error_subtype_preserved(self) -> None:
        engine = _RaisingEngine(OCREngineUnavailableError("no binary"))
        with pytest.raises(OCREngineUnavailableError) as excinfo:
            ocr_page(_rendered(4), engine)
        assert "page 4" in str(excinfo.value)

    def test_unknown_exception_propagates_unchanged(self) -> None:
        engine = _RaisingEngine(RuntimeError("raw engine crash"))
        with pytest.raises(RuntimeError, match="raw engine crash") as excinfo:
            ocr_page(_rendered(1), engine)
        assert type(excinfo.value) is RuntimeError

    def test_multi_page_failure_preserves_page_context(self) -> None:
        engine = _RaisingEngine(OCRError("bad page"), fail_on_call=2)
        with pytest.raises(OCRError, match="page 2") as excinfo:
            list(ocr_pages([_rendered(1), _rendered(2), _rendered(3)], engine))
        assert "bad page" in str(excinfo.value)
        assert engine.calls == 2

    def test_multi_page_failure_is_not_skipped(self) -> None:
        engine = _RaisingEngine(OCRError("fail"))
        with pytest.raises(OCRError):
            list(ocr_pages([_rendered(1), _rendered(2), _rendered(3)], engine))
        # Processing stopped at the first failure: page 3 was never reached.
        assert engine.calls == 1


# --------------------------------------------------------------------------- #
# TesseractEngine configuration (argument validation only: no OCR execution)
# --------------------------------------------------------------------------- #


class TestTesseractEngineConfig:
    def test_default_language_constant(self) -> None:
        assert DEFAULT_OCR_LANGUAGE == "eng"

    @pytest.mark.parametrize("language", [None, "", "   ", True, 5, b"eng", ["eng"]])
    def test_invalid_language_rejected(self, language) -> None:
        with pytest.raises(ValueError):
            TesseractEngine(language=language)  # type: ignore[arg-type]

    @pytest.mark.parametrize("cmd", [True, 5, ["tesseract"], {"a": 1}, object()])
    def test_invalid_tesseract_cmd_rejected(self, cmd) -> None:
        with pytest.raises(ValueError):
            TesseractEngine(tesseract_cmd=cmd)  # type: ignore[arg-type]

    def test_validation_precedes_dependency_import(self) -> None:
        # Argument validation must fail cleanly and deterministically even
        # without the optional OCR packages installed. (Validation runs
        # before the pytesseract/Pillow import inside the constructor.)
        with pytest.raises(ValueError):
            TesseractEngine(language="")
        with pytest.raises(ValueError):
            TesseractEngine(tesseract_cmd=12345)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Public API surface
# --------------------------------------------------------------------------- #


class TestPublicAPI:
    def test_ocr_api_is_exported(self) -> None:
        from kindle_converter.pdf import (
            DEFAULT_OCR_LANGUAGE,
            OCREngine,
            OCRError,
            OCREngineUnavailableError,
            OCRResult,
            TesseractEngine,
            ocr_page,
            ocr_pages,
        )

        assert DEFAULT_OCR_LANGUAGE == "eng"
        assert hasattr(OCREngine, "recognize")
        assert ocr_page is not None
        assert ocr_pages is not None

    def test_internal_helpers_are_not_exported(self) -> None:
        import kindle_converter.pdf as pdf_api
        from kindle_converter.pdf import ocr as ocr_module

        for name in (
            "_validate_rendered_page",
            "_validate_image",
            "_validate_engine",
            "_validate_language",
            "_validate_tesseract_cmd",
            "_require_ocr_dependencies",
            "_require_tesseract_executable",
        ):
            assert name not in pdf_api.__all__
            assert hasattr(ocr_module, name)  # still importable internally

    def test_exception_hierarchy(self) -> None:
        assert issubclass(OCREngineUnavailableError, OCRError)
        assert issubclass(OCRError, Exception)

    def test_ocr_module_is_importable_without_extra(self) -> None:
        # Importing the OCR submodule must not require pytesseract/Pillow at
        # import time (they are optional; imported lazily by the engine).
        import kindle_converter.pdf.ocr  # noqa: F401

        assert True

    def test_rendered_page_is_still_exported_by_package(self) -> None:
        import kindle_converter.pdf as pdf_api

        assert pdf_api.RenderedPage is RenderedPage