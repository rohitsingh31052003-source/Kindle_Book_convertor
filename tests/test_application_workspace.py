"""Tests for the M5.7 application-level workspace lifecycle.

These tests verify that :class:`kindle_converter.application.workspace.ConversionWorkspace`
is created and deterministically cleaned up for every conversion path:
success, expected application errors, unexpected exceptions, and
workspace creation failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kindle_converter.application import (
    AZW3OutputError,
    ConversionApplication,
    ConversionRequest,
    OutputFormat,
)
from kindle_converter.application.errors import (
    ConversionFailedError,
    WorkspaceError,
)
from kindle_converter.application.workspace import ConversionWorkspace
from kindle_converter.epub import (
    AZW3ConversionError,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
)
from kindle_converter.pdf import (
    OCREngineUnavailableError,
    PDFAnalysis,
    PDFReadError,
    PDFType,
    PageAnalysis,
)


def _analysis(pages: int = 2) -> PDFAnalysis:
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


class _RecordingBackend:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def convert(self, source, destination):
        self.calls.append((source, destination))
        if self._error is not None:
            raise self._error
        destination.write_bytes(b"AZW3-fake")


class _FakePipeline:
    def __init__(self, monkeypatch, tmp_path, *, analysis_error=None, epub_error=None, backend=None):
        self.analysis_calls = []
        self.epub_calls = []
        self.validation_calls = []
        self.analysis_error = analysis_error
        self.epub_error = epub_error
        self._backend = backend
        self._monkeypatch = monkeypatch
        self._tmp_path = tmp_path
        self._patch()

    def _patch(self):
        import kindle_converter.application.converter as conv

        fake_epub = self._tmp_path / "book.epub"

        def fake_analyze(source):
            self.analysis_calls.append(Path(source))
            if self.analysis_error is not None:
                raise self.analysis_error
            return _analysis()

        def fake_convert_pdf_to_epub(source, out, *, engine=None, renderer=None, cover=None):
            self.epub_calls.append({"source": Path(source), "out": Path(out), "engine": engine, "renderer": renderer, "cover": cover})
            if self.epub_error is not None:
                raise self.epub_error
            fake_epub.write_bytes(b"PK fake-epub")

        def fake_validate(path):
            self.validation_calls.append(Path(path))
            return _valid_validation()

        def fake_azw3(source, destination, *, calibre_path=None, backend=None):
            assert backend is self._backend
            return self._backend.convert(Path(source), Path(destination))

        self._monkeypatch.setattr(conv, "analyze_pdf", fake_analyze)
        self._monkeypatch.setattr(conv, "convert_pdf_to_epub", fake_convert_pdf_to_epub)
        self._monkeypatch.setattr(conv, "validate_epub", fake_validate)
        self._monkeypatch.setattr(conv, "convert_epub_to_azw3", fake_azw3)


def _request(tmp_path, **overrides):
    pdf = tmp_path / "book.pdf"
    if not pdf.exists():
        pdf.write_bytes(b"%PDF-1.4 fake")
    defaults = {"input_pdf": pdf, "output_directory": tmp_path, "formats": frozenset({OutputFormat.EPUB})}
    defaults.update(overrides)
    return ConversionRequest(**defaults)


# --------------------------------------------------------------------------
# Helpers for workspace tracking
# --------------------------------------------------------------------------


def _workspace_cleanup_called(monkeypatch) -> list:
    """Record all paths passed to ConversionWorkspace.cleanup()."""
    calls = []

    def _spy(self):
        if self._path is not None:
            calls.append(self._path)
        if self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
            except OSError:
                pass
            self._temp_dir = None
            self._path = None

    monkeypatch.setattr(ConversionWorkspace, "cleanup", _spy)
    return calls


# --------------------------------------------------------------------------
# Workspace cleanup verification for each conversion path
# --------------------------------------------------------------------------


class TestWorkspaceCleanupOnSuccess:
    def test_epub_only_conversion_cleans_up_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _FakePipeline(monkeypatch, tmp_path)
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(tmp_path)
        ConversionApplication().convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()

    def test_epub_plus_azw3_conversion_cleans_up_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        backend = _RecordingBackend()
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        ConversionApplication(azw3_backend=backend).convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()


class TestWorkspaceCleanupOnFailure:
    def test_analysis_failure_cleans_up_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cause = PDFReadError("corrupt pdf")
        _FakePipeline(monkeypatch, tmp_path, analysis_error=cause)
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(tmp_path)
        with pytest.raises(ConversionFailedError):
            ConversionApplication().convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()

    def test_ocr_failure_cleans_up_workspace(self, tmp_path: Path, monkeypatch) -> None:
        cause = OCREngineUnavailableError("no tesseract")
        _FakePipeline(monkeypatch, tmp_path, epub_error=cause)
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(tmp_path)
        with pytest.raises(ConversionFailedError):
            ConversionApplication().convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()

    def test_epub_generation_failure_cleans_up_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cause = OCREngineUnavailableError("ocr boom")
        _FakePipeline(monkeypatch, tmp_path, epub_error=cause)
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(tmp_path)
        with pytest.raises(ConversionFailedError):
            ConversionApplication().convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()

    def test_azw3_failure_after_epub_succeeds_cleans_up_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cause = AZW3ConversionError("calibre exploded")
        backend = _RecordingBackend(error=cause)
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        with pytest.raises(AZW3OutputError):
            ConversionApplication(azw3_backend=backend).convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()

    def test_unexpected_exception_cleans_up_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        class _Boom(Exception):
            pass

        _FakePipeline(monkeypatch, tmp_path, epub_error=_Boom("boom"))
        calls = _workspace_cleanup_called(monkeypatch)
        request = _request(tmp_path)
        with pytest.raises(_Boom):
            ConversionApplication().convert(request)
        assert calls
        last_path = calls[-1]
        assert last_path is not None
        assert not last_path.exists()


# --------------------------------------------------------------------------
# Successful EPUB remains when AZW3 fails
# --------------------------------------------------------------------------


class TestEPUBSurvivesAZW3Failure:
    def test_epub_remains_in_output_dir_when_azw3_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cause = AZW3ConversionError("calibre exploded")
        backend = _RecordingBackend(error=cause)
        _FakePipeline(monkeypatch, tmp_path, backend=backend)
        request = _request(
            tmp_path, formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
        )
        with pytest.raises(AZW3OutputError):
            ConversionApplication(azw3_backend=backend).convert(request)
        epub_path = tmp_path / "book.epub"
        assert epub_path.exists()
        assert epub_path.stat().st_size > 0


# --------------------------------------------------------------------------
# Workspace creation failure
# --------------------------------------------------------------------------


class TestWorkspaceCreationFailure:
    def test_workspace_creation_failure_raises(self, monkeypatch) -> None:
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space left on device")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        with pytest.raises(WorkspaceError, match="temporary workspace"):
            with workspace:
                pass

    def test_workspace_creation_failure_preserves_cause(self, monkeypatch) -> None:
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space left on device")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        try:
            with workspace:
                pass
        except WorkspaceError as exc:
            assert isinstance(exc.__cause__, OSError)
            assert "no space" in str(exc.__cause__)

    def test_workspace_error_is_application_error(self, monkeypatch) -> None:
        from kindle_converter.application.errors import ApplicationError
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space left on device")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        try:
            with workspace:
                pass
        except WorkspaceError as exc:
            assert isinstance(exc, ApplicationError)


# --------------------------------------------------------------------------
# Nested/intermediate artifacts
# --------------------------------------------------------------------------


class TestWorkspaceNestedArtifacts:
    def test_nested_intermediate_files_are_cleaned(self) -> None:
        workspace = ConversionWorkspace()
        with workspace:
            path = workspace.path
            staging = path / "staging"
            staging.mkdir(parents=True)
            (staging / "partial.epub").write_bytes(b"partial")
            (path / "intermediate.bin").write_bytes(b"x" * 50)
            deep = staging / "deep"
            deep.mkdir()
            (deep / "token.txt").write_text("secret")
        assert not path.exists()

    def test_empty_workspace_is_cleaned(self) -> None:
        workspace = ConversionWorkspace()
        with workspace:
            path = workspace.path
        assert not path.exists()
