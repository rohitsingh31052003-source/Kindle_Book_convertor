"""Headless packaged-application smoke harness (M6.5).

The frozen Windows application cannot be driven by the Python interpreter
that built it, so verification exercises the real artifact instead. This
module ships *inside* the packaged application and exposes three
self-contained subcommands that drive the genuine :class:`MainWindow`
workflow (analysis, configuration, background conversion through the
M5.1 application boundary) headlessly:

    KindleBookConverter.exe --smoke-check
    KindleBookConverter.exe --smoke <input.pdf> <output-dir> [--azw3] [--expect-failure]
    KindleBookConverter.exe --sysinfo

Every subcommand returns a process exit code -- 0 success, non-zero
failure -- and can write a machine-readable result to ``--report <path>``
(the windowed PyInstaller executable detaches stdout, so the verification
tooling uses the report file and treating the exit code as the primary
signal). ``--sysinfo`` prints a single JSON document describing the
runtime's identity, frozen layout, dependency set, and external-tool
availability, so the verifier can distinguish *packaged-application
behavior* from *OCR/Calibre availability*.

The one deliberately *expected* non-success outcome is OCR unavailability:
with ``tesseract`` absent from ``PATH``, a scanned/mixed document fails with
the TesseractEngine "not found on PATH" error, which is reported as
``expected: true`` with exit code 0 (the ``--expect-failure`` flag forces a
conversion failure to be expected for any error and exists for one-off
manual checks).

This module is never imported on the normal GUI launch path.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from kindle_converter._meta import (  # noqa: E402
    APP_NAME,
    EXECUTABLE_NAME,
    application_version,
    bundle_root,
    is_frozen,
)
from kindle_converter.application import ConversionApplication  # noqa: E402
from kindle_converter.epub import validate_epub  # noqa: E402

from .main_window import MainWindow, UiState  # noqa: E402

__all__ = ["dispatch"]

#: Names of the distributions whose presence/version ``--sysinfo`` reports.
_DEPENDENCY_DISTRIBUTIONS = (
    "PySide6",
    "PyMuPDF",
    "ebooklib",
    "pytesseract",
    "Pillow",
)

#: Timeout (seconds) for one GUI-driven conversion to reach a terminal state.
_CONVERSION_TIMEOUT = 300.0

#: Timeout (seconds) for the QThread to release after a completed conversion.
_SETTLE_TIMEOUT = 30.0


def _emit(text: str) -> None:
    """Print ``text`` when stdout exists, never raising in windowed mode."""
    try:
        print(text, flush=True)
    except (ValueError, OSError, AttributeError):
        pass


def _write_report(report_path: Path | None, text: str) -> None:
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _dependency_status() -> dict[str, str]:
    return {name: _version(name) for name in _DEPENDENCY_DISTRIBUTIONS}


def _module_present(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _distribution_present(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def check_main_window() -> str:
    """Create and show the real window; report its identity.

    ``NOT RUNNING frozen`` is the honest answer when this harness is invoked
    from a source checkout -- in that case ``--sysinfo``/``--smoke`` are
    exercised by the in-process tests instead.
    """
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    window = MainWindow()
    window.show()
    try:
        ok = window.windowTitle() == APP_NAME and window.state is UiState.NO_INPUT
        state = "ok" if ok else "unexpected"
        return json.dumps(
            {
                "ok": ok,
                "state": state,
                "frozen": is_frozen(),
                "title": window.windowTitle(),
                "window_state": window.state.value,
            },
            indent=2,
        )
    finally:
        window.close()


def _qapp() -> QApplication:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    return application


def _wait_for(predicate, timeout: float, what: str) -> None:
    """Spin the GUI event loop until ``predicate()`` becomes true."""
    application = QCoreApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if application is not None:
            application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise TimeoutError(f"timed out waiting for {what}")


def run_conversion(
    input_pdf: Path,
    output_dir: Path,
    *,
    azw3: bool = False,
    expect_failure: bool = False,
) -> tuple[int, str]:
    """Drive one real conversion through the window; return (exit code, report).

    The window performs the exact user workflow: select input, analyze,
    pick output directory (+ the requested format), convert in the
    background, and -- on success -- structurally validate the produced
    EPUB through the existing :func:`~kindle_converter.epub.validate_epub`
    validator. The result is reported from the real application-layer
    ``ConversionResult`` and never reconstructed.
    """
    application = _qapp()
    window = MainWindow()
    window.show()
    try:
        window.select_input(str(input_pdf))
        window.analyze_selected()
        if window.state is UiState.ANALYSIS_FAILED:
            return 1, _report_failure("no exception", "analysis failed")

        output_dir.mkdir(parents=True, exist_ok=True)
        window.select_output_directory(str(output_dir))
        if azw3:
            combo = window.findChild(QComboBox, "outputFormatCombo")
            if combo is not None:
                combo.setCurrentIndex(1)  # AZW3 => EPUB + AZW3
        if not window.is_configuration_ready:
            return 1, _report_failure("no exception", "configuration not ready")

        window.convert_selected()
        _wait_for(
            lambda: window.state
            in (UiState.COMPLETED, UiState.CONVERSION_FAILED),
            _CONVERSION_TIMEOUT,
            "conversion completion",
        )

        if window.state is UiState.COMPLETED:
            result = window.last_result
            if result is None:
                return 1, _report_failure("no exception", "conversion reported success without a result")
            report = _succeeded_report(result, azw3=azw3)
            return 0, report

        error = window.last_error
        message = f"conversion failed: {error!r}" if error is not None else "conversion failed"
        if expect_failure or _is_graceful_unavailable(message):
            return 0, _report_failure(message, message, expected=True)
        return 1, _report_failure(message, message)
    finally:
        try:
            _wait_for(lambda: window._thread is None, _SETTLE_TIMEOUT, "thread release")
        except TimeoutError:
            pass
        window.close()


def _is_graceful_unavailable(message: str) -> bool:
    """True when a conversion failure is the OCR-engine-unavailable path.

    OCR is an external, optional runtime dependency (M6.1 §10): without the
    ``tesseract`` executable on ``PATH``, scanned/mixed documents fail with
    the TesseractEngine "not found on PATH" error. That genuine, designed
    outcome is reported as *expected* (exit 0) so a clean machine without
    Tesseract converts and validates text documents but does not error on
    OCR-only inputs.
    """
    return "tesseract executable" in message.lower() and "was not found on path" in message.lower()


def _report_failure(kind: str, message: str, *, expected: bool = False) -> str:
    return json.dumps(
        {
            "ok": False,
            "expected": expected,
            "state": "conversion_failed",
            "error": {"kind": kind, "message": message},
            "frozen": is_frozen(),
        },
        indent=2,
    )


def _succeeded_report(result, *, azw3: bool) -> str:
    epub_path = result.epub_path
    epub_present = epub_path is not None and Path(epub_path).is_file()
    validation = validate_epub(epub_path) if epub_present else None
    validation_valid = bool(validation is not None and validation.valid)
    azw3_path = result.azw3_path
    azw3_present = azw3_path is not None and Path(azw3_path).is_file()
    issues = []
    if not epub_present:
        issues.append("epub output missing")
    if not validation_valid:
        issues.append("epub validation failed")
    if azw3 and not azw3_present:
        issues.append("azw3 output missing")
    return json.dumps(
        {
            "ok": not issues,
            "expected": False,
            "state": "completed",
            "frozen": is_frozen(),
            "epub": {
                "path": str(epub_path) if epub_path is not None else None,
                "present": epub_present,
                "validation_valid": validation_valid,
                "validation_warnings": validation.warning_count if validation else None,
                "validation_errors": validation.error_count if validation else None,
                "version": validation.epub_version if validation else None,
            },
            "azw3": {
                "path": str(azw3_path) if azw3_path is not None else None,
                "present": azw3_present,
                "requested": azw3,
            },
            "document_type": result.document_type.value if result.document_type else None,
            "issues": issues,
        },
        indent=2,
    )


def sysinfo() -> str:
    """A single JSON document describing this runtime."""
    return json.dumps(
        {
            "ok": True,
            "frozen": is_frozen(),
            "application": {
                "name": APP_NAME,
                "executable_name": EXECUTABLE_NAME,
                "version": application_version(),
                "bundle_root": str(bundle_root()),
            },
            "runtime": {
                "executable": sys.executable,
                "prefix": sys.prefix,
                "meipass": str(getattr(sys, "_MEIPASS", "")),
                "python_version": sys.version.split()[0],
                "platform": sys.platform,
                "sys_path": list(sys.path),
            },
            "dependencies": _dependency_status(),
            "ocr": {
                "python_wrappers": {
                    "pytesseract": _distribution_present("pytesseract"),
                    "Pillow": _distribution_present("Pillow"),
                    "pytesseract_module": _module_present("pytesseract"),
                    "PIL_module": _module_present("PIL"),
                },
                "tesseract_executable": (
                    shutil.which("tesseract") or shutil.which("tesseract.exe") or None
                ),
            },
            "calibre": {
                "ebook_convert": (
                    shutil.which("ebook-convert")
                    or shutil.which("ebook-convert.exe")
                    or None
                )
            },
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(arguments: Sequence[str]) -> int:
    """Run one smoke subcommand and return its process exit code.

    ``arguments`` are the CLI arguments after the ``--smoke`` marker (``[]``
    for ``--smoke`` + subcommand as first argument). Recognized forms:

    * ``--smoke-check [--report P]``
    * ``--smoke <input.pdf> <output-dir> [--azw3] [--expect-failure] [--report P]``
    * ``--sysinfo [--report P]``
    """
    arguments = list(arguments)
    if not arguments:
        _emit("usage: --smoke-check | --smoke <pdf> <outdir> [...] | --sysinfo")
        return 2
    command = arguments[0]
    rest = arguments[1:]

    report_path: Path | None = None
    if "--report" in rest:
        index = rest.index("--report")
        if index + 1 < len(rest):
            report_path = Path(rest[index + 1])

    try:
        if command == "--smoke-check":
            text = check_main_window()
            _emit(text)
            _write_report(report_path, text)
            return 0
        if command == "--sysinfo":
            text = sysinfo()
            _emit(text)
            _write_report(report_path, text)
            return 0
        if command == "--smoke":
            flags = {token for token in rest if token.startswith("--") and token != "--report"}
            positional = [token for token in rest if not token.startswith("--")]
            if len(positional) < 2:
                _emit("usage: --smoke <input.pdf> <output-dir> [--azw3] [--expect-failure] [--report P]")
                return 2
            input_pdf = Path(positional[0])
            output_dir = Path(positional[1])
            if not input_pdf.is_file():
                _emit(f"input PDF not found: {input_pdf}")
                return 1
            code, text = run_conversion(
                input_pdf,
                output_dir,
                azw3=True if "--azw3" in flags else False,
                expect_failure=True if "--expect-failure" in flags else False,
            )
            _emit(text)
            _write_report(report_path, text)
            return code
        _emit(f"unknown smoke command: {command}")
        return 2
    except TimeoutError as exc:
        _emit(f"smoke timed out: {exc}")
        return 1
    except Exception as exc:  # keep the artifact honest: one line, non-zero exit
        _emit(f"smoke crashed: {type(exc).__name__}: {exc}")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Module entry point mirroring ``kindle_converter.ui.app.main``."""
    return dispatch(list(argv) if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())