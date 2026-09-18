"""Tests for the M5.1 application / pipeline API.

These tests cover the application boundary only: request validation, progress
contract, error boundary, orchestration (with fakes), and the result contract.
They never touch real PDF/OCR/EPUB internals -- orchestration tests monkeypatch
the public pipeline entry points, matching the deterministic, injectable
testing philosophy already established in the project (M3.5/M4.1).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from kindle_converter.application import (
    AZW3OutputError,
    ApplicationError,
    ConversionApplication,
    ConversionProgress,
    ConversionStage,
    ConversionRequest,
    ConversionResult,
    OutputFormat,
    ProgressCallback,
    ValidationFailedError,
)
from kindle_converter.application.errors import (
    ConversionFailedError,
    InvalidRequestError,
    OutputError,
)
from kindle_converter.document import Image
from kindle_converter.epub import (
    AZW3ConversionBackend,
    AZW3ConversionError,
    EPUBValidationError,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
)
from kindle_converter.pdf import (
    EmptyPDFError,
    OCREngineUnavailableError,
    PDFAnalysis,
    PDFReadError,
    PDFType,
    PageAnalysis,
)
from kindle_converter import pipeline as _pipeline

# --------------------------------------------------------------------------
# Fakes / helpers
# --------------------------------------------------------------------------


class _RecordingBackend:
    """An AZW3 backend that records calls and never touches Calibre."""

    def __init__(self, error: AZW3ConversionError | None = None) -> None:
        self.calls: list[tuple[Path, Path]] = []
        self._error = error

    def convert(self, source: Path, destination: Path) -> None:
        self.calls.append((source, destination))
        if self._error is not None:
            raise self._error
        destination.write_bytes(b"AZW3-fake")  # a non-empty artifact


def _analysis(pages: int = 1) -> PDFAnalysis:
    return PDFAnalysis(
        page_count=pages,
        text_page_count=pages,
        image_page_count=0,
        text_density=42.0,
        document_type=PDFType.TEXT,
        pages=[
            PageAnalysis(
                page_number=i + 1,
                has_image=False,
                has_meaningful_text=True,
                char_count=42,
                text="hello",
            )
            for i in range(pages)
        ],
    )


def _request(tmp_path: Path, **overrides) -> ConversionRequest:
    pdf = tmp_path / "book.pdf"
    if not pdf.exists():
        pdf.write_bytes(b"%PDF-1.4 fake")
    defaults = {
        "input_pdf": pdf,
        "output_directory": tmp_path,
        "formats": frozenset({OutputFormat.EPUB}),
    }
    defaults.update(overrides)
    return ConversionRequest(**defaults)
def _valid_validation() -> EPUBValidationResult:
    return EPUBValidationResult(
        package_document="container.xml parsed",
        epub_version="3.0",
        spine_documents=["chapter-00.xhtml"],
        issues=[
            EPUBValidationIssue(
                code="style-hint",
                message="minor styling note",
                severity=EPUBValidationSeverity.WARNING,
                location="chapter-00.xhtml",
            )
        ],
    )


def _invalid_validation() -> EPUBValidationResult:
    return EPUBValidationResult(
        package_document="container.xml parsed",
        epub_version="3.0",
        spine_documents=["chapter-00.xhtml"],
        issues=[
            EPUBValidationIssue(
                code="missing-metadata",
                message="dc:title is missing",
                severity=EPUBValidationSeverity.ERROR,
                location="package.opf",
            )
        ],
    )


class _FakePipeline:
    """Monkeypatch stand-ins for the four public stage functions.

    Each stand-in records its calls and optionally raises, simulating the
    existing M1--M4 stages without touching real PDF/OCR/EPUB code.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        analysis_error: Exception | None = None,
        epub_error: Exception | None = None,
        validation_result: EPUBValidationResult | None = None,
        validation_error: Exception | None = None,
        backend: _RecordingBackend | None = None,
    ) -> None:
        self.analysis_calls: list[Path] = []
        self.epub_calls: list[dict] = []
        self.validation_calls: list[Path] = []
        self.analysis_error = analysis_error
        self.epub_error = epub_error
        self.validation_result = (
            validation_result if validation_result is not None else _valid_validation()
        )
        self.validation_error = validation_error
        self._backend = backend
        self._monkeypatch(monkeypatch, tmp_path)

    def _monkeypatch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import kindle_converter.application.converter as conv

        fake_epub = tmp_path / "book.epub"

        def fake_analyze(source):
            self.analysis_calls.append(Path(source))
            if self.analysis_error is not None:
                raise self.analysis_error
            return _analysis()

        def fake_convert_pdf_to_epub(source, out, *, engine=None, renderer=None, cover=None):
            self.epub_calls.append(
                {
                    "source": Path(source),
                    "out": Path(out),
                    "engine": engine,
                    "renderer": renderer,
                    "cover": cover,
                }
            )
            if self.epub_error is not None:
                raise self.epub_error
            fake_epub.write_bytes(b"PK fake-epub")

        def fake_validate(path):
            self.validation_calls.append(Path(path))
            if self.validation_error is not None:
                raise self.validation_error
            return self.validation_result

        def fake_azw3(source, destination, *, calibre_path=None, backend=None):
            assert backend is self._backend
            return self._backend.convert(Path(source), Path(destination))

        monkeypatch.setattr(conv, "analyze_pdf", fake_analyze)
        monkeypatch.setattr(conv, "convert_pdf_to_epub", fake_convert_pdf_to_epub)
        monkeypatch.setattr(conv, "validate_epub", fake_validate)
        monkeypatch.setattr(conv, "convert_epub_to_azw3", fake_azw3)

    
# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


class TestRequestValidation:
    def test_valid_default_request(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        assert request.formats == frozenset({OutputFormat.EPUB})
        assert request.cover is None
        assert request.validate is True
        assert request.calibre_path is None

    def test_formats_normalized_to_frozenset(self, tmp_path: Path) -> None:
        request = _request(
            tmp_path, formats=[OutputFormat.EPUB, OutputFormat.AZW3]
        )
        assert isinstance(request.formats, frozenset)
        assert request.formats == frozenset({OutputFormat.EPUB, OutputFormat.AZW3})

    def test_azw3_without_epub_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="AZW3 output requires EPUB"):
            _request(tmp_path, formats=frozenset({OutputFormat.AZW3}))

    def test_empty_formats_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="at least one output format"):
            _request(tmp_path, formats=frozenset())

    def test_non_output_format_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="non-OutputFormat"):
            _request(tmp_path, formats={"epub"})

    def test_non_path_input_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="input_pdf must be a filesystem path"):
            _request(tmp_path, input_pdf=12345)  # type: ignore[arg-type]

    def test_non_path_output_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="output_directory must be"):
            _request(tmp_path, output_directory=None)  # type: ignore[arg-type]

    def test_unsupported_cover_type_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="cover must be"):
            _request(tmp_path, cover=object())  # type: ignore[arg-type]

    def test_non_bool_validate_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="validate must be a bool"):
            _request(tmp_path, validate="yes")  # type: ignore[arg-type]

    def test_bad_calibre_type_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="calibre_path must be"):
            _request(tmp_path, calibre_path=42)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Orchestration (fakes stand in for the M1--M4 stages)
# --------------------------------------------------------------------------


class TestOrchestration:
    def test_epub_only_conversion(self, tmp_path: Path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        result = ConversionApplication().convert(_request(tmp_path))
        assert result.azw3_path is None
        assert result.epub_path == tmp_path / "book.epub"
        assert result.epub_path.exists()
        assert result.validation is not None and result.validation.valid
        assert result.page_count == 1
        assert result.document_type is PDFType.TEXT

    def test_azw3_invoked_when_requested(self, tmp_path: Path, monkeypatch) -> None:
        backend = _RecordingBackend()
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        result = ConversionApplication(azw3_backend=backend).convert(request)
        assert len(backend.calls) == 1
        assert backend.calls[0] == (tmp_path / "book.epub", tmp_path / "book.azw3")
        assert result.azw3_path == tmp_path / "book.azw3"
        assert result.azw3_path.exists()
        assert result.outputs == (result.epub_path, result.azw3_path)

    def test_azw3_not_invoked_when_not_requested(self, tmp_path: Path, monkeypatch) -> None:
        backend = _RecordingBackend()
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        ConversionApplication(azw3_backend=backend).convert(_request(tmp_path))
        assert backend.calls == []

    def test_cover_and_seams_passed_through(self, tmp_path: Path, monkeypatch) -> None:
        pipeline = _FakePipeline(monkeypatch, tmp_path)
        import base64

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        cover = Image(data=png, alt_text="cover")
        request = _request(tmp_path, cover=cover)
        engine, renderer = object(), object()
        app = ConversionApplication(engine=engine, renderer=renderer)  # type: ignore[arg-type]
        app.convert(request)
        call = pipeline.epub_calls[0]
        resolved = call["cover"]
        assert resolved.data == cover.data
        assert resolved.alt_text == cover.alt_text
        # The untyped cover was validated/sniffed by load_cover (M4.4 reuse).
        assert resolved.content_type == "image/png"
        assert call["engine"] is engine
        assert call["renderer"] is renderer
        assert call["source"] == tmp_path / "book.pdf"
        assert call["out"] == tmp_path / "book.epub"

    def test_validate_false_skips_validation(self, tmp_path: Path, monkeypatch) -> None:
        pipeline = _FakePipeline(monkeypatch, tmp_path)
        request = _request(tmp_path, validate=False)
        result = ConversionApplication().convert(request)
        assert pipeline.validation_calls == []
        assert result.validation is None
        assert result.valid is None
        assert result.warnings == ()

    def test_stage_functions_called_in_order(self, tmp_path: Path, monkeypatch) -> None:
        backend = _RecordingBackend()
        pipeline = _FakePipeline(monkeypatch, tmp_path, backend=backend)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        ConversionApplication(azw3_backend=backend).convert(request)
        assert len(pipeline.analysis_calls) == 1
        assert len(pipeline.epub_calls) == 1
        assert len(pipeline.validation_calls) == 1
        assert len(backend.calls) == 1

# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class TestProgress:
    def _stages(self, events) -> list[ConversionStage]:
        return [event.stage for event in events]

    def test_progress_sequence_for_full_conversion(self, tmp_path, monkeypatch) -> None:
        backend = _RecordingBackend()
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        events: list[ConversionProgress] = []
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        ConversionApplication(azw3_backend=backend).convert(
            request, progress=events.append
        )
        assert self._stages(events) == [
            ConversionStage.ANALYSIS,
            ConversionStage.ANALYSIS,
            ConversionStage.EPUB,
            ConversionStage.EPUB,
            ConversionStage.VALIDATION,
            ConversionStage.VALIDATION,
            ConversionStage.AZW3,
            ConversionStage.AZW3,
            ConversionStage.COMPLETE,
        ]
        first, last = events[0], events[1]
        assert first.current == 0 and first.total == 0  # start event
        assert last.current == last.total == 1  # finish event

    def test_progress_is_optional(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        result = ConversionApplication().convert(_request(tmp_path))
        assert result.epub_path.exists()  # no callback, no crash

    def test_progress_events_are_well_formed(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        events: list[ConversionProgress] = []
        ConversionApplication().convert(_request(tmp_path), progress=events.append)
        assert events
        for event in events:
            assert isinstance(event.stage, ConversionStage)
            assert isinstance(event.message, str) and event.message
            assert 0 <= event.current <= event.total
        assert events[-1].stage is ConversionStage.COMPLETE

    def test_progress_does_not_change_behavior(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        events: list[ConversionProgress] = []
        without = ConversionApplication().convert(_request(tmp_path))
        with_cb = ConversionApplication().convert(
            _request(tmp_path), progress=events.append
        )
        assert without.epub_path == with_cb.epub_path
        assert without.warnings == with_cb.warnings
        assert without.validation == with_cb.validation
        assert events  # and the callback did see events

    def test_no_complete_event_on_failure(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(
            monkeypatch, tmp_path, analysis_error=PDFReadError("unreadable")
        )
        events: list[ConversionProgress] = []
        with pytest.raises(ConversionFailedError):
            ConversionApplication().convert(_request(tmp_path), progress=events.append)
        assert self._stages(events) == [ConversionStage.ANALYSIS]  # start only


# --------------------------------------------------------------------------
# Result contract
# --------------------------------------------------------------------------


class TestResult:
    def test_warnings_preserved_from_validation(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path, validation_result=_valid_validation())
        result = ConversionApplication().convert(_request(tmp_path))
        assert result.warnings == ("minor styling note",)

    def test_request_is_echoed(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        request = _request(tmp_path)
        result = ConversionApplication().convert(request)
        assert result.request is request
        assert result.requested_formats == request.formats

    def test_output_paths_are_deterministic(self, tmp_path, monkeypatch) -> None:
        backend = _RecordingBackend()
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        result = ConversionApplication(azw3_backend=backend).convert(request)
        assert result.epub_path == tmp_path / "book.epub"
        assert result.azw3_path == tmp_path / "book.azw3"
        assert result.outputs == (tmp_path / "book.epub", tmp_path / "book.azw3")

    def test_result_is_immutable(self, tmp_path, monkeypatch) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        result = ConversionApplication().convert(_request(tmp_path))
        with pytest.raises(Exception):
            result.epub_path = tmp_path / "other.epub"  # type: ignore[misc]

# --------------------------------------------------------------------------
# Error boundary
# --------------------------------------------------------------------------


class TestErrors:
    def test_missing_input_raises_invalid_request(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        request.input_pdf.unlink()
        with pytest.raises(InvalidRequestError, match="does not exist"):
            ConversionApplication().convert(request)

    def test_input_is_directory(self, tmp_path: Path) -> None:
        (tmp_path / "dir.pdf").mkdir()
        with pytest.raises(InvalidRequestError, match="not a regular file"):
            ConversionApplication().convert(_request(tmp_path, input_pdf=tmp_path / "dir.pdf"))

    def test_missing_output_directory(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="output directory"):
            ConversionApplication().convert(
                _request(tmp_path, output_directory=tmp_path / "missing")
            )

    def test_output_path_is_file(self, tmp_path: Path) -> None:
        f = tmp_path / "afile"
        f.write_text("x")
        with pytest.raises(InvalidRequestError, match="not a directory"):
            ConversionApplication().convert(_request(tmp_path, output_directory=f))

    def test_analysis_failure_chains_cause(self, tmp_path, monkeypatch) -> None:
        cause = PDFReadError("corrupt pdf")
        _FakePipeline(monkeypatch, tmp_path, analysis_error=cause)
        with pytest.raises(ConversionFailedError) as exc_info:
            ConversionApplication().convert(_request(tmp_path))
        assert exc_info.value.__cause__ is cause
        assert exc_info.value.stage is ConversionStage.ANALYSIS

    def test_epub_stage_failure_chains_cause(self, tmp_path, monkeypatch) -> None:
        cause = OCREngineUnavailableError("no tesseract")
        _FakePipeline(monkeypatch, tmp_path, epub_error=cause)
        request = _request(tmp_path)
        with pytest.raises(ConversionFailedError) as exc_info:
            ConversionApplication().convert(request)
        assert exc_info.value.__cause__ is cause
        assert exc_info.value.stage is ConversionStage.EPUB
        assert "PDF to EPUB conversion failed" in str(exc_info.value)

    def test_validation_failure_preserves_result(self, tmp_path, monkeypatch) -> None:
        validation = _invalid_validation()
        _FakePipeline(monkeypatch, tmp_path, validation_result=validation)
        with pytest.raises(ValidationFailedError) as exc_info:
            ConversionApplication().convert(_request(tmp_path))
        assert exc_info.value.validation is validation
        assert exc_info.value.epub_path == tmp_path / "book.epub"
        assert exc_info.value.epub_path.exists()

    def test_validation_crash_chains_cause(self, tmp_path, monkeypatch) -> None:
        cause = EPUBValidationError("not a zip")
        _FakePipeline(monkeypatch, tmp_path, validation_error=cause)
        with pytest.raises(ValidationFailedError) as exc_info:
            ConversionApplication().convert(_request(tmp_path))
        assert exc_info.value.__cause__ is cause
        assert exc_info.value.validation is None

    def test_azw3_failure_chains_cause(self, tmp_path, monkeypatch) -> None:
        cause = AZW3ConversionError("calibre exploded")
        backend = _RecordingBackend(error=cause)
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        with pytest.raises(AZW3OutputError) as exc_info:
            ConversionApplication(azw3_backend=backend).convert(request)
        assert exc_info.value.__cause__ is cause
        assert exc_info.value.cause is cause
        assert exc_info.value.epub_path == tmp_path / "book.epub"
        assert exc_info.value.epub_path.exists()

    def test_missing_calibre_path_precheck(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRequestError, match="calibre_path"):
            ConversionApplication().convert(
                _request(
                    tmp_path,
                    formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3}),
                    calibre_path=tmp_path / "no-such-calibre.exe",
                )
            )

    def test_application_errors_are_pipeline_errors(self) -> None:
        from kindle_converter.pipeline import PipelineError

        for error in (InvalidRequestError(), ConversionFailedError("x", stage=ConversionStage.EPUB)):
            assert isinstance(error, PipelineError)
            assert isinstance(error, ApplicationError)

    def test_unexpected_error_propagates_unchanged(self, tmp_path, monkeypatch) -> None:
        class _Boom(Exception):
            pass

        _FakePipeline(monkeypatch, tmp_path, epub_error=_Boom("programmer error"))
        with pytest.raises(_Boom, match="programmer error"):
            ConversionApplication().convert(_request(tmp_path))

# --------------------------------------------------------------------------
# End-to-end through the real (unmocked) pipeline
# --------------------------------------------------------------------------


class TestEndToEnd:
    """One small real-pipeline test: the application composes real M1--M4 stages."""

    def test_real_text_pdf_to_epub(self, tmp_path: Path) -> None:
        pdf = tmp_path / "real.pdf"
        doc = pymupdf.open()
        for index in range(2):
            page = doc.new_page(width=612, height=792)
            page.insert_textbox(
                pymupdf.Rect(72, 100, 500, 700),
                f"Body text line one on page {index + 1}. "
                f"Another sentence follows to give the page meaningful content.\n\n"
                f"Unique marker {index}.",
                fontname="helv",
                fontsize=12,
            )
        doc.save(str(pdf))
        doc.close()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        request = ConversionRequest(
            input_pdf=pdf,
            output_directory=out_dir,
            formats=frozenset({OutputFormat.EPUB}),
        )
        events: list[ConversionProgress] = []
        result = ConversionApplication().convert(request, progress=events.append)
        assert result.epub_path == out_dir / "real.epub"
        assert result.epub_path.exists() and result.epub_path.stat().st_size > 0
        assert result.azw3_path is None
        assert result.validation is not None
        assert result.validation.valid
        assert result.page_count == 2
        assert result.document_type is PDFType.TEXT
        assert events[-1].stage is ConversionStage.COMPLETE
        stages_seen = {event.stage for event in events}
        assert {
            ConversionStage.ANALYSIS,
            ConversionStage.EPUB,
            ConversionStage.VALIDATION,
            ConversionStage.COMPLETE,
        } <= stages_seen

