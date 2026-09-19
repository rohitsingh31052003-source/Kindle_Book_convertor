# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Kindle Book Converter Windows bundle (M6.5).

Built by ``build_tools/build_windows.py`` from a clean, project-dedicated
virtual environment. Every path is resolved relative to this spec file
(``SPECPATH``), so the build is independent of the working directory.

Layout decisions (M6.5):
* onedir bundle ``dist/KindleBookConverter/`` with a single GUI-subsystem
  executable ``KindleBookConverter.exe`` (no console window; the
  ``--smoke``/``--sysinfo`` verification subcommands write their reports to
  the ``--report`` file rather than stdout);
* the PySide6, PyMuPDF, ebooklib/lxml, pillow, and pytesseract hooks bundled
  with PyInstaller perform their own plugin/binary collection;
* the installed distribution metadata is shipped so the frozen application
  resolves versions through ``importlib.metadata`` exactly like the installed
  package. ``copy_metadata`` is used for ``kindle-converter`` plus every
  runtime distribution (PySide6, PyMuPDF, ebooklib, lxml, Pillow,
  pytesseract); the resulting ``*.dist-info`` directories also give the
  verifier a filesystem-level proof that each pure-python distribution was
  frozen;
* the scanned-PDF OCR path remains external: the Python wrappers
  (``pytesseract`` + Pillow) are bundled, but the ``tesseract`` executable
  is discovered on ``PATH`` at runtime, exactly as in a source checkout.
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
VERSION_FILE = ROOT / "build" / "version_info.txt"

a = Analysis(
    [str(ROOT / "build_tools" / "windows_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=(
        copy_metadata("kindle-converter")
        + copy_metadata("PySide6")
        + copy_metadata("PyMuPDF")
        + copy_metadata("ebooklib")
        + copy_metadata("lxml")
        + copy_metadata("Pillow")
        + copy_metadata("pytesseract")
    ),
    hiddenimports=[
        # The packaged --smoke/--sysinfo verification harness is dispatched
        # lazily from app.main(); ensure it ships and loads in the bundle.
        "kindle_converter.ui.smoke",
        # pytesseract/Pillow are imported lazily by the OCR layer; bundled so
        # the graceful unavailable-engine behavior is exactly as designed.
        "pytesseract",
        "PIL",
        "PIL.Image",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KindleBookConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    version=str(VERSION_FILE) if VERSION_FILE.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="KindleBookConverter",
)