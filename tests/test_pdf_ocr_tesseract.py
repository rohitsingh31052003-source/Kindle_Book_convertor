"""Optional Tesseract integration tests (Milestone 3.3).

These tests run the real Tesseract OCR executable through the
:class:`TesseractEngine`. They are **not** part of the deterministic core
suite and are skipped cleanly when either prerequisite is missing:

* the optional Python wrappers (``pytesseract``/``Pillow``, the ``ocr``
  extra) are not installed, or
* the Tesseract executable cannot be discovered.

Executable discovery (mirrors the engine): the ``TESSERACT_CMD``
environment variable takes precedence, otherwise the ``tesseract`` command
is looked up on ``PATH``. Nothing is downloaded or installed here.

Enable on a machine without Tesseract on PATH:

    TESSERACT_CMD="C:/path/to/tesseract.exe" python -m pytest

All input images are tiny synthetic PDFs generated on the fly with PyMuPDF:
no internet, no real books, no fixture files.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pymupdf
import pytest

pytest.importorskip("pytesseract")

from kindle_converter.pdf import (  # noqa: E402
    OCRError,
    OCREngineUnavailableError,
    render_page,
    render_pages,
)
from kindle_converter.pdf.ocr import TesseractEngine, ocr_page, ocr_pages  # noqa: E402


def _discover_tesseract() -> str | None:
    """Return an explicit Tesseract executable path, or ``None``."""
    env = os.environ.get("TESSERACT_CMD")
    if env:
        return env
    return shutil.which("tesseract")


@pytest.fixture(scope="module")
def tesseract_cmd() -> str:
    """Path to a usable Tesseract executable; skips the module when absent."""
    path = _discover_tesseract()
    if path is None:
        pytest.skip(
            "Tesseract executable not found: set TESSERACT_CMD or add "
            "tesseract to PATH to run the OCR integration tests"
        )
    return path


WIDTH = 595
HEIGHT = 842


def _synthetic_pdf(path: Path, lines: dict[int, str]) -> str:
    """One-page-ish synthetic PDF; ``lines`` maps page number -> text."""
    doc = pymupdf.open()
    for page_number in sorted(lines):
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        page.insert_text(
            (72, 120),
            lines[page_number],
            fontname="helv",
            fontsize=28,
        )
    doc.save(str(path))
    doc.close()
    return str(path)


# --------------------------------------------------------------------------- #
# Engine construction / unavailability (deterministic, no real binary needed)
# --------------------------------------------------------------------------- #


class TestEngineUnavailable:
    def test_missing_explicit_executable_raises_unavailable(self, tmp_path) -> None:
        missing = tmp_path / "no_such_tesseract.exe"
        with pytest.raises(OCREngineUnavailableError, match="not found"):
            TesseractEngine(tesseract_cmd=str(missing))

    def test_missing_executable_error_message_is_actionable(self, tmp_path) -> None:
        with pytest.raises(OCREngineUnavailableError) as excinfo:
            TesseractEngine(tesseract_cmd=str(tmp_path / "missing.exe"))
        text = str(excinfo.value)
        assert "tesseract_cmd" in text and "install" in text.lower()

    def test_unrunnable_executable_raises_ocr_error(self, tmp_path) -> None:
        # A file that exists but is not an executable: construction succeeds
        # (the path exists) and the failure surfaces when OCR actually runs.
        fake_exe = tmp_path / "not_an_executable.txt"
        fake_exe.write_text("this is not a tesseract executable", encoding="ascii")
        engine = TesseractEngine(tesseract_cmd=str(fake_exe))
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 32, 32))
        pix.set_rect(pix.irect, (255, 255, 255, 255))
        with pytest.raises(OCRError):
            engine.recognize(pix)
        # pytesseract exposes the executable path only as a module global;
        # the engine pins it deterministically (documented behavior).
        import pytesseract

        assert pytesseract.pytesseract.tesseract_cmd == str(fake_exe)


# --------------------------------------------------------------------------- #
# Real recognition (requires a working Tesseract executable)
# --------------------------------------------------------------------------- #


class TestRealRecognition:
    def test_single_page_recognizes_known_text(self, tmp_path, tesseract_cmd) -> None:
        path = _synthetic_pdf(
            tmp_path / "one.pdf", {1: "The quick brown fox jumps over the lazy dog"}
        )
        rendered = render_page(path, 1, dpi=300)
        engine = TesseractEngine(language="eng", tesseract_cmd=tesseract_cmd)
        result = ocr_page(rendered, engine)
        assert result.page_number == 1
        assert isinstance(result.text, str)
        assert len(result.text.strip()) > 0
        assert "quick" in result.text.lower()
        assert "fox" in result.text.lower()

    def test_engine_is_reusable_across_pages(self, tmp_path, tesseract_cmd) -> None:
        # Lifecycle: one engine instance serves every page.
        path = _synthetic_pdf(
            tmp_path / "pages.pdf",
            {1: "First page distinctive word SUNRISE", 2: "Second page word MOONLIGHT"},
        )
        engine = TesseractEngine(language="eng", tesseract_cmd=tesseract_cmd)
        results = list(ocr_pages(render_pages(path, dpi=300), engine))
        assert [r.page_number for r in results] == [1, 2]
        joined = " ".join(r.text.lower() for r in results)
        assert "sunrise" in joined
        assert "moonlight" in joined

    def test_results_carry_raw_text_by_default(self, tmp_path, tesseract_cmd) -> None:
        path = _synthetic_pdf(tmp_path / "raw.pdf", {1: "Alphabetical contents"})
        rendered = render_page(path, 1, dpi=300)
        result = ocr_page(rendered, TesseractEngine(tesseract_cmd=tesseract_cmd))
        # Tesseract typically appends a trailing newline; this raw output is
        # deliberately preserved (OCR cleanup is a later milestone).
        assert "alphabetical" in result.text.lower()

    def test_unknown_language_raises_ocr_error(self, tmp_path, tesseract_cmd) -> None:
        path = _synthetic_pdf(tmp_path / "lang.pdf", {1: "Hello"})
        rendered = render_page(path, 1, dpi=300)
        engine = TesseractEngine(
            language="xx_not_a_real_language", tesseract_cmd=tesseract_cmd
        )
        with pytest.raises(OCRError) as excinfo:
            ocr_page(rendered, engine)
        assert "Tesseract" in str(excinfo.value) or "failed" in str(excinfo.value)


class TestPathDiscovery:
    def test_path_discovery_via_path(self, tmp_path, tesseract_cmd) -> None:
        if shutil.which("tesseract") is None:
            pytest.skip("tesseract is not on PATH; TESSERACT_CMD path was used")
        path = _synthetic_pdf(tmp_path / "discover.pdf", {1: "Discovery test"})
        rendered = render_page(path, 1, dpi=300)
        result = ocr_page(rendered, TesseractEngine())
        assert len(result.text.strip()) > 0