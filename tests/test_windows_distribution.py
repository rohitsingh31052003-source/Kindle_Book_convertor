"""M8.2 — Windows distribution archive tests.

These tests cover the distribution-packaging *configuration* deterministically,
without a build artifact or archive of the real bundle. They exercise the pure
helpers that :mod:`build_tools.package_distribution` uses to create and verify
production archives: archive naming (from the single version source), checksum
format, reproducible member ordering, archive layout, content parity, and the
GUI-subsystem property of an executable read from *inside* the archive.

The real distribution artifact is produced and verified by
``build_tools/package_distribution.py`` against the built bundle (see
``docs/windows-packaging.md``), exactly as the M6.5 package validation is
performed by ``build_tools/verify_windows_package.py`` and not by unit tests.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from build_tools import common
from build_tools.package_distribution import (
    checksum_hex,
    content_parity_issues,
    create_distribution,
    layout_issues,
    verify_distribution,
)
from build_tools.verify_windows_package import CheckResult

pytestmark = pytest.mark.packaging


def _gui_pe_bytes() -> bytes:
    """A minimal PE32+ image whose ``Subsystem`` field is GUI (2).

    The parser in ``build_tools.common.pe_subsystem_bytes`` only needs the MZ
    magic, the ``e_lfanew`` offset, the ``PE\\0\\0`` signature, the PE32+
    optional-header magic, and the 16-bit Subsystem field at optional offset
    68 -- enough to build a tiny valid image entirely in a test.
    """
    e_lfanew = 0x40
    optional_offset = e_lfanew + 24
    data = bytearray(b"\0" * (optional_offset + 96))
    data[0:2] = b"MZ"
    data[0x3C:0x40] = e_lfanew.to_bytes(4, "little")
    data[e_lfanew : e_lfanew + 4] = b"PE\0\0"
    data[optional_offset : optional_offset + 2] = (0x20B).to_bytes(2, "little")
    data[optional_offset + 68 : optional_offset + 70] = (2).to_bytes(2, "little")
    return bytes(data)


def _console_pe_bytes() -> bytes:
    data = bytearray(_gui_pe_bytes())
    e_lfanew = 0x40
    optional_offset = e_lfanew + 24
    data[optional_offset + 68 : optional_offset + 70] = (3).to_bytes(2, "little")
    return bytes(data)


def _make_bundle(tmp_path: Path, *, exe_bytes: bytes | None = None) -> Path:
    """A synthetic bundle mirroring the real onedir layout."""
    bundle = tmp_path / "KindleBookConverter"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    if exe_bytes is None:
        exe_bytes = _gui_pe_bytes()
    (bundle / "KindleBookConverter.exe").write_bytes(exe_bytes)
    (internal / "python314.dll").write_bytes(b"runtime-bytes")
    module = internal / "kindle_converter"
    module.mkdir()
    (module / "__init__.py").write_text("__version__ = '0.1.0'\n", encoding="utf-8")
    (internal / "kindle_converter-0.1.0.dist-info").mkdir()
    (internal / "kindle_converter-0.1.0.dist-info" / "METADATA").write_text(
        "Name: kindle-converter\nVersion: 0.1.0\n", encoding="utf-8"
    )
    return bundle


class TestDistributionNaming:
    """The archive name is derived from the single version source."""

    def test_archive_name_embeds_platform_and_bundle(self) -> None:
        assert common.distribution_archive_name("0.1.0") == (
            "KindleBookConverter-Windows-x64-0.1.0.zip"
        )
        assert common.PLATFORM_TAG == "Windows-x64"

    def test_default_version_comes_from_pyproject(self) -> None:
        assert common.distribution_archive_name() == (
            f"KindleBookConverter-Windows-x64-{common.project_version()}.zip"
        )

    def test_archive_path_lives_beside_the_bundle(self) -> None:
        archive = common.distribution_archive_path("0.1.0")
        assert archive.parent == common.DIST_DIR
        assert archive.name == "KindleBookConverter-Windows-x64-0.1.0.zip"

    def test_checksum_path_extends_archive_name(self) -> None:
        archive = common.distribution_archive_path("0.1.0")
        assert common.distribution_checksum_path(archive).name == (
            "KindleBookConverter-Windows-x64-0.1.0.zip.sha256"
        )


class TestChecksum:
    """SHA-256 checksum generation produced for the archive."""

    def test_checksum_hex_matches_known_value(self, tmp_path: Path) -> None:
        payload = tmp_path / "payload.bin"
        payload.write_bytes(b"hello world")
        assert checksum_hex(payload) == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_checksum_file_uses_sha256sum_line_format(self, tmp_path: Path) -> None:
        from build_tools.package_distribution import write_checksum

        archive = tmp_path / "KindleBookConverter-Windows-x64-0.1.0.zip"
        archive.write_bytes(b"zip-bytes")
        sha_path = write_checksum(archive)
        line = sha_path.read_text(encoding="utf-8").strip()
        assert line == f"{checksum_hex(archive)}  {archive.name}"


class TestArchiveLayout:
    """The archive must hold the bundle under a single root folder."""

    def test_correct_layout_has_no_issues(self) -> None:
        names = [
            "KindleBookConverter/KindleBookConverter.exe",
            "KindleBookConverter/_internal/python314.dll",
            "KindleBookConverter/_internal/PySide6/Qt6Core.dll",
        ]
        assert layout_issues(names) == []

    def test_empty_archive_is_rejected(self) -> None:
        assert "no members" in layout_issues([])[0]

    def test_wrong_root_is_rejected(self) -> None:
        names = ["Other/App.exe", "Other/_internal/x.dll"]
        issues = layout_issues(names)
        assert any("root" in issue for issue in issues)

    def test_missing_exe_or_internal_is_rejected(self) -> None:
        no_exe = ["KindleBookConverter/_internal/x.dll"]
        assert any("does not contain" in issue for issue in layout_issues(no_exe))
        no_internal = ["KindleBookConverter/KindleBookConverter.exe"]
        assert any("_internal" in issue for issue in layout_issues(no_internal))


class TestContentParity:
    """The archive must contain exactly the bundle's files, prefixed."""

    def test_parity_ok_when_all_members_match(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        files = common.iter_bundle_files(bundle)
        names = [
            f"KindleBookConverter/{item.relative_to(bundle).as_posix()}"
            for item in files
        ]
        assert content_parity_issues(names, files) == []

    def test_missing_and_extra_members_are_reported(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        files = common.iter_bundle_files(bundle)
        names = ["KindleBookConverter/KindleBookConverter.exe"]
        issues = content_parity_issues(names, files)
        assert any("missing" in issue for issue in issues)
        names = [f"KindleBookConverter/{f.relative_to(bundle).as_posix()}" for f in files]
        names.append("KindleBookConverter/extra.txt")
        issues = content_parity_issues(names, files)
        assert any("extra" in issue for issue in issues)

    def test_empty_bundle_is_rejected(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert content_parity_issues([], []) == ["bundle has no files"]


class TestPeSubsystemBytesFromArchive:
    """The distribution verifier inspects the exe inside the ZIP."""

    def test_gui_executable_read_from_archive_is_recognized(self, tmp_path: Path) -> None:
        from build_tools import common as module_common

        assert module_common.pe_subsystem_bytes(_gui_pe_bytes()) == 2
        assert module_common.pe_subsystem_bytes(_console_pe_bytes()) == 3


class TestDistributionRoundTrip:
    """create_distribution + verify_distribution on a synthetic bundle."""

    def test_verify_passes_for_a_full_synthetic_bundle(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        archive = tmp_path / "out" / common.distribution_archive_name("0.1.0")
        create_distribution(bundle, version="0.1.0", archive=archive)

        report = verify_distribution(archive, bundle)
        assert report.passed, [c.name for c in report.checks if c.status == "fail"]
        assert all(c.status == "ok" for c in report.checks)
        assert common.distribution_checksum_path(archive).is_file()

    def test_console_executable_fails_gui_check(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path, exe_bytes=_console_pe_bytes())
        archive = tmp_path / "out" / common.distribution_archive_name("0.1.0")
        create_distribution(bundle, version="0.1.0", archive=archive)

        report = verify_distribution(archive, bundle)
        gui = next(c for c in report.checks if c.name == "gui_subsystem")
        assert gui.status == "fail"
        assert report.passed is False

    def test_missing_bundle_members_fail_parity(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        archive = tmp_path / "out" / common.distribution_archive_name("0.1.0")
        create_distribution(bundle, version="0.1.0", archive=archive)

        (bundle / "_internal" / "python314.dll").unlink()
        report = verify_distribution(archive, bundle)
        parity = next(c for c in report.checks if c.name == "content_parity")
        assert parity.status == "fail"

    def test_checksum_tamper_is_detected(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        archive = tmp_path / "out" / common.distribution_archive_name("0.1.0")
        create_distribution(bundle, version="0.1.0", archive=archive)

        sha = common.distribution_checksum_path(archive)
        line = sha.read_text(encoding="utf-8")
        tampered = line.replace("  ", "  cafebabe", 1)
        sha.write_text(tampered, encoding="utf-8")
        report = verify_distribution(archive, bundle)
        checksum = next(c for c in report.checks if c.name == "checksum")
        assert checksum.status == "fail"

    def test_creation_is_reproducible(self, tmp_path: Path) -> None:
        """Two archives from the same bundle are byte-identical."""
        bundle = _make_bundle(tmp_path)
        first = tmp_path / "first" / common.distribution_archive_name("0.1.0")
        second = tmp_path / "second" / common.distribution_archive_name("0.1.0")
        create_distribution(bundle, version="0.1.0", archive=first)
        create_distribution(bundle, version="0.1.0", archive=second)
        assert first.read_bytes() == second.read_bytes()

    def test_member_order_is_sorted(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        archive = tmp_path / "out" / common.distribution_archive_name("0.1.0")
        create_distribution(bundle, version="0.1.0", archive=archive)
        with zipfile.ZipFile(archive) as zip_handle:
            names = zip_handle.namelist()
        assert names == sorted(names)

    def test_missing_archive_fails_verification(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        archive = tmp_path / "absent.zip"
        report = verify_distribution(archive, bundle)
        assert report.passed is False
        assert report.checks[0].name == "archive_present"
        assert report.checks[0].status == "fail"

    def test_corrupt_archive_fails_integrity(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        archive = tmp_path / "corrupt.zip"
        archive.write_bytes(b"this is not a zip file")
        report = verify_distribution(archive, bundle)
        integrity = next(c for c in report.checks if c.name == "archive_integrity")
        assert integrity.status == "fail"