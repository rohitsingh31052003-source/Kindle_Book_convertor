"""M6.5 — Windows packaging tests.

These tests cover the packaging *configuration* deterministically, without a
build artifact: version/identity single-sourcing, the dependency contract the
bundle must ship, version-resource generation, PE inspection, development-path
scanning, and the headless smoke dispatch seam. Execution of the real artifact
is performed by :mod:`build_tools.verify_windows_package` on the built bundle
(see ``docs/windows-packaging.md``).

The Qt-dependent smoke tests are skipped cleanly when PySide6 is absent (the
``ui`` extra), matching the existing ``ui`` test modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from build_tools import common
from build_tools.verify_windows_package import (
    forbidden_items_present,
    missing_runtime_components,
    parse_version_resource_text,
    run_app,
)

pytestmark = pytest.mark.packaging


def test_build_tools_resolves_to_repository_not_site_packages() -> None:
    """``build_tools`` must shadow nothing and come from this checkout."""
    assert "site-packages" not in str(Path(common.__file__).resolve())
    assert common.REPO_ROOT.is_dir()
    assert (common.REPO_ROOT / "pyproject.toml").is_file()


class TestVersionSingleSourcing:
    """pyproject.toml is the single version source; every consumer agrees."""

    def test_pyproject_version_equals_package_version(self) -> None:
        import kindle_converter

        assert common.project_version() == kindle_converter.__version__
        assert kindle_converter.__version__  # non-empty (M1 contract)

    def test_fallback_matches_pyproject(self) -> None:
        from kindle_converter._meta import _VERSION_FALLBACK

        assert _VERSION_FALLBACK == common.project_version()

    def test_resolved_version_matches_pyproject(self) -> None:
        from kindle_converter._meta import application_version

        assert application_version() == common.project_version()

    def test_identity_constants_agree(self) -> None:
        from kindle_converter import _meta

        assert common.DISTRIBUTION_NAME == _meta.DISTRIBUTION_NAME
        assert common.EXECUTABLE_NAME == _meta.EXECUTABLE_NAME
        assert common.APP_NAME == _meta.APP_NAME
        assert common.BUNDLE_NAME == _meta.EXECUTABLE_NAME


class TestDependencyContract:
    """The frozen bundle must ship exactly the project's declared deps."""

    def test_required_dependencies_pinned(self) -> None:
        deps = common.project_dependencies()
        assert deps["required"] == ["PyMuPDF>=1.24,<2", "ebooklib>=0.19,<1"]

    def test_optional_dependency_categories(self) -> None:
        deps = common.project_dependencies()
        assert deps["ocr"] == ["pytesseract>=0.3.13,<1", "Pillow>=10.0,<13"]
        assert deps["ui"] == ["PySide6>=6.8,<7"]
        assert deps["dev"] == ["pytest>=8.0,<9"]

    def test_version_resource_generation(self) -> None:
        text = common.version_info_text("0.1.0")
        assert "filevers=(0, 1, 0, 0)" in text
        assert "prodvers=(0, 1, 0, 0)" in text
        assert "StringStruct('FileVersion', '0, 1, 0, 0')" in text
        assert "StringStruct('OriginalFilename', 'KindleBookConverter.exe')" in text
        assert "'000004b0'" in text

    def test_version_resource_accepts_any_version_string(self) -> None:
        text = common.version_info_text("1.2.3.4")
        assert "filevers=(1, 2, 3, 4)" in text
        text = common.version_info_text("2.0")
        assert "filevers=(2, 0, 0, 0)" in text


class TestVersionResourceParsing:
    """The verifier's ``pyi-grab_version``-output parser."""

    def test_parses_file_version_fields(self) -> None:
        output = (
            "VSVersionInfo(\n"
            "  ffi=FixedFileInfo(\n"
            "    filevers=(0, 1, 0, 0),\n"
            "    prodvers=(6, 22, 3, 0),\n"
            "  kids=[\n"
            "    StringFileInfo([StringTable('000004b0',\n"
            "      [StringStruct('FileVersion', '0, 1, 0, 0'),\n"
            "       StringStruct('ProductVersion', '0.1.0')]))\n"
        )
        values = parse_version_resource_text(output)
        assert values["filevers"] == "(0, 1, 0, 0)"
        assert values["prodvers"] == "(6, 22, 3, 0)"
        assert values["FileVersion"] == "0, 1, 0, 0"
        assert values["ProductVersion"] == "0.1.0"

    def test_empty_on_unrelated_text(self) -> None:
        assert parse_version_resource_text("hello world") == {}


class TestPEInspection:
    """PE-subsystem detection used by the verifier."""

    def test_non_pe_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "not.exe"
        path.write_bytes(b"MZ no such thing here")
        assert common.pe_subsystem(path) is None
        assert common.is_gui_executable(path) is False

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert common.pe_subsystem(tmp_path / "absent.exe") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows PE only")
    def test_interpreter_is_a_console_pe(self) -> None:
        """``python.exe`` is a console (not GUI) subsystem PE image."""
        assert common.pe_subsystem(Path(sys.executable)) == 3
        assert common.is_gui_executable(Path(sys.executable)) is False


class TestBundleLayoutChecks:
    """Pure layout checks used by the verifier (armored offline)."""

    def test_runtime_components_recognized(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "python314.dll").write_bytes(b"x")
        pyside = bundle / "PySide6"
        pyside.mkdir(parents=True)
        (pyside / "Qt6Core.dll").write_bytes(b"x")
        plugins = pyside / "plugins"
        (plugins / "platforms").mkdir(parents=True)
        (plugins / "platforms" / "qwindows.dll").write_bytes(b"x")
        (plugins / "imageformats").mkdir(parents=True)
        (plugins / "imageformats" / "qpng.dll").write_bytes(b"x")
        (bundle / "kindle_converter-0.1.0.dist-info").mkdir()
        (bundle / "pymupdf").mkdir()
        (bundle / "pymupdf" / "_mupdf.pyd").write_bytes(b"x")
        (bundle / "ebooklib-0.20.0.dist-info").mkdir()
        (bundle / "lxml").mkdir()
        (bundle / "lxml" / "etree.cp312-win_amd64.pyd").write_bytes(b"x")
        (bundle / "PIL").mkdir()
        (bundle / "PIL" / "_imaging.cp312-win_amd64.pyd").write_bytes(b"x")
        (bundle / "pytesseract-0.3.13.dist-info").mkdir()
        assert missing_runtime_components(bundle) == []

    def test_forbidden_dev_artifacts_detected(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        (bundle / "kindle_converter").mkdir(parents=True)
        (bundle / "tests").mkdir()
        (bundle / "pytest").mkdir()
        (bundle / "pyproject.toml").write_text("[project]\n")
        found = forbidden_items_present(bundle)
        assert any("tests" in item for item in found)
        assert any("pytest" in item for item in found)
        assert any("pyproject.toml" in item for item in found)

    def test_dev_path_scan_finds_plain_and_utf16(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        (bundle / "kindle_converter").mkdir(parents=True)
        plain = bundle / "note.txt"
        plain.write_text("C:/Kindle Book Convertor/src\n", encoding="utf-8")
        wide = bundle / "wide.txt"
        wide.write_text("C:\\Kindle Book Convertor\\build", encoding="utf-16-le")
        matches = common.scan_for_strings(bundle, ["C:\\Kindle Book Convertor"])
        assert "note.txt" in matches
        assert "wide.txt" in matches

    def test_clean_bundle_has_no_leaks(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        (bundle / "kindle_converter").mkdir(parents=True)
        (bundle / "text.txt").write_text("nothing suspicious\n", encoding="utf-8")
        assert common.scan_for_strings(bundle, ["C:\\Kindle Book Convertor"]) == {}


class TestSmokeDispatchSeam:
    """The packaged-entry dispatch (requires the ``ui`` extra)."""

    def test_unknown_or_empty_usage(self) -> None:
        from kindle_converter.ui.smoke import dispatch

        assert dispatch([]) == 2
        assert dispatch(["--nonsense"]) == 2

    def test_dispatch_runs_main_window_check(self) -> None:
        pytest.importorskip("PySide6")
        from kindle_converter.ui.smoke import dispatch

        assert dispatch(["--smoke-check"]) == 0

    def test_ocr_unavailable_classified_as_graceful(self) -> None:
        """Missing-tesseract failures are *expected* (exit 0), not crashes."""
        from kindle_converter.ui.smoke import _is_graceful_unavailable

        message = (
            "conversion failed: ConversionFailedError(\"PDF to EPUB conversion failed: "
            "OCR failed on page 1: The Tesseract executable 'tesseract' was not found "
            "on PATH; install Tesseract or pass an explicit tesseract_cmd to "
            "TesseractEngine.\")"
        )
        assert _is_graceful_unavailable(message) is True
        assert _is_graceful_unavailable("conversion failed: disk full") is False

    def test_sysinfo_returns_json(self) -> None:
        pytest.importorskip("PySide6")
        from kindle_converter.ui.smoke import sysinfo

        import json

        payload = json.loads(sysinfo())
        assert payload["frozen"] is False
        assert payload["application"]["version"] == common.project_version()

    def test_app_main_dispatches_smoke_without_event_loop(self) -> None:
        pytest.importorskip("PySide6")
        from kindle_converter.ui import app

        assert app.main(["--smoke-check"]) == 0
        assert app.main(["--sysinfo"]) == 0


class TestRunAppIsolation:
    """The verifier's isolation helper misbehaves on genuinely foreign args."""

    def test_run_app_reports_bogus_command(self, tmp_path: Path) -> None:
        if sys.platform != "win32":
            pytest.skip("Windows scratch executable only")
        scratch = tmp_path / "exe"
        scratch.mkdir()
        exe = scratch / "echo.exe"
        # A minimal real executable that exits 7.
        script = scratch / "exit.py"
        script.write_text("import sys; sys.exit(7)\n", encoding="utf-8")
        build_python = Path(sys.executable)
        # Use the interpreter to emulate "an executable that returns 7".
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        completed = run_app(
            build_python,
            [str(script)],
            cwd=tmp_path,
            report_path=tmp_path / "out.json",
            timeout=30,
        )
        assert completed[0] == 7
        assert completed[1] is None