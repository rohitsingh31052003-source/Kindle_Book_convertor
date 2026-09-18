"""Unit tests for the M5.6 platform-opening seam (``ui.platform``).

These exercise the real ``open_path`` implementation end-to-end without ever
launching an external program: ``os.startfile`` and ``subprocess.Popen`` are
replaced with recording doubles and ``sys.platform`` is monkeypatched per test.
The module is small and purpose-built, so the three platform branches plus the
two failure translations to :class:`PlatformOpenError` are covered directly.

Importing ``kindle_converter.ui.platform`` loads the ``ui`` package, which
requires PySide6; the module therefore skips cleanly when the optional ``ui``
extra is absent (consistent with the other ``ui`` tests).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from kindle_converter.ui.platform import PlatformOpenError, open_path  # noqa: E402


class _RecordingStarter:
    """Records the path passed to the real ``os.startfile`` behavior."""

    def __init__(self, error: OSError | None = None) -> None:
        self.calls: list[str] = []
        self.error = error

    def __call__(self, path: str) -> None:
        self.calls.append(path)
        if self.error is not None:
            raise self.error

    @property
    def captured(self) -> list[Path]:
        return [Path(call) for call in self.calls]


class _RecordingPopen:
    """Records the argv ``subprocess.Popen`` was asked to launch."""

    def __init__(self, error: OSError | FileNotFoundError | None = None) -> None:
        self.calls: list[list[str]] = []
        self.error = error

    def __call__(self, argv: list[str]) -> None:
        self.calls.append(argv)
        if self.error is not None:
            raise self.error


@pytest.fixture
def platform(monkeypatch) -> str:
    """Fixture callback so tests can switch the reported platform."""

    def _set(name: str) -> None:
        monkeypatch.setattr("kindle_converter.ui.platform.sys.platform", name)

    return _set


class TestOpenPathWindows:
    def test_uses_startfile_with_the_exact_path(self, monkeypatch, platform) -> None:
        starter = _RecordingStarter()
        monkeypatch.setattr("kindle_converter.ui.platform.os.startfile", starter)
        platform("win32")

        open_path(Path(r"C:\Books\MyBook.epub"))

        assert starter.captured == [Path(r"C:\Books\MyBook.epub")]

    def test_raises_platform_open_error_on_oserror(self, monkeypatch, platform) -> None:
        starter = _RecordingStarter(error=OSError(5, "access denied"))
        monkeypatch.setattr("kindle_converter.ui.platform.os.startfile", starter)
        platform("win32")

        with pytest.raises(PlatformOpenError) as exc_info:
            open_path(Path(r"C:\Books\MyBook.epub"))

        assert "could not open" in str(exc_info.value)


class TestOpenPathMacOS:
    def test_uses_open_command_on_darwin(self, monkeypatch, platform) -> None:
        popen = _RecordingPopen()
        monkeypatch.setattr(
            "kindle_converter.ui.platform.subprocess.Popen", popen
        )
        platform("darwin")

        target = Path("/tmp/folder")
        open_path(target)

        assert popen.calls == [["open", str(target)]]

    def test_raises_platform_open_error_on_failure(self, monkeypatch, platform) -> None:
        popen = _RecordingPopen(error=OSError(13, "permission denied"))
        monkeypatch.setattr(
            "kindle_converter.ui.platform.subprocess.Popen", popen
        )
        platform("darwin")

        target = Path("/tmp/folder")
        with pytest.raises(PlatformOpenError) as exc_info:
            open_path(target)

        assert "could not open" in str(exc_info.value)


class TestOpenPathLinux:
    def test_uses_xdg_open_by_default(self, monkeypatch, platform) -> None:
        popen = _RecordingPopen()
        monkeypatch.setattr(
            "kindle_converter.ui.platform.subprocess.Popen", popen
        )
        platform("linux")

        target = Path("/home/user/book.epub")
        open_path(target)

        assert popen.calls == [["xdg-open", str(target)]]

    def test_raises_platform_open_error_when_handler_missing(
        self, monkeypatch, platform
    ) -> None:
        popen = _RecordingPopen(error=FileNotFoundError("xdg-open"))
        monkeypatch.setattr(
            "kindle_converter.ui.platform.subprocess.Popen", popen
        )
        platform("linux")

        target = Path("/home/user/book.epub")
        with pytest.raises(PlatformOpenError) as exc_info:
            open_path(target)

        assert "no system handler" in str(exc_info.value)


class TestOpenPathContract:
    def test_platform_open_error_is_a_user_facing_exception(self) -> None:
        error = PlatformOpenError("an explanation")
        assert "an explanation" in str(error)

    def test_open_path_does_not_assert_existence(self, monkeypatch, platform) -> None:
        # Deliberate contract: existence is the caller's job (so it can choose
        # the precise user-facing message for a missing output).
        starter = _RecordingStarter()
        monkeypatch.setattr("kindle_converter.ui.platform.os.startfile", starter)
        platform("win32")

        open_path(Path(r"C:\Definitely\Missing\Ghost.epub"))

        assert starter.captured == [Path(r"C:\Definitely\Missing\Ghost.epub")]

    def test_os_and_file_not_found_errors_never_leak(self, monkeypatch, platform) -> None:
        def raising_popen(argv: list[str]) -> None:
            raise OSError("boom")

        monkeypatch.setattr("kindle_converter.ui.platform.subprocess.Popen", raising_popen)
        platform("linux")

        with pytest.raises(PlatformOpenError):
            open_path(Path("/tmp/book.epub"))