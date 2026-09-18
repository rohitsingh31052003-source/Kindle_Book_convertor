"""AZW3 conversion tests (Milestone 4.3).

Deterministic and fully offline: Calibre is never required and the real
``ebook-convert`` executable is never launched here. The Calibre adapter is
exercised through a stubbed ``subprocess.run`` that records the exact argv
(and asserts ``shell=False``) and scripts the exit code / output; the public
API is exercised with injected fake backends. This keeps command
construction, executable discovery, exit-code handling, output verification,
and the public error taxonomy fully deterministic and cross-platform.

The optional real-Calibre integration tests live in
``tests/test_epub_azw3_calibre.py`` and skip cleanly when Calibre is absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kindle_converter.epub import (
    AZW3BackendUnavailableError,
    AZW3ConversionBackend,
    AZW3ConversionError,
    AZW3ConversionFailedError,
    AZW3ConversionResult,
    AZW3InvalidInputError,
    AZW3InvalidOutputError,
    CalibreBackend,
    convert_epub_to_azw3,
)
from kindle_converter.epub.calibre import (
    CALIBRE_EXECUTABLE,
    MAX_REPORTED_OUTPUT_CHARS,
    build_calibre_command,
)

#: The fake payload a stubbed "successful" conversion writes.
FAKE_AZW3 = b"fake AZW3 payload for the M4.3 tests"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_epub(tmp_path: Path, name: str = "book.epub") -> Path:
    """A trivially existing source EPUB (content is not inspected here)."""
    path = tmp_path / name
    path.write_bytes(b"M4.3 test EPUB (contents are not inspected)")
    return path


def _existing_exe(tmp_path: Path, name: str = "ebook-convert.exe") -> Path:
    """A dummy file that exists (the constructor only checks existence)."""
    path = tmp_path / name
    path.write_bytes(b"")
    return path


class StubSubprocess:
    """Records each ``subprocess.run`` call and returns a scripted result.

    Instances are installed as ``kindle_converter.epub.calibre.subprocess.run``
    so the real Calibre executable is never invoked. The stub always asserts
    the process is launched with ``shell=False``, and by default writes the
    fake AZW3 payload to the destination path it was given.
    """

    def __init__(
        self,
        returncode: int = 0,
        *,
        write_output: bool = True,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.write_output = write_output
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((list(args), kwargs))
        assert kwargs.get("shell") is False, "subprocess must run without a shell"
        if self.write_output and len(args) >= 3:
            Path(args[-1]).write_bytes(FAKE_AZW3)
        return subprocess.CompletedProcess(
            args, self.returncode, self.stdout, self.stderr
        )


@pytest.fixture
def stub_subprocess(monkeypatch) -> StubSubprocess:
    """Install a recording, success-by-default ``subprocess.run``."""
    instance = StubSubprocess()
    monkeypatch.setattr("kindle_converter.epub.calibre.subprocess.run", instance)
    return instance


class FakeBackend:
    """A deterministic :class:`AZW3ConversionBackend` for API-level tests.

    ``convert`` records its call, then either raises a configured error or
    materializes the destination: a non-empty payload, an empty file, or a
    directory -- whichever the test needs.
    """

    def __init__(
        self,
        *,
        error: Exception | None = None,
        write_payload: bytes | None = FAKE_AZW3,
        make_directory: bool = False,
    ) -> None:
        self.error = error
        self.write_payload = write_payload
        self.make_directory = make_directory
        self.calls: list[tuple[Path, Path]] = []

    def convert(self, source: Path, destination: Path) -> None:
        self.calls.append((Path(source), Path(destination)))
        if self.error is not None:
            raise self.error
        if self.make_directory:
            destination.mkdir(exist_ok=True)
        elif self.write_payload is not None:
            destination.write_bytes(self.write_payload)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


class TestPublicAPI:
    def test_public_names_are_exported(self) -> None:
        import kindle_converter
        from kindle_converter import epub

        assert callable(epub.convert_epub_to_azw3)
        assert callable(kindle_converter.epub.convert_epub_to_azw3)
        assert epub.CalibreBackend is CalibreBackend
        assert epub.AZW3ConversionBackend is AZW3ConversionBackend

    def test_error_taxonomy_subclasses_base(self) -> None:
        for error in (
            AZW3BackendUnavailableError,
            AZW3ConversionFailedError,
            AZW3InvalidInputError,
            AZW3InvalidOutputError,
        ):
            assert issubclass(error, AZW3ConversionError)

    def test_errors_are_distinct(self) -> None:
        errors = {
            AZW3BackendUnavailableError,
            AZW3ConversionFailedError,
            AZW3InvalidInputError,
            AZW3InvalidOutputError,
        }
        assert len(errors) == 4

    def test_valid_conversion_returns_result(self, tmp_path: Path) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "book.azw3"
        backend = FakeBackend()
        result = convert_epub_to_azw3(source, destination, backend=backend)
        assert isinstance(result, AZW3ConversionResult)
        assert result.source == Path(source)
        assert result.destination == Path(destination)
        assert destination.is_file()
        assert destination.stat().st_size > 0

    def test_injected_backend_takes_precedence_over_calibre_path(
        self, tmp_path: Path
    ) -> None:
        source = _make_epub(tmp_path)
        backend = FakeBackend()
        missing = tmp_path / "missing-ebook-convert.exe"
        result = convert_epub_to_azw3(
            source, tmp_path / "out.azw3", calibre_path=missing, backend=backend
        )
        assert result.destination == tmp_path / "out.azw3"
        # The missing calibre_path was never consulted.
        assert backend.calls[-1] == (Path(source), Path(result.destination))


# --------------------------------------------------------------------------- #
# Command construction
# --------------------------------------------------------------------------- #


class TestCommandConstruction:
    def test_command_is_executable_then_input_then_output(self) -> None:
        command = build_calibre_command(
            "C:/Calibre/ebook-convert.exe", "in.epub", "out.azw3"
        )
        assert command == ["C:/Calibre/ebook-convert.exe", "in.epub", "out.azw3"]

    def test_paths_with_spaces_are_preserved_as_single_arguments(self) -> None:
        source = "C:/My Books/Input Dir/Chapter 1.epub"
        destination = "C:/Kindle Output/Dir/book file.azw3"
        command = build_calibre_command(
            "C:/Program Files/Calibre/ebook-convert.exe", source, destination
        )
        assert len(command) == 3
        assert command[1] == source
        assert command[2] == destination

    def test_construction_is_deterministic(self) -> None:
        args = ("C:/Calibre/ebook-convert.exe", "C:/in book.epub", "C:/out book.azw3")
        assert build_calibre_command(*args) == build_calibre_command(*args)

    def test_changing_output_changes_only_the_output_argument(self) -> None:
        executable, source = "ebook-convert.exe", "in.epub"
        base = build_calibre_command(executable, source, "a.azw3")
        changed = build_calibre_command(executable, source, "b.azw3")
        assert base[:2] == changed[:2]
        assert base[2] != changed[2]


# --------------------------------------------------------------------------- #
# Executable discovery
# --------------------------------------------------------------------------- #


class TestExecutableDiscovery:
    def test_explicit_executable_path_is_respected(
        self, tmp_path: Path, stub_subprocess: StubSubprocess
    ) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "out.azw3"
        exe = _existing_exe(tmp_path, "custom-ebook-convert.exe")
        convert_epub_to_azw3(source, destination, calibre_path=exe)
        args, _ = stub_subprocess.calls[-1]
        assert args[0] == str(exe)
        assert args[1] == str(source)
        assert args[2] == str(destination)

    def test_constructor_rejects_missing_explicit_executable(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "no-such-ebook-convert.exe"
        with pytest.raises(AZW3BackendUnavailableError, match="does not exist"):
            CalibreBackend(executable=missing)

    def test_missing_explicit_executable_raises_via_api(self, tmp_path: Path) -> None:
        source = _make_epub(tmp_path)
        with pytest.raises(AZW3BackendUnavailableError, match="does not exist"):
            convert_epub_to_azw3(
                source, tmp_path / "out.azw3", calibre_path=tmp_path / "missing.exe"
            )

    def test_path_discovery_is_attempted_without_explicit_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = _make_epub(tmp_path)
        discovered = _existing_exe(tmp_path, "discovered-ebook-convert.exe")
        seen: list[str] = []

        def fake_which(tool: str) -> str | None:
            seen.append(tool)
            return str(discovered)

        instance = StubSubprocess()
        monkeypatch.setattr("kindle_converter.epub.calibre.subprocess.run", instance)
        monkeypatch.setattr("kindle_converter.epub.calibre.shutil.which", fake_which)

        convert_epub_to_azw3(source, tmp_path / "out.azw3")

        assert seen == [CALIBRE_EXECUTABLE]
        assert instance.calls[-1][0][0] == str(discovered)

    def test_path_discovery_failure_raises_backend_unavailable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = _make_epub(tmp_path)
        monkeypatch.setattr(
            "kindle_converter.epub.calibre.shutil.which", lambda tool: None
        )
        with pytest.raises(AZW3BackendUnavailableError) as excinfo:
            convert_epub_to_azw3(source, tmp_path / "out.azw3")
        text = str(excinfo.value).lower()
        assert CALIBRE_EXECUTABLE in text
        assert "calibre" in text
        assert "path" in text


# --------------------------------------------------------------------------- #
# Subprocess behavior (stubbed, no real Calibre)
# --------------------------------------------------------------------------- #


class TestSubprocessBehavior:
    def test_invokes_run_without_shell_and_captures_output(
        self, tmp_path: Path, stub_subprocess: StubSubprocess
    ) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "out.azw3"
        exe = _existing_exe(tmp_path)
        convert_epub_to_azw3(source, destination, calibre_path=exe)
        args, kwargs = stub_subprocess.calls[-1]
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert destination.is_file()
        assert destination.stat().st_size > 0

    def test_successful_conversion_verifies_output_and_returns_result(
        self, tmp_path: Path, stub_subprocess: StubSubprocess
    ) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "converted.azw3"
        result = convert_epub_to_azw3(
            source, destination, calibre_path=_existing_exe(tmp_path)
        )
        assert isinstance(result, AZW3ConversionResult)
        assert result.destination == destination
        assert Path(destination).read_bytes() == FAKE_AZW3

    def test_nonzero_exit_raises_conversion_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "out.azw3"
        instance = StubSubprocess(
            returncode=7,
            write_output=False,
            stderr="boom: cannot parse the input EPUB",
        )
        monkeypatch.setattr("kindle_converter.epub.calibre.subprocess.run", instance)
        with pytest.raises(AZW3ConversionFailedError) as excinfo:
            convert_epub_to_azw3(
                source, destination, calibre_path=_existing_exe(tmp_path)
            )
        text = str(excinfo.value)
        assert "7" in text
        assert str(source) in text
        assert str(destination) in text
        assert "boom: cannot parse the input EPUB" in text
        assert not destination.exists()

    def test_stderr_is_preferred_and_output_is_capped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "out.azw3"
        huge = "x" * 100_000
        instance = StubSubprocess(
            returncode=3,
            write_output=False,
            stderr="ERR:" + huge,
            stdout="STDOUT:" + huge,
        )
        monkeypatch.setattr("kindle_converter.epub.calibre.subprocess.run", instance)
        with pytest.raises(AZW3ConversionFailedError) as excinfo:
            convert_epub_to_azw3(
                source, destination, calibre_path=_existing_exe(tmp_path)
            )
        text = str(excinfo.value)
        assert text.startswith("AZW3 conversion failed:")
        assert "ERR:" in text
        assert "STDOUT:" not in text
        # The diagnostic detail is capped at MAX_REPORTED_OUTPUT_CHARS; the
        # message never dumps the 100 KB blobs.
        assert text.endswith("...")
        assert len(text) < MAX_REPORTED_OUTPUT_CHARS + 1000

    def test_unrunnable_executable_raises_backend_unavailable(
        self, tmp_path: Path
    ) -> None:
        dummy = tmp_path / "not-a-program.txt"
        dummy.write_text("this is not an executable", encoding="ascii")
        source = _make_epub(tmp_path)
        backend = CalibreBackend(executable=dummy)
        with pytest.raises(AZW3BackendUnavailableError, match="could not run"):
            backend.convert(source, tmp_path / "out.azw3")


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


class TestInputValidation:
    def test_missing_source_raises_invalid_input(self, tmp_path: Path) -> None:
        with pytest.raises(AZW3InvalidInputError, match="does not exist"):
            convert_epub_to_azw3(tmp_path / "missing.epub", tmp_path / "out.azw3")

    def test_directory_source_raises_invalid_input(self, tmp_path: Path) -> None:
        directory = tmp_path / "book directory"
        directory.mkdir()
        with pytest.raises(AZW3InvalidInputError, match="not a regular file"):
            convert_epub_to_azw3(directory, tmp_path / "out.azw3")

    def test_non_path_source_raises_invalid_input(self, tmp_path: Path) -> None:
        with pytest.raises(AZW3InvalidInputError, match="filesystem path"):
            convert_epub_to_azw3(None, tmp_path / "out.azw3")  # type: ignore[arg-type]

    def test_non_path_destination_raises_invalid_input(self, tmp_path: Path) -> None:
        source = _make_epub(tmp_path)
        with pytest.raises(AZW3InvalidInputError, match="filesystem path"):
            convert_epub_to_azw3(source, None)  # type: ignore[arg-type]

    def test_input_validation_happens_before_backend_ran(
        self, tmp_path: Path, stub_subprocess: StubSubprocess
    ) -> None:
        with pytest.raises(AZW3InvalidInputError):
            convert_epub_to_azw3(tmp_path / "missing.epub", tmp_path / "out.azw3")
        assert not stub_subprocess.calls


# --------------------------------------------------------------------------- #
# Output validation
# --------------------------------------------------------------------------- #


class TestOutputValidation:
    def test_backend_receives_source_and_destination(
        self, tmp_path: Path
    ) -> None:
        source = _make_epub(tmp_path)
        destination = tmp_path / "out.azw3"
        backend = FakeBackend()
        convert_epub_to_azw3(source, destination, backend=backend)
        assert backend.calls[-1] == (Path(source), Path(destination))

    def test_missing_output_raises_invalid_output(self, tmp_path: Path) -> None:
        backend = FakeBackend(write_payload=None)
        source = _make_epub(tmp_path)
        with pytest.raises(AZW3InvalidOutputError, match="no output"):
            convert_epub_to_azw3(source, tmp_path / "out.azw3", backend=backend)

    def test_empty_output_raises_invalid_output(self, tmp_path: Path) -> None:
        backend = FakeBackend(write_payload=b"")
        source = _make_epub(tmp_path)
        with pytest.raises(AZW3InvalidOutputError, match="empty"):
            convert_epub_to_azw3(source, tmp_path / "out.azw3", backend=backend)

    def test_directory_output_raises_invalid_output(self, tmp_path: Path) -> None:
        backend = FakeBackend(make_directory=True)
        source = _make_epub(tmp_path)
        with pytest.raises(AZW3InvalidOutputError, match="not a regular file"):
            convert_epub_to_azw3(source, tmp_path / "out.azw3", backend=backend)

    def test_backend_failure_propagates_unchanged(self, tmp_path: Path) -> None:
        backend = FakeBackend(error=AZW3ConversionFailedError("backend failed"))
        source = _make_epub(tmp_path)
        with pytest.raises(AZW3ConversionFailedError, match="backend failed"):
            convert_epub_to_azw3(source, tmp_path / "out.azw3", backend=backend)


# --------------------------------------------------------------------------- #
# Cross-platform paths
# --------------------------------------------------------------------------- #


class TestCrossPlatformPaths:
    def test_spaces_and_nested_directories_roundtrip_through_subprocess(
        self, tmp_path: Path, stub_subprocess: StubSubprocess
    ) -> None:
        source_dir = tmp_path / "source dir" / "nested"
        source_dir.mkdir(parents=True)
        source = source_dir / "my book.epub"
        source.write_bytes(b"epub")

        destination_dir = tmp_path / "output dir" / "nested"
        destination_dir.mkdir(parents=True)
        destination = destination_dir / "my book file.azw3"

        exe_dir = tmp_path / "Program Files" / "Calibre"
        exe_dir.mkdir(parents=True)
        exe = exe_dir / "ebook-convert.exe"
        exe.write_bytes(b"")

        convert_epub_to_azw3(source, destination, calibre_path=exe)

        args, _ = stub_subprocess.calls[-1]
        assert args == [str(exe), str(source), str(destination)]
        assert destination.is_file()
        assert destination.read_bytes() == FAKE_AZW3