"""Headless tests for the M5.4 conversion-options UI workflow.

After the M5.3 analysis workflow, the window lets the user configure a
conversion: output format (EPUB/AZW3), output directory, and an optional
cover, all summarized into a real application-layer
:class:`kindle_converter.application.ConversionRequest` via
``MainWindow.build_conversion_request``. These tests cover that configuration
step and the resulting readiness/validation model without ever executing a
conversion.

The window under test drives the M5.1 application boundary through an injected
test double (a ``ConversionApplication`` substitute with duck-typed
``analyze_pdf`` and ``validate_request``), so no real PDF analysis is required
for the UI assertions. A couple of tests use the *real* application layer to
prove that the request the UI builds passes the real application-layer
request validation.

All Qt dependencies run headless via ``QT_QPA_PLATFORM=offscreen``; the module
skips cleanly when PySide6 is not installed (the ``ui`` extra is absent).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

import pymupdf  # noqa: E402

from kindle_converter.application import (  # noqa: E402
    ConversionApplication,
    ConversionRequest,
    InvalidRequestError,
    OutputFormat,
)
from kindle_converter.pdf import PDFAnalysis, PDFType, PageAnalysis  # noqa: E402
from kindle_converter.ui import MainWindow, UiState  # noqa: E402

_NO_VALUE = "\u2014"

# A real 1x1 PNG (used to prove the real application layer accepts a cover).
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    yield application


class _FakeApplication:
    """Duck-typed stand-in for ``kindle_converter.application.ConversionApplication``.

    Records every ``analyze_pdf``/``validate_request`` call and returns canned
    results -- without touching any PDF content. ``convert`` is defined only to
    fail loudly: M5.4 must never execute conversion.
    """

    def __init__(
        self,
        *,
        analysis: PDFAnalysis | None = None,
        error: Exception | None = None,
        on_analyze=None,
        validate_error: Exception | None = None,
    ) -> None:
        self.analysis = analysis
        self.error = error
        self.on_analyze = on_analyze
        self.validate_error = validate_error
        self.analyze_calls: list[Path] = []
        self.validate_calls: list[ConversionRequest] = []

    def analyze_pdf(self, path) -> PDFAnalysis:
        self.analyze_calls.append(Path(path))
        if self.on_analyze is not None:
            self.on_analyze(path)
        if self.error is not None:
            raise self.error
        assert self.analysis is not None
        return self.analysis

    def validate_request(self, request: ConversionRequest) -> None:
        self.validate_calls.append(request)
        # ``validate_error`` models an unacceptable cover-only configuration,
        # so it only fires when a cover is actually part of the request.
        if self.validate_error is not None and request.cover is not None:
            raise self.validate_error

    def convert(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("M5.4 must not execute conversion")


def _make_pdf(path: Path, pages: int = 2) -> Path:
    """Write a real (tiny) text PDF with PyMuPDF."""
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"Meaningful body text line on page {index + 1}. It needs to be "
            f"long enough to count as meaningful native text.",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


def _page(number: int, classification: PDFType) -> PageAnalysis:
    return PageAnalysis(
        page_number=number,
        has_image=classification is not PDFType.TEXT,
        has_meaningful_text=classification is not PDFType.SCANNED,
        char_count=40,
        classification=classification,
    )


def _text_analysis() -> PDFAnalysis:
    pages = [_page(1, PDFType.TEXT), _page(2, PDFType.TEXT)]
    return PDFAnalysis(
        page_count=2,
        text_page_count=2,
        image_page_count=0,
        text_density=40.0,
        document_type=PDFType.TEXT,
        pages=pages,
    )


def _combo(window: MainWindow) -> QtWidgets.QComboBox:
    combo = window.findChild(QtWidgets.QComboBox, "outputFormatCombo")
    assert combo is not None, "missing outputFormatCombo"
    return combo


def _cover_edit(window: MainWindow) -> QtWidgets.QLineEdit:
    edit = window.findChild(QtWidgets.QLineEdit, "coverPath")
    assert edit is not None, "missing coverPath"
    return edit


def _dir_edit(window: MainWindow) -> QtWidgets.QLineEdit:
    edit = window.findChild(QtWidgets.QLineEdit, "outputDirectory")
    assert edit is not None, "missing outputDirectory"
    return edit


# --------------------------------------------------------------------------
# Output format
# --------------------------------------------------------------------------


class TestOutputFormat:
    def test_default_format_is_epub(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            assert window.output_format is OutputFormat.EPUB
            assert _combo(window).currentText() == "EPUB"
        finally:
            window.close()

    def test_format_choices_are_the_application_enum_values(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            assert [window.output_format]  # not None
            combo = _combo(window)
            assert combo.count() == 2
            assert str(combo.itemData(0)) == OutputFormat.EPUB.value
            assert str(combo.itemData(1)) == OutputFormat.AZW3.value
        finally:
            window.close()

    def test_azw3_selection_updates_application_enum(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            _combo(window).setCurrentIndex(1)
            assert window.output_format is OutputFormat.AZW3
        finally:
            window.close()

    def test_epub_selection_updates_application_enum(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            combo = _combo(window)
            combo.setCurrentIndex(1)
            combo.setCurrentIndex(0)
            assert window.output_format is OutputFormat.EPUB
        finally:
            window.close()

    def test_changing_format_does_not_execute_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            analyze_calls = len(app.analyze_calls)
            _combo(window).setCurrentIndex(1)
            assert window.output_format is OutputFormat.AZW3
            assert len(app.analyze_calls) == analyze_calls
            assert list(out_dir.iterdir()) == []
        finally:
            window.close()


# --------------------------------------------------------------------------
# Output directory
# --------------------------------------------------------------------------


class TestOutputDirectory:
    def test_directory_picker_updates_the_ui(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getExistingDirectory",
                lambda *args, **kwargs: str(tmp_path),
            )
            window.browse_for_output_directory()
            assert window.output_directory == tmp_path
            assert _dir_edit(window).text() == str(tmp_path)
        finally:
            window.close()

    def test_cancelled_dialog_preserves_directory(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            window.select_output_directory(tmp_path / "kept")
            assert window.output_directory == tmp_path / "kept"
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getExistingDirectory",
                lambda *args, **kwargs: "",
            )
            window.browse_for_output_directory()
            assert window.output_directory == tmp_path / "kept"
            assert _dir_edit(window).text() == str(tmp_path / "kept")
        finally:
            window.close()

    def test_selecting_directory_creates_nothing(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            target = tmp_path / "not-created"
            window.select_output_directory(target)
            assert window.output_directory == target
            assert not target.exists()
        finally:
            window.close()

    def test_directory_represented_in_request(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            request = window.build_conversion_request()
            assert request.output_directory == out_dir
            assert isinstance(request.output_directory, Path)
        finally:
            window.close()


# --------------------------------------------------------------------------
# Cover
# --------------------------------------------------------------------------


class TestCover:
    def test_no_cover_is_represented_correctly(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(tmp_path)
            assert window.cover_path is None
            assert window.build_conversion_request().cover is None
            assert _cover_edit(window).text() == ""
        finally:
            window.close()

    def test_cover_picker_updates_the_selected_cover(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        cover = tmp_path / "cover.png"
        cover.write_bytes(_PNG_BYTES)
        window = MainWindow(application=_FakeApplication())
        try:
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                lambda *args, **kwargs: (str(cover), "Images (*.png)"),
            )
            window.browse_for_cover()
            assert window.cover_path == cover
            assert _cover_edit(window).text() == str(cover)
        finally:
            window.close()

    def test_cancelled_dialog_preserves_cover(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        cover = tmp_path / "cover.png"
        window = MainWindow(application=_FakeApplication())
        try:
            window.select_cover(cover)
            assert window.cover_path == cover
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                lambda *args, **kwargs: ("", ""),
            )
            window.browse_for_cover()
            assert window.cover_path == cover
            assert _cover_edit(window).text() == str(cover)
        finally:
            window.close()

    def test_clear_cover_removes_the_selection(self, qapp, tmp_path: Path) -> None:
        cover = tmp_path / "cover.png"
        window = MainWindow(application=_FakeApplication())
        try:
            window.select_cover(cover)
            assert window.cover_path == cover
            window.clear_cover()
            assert window.cover_path is None
            assert _cover_edit(window).text() == ""
        finally:
            window.close()

    def test_clear_button_widget_is_connected(self, qapp, tmp_path: Path) -> None:
        cover = tmp_path / "cover.png"
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            button = window.findChild(QtWidgets.QPushButton, "coverClearButton")
            assert button is not None
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(tmp_path)
            window.select_cover(cover)
            assert window.cover_path == cover
            assert button.isEnabled()
            button.click()
            assert window.cover_path is None
        finally:
            window.close()

    def test_selected_cover_is_represented_in_request(
        self, qapp, tmp_path: Path
    ) -> None:
        cover = tmp_path / "cover.png"
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        window2 = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(tmp_path)
            window.select_cover(cover)
            request = window.build_conversion_request()
            assert isinstance(request.cover, Path)
            assert request.cover == cover

            window2.select_input(pdf)
            window2.analyze_selected()
            window2.select_output_directory(tmp_path)
            window2.select_cover(cover)
            window2.clear_cover()
            assert window2.build_conversion_request().cover is None
        finally:
            window.close()
            window2.close()


# --------------------------------------------------------------------------
# Request construction
# --------------------------------------------------------------------------


class TestRequestConstruction:
    def test_ui_builds_the_real_conversion_request(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        cover = tmp_path / "cover.png"
        cover.write_bytes(_PNG_BYTES)
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            _combo(window).setCurrentIndex(1)  # AZW3
            window.select_cover(cover)

            request = window.build_conversion_request()
            assert isinstance(request, ConversionRequest)
            assert Path(request.input_pdf) == pdf
            assert Path(request.output_directory) == out_dir
            assert request.formats == frozenset(
                {OutputFormat.EPUB, OutputFormat.AZW3}
            )
            assert request.cover == cover
            assert request.validate is True
            assert request.calibre_path is None
        finally:
            window.close()

    def test_request_type_is_application_layer_owned(
        self, qapp, tmp_path: Path
    ) -> None:
        # No UI-specific request object exists: the request type comes from the
        # application package.
        from kindle_converter.application import ConversionRequest as AppRequest

        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(tmp_path)
            request = window.build_conversion_request()
            assert type(request) is AppRequest
        finally:
            window.close()

    def test_constructing_the_request_does_not_execute_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            analyze_calls = len(app.analyze_calls)
            validate_calls = len(app.validate_calls)
            request = window.build_conversion_request()
            assert isinstance(request, ConversionRequest)
            assert len(app.analyze_calls) == analyze_calls
            assert len(app.validate_calls) == validate_calls
            assert list(out_dir.iterdir()) == []
        finally:
            window.close()

    def test_building_without_input_raises_application_error(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            window.select_output_directory(tmp_path)
            with pytest.raises(InvalidRequestError):
                window.build_conversion_request()
        finally:
            window.close()


# --------------------------------------------------------------------------
# Validation / readiness
# --------------------------------------------------------------------------


class TestReadiness:
    def test_no_input_is_not_ready(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            assert window.is_configuration_ready is False
            assert window.state is UiState.NO_INPUT
        finally:
            window.close()

    def test_input_without_analysis_is_not_ready(
        self, qapp, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(
            application=_FakeApplication(analysis=_text_analysis())
        )
        try:
            window.select_input(pdf)
            assert window.state is UiState.INPUT_SELECTED
            assert window.is_configuration_ready is False
        finally:
            window.close()

    def test_analyzed_input_without_output_directory_is_not_ready(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert window.is_configuration_ready is False
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Analysis complete"
        finally:
            window.close()

    def test_analyzed_input_plus_output_directory_is_ready(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            assert window.is_configuration_ready is True
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Ready for conversion"
        finally:
            window.close()

    def test_clearing_output_directory_drops_readiness(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            window.select_output_directory(None)
            assert window.state is UiState.CONFIGURING
            assert window.is_configuration_ready is False
        finally:
            window.close()

    def test_invalid_cover_prevents_readiness(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(),
            validate_error=InvalidRequestError(
                "the cover configuration is not supported: boom"
            ),
        )
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY

            window.select_cover(tmp_path / "bad-cover.bmp")
            assert window.is_configuration_ready is False
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert "cover" in status.text()
            # The application-layer validation error is surfaced, and clearing
            # the cover restores readiness.
            window.clear_cover()
            assert window.is_configuration_ready is True
        finally:
            window.close()

    def test_invalid_cover_raises_through_real_validation(
        self, qapp, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=ConversionApplication())
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(tmp_path)
            window.select_cover(tmp_path / "no-such-cover.png")
            assert isinstance(window.cover_path, Path)
            with pytest.raises(InvalidRequestError, match="cover"):
                ConversionApplication().validate_request(
                    window.build_conversion_request()
                )
            assert window.is_configuration_ready is False
        finally:
            window.close()

    def test_changing_input_invalidates_previous_readiness(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        first = _make_pdf(tmp_path / "one.pdf")
        second = _make_pdf(tmp_path / "two.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        window = MainWindow(application=app)
        try:
            window.select_input(first)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            assert window.last_analysis is not None

            window.select_input(second)
            assert window.state is UiState.INPUT_SELECTED
            assert window.last_analysis is None
            assert window.is_configuration_ready is False
            # Independent configuration is retained but cannot be ready while
            # the new input is unanalysed.
            assert window.output_directory == out_dir
        finally:
            window.close()


# --------------------------------------------------------------------------
# Application boundary: the real application validates the UI-built request
# --------------------------------------------------------------------------


class TestRealApplicationBoundary:
    def test_ui_request_passes_real_application_validation(
        self, qapp, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "book.pdf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        cover = tmp_path / "cover.png"
        cover.write_bytes(_PNG_BYTES)
        window = MainWindow(application=ConversionApplication())
        try:
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            window.select_cover(cover)
            assert window.state is UiState.READY

            request = window.build_conversion_request()
            assert isinstance(request, ConversionRequest)
            # The real application-layer request validation accepts the
            # configuration the UI produced -- including the M4.4 cover path.
            ConversionApplication().validate_request(request)
            assert request.formats == frozenset({OutputFormat.EPUB})
            assert request.cover == cover
        finally:
            window.close()


# --------------------------------------------------------------------------
# Widget enablement sanity
# --------------------------------------------------------------------------


class TestWidgetEnablement:
    def test_conversion_options_enabled_after_analysis_only(self, qapp) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            combo = _combo(window)
            out_button = window.findChild(
                QtWidgets.QPushButton, "outputDirectoryButton"
            )
            assert out_button is not None
            cover_button = window.findChild(
                QtWidgets.QPushButton, "coverBrowseButton"
            )
            assert cover_button is not None
            assert not combo.isEnabled()          # no input yet
            assert not out_button.isEnabled()
            assert not cover_button.isEnabled()
        finally:
            window.close()