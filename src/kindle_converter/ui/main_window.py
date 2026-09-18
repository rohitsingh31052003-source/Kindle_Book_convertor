"""Main application window (M5.2, M5.3 input + analysis, M5.4 options, M5.5 conversion, M5.6 results).

The :class:`MainWindow` implements the desktop workflow: select a PDF, analyze
it, and read a summary of the analysis, then configure a conversion request
(output format, output directory, optional cover) whose readiness the window
tracks explicitly. It is deliberately a thin presentation/orchestration layer:

* it never parses, opens, or reads the PDF itself -- the displayed path is
  simply the path the user picked;
* selection, analysis, and configuration validation always go through the M5.1
  application boundary (:class:`~kindle_converter.application.ConversionApplication`);
* analysis failures and unexpected errors are translated into user-readable
  status messages, never Python tracebacks.

The dependency direction stays UI -> Application API -> core, never the
reverse: PySide6 never appears outside ``kindle_converter.ui``.

M5.3 performs **no conversion**: after a successful analysis the window is
ready for the conversion workflow and nothing else happens.

M5.4 adds the conversion-options step and keeps it synchronous and
non-executing: the window can choose the output format (EPUB/AZW3), pick an
output directory, and optionally pick/clear a cover. The current state is
summarized into a real application-layer :class:`ConversionRequest` via
:meth:`MainWindow.build_conversion_request`, and ready only when an analyzed
input plus a usable output directory and format are present (and any supplied
cover is acceptable to the application layer). M5.4 adds **no conversion
execution**: ``ConversionApplication.convert`` is never invoked from normal UI
interaction, no ``QThread``/worker is introduced, and no progress or result UI
is shown. Background execution is explicitly reserved for M5.5.

M5.5 adds the conversion-execution step on top of the M5.4 ``READY`` state.
Clicking **Convert** builds the current configuration into the real
application-layer :class:`~kindle_converter.application.ConversionRequest`,
validates it through :meth:`ConversionApplication.validate_request`, and runs
it on a dedicated :class:`QThread` via
:class:`~kindle_converter.ui.worker.ConversionWorker`. The worker only calls
:meth:`ConversionApplication.convert` (the application layer stays the sole
owner of analysis, processing/OCR, reconstruction, EPUB generation,
validation, AZW3 conversion, cover handling, and output-path calculation) and
forwards the *existing*
:class:`~kindle_converter.application.ConversionProgress` events to the window
over Qt signals. During a conversion the window is in
:attr:`UiState.CONVERTING`: the indeterminate progress bar is active, each
application stage message is shown as status text, and every control that
could change the active request (browse, analyze, output format, output
directory, cover, convert) is disabled. A successful conversion is retained as
the real :class:`~kindle_converter.application.ConversionResult` for M5.6; a
failure is shown as a status message and the window returns to a usable
non-converting state. M5.5 implements **no** cancellation (the application
pipeline has no cooperative-cancellation hook, so the worker cannot be safely
stopped mid-conversion), no results screen, and no automatic output opening.

M5.6 adds the post-conversion **results experience** on top of that retained
result. After a successful conversion the window shows a dedicated results
section rendered exclusively from the real
:class:`~kindle_converter.application.ConversionResult`: completion status,
output format, the EPUB output path, the AZW3 output path when generated, the
existing :class:`~kindle_converter.epub.EPUBValidationResult` (status,
warning/error counts, and their details), and local actions to open the
generated outputs or their containing folder. The UI never reruns conversion
or EPUB validation, never recomputes output paths, and never constructs its
own result/validation copies: the application object is the sole source of
every displayed value. Results are hidden before any successful conversion and
are cleared whenever a new request invalidates them (new input, configuration
change, or the start of another conversion), so a previous result is never
presented as the current request's result. Output opening goes through a small
injectable :func:`~kindle_converter.ui.platform.open_path` seam; failures are
shown as a concise, local user-facing status and never crash the window.

Visual polish (themes, icons, fonts, animations) is deliberately out of scope;
this is a clean default Qt layout only.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kindle_converter.application import (
    ApplicationError,
    ConversionApplication,
    ConversionFailedError,
    ConversionProgress,
    ConversionRequest,
    ConversionResult,
    InvalidRequestError,
    OutputFormat,
)
from kindle_converter.epub import EPUBValidationResult
from kindle_converter.pdf import PDFType
from kindle_converter.pdf.models import PDFAnalysis

from .platform import PlatformOpenError, open_path
from .worker import ConversionWorker

logger = logging.getLogger(__name__)

#: Application and window title.
APP_TITLE = "Kindle Book Converter"

#: Initial window dimensions in logical pixels.
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 640

_SUBTITLE_TEXT = "PDF \u2192 Kindle-ready ebook converter"
_INPUT_SECTION_TEXT = "Input PDF"
_ANALYSIS_SECTION_TEXT = "PDF Analysis"
_CONVERSION_SECTION_TEXT = "Conversion Options"
_RESULTS_SECTION_TEXT = "Conversion Results"
_VALIDATION_SECTION_TEXT = "EPUB Validation"

_STATUS_NO_INPUT = "Select a PDF file to begin"
_STATUS_INPUT_SELECTED = "Ready to analyze"
_STATUS_ANALYZING = "Analyzing..."
_STATUS_ANALYSIS_COMPLETE = "Analysis complete"
_STATUS_ANALYSIS_FAILED = "Analysis failed. Please fix the input and try again."
_STATUS_CONFIGURING = "Configure conversion options to proceed"
_STATUS_READY = "Ready for conversion"
_STATUS_CONVERTING = "Converting..."
_STATUS_COMPLETED = "Conversion complete"
_STATUS_CONVERSION_FAILED = "Conversion failed"

_ERROR_NO_INPUT = "Please select a PDF file."
_ERROR_NOT_FOUND = "The selected PDF file could not be found."
_ERROR_NOT_A_FILE = "The selected input is not a regular file."
_ERROR_CANNOT_READ = "The selected PDF file could not be read."
_ERROR_INVALID_INPUT = "The selected input is not a valid PDF."
_ERROR_ANALYSIS_FAILED = "The PDF could not be analyzed."
_ERROR_CONVERSION_FAILED = "Conversion failed."

#: Status text shown in the results section after a successful conversion.
_RESULT_STATUS_COMPLETE = "Conversion complete"

#: Validation status values for ``ConversionResult.valid``.
_VALIDATION_VALID = "Valid"
_VALIDATION_INVALID = "Invalid"
_VALIDATION_NOT_RUN = "Not run"

#: Output-action feedback (M5.6, kept local to the results section).
_ACTION_NO_RESULT = "No output is available to open."
_ACTION_OUTPUT_MISSING = "The output is no longer available: {}"
_ACTION_OPEN_FAILED = "Could not open {}: {}"

#: Placeholder shown for every analysis value until an analysis is displayed.
_NO_VALUE = "\u2014"

#: File-dialog filter for the PDF picker.
_PDF_FILTER = "PDF files (*.pdf);;All files (*)"

#: File-dialog filter for the cover picker. Mirrors the image formats the
#: M4.4 ``load_cover`` implementation accepts (JPEG, PNG, GIF, SVG).
_COVER_FILTER = (
    "Images (*.jpg *.jpeg *.png *.gif *.svg);;All files (*)"
)

#: Placeholder text for the output-directory field.
_NO_OUTPUT_DIRECTORY_TEXT = "No directory selected"

#: Placeholder text for the cover field.
_NO_COVER_TEXT = "None"

#: One UI output-format option (label, application enum value).
_FORMAT_OPTIONS: tuple[tuple[str, OutputFormat], ...] = (
    ("EPUB", OutputFormat.EPUB),
    ("AZW3", OutputFormat.AZW3),
)

#: The ``formats`` set each user-facing output-format selection implies. The
#: application-layer rule is that EPUB is always produced and AZW3 is an
#: optional additional artifact, so picking AZW3 asks for both.
_FORMAT_TO_FORMATS: dict[OutputFormat, frozenset[OutputFormat]] = {
    OutputFormat.EPUB: frozenset({OutputFormat.EPUB}),
    OutputFormat.AZW3: frozenset({OutputFormat.EPUB, OutputFormat.AZW3}),
}


class UiState(Enum):
    """The explicit, small UI state model (M5.3, options M5.4, conversion M5.5).

    ``NO_INPUT -> INPUT_SELECTED -> ANALYZING -> ANALYSIS_COMPLETE`` is the
    happy analysis path; ``ANALYZING -> ANALYSIS_FAILED`` is the error path.
    After a successful analysis the window is configuring a conversion:
    ``ANALYSIS_COMPLETE`` is the just-analyzed state, ``CONFIGURING`` means a
    configuration change is incomplete, and ``READY`` means the current
    configuration (analyzed input + output directory + valid format + optional
    acceptable cover) is ready for conversion. M5.5 extends the model with the
    execution states: ``READY -> CONVERTING`` while a background conversion is
    running, then ``COMPLETED`` on success or ``CONVERSION_FAILED`` on failure
    (both leave the configuration intact so the user can convert again). The
    state drives widget enablement and the status message.
    """

    #: No PDF has been selected yet.
    NO_INPUT = "no_input"
    #: A PDF is selected and ready to be analyzed.
    INPUT_SELECTED = "input_selected"
    #: A synchronous analysis is running (controls that would conflict are
    #: disabled, duplicate analysis is prevented).
    ANALYZING = "analyzing"
    #: The last analysis finished successfully and its summary is displayed;
    #: conversion options are now available (M5.4).
    ANALYSIS_COMPLETE = "analysis_complete"
    #: The last analysis failed; the selected input is preserved for retry.
    ANALYSIS_FAILED = "analysis_failed"
    #: Analysis is complete but the conversion configuration is not (yet) ready
    #: (for example no output directory is selected, or the cover is invalid).
    CONFIGURING = "configuring"
    #: Everything the application layer needs for one conversion is set: an
    #: analyzed input, an output directory, a valid output format, and an
    #: acceptable optional cover.
    READY = "ready"
    #: A background conversion is running (M5.5); every control that could
    #: change the active request is disabled and no second conversion can start.
    CONVERTING = "converting"
    #: A conversion finished successfully; the real application-layer
    #: ``ConversionResult`` is retained and its results section is populated
    #: (M5.6). The configuration is intact so another conversion can be
    #: started.
    COMPLETED = "completed"
    #: A conversion failed; the failure is shown as a status message and the
    #: window returns to a usable non-converting state for retry.
    CONVERSION_FAILED = "conversion_failed"


class MainWindow(QMainWindow):
    """The main application window: input selection, analysis, conversion.

    Widgets carry stable ``objectName`` values (``appTitle``, ``subtitle``,
    ``inputSectionTitle``, ``inputPath``, ``browseButton``, ``analyzeButton``,
    ``analysisSectionTitle``, ``pagesValue``, ``documentTypeValue``,
    ``textPagesValue``, ``scannedPagesValue``, ``mixedPagesValue``,
    ``ocrRequiredValue``, ``conversionSectionTitle``, ``outputFormatCombo``,
    ``outputDirectory``, ``outputDirectoryButton``, ``coverPath``,
    ``coverBrowseButton``, ``coverClearButton``, ``convertButton``,
    ``progressBar``, ``statusLabel``, ``resultsSection``,
    ``resultsSectionTitle``, ``resultStatusValue``, ``resultFormatValue``,
    ``epubOutputPath``, ``azw3OutputPath``, ``validationSectionTitle``,
    ``validationStatusValue``, ``validationWarningsValue``,
    ``validationErrorsValue``, ``validationWarningsDetails``,
    ``validationErrorsDetails``, ``openEpubButton``, ``openAzw3Button``,
    ``openFolderButton``, ``outputActionStatus``) so tests and later
    milestones can locate them without depending on layout order.

    ``application`` is the M5.1 boundary double-callers can inject for tests;
    when omitted a real :class:`ConversionApplication` is used. The same
    instance is handed to every :class:`ConversionWorker` this window spawns,
    so in tests a deterministic fake application fully controls the background
    conversion behavior.

    ``opener`` is the injectable platform-opening seam for the M5.6 output
    actions: a callable ``opener(path: Path) -> None`` invoked to open a
    generated file or its containing folder with the OS default handler. When
    omitted, :func:`kindle_converter.ui.platform.open_path` is used; tests
    inject a recording double so they can verify the exact path an action
    would open without launching an external application.

    The conversion thread lifecycle is explicit: each conversion gets exactly
    one :class:`QThread` and one :class:`~kindle_converter.ui.worker.ConversionWorker`;
    worker completion stops its own thread; the thread's ``finished`` signal
    releases the retained references, so a later conversion starts with fresh
    worker state. Closing the window while a conversion is running waits for
    that thread to finish (M5.5 has no cancellation), so a running ``QThread``
    is never destroyed unsafely.
    """

    def __init__(
        self, application: ConversionApplication | None = None, opener=None
    ) -> None:
        super().__init__()
        self._application = (
            application if application is not None else ConversionApplication()
        )
        self._opener = opener if opener is not None else open_path
        self._selected_path: Path | None = None
        self._last_analysis: PDFAnalysis | None = None
        self._output_directory: Path | None = None
        self._cover_path: Path | None = None
        self._state: UiState = UiState.NO_INPUT
        self._conversion_result: ConversionResult | None = None
        self._conversion_error: Exception | None = None
        self._thread: QThread | None = None
        self._worker: ConversionWorker | None = None
        self.setWindowTitle(APP_TITLE)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._build_central_area()
        self._apply_state(UiState.NO_INPUT)

    # ------------------------------------------------------------------
    # Public state inspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> UiState:
        """The current :class:`UiState` of the window."""
        return self._state

    @property
    def selected_path(self) -> Path | None:
        """The path currently selected, or ``None`` when nothing is selected."""
        return self._selected_path

    @property
    def last_analysis(self) -> PDFAnalysis | None:
        """The most recent successful analysis, or ``None`` when none exists."""
        return self._last_analysis

    @property
    def output_directory(self) -> Path | None:
        """The selected output directory, or ``None`` when none is selected."""
        return self._output_directory

    @property
    def cover_path(self) -> Path | None:
        """The selected cover path, or ``None`` when no cover is selected."""
        return self._cover_path

    @property
    def output_format(self) -> OutputFormat | None:
        """The selected application-layer :class:`OutputFormat`.

        ``None`` only in an impossible/invalid combo state; the combo is only
        ever populated with valid :class:`OutputFormat` values.
        """
        data = self._output_format_combo.currentData()
        if not isinstance(data, str):
            return None
        try:
            return OutputFormat(data)
        except ValueError:
            return None

    @property
    def is_configuration_ready(self) -> bool:
        """Whether the current configuration is ready for conversion."""
        return self._configuration_ready()

    @property
    def last_result(self) -> ConversionResult | None:
        """The most recent successful ``ConversionResult`` (retained for M5.6).

        ``None`` before any conversion completes or when the last conversion
        failed. This is the real application-layer result object, never a UI
        reconstruction of its fields.
        """
        return self._conversion_result

    @property
    def last_error(self) -> Exception | None:
        """The most recent conversion failure, or ``None``.

        Set when a conversion (or its pre-flight validation) failed, so M5.6+
        and tests can inspect the exact application-layer error.
        """
        return self._conversion_error

    # ------------------------------------------------------------------
    # Input selection
    # ------------------------------------------------------------------

    def browse_for_input(self) -> None:
        """Open the PDF picker and apply the chosen file.

        Uses :class:`QFileDialog` restricted to PDF files. When the dialog is
        cancelled the current selection (if any) is preserved and no error is
        shown.
        """
        selected, _ = QFileDialog.getOpenFileName(
            self, "Select a PDF file", "", _PDF_FILTER
        )
        if selected:
            self.select_input(selected)

    def select_input(self, path) -> None:
        """Select ``path`` as the input PDF (or ``None`` to clear the input).

        Only stores and displays the path -- the PDF is never opened or read
        here -- and invalidates any stale analysis from a previous file. A
        no-op while a conversion is running, since the active request must not
        change under the worker.
        """
        if self._state is UiState.CONVERTING:
            return
        if path is None:
            self._selected_path = None
            self._path_edit.clear()
            self._clear_analysis()
            self._clear_result()
            self._apply_state(UiState.NO_INPUT)
            return
        self._selected_path = Path(path)
        self._path_edit.setText(str(self._selected_path))
        self._clear_analysis()
        self._clear_result()
        self._apply_state(UiState.INPUT_SELECTED)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_selected(self) -> None:
        """Analyze the selected PDF through the application layer.

        Synchronous in M5.3 (no background execution). A repeated call while
        an analysis is running is a no-op; the Analyze button is also disabled
        during :attr:`UiState.ANALYZING`. A call while a conversion is running
        is likewise a no-op. Failures are translated into
        user-readable status messages and leave the window usable for a retry.
        """
        if self._state in (UiState.ANALYZING, UiState.CONVERTING):
            return
        selected = self._selected_path
        if selected is None:
            self._show_error(_ERROR_NO_INPUT)
            return
        path = Path(selected)
        if not path.exists():
            self._show_error(_ERROR_NOT_FOUND)
            return
        if not path.is_file():
            self._show_error(_ERROR_NOT_A_FILE)
            return

        self._apply_state(UiState.ANALYZING)
        try:
            analysis = self._application.analyze_pdf(path)
        except InvalidRequestError as exc:
            self._show_error(self._translate_invalid_request(exc))
            return
        except ConversionFailedError:
            self._show_error(_ERROR_ANALYSIS_FAILED)
            return
        except ApplicationError:
            self._show_error(_ERROR_ANALYSIS_FAILED)
            return
        except Exception:
            logger.exception("Unexpected error while analyzing %s", path)
            self._show_error(_ERROR_ANALYSIS_FAILED)
            return

        self._last_analysis = analysis
        self._display_analysis(analysis)
        self._apply_state(UiState.ANALYSIS_COMPLETE)
        if self._configuration_ready():
            # The configuration was already complete for this file (for
            # example an output directory and format were retained from before).
            self._apply_state(UiState.READY)

    # ------------------------------------------------------------------
    # Conversion configuration (M5.4)
    # ------------------------------------------------------------------

    def browse_for_output_directory(self) -> None:
        """Open a native directory picker and apply the chosen directory.

        Uses :class:`QFileDialog.getExistingDirectory`. When the dialog is
        cancelled the current selection (if any) is preserved. The directory
        is only recorded and displayed -- nothing is created on disk.
        """
        start = str(self._output_directory) if self._output_directory else ""
        directory = QFileDialog.getExistingDirectory(
            self, "Select an output directory", start
        )
        if directory:
            self.select_output_directory(directory)

    def select_output_directory(self, path) -> None:
        """Select ``path`` as the output directory (or ``None`` to clear it).

        Stores and displays the existing selection; the directory is never
        created just because it was selected. Re-evaluates configuration
        readiness when an analysis exists. A no-op while a conversion is
        running.
        """
        if self._state is UiState.CONVERTING:
            return
        if path is None:
            self._output_directory = None
            self._output_directory_edit.clear()
            self._refresh_configuration()
            return
        self._output_directory = Path(path)
        self._output_directory_edit.setText(str(self._output_directory))
        self._refresh_configuration()

    def browse_for_cover(self) -> None:
        """Open a native image picker and apply the chosen cover.

        Uses :class:`QFileDialog` restricted to the image formats the existing
        M4.4 ``load_cover`` implementation accepts. When the dialog is
        cancelled the current selection (if any) is preserved.
        """
        selected, _ = QFileDialog.getOpenFileName(
            self, "Select a cover image", "", _COVER_FILTER
        )
        if selected:
            self.select_cover(selected)

    def select_cover(self, path) -> None:
        """Select ``path`` as the cover image (or ``None`` to clear it).

        Only stores and displays the path -- no image processing happens in
        the UI. The application layer owns cover loading/validation (M4.4);
        readiness reflects whether the application accepts the selection. A
        no-op while a conversion is running.
        """
        if self._state is UiState.CONVERTING:
            return
        if path is None:
            self._cover_path = None
            self._cover_edit.clear()
            self._refresh_configuration()
            return
        self._cover_path = Path(path)
        self._cover_edit.setText(str(self._cover_path))
        self._refresh_configuration()

    def clear_cover(self) -> None:
        """Remove the selected cover, if any (no-op while converting)."""
        if self._state is UiState.CONVERTING:
            return
        self._cover_path = None
        self._cover_edit.clear()
        self._refresh_configuration()

    def _on_output_format_changed(self, index: int) -> None:
        self._refresh_configuration()

    def build_conversion_request(self) -> ConversionRequest:
        """Build the application-layer :class:`ConversionRequest` from UI state.

        Reads the current input path, output directory, output format, and
        optional cover and constructs the real
        :class:`~kindle_converter.application.ConversionRequest`. Structural
        validation runs in the request constructor (``validate=True``, the
        application's default EPUB-validation setting); no conversion is
        executed, nothing is written, and nothing is analyzed here. The
        requested ``formats`` follow the application-layer contract: EPUB is
        always included and selecting AZW3 asks for EPUB + AZW3.

        Raises
        ------
        InvalidRequestError
            When the current UI state cannot produce a structurally valid
            request (no input, no output format, etc.).
        """
        formats = _FORMAT_TO_FORMATS.get(self.output_format, frozenset())
        return ConversionRequest(
            input_pdf=self._selected_path,
            output_directory=self._output_directory,
            formats=formats,
            cover=self._cover_path,
            validate=True,
            calibre_path=None,
        )

    # ------------------------------------------------------------------
    # Conversion execution (M5.5)
    # ------------------------------------------------------------------

    def convert_selected(self) -> None:
        """Start a background conversion of the current ready configuration.

        Builds the real application-layer :class:`ConversionRequest`, validates
        it through :meth:`ConversionApplication.validate_request` (the same
        pre-flight checks ``convert`` performs), and runs it on a dedicated
        :class:`QThread` via :class:`~kindle_converter.ui.worker.ConversionWorker`.

        The worker delegates the whole conversion to
        :meth:`ConversionApplication.convert` and only reports progress/result/
        failure over Qt signals; the GUI thread stays responsive and owns every
        UI update. While the conversion runs the window is in
        :attr:`UiState.CONVERTING`: all conflicting controls are disabled and a
        second conversion cannot start. A successful
        :class:`~kindle_converter.application.ConversionResult` is retained on
        :attr:`last_result` for M5.6; a failure is shown as a status message.

        Calling this while a conversion is running, or while the configuration
        is not ready, is a no-op (with the unmet-condition message shown).
        """
        if self._state is UiState.CONVERTING:
            return
        if not self.is_configuration_ready:
            self._status_label.setText(self._configuration_problem())
            return
        try:
            request = self.build_conversion_request()
            self._application.validate_request(request)
        except ApplicationError as exc:
            self._on_conversion_failed(exc)
            return

        worker = ConversionWorker(self._application, request)
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_conversion_progress)
        worker.succeeded.connect(self._on_conversion_succeeded)
        worker.failed.connect(self._on_conversion_failed)
        # ``thread.quit`` is thread-safe and runs here directly in the worker
        # thread the moment ``finished`` fires, so thread shutdown never waits
        # on a queued call back into the GUI thread.
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._on_conversion_thread_finished(thread))
        thread.finished.connect(thread.deleteLater)

        # References are retained on the window until the thread finishes, so
        # the worker is never garbage-collected while it is running.
        self._thread = thread
        self._worker = worker
        # A new conversion invalidates any previous result: its presentation
        # must not be shown as though it belonged to this request.
        self._clear_result()
        self._conversion_error = None
        self._apply_state(UiState.CONVERTING)
        thread.start()

    def _on_conversion_progress(self, progress: ConversionProgress) -> None:
        """Apply one application progress event to the status text.

        Runs on the GUI thread (queued from the worker). The displayed text is
        the stage/status message the application layer actually reported; no
        percentage is fabricated.
        """
        if isinstance(progress, ConversionProgress):
            self._status_label.setText(progress.message)

    def _on_conversion_succeeded(self, result: ConversionResult) -> None:
        """Retain the real ``ConversionResult`` and finish the conversion.

        The result is kept verbatim (never reconstructed from fields) and its
        results/validation section is populated from that exact object (M5.6);
        no conversion or validation is rerun here. The window becomes
        :attr:`UiState.COMPLETED`, which now corresponds to a populated
        results presentation.
        """
        self._conversion_result = result
        self._populate_results(result)
        self._apply_state(UiState.COMPLETED)

    def _on_conversion_failed(self, error: Exception) -> None:
        """Show a conversion failure and return the UI to a usable state.

        Application errors are shown verbatim (they are user-facing);
        unexpected errors collapse to a concise generic message (they are
        already logged by the worker). The configuration is preserved so the
        user can retry.
        """
        self._conversion_error = error
        if isinstance(error, ApplicationError):
            message = str(error)
        else:
            message = _ERROR_CONVERSION_FAILED
        self._apply_state(UiState.CONVERSION_FAILED, status=message)

    def _on_conversion_thread_finished(self, thread: QThread) -> None:
        """Release the finished conversion thread/worker references.

        Guarded by identity so a stale ``finished`` delivery from a previous
        conversion can never clobber the references of a newer conversion.
        """
        if self._thread is not thread:
            return
        self._worker = None
        self._thread = None

    # ------------------------------------------------------------------
    # Output actions (M5.6)
    # ------------------------------------------------------------------

    def open_epub(self) -> None:
        """Open the generated EPUB with the OS default application.

        Reads the exact ``epub_path`` from the retained real
        :class:`~kindle_converter.application.ConversionResult`; the path is
        never recomputed. Failures (missing output, failed platform open) are
        shown locally in the results section and never crash the window.
        """
        result = self._conversion_result
        self._open_output(result.epub_path if result is not None else None)

    def open_azw3(self) -> None:
        """Open the generated AZW3 with the OS default application.

        Reads the exact ``azw3_path`` from the retained real result; a no-op
        (with local feedback) when the current result has no AZW3 output.
        """
        result = self._conversion_result
        self._open_output(result.azw3_path if result is not None else None)

    def open_output_folder(self) -> None:
        """Open the folder containing the generated outputs.

        Uses the real result's directory (``epub_path.parent``), which is the
        containing directory of every produced artifact, never a reconstructed
        path.
        """
        result = self._conversion_result
        self._open_output(
            result.epub_path.parent if result is not None else None
        )

    def _open_output(self, path: Path | None) -> None:
        """Open ``path`` safely, reporting failures instead of crashing.

        M5.6 keeps output-action failures local to the action itself (full
        error architecture is a later milestone): a missing result, a missing
        output path, or a failed platform open shows a concise user-facing
        status on the results section. The conversion result and its output
        files are never modified.
        """
        if path is None:
            self._set_output_action_status(_ACTION_NO_RESULT)
            return
        if not Path(path).exists():
            self._set_output_action_status(_ACTION_OUTPUT_MISSING.format(path))
            return
        try:
            self._opener(Path(path))
        except (PlatformOpenError, OSError) as exc:
            logger.exception("Could not open output %s", path)
            self._set_output_action_status(_ACTION_OPEN_FAILED.format(path, exc))
        else:
            self._set_output_action_status("")

    def _set_output_action_status(self, message: str) -> None:
        """Show (or, with ``message == ""``, hide) the output-action status."""
        self._output_action_status.setText(message)
        self._output_action_status.setVisible(bool(message))

    def closeEvent(self, event) -> None:
        """Wait for an in-flight conversion before closing.

        M5.5 has no cancellation, so the only safe way to close while a
        conversion is running is to let it finish before destroying the
        window: a running ``QThread`` must never be destroyed. The worker
        stops its own thread as soon as ``convert`` returns, so ``wait()``
        completes without deadlocking the GUI thread.
        """
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.wait()
        event.accept()

    # ------------------------------------------------------------------
    # Readiness helpers
    # ------------------------------------------------------------------

    def _refresh_configuration(self) -> None:
        """Re-evaluate readiness after a conversion-option change.

        Only meaningful after a completed analysis: with none, the state stays
        wherever analysis left it (a configuration cannot become ready before
        its PDF is analyzed). Otherwise the window becomes
        :attr:`UiState.READY` exactly when the configuration is valid, and
        :attr:`UiState.CONFIGURING` with a concrete hint otherwise. A no-op
        while a conversion is running (the active request must not change).

        Because this runs on every conversion-option change, any previously
        completed result is invalidated here: a ``ConversionResult`` is
        associated with the conversion that produced it, never with whatever
        configuration happens to be visible now.
        """
        if self._state is UiState.CONVERTING:
            return
        self._clear_result()
        if self._last_analysis is None:
            return
        if self._configuration_ready():
            self._apply_state(UiState.READY)
        else:
            self._apply_state(UiState.CONFIGURING, status=self._configuration_problem())

    def _configuration_ready(self) -> bool:
        """Whether the UI state amounts to a ready conversion configuration.

        Requires an analyzed, still-valid input PDF, an output directory, and
        a valid output format; any supplied cover must be acceptable to the
        application layer. Business validation is delegated to
        :meth:`ConversionApplication.validate_request` -- it is never
        reimplemented in the UI.
        """
        if self._selected_path is None or self._last_analysis is None:
            return False
        if self._output_directory is None:
            return False
        try:
            request = self.build_conversion_request()
        except InvalidRequestError:
            return False
        try:
            self._application.validate_request(request)
        except ApplicationError:
            return False
        return True

    def _configuration_problem(self) -> str:
        """A user-readable reason the configuration is not ready."""
        if self._selected_path is None:
            return _ERROR_NO_INPUT
        if self._last_analysis is None:
            return "Analyze the selected PDF before converting."
        if self._output_directory is None:
            return "Select an output directory to continue."
        try:
            request = self.build_conversion_request()
        except InvalidRequestError as exc:
            return self._translate_config_error(exc)
        try:
            self._application.validate_request(request)
        except InvalidRequestError as exc:
            return self._translate_config_error(exc)
        except ApplicationError:
            return "The conversion configuration is not currently usable."
        return "Configure conversion options to proceed."

    @staticmethod
    def _translate_config_error(exc: InvalidRequestError) -> str:
        """Map an application ``InvalidRequestError`` from request validation."""
        message = str(exc)
        if "output directory" in message:
            return "The selected output directory is not usable."
        if "cover" in message:
            return "The selected cover could not be used."
        return message

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_central_area(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        title = QLabel(APP_TITLE, central)
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = title.font()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel(_SUBTITLE_TEXT, central)
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(12)
        subtitle.setFont(subtitle_font)
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        # --- Input section ------------------------------------------------
        input_title = QLabel(_INPUT_SECTION_TEXT, central)
        input_title.setObjectName("inputSectionTitle")
        layout.addWidget(input_title)

        input_row = QWidget(central)
        input_row_layout = QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)
        self._path_edit = QLineEdit(input_row)
        self._path_edit.setObjectName("inputPath")
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("No PDF selected")
        input_row_layout.addWidget(self._path_edit, 1)
        self._browse_button = QPushButton("Browse...", input_row)
        self._browse_button.setObjectName("browseButton")
        self._browse_button.clicked.connect(self.browse_for_input)
        input_row_layout.addWidget(self._browse_button)
        layout.addWidget(input_row)

        self._analyze_button = QPushButton("Analyze PDF", central)
        self._analyze_button.setObjectName("analyzeButton")
        self._analyze_button.clicked.connect(self.analyze_selected)
        layout.addWidget(self._analyze_button, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addSpacing(8)

        # --- Analysis section ---------------------------------------------
        analysis_title = QLabel(_ANALYSIS_SECTION_TEXT, central)
        analysis_title.setObjectName("analysisSectionTitle")
        layout.addWidget(analysis_title)

        form_host = QWidget(central)
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        self._pages_value = self._add_analysis_row(form, "Pages:", "pagesValue", central)
        self._document_type_value = self._add_analysis_row(
            form, "Document type:", "documentTypeValue", central
        )
        self._text_pages_value = self._add_analysis_row(
            form, "Text pages:", "textPagesValue", central
        )
        self._scanned_pages_value = self._add_analysis_row(
            form, "Scanned pages:", "scannedPagesValue", central
        )
        self._mixed_pages_value = self._add_analysis_row(
            form, "Mixed pages:", "mixedPagesValue", central
        )
        self._ocr_required_value = self._add_analysis_row(
            form, "OCR required:", "ocrRequiredValue", central
        )
        layout.addWidget(form_host)

        layout.addSpacing(12)

        # --- Conversion options section (M5.4) ----------------------------
        options_title = QLabel(_CONVERSION_SECTION_TEXT, central)
        options_title.setObjectName("conversionSectionTitle")
        layout.addWidget(options_title)

        options_form_host = QWidget(central)
        options_form = QFormLayout(options_form_host)
        options_form.setContentsMargins(0, 0, 0, 0)

        format_row = QWidget(options_form_host)
        format_row_layout = QHBoxLayout(format_row)
        format_row_layout.setContentsMargins(0, 0, 0, 0)
        self._output_format_combo = QComboBox(format_row)
        self._output_format_combo.setObjectName("outputFormatCombo")
        for label, enum_value in _FORMAT_OPTIONS:
            self._output_format_combo.addItem(label, enum_value.value)
        self._output_format_combo.setCurrentIndex(0)  # EPUB is the default
        self._output_format_combo.currentIndexChanged.connect(
            self._on_output_format_changed
        )
        format_row_layout.addWidget(self._output_format_combo)
        format_row_layout.addStretch(1)
        options_form.addRow("Output format:", format_row)

        output_row = QWidget(options_form_host)
        output_row_layout = QHBoxLayout(output_row)
        output_row_layout.setContentsMargins(0, 0, 0, 0)
        self._output_directory_edit = QLineEdit(output_row)
        self._output_directory_edit.setObjectName("outputDirectory")
        self._output_directory_edit.setReadOnly(True)
        self._output_directory_edit.setPlaceholderText(_NO_OUTPUT_DIRECTORY_TEXT)
        output_row_layout.addWidget(self._output_directory_edit, 1)
        self._output_directory_button = QPushButton("Browse...", output_row)
        self._output_directory_button.setObjectName("outputDirectoryButton")
        self._output_directory_button.clicked.connect(
            self.browse_for_output_directory
        )
        output_row_layout.addWidget(self._output_directory_button)
        options_form.addRow("Output directory:", output_row)

        cover_row = QWidget(options_form_host)
        cover_row_layout = QHBoxLayout(cover_row)
        cover_row_layout.setContentsMargins(0, 0, 0, 0)
        self._cover_edit = QLineEdit(cover_row)
        self._cover_edit.setObjectName("coverPath")
        self._cover_edit.setReadOnly(True)
        self._cover_edit.setPlaceholderText(_NO_COVER_TEXT)
        cover_row_layout.addWidget(self._cover_edit, 1)
        self._cover_browse_button = QPushButton("Browse...", cover_row)
        self._cover_browse_button.setObjectName("coverBrowseButton")
        self._cover_browse_button.clicked.connect(self.browse_for_cover)
        cover_row_layout.addWidget(self._cover_browse_button)
        self._cover_clear_button = QPushButton("Clear", cover_row)
        self._cover_clear_button.setObjectName("coverClearButton")
        self._cover_clear_button.clicked.connect(self.clear_cover)
        cover_row_layout.addWidget(self._cover_clear_button)
        options_form.addRow("Cover:", cover_row)

        layout.addWidget(options_form_host)

        layout.addSpacing(12)

        # --- Conversion action + progress (M5.5) ---------------------------
        convert_row = QWidget(central)
        convert_row_layout = QHBoxLayout(convert_row)
        convert_row_layout.setContentsMargins(0, 0, 0, 0)
        self._convert_button = QPushButton("Convert", convert_row)
        self._convert_button.setObjectName("convertButton")
        self._convert_button.clicked.connect(self.convert_selected)
        convert_row_layout.addWidget(self._convert_button)
        self._progress_bar = QProgressBar(convert_row)
        self._progress_bar.setObjectName("progressBar")
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        convert_row_layout.addWidget(self._progress_bar, 1)
        layout.addWidget(convert_row)

        layout.addSpacing(12)

        # --- Results + validation section (M5.6) ---------------------------
        self._build_results_section(central, layout)

        layout.addSpacing(12)

        self._status_label = QLabel(central)
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addStretch(1)

        self.setCentralWidget(central)

    @staticmethod
    def _add_analysis_row(
        form: QFormLayout, label: str, object_name: str, parent: QWidget
    ) -> QLabel:
        """Add one ``label``/value row to the analysis form and return the value."""
        name = QLabel(label, parent)
        value = QLabel(_NO_VALUE, parent)
        value.setObjectName(object_name)
        form.addRow(name, value)
        return value

    def _build_results_section(self, central: QWidget, layout: QVBoxLayout) -> None:
        """Build the M5.6 results + validation presentation.

        The section is hidden by default and becomes visible only when a real
        :class:`~kindle_converter.application.ConversionResult` is rendered
        (:meth:`_populate_results`); it is hidden again by
        :meth:`_clear_result` whenever a new request invalidates the previous
        result. Every widget carries a stable ``objectName`` so tests (and
        later milestones) can locate it without depending on layout order.
        Output paths and validation values are populated from the exact
        application result fields -- never recomputed and never revalidated.
        """
        section = QWidget(central)
        section.setObjectName("resultsSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel(_RESULTS_SECTION_TEXT, section)
        title.setObjectName("resultsSectionTitle")
        section_layout.addWidget(title)

        form_host = QWidget(section)
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        self._result_status_value = self._add_analysis_row(
            form, "Status:", "resultStatusValue", form_host
        )
        self._result_format_value = self._add_analysis_row(
            form, "Format:", "resultFormatValue", form_host
        )
        self._epub_output_value = self._add_analysis_row(
            form, "EPUB output:", "epubOutputPath", form_host
        )
        self._azw3_output_label = QLabel("AZW3 output:", form_host)
        self._azw3_output_value = QLabel(_NO_VALUE, form_host)
        self._azw3_output_value.setObjectName("azw3OutputPath")
        form.addRow(self._azw3_output_label, self._azw3_output_value)
        section_layout.addWidget(form_host)

        validation_title = QLabel(_VALIDATION_SECTION_TEXT, section)
        validation_title.setObjectName("validationSectionTitle")
        section_layout.addWidget(validation_title)

        validation_form_host = QWidget(section)
        validation_form = QFormLayout(validation_form_host)
        validation_form.setContentsMargins(0, 0, 0, 0)
        self._validation_status_value = self._add_analysis_row(
            validation_form, "Status:", "validationStatusValue", validation_form_host
        )
        self._validation_warnings_value = self._add_analysis_row(
            validation_form, "Warnings:", "validationWarningsValue", validation_form_host
        )
        self._validation_errors_value = self._add_analysis_row(
            validation_form, "Errors:", "validationErrorsValue", validation_form_host
        )
        section_layout.addWidget(validation_form_host)

        self._validation_warnings_details = QLabel("", section)
        self._validation_warnings_details.setObjectName("validationWarningsDetails")
        self._validation_warnings_details.setWordWrap(True)
        self._validation_warnings_details.setVisible(False)
        section_layout.addWidget(self._validation_warnings_details)

        self._validation_errors_details = QLabel("", section)
        self._validation_errors_details.setObjectName("validationErrorsDetails")
        self._validation_errors_details.setWordWrap(True)
        self._validation_errors_details.setVisible(False)
        section_layout.addWidget(self._validation_errors_details)

        buttons_host = QWidget(section)
        buttons = QHBoxLayout(buttons_host)
        buttons.setContentsMargins(0, 0, 0, 0)
        self._open_epub_button = QPushButton("Open EPUB", buttons_host)
        self._open_epub_button.setObjectName("openEpubButton")
        self._open_epub_button.clicked.connect(self.open_epub)
        buttons.addWidget(self._open_epub_button)
        self._open_azw3_button = QPushButton("Open AZW3", buttons_host)
        self._open_azw3_button.setObjectName("openAzw3Button")
        self._open_azw3_button.clicked.connect(self.open_azw3)
        buttons.addWidget(self._open_azw3_button)
        self._open_folder_button = QPushButton("Open Folder", buttons_host)
        self._open_folder_button.setObjectName("openFolderButton")
        self._open_folder_button.clicked.connect(self.open_output_folder)
        buttons.addWidget(self._open_folder_button)
        buttons.addStretch(1)
        section_layout.addWidget(buttons_host)

        self._output_action_status = QLabel("", section)
        self._output_action_status.setObjectName("outputActionStatus")
        self._output_action_status.setWordWrap(True)
        self._output_action_status.setVisible(False)
        section_layout.addWidget(self._output_action_status)

        section.setVisible(False)
        self._results_section = section
        layout.addWidget(section)

    # ------------------------------------------------------------------
    # State / display helpers
    # ------------------------------------------------------------------

    def _apply_state(self, state: UiState, status: str | None = None) -> None:
        """Apply ``state``: widget enablement, ``self._state``, status text.

        ``status`` overrides the state's default status message (used to show
        concrete error text for :attr:`UiState.ANALYSIS_FAILED`, the
        :attr:`UiState.CONFIGURING` hint, and conversion failures).

        Conversion-option widgets (output format, output directory, cover) are
        enabled only once an analysis has completed successfully -- the M5.4
        workflow is linear (analyze first, then configure). During a
        conversion (:attr:`UiState.CONVERTING`) every control that could
        change the active request is disabled. The progress bar is
        indeterminate while a conversion runs (the M5.1 progress model is
        stage-oriented, so no percentage is fabricated), full on success, and
        reset on failure/idle.
        """
        self._state = state
        busy = state in (UiState.ANALYZING, UiState.CONVERTING)
        has_input = state is not UiState.NO_INPUT
        analysis_done = state in (
            UiState.ANALYSIS_COMPLETE,
            UiState.CONFIGURING,
            UiState.READY,
            UiState.CONVERTING,
            UiState.COMPLETED,
            UiState.CONVERSION_FAILED,
        )
        convert_ready = state in (
            UiState.READY,
            UiState.COMPLETED,
            UiState.CONVERSION_FAILED,
        )
        self._browse_button.setEnabled(not busy)
        self._analyze_button.setEnabled(has_input and not busy)
        self._output_format_combo.setEnabled(analysis_done and not busy)
        self._output_directory_button.setEnabled(analysis_done and not busy)
        self._cover_browse_button.setEnabled(analysis_done and not busy)
        self._cover_clear_button.setEnabled(analysis_done and not busy)
        self._convert_button.setEnabled(convert_ready and not busy)

        if state is UiState.CONVERTING:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setValue(0)
        elif state is UiState.COMPLETED:
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(1)
        else:
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)

        if status is not None:
            self._status_label.setText(status)
        elif state is UiState.NO_INPUT:
            self._status_label.setText(_STATUS_NO_INPUT)
        elif state is UiState.INPUT_SELECTED:
            self._status_label.setText(_STATUS_INPUT_SELECTED)
        elif state is UiState.ANALYZING:
            self._status_label.setText(_STATUS_ANALYZING)
        elif state is UiState.ANALYSIS_COMPLETE:
            self._status_label.setText(_STATUS_ANALYSIS_COMPLETE)
        elif state is UiState.ANALYSIS_FAILED:
            self._status_label.setText(_STATUS_ANALYSIS_FAILED)
        elif state is UiState.CONFIGURING:
            self._status_label.setText(_STATUS_CONFIGURING)
        elif state is UiState.READY:
            self._status_label.setText(_STATUS_READY)
        elif state is UiState.CONVERTING:
            self._status_label.setText(_STATUS_CONVERTING)
        elif state is UiState.COMPLETED:
            self._status_label.setText(_STATUS_COMPLETED)
        elif state is UiState.CONVERSION_FAILED:
            self._status_label.setText(_STATUS_CONVERSION_FAILED)

    def _show_error(self, message: str) -> None:
        """Enter the failed state, never presenting stale analysis as current."""
        self._clear_analysis()
        self._apply_state(UiState.ANALYSIS_FAILED, status=message)

    def _clear_analysis(self) -> None:
        """Reset the analysis summary to its empty placeholders."""
        self._last_analysis = None
        self._pages_value.setText(_NO_VALUE)
        self._document_type_value.setText(_NO_VALUE)
        self._text_pages_value.setText(_NO_VALUE)
        self._scanned_pages_value.setText(_NO_VALUE)
        self._mixed_pages_value.setText(_NO_VALUE)
        self._ocr_required_value.setText(_NO_VALUE)

    def _clear_result(self) -> None:
        """Drop the retained result and hide its presentation (M5.6).

        Called whenever the current request is invalidated -- a new input, a
        conversion-option change, or the start of another conversion -- so a
        previous ``ConversionResult`` is never presented as though it belonged
        to the current request. The output files on disk are never touched.
        """
        self._conversion_result = None
        self._results_section.setVisible(False)

    def _populate_results(self, result: ConversionResult) -> None:
        """Render the real ``ConversionResult`` in the results section.

        Every value is read from the application result object: the completion
        status, the output format (from ``requested_formats``), the exact
        ``epub_path``/``azw3_path``, and the existing
        :class:`~kindle_converter.epub.EPUBValidationResult`. Nothing is
        recomputed, rerun, or reconstructed, and the section becomes visible.
        """
        self._set_output_action_status("")
        self._result_status_value.setText(_RESULT_STATUS_COMPLETE)
        self._result_format_value.setText(_result_format_label(result))
        self._epub_output_value.setText(str(result.epub_path))
        has_azw3 = result.azw3_path is not None
        self._azw3_output_label.setVisible(has_azw3)
        self._azw3_output_value.setVisible(has_azw3)
        self._azw3_output_value.setText(
            str(result.azw3_path) if has_azw3 else _NO_VALUE
        )
        self._open_azw3_button.setVisible(has_azw3)
        self._populate_validation(result.validation)
        self._results_section.setVisible(True)

    def _populate_validation(
        self, validation: EPUBValidationResult | None
    ) -> None:
        """Render the existing ``EPUBValidationResult`` (never rerun).

        ``validation`` is exactly the application result's ``validation``
        field. When validation was disabled it is ``None`` and the section
        reports "Not run"; otherwise the status, error/warning counts, and the
        structured issue messages come straight from the result object.
        """
        if validation is None:
            self._validation_status_value.setText(_VALIDATION_NOT_RUN)
            self._validation_warnings_value.setText(_NO_VALUE)
            self._validation_errors_value.setText(_NO_VALUE)
            self._validation_warnings_details.setVisible(False)
            self._validation_errors_details.setVisible(False)
            return
        self._validation_status_value.setText(
            _VALIDATION_VALID if validation.valid else _VALIDATION_INVALID
        )
        self._validation_warnings_value.setText(str(validation.warning_count))
        self._validation_errors_value.setText(str(validation.error_count))
        warnings_text = "\n".join(
            f"\u2022 {issue.message}" for issue in validation.warnings
        )
        errors_text = "\n".join(
            f"\u2022 {issue.message}" for issue in validation.errors
        )
        self._validation_warnings_details.setText(warnings_text)
        self._validation_warnings_details.setVisible(bool(warnings_text))
        self._validation_errors_details.setText(errors_text)
        self._validation_errors_details.setVisible(bool(errors_text))

    def _display_analysis(self, analysis: PDFAnalysis) -> None:
        """Render the existing ``PDFAnalysis`` fields in the summary form.

        Text/scanned/mixed counts are derived deterministically from the
        analysis' per-page ``PageAnalysis.classification`` values (mutually
        exclusive, summing to the page count). ``OCR required`` is ``Yes``
        exactly when any page is classified SCANNED or MIXED (those are the
        pages the M3.5 routing layer renders and OCR's).
        """
        text_pages, scanned_pages, mixed_pages = _page_breakdown(analysis)
        self._pages_value.setText(str(analysis.page_count))
        self._document_type_value.setText(_human_type(analysis.document_type))
        self._text_pages_value.setText(str(text_pages))
        self._scanned_pages_value.setText(str(scanned_pages))
        self._mixed_pages_value.setText(str(mixed_pages))
        self._ocr_required_value.setText(
            "Yes" if (scanned_pages + mixed_pages) > 0 else "No"
        )

    @staticmethod
    def _translate_invalid_request(exc: InvalidRequestError) -> str:
        """Map an application ``InvalidRequestError`` to a user-readable message."""
        message = str(exc)
        if "does not exist" in message:
            return _ERROR_NOT_FOUND
        if "not a regular file" in message:
            return _ERROR_NOT_A_FILE
        if "cannot be read" in message:
            return _ERROR_CANNOT_READ
        return _ERROR_INVALID_INPUT


def _page_breakdown(analysis: PDFAnalysis) -> tuple[int, int, int]:
    """Return ``(text_pages, scanned_pages, mixed_pages)`` for ``analysis``.

    Both real analyses (per-page classifications present) and minimal stub
    values (no per-page rows) are supported: with rows, the three counts are
    the per-page classifications and sum to the page count; without rows the
    ``text_page_count`` is used and the remaining pages are treated as
    scanned (the analyzer classifies every page without meaningful text as
    SCANNED).
    """
    if analysis.pages:
        text = scanned = mixed = 0
        for page in analysis.pages:
            if page.classification is PDFType.SCANNED:
                scanned += 1
            elif page.classification is PDFType.MIXED:
                mixed += 1
            else:
                text += 1
        return text, scanned, mixed
    text = analysis.text_page_count
    scanned = max(analysis.page_count - text, 0)
    return text, scanned, 0


def _human_type(document_type: PDFType | None) -> str:
    """Format a ``PDFType`` for display (``PDFType.TEXT`` -> ``"Text"``)."""
    if document_type is None:
        return "Unknown"
    return document_type.value.capitalize()


def _result_format_label(result: ConversionResult) -> str:
    """A deterministic, user-readable output-format label for ``result``.

    Reads the request's formats from the result itself (the application
    snapshot, never the current UI combo): EPUB always precedes AZW3 in the
    label, producing ``"EPUB"`` or ``"EPUB + AZW3"``.
    """
    names = []
    if OutputFormat.EPUB in result.requested_formats:
        names.append("EPUB")
    if OutputFormat.AZW3 in result.requested_formats:
        names.append("AZW3")
    return " + ".join(names) if names else "Unknown"