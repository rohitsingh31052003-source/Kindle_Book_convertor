"""M5.2 dependency-boundary tests.

These tests verify the architectural direction of the UI layer without
importing PySide6, so they run even in a base installation (no ``ui`` extra):

* the core package imports with PySide6 import-blocked;
* the application layer never imports the UI package;
* the only modules that import PySide6 live inside ``kindle_converter.ui``.

The subprocess-based checks use a fresh interpreter with the repo's ``src``
directory on ``sys.path`` (mirroring the ``pythonpath = ["src"]`` pytest
configuration), so they pass whether or not the package is installed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import kindle_converter

_SRC = Path(kindle_converter.__file__).resolve().parent.parent


def _run(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_core_import_requires_no_pyside6() -> None:
    code = """
        import builtins
        _real_import = builtins.__import__

        def _guarded(name, *args, **kwargs):
            if name == "PySide6" or name.startswith("PySide6."):
                raise ImportError("core package must not import " + name)
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _guarded

        import kindle_converter
        assert callable(kindle_converter.convert_pdf_to_epub)
        print("core-import-ok")
    """
    result = _run(code)
    assert result.returncode == 0, result.stderr
    assert "core-import-ok" in result.stdout


def test_application_package_never_imports_ui() -> None:
    code = """
        import builtins
        _real_import = builtins.__import__

        def _guarded(name, *args, **kwargs):
            if name == "kindle_converter.ui" or name.startswith(
                "kindle_converter.ui."
            ):
                raise ImportError("application layer must not import " + name)
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _guarded

        import kindle_converter.application
        import kindle_converter.application.converter
        import kindle_converter.application.errors
        import kindle_converter.application.progress
        import kindle_converter.application.request
        import kindle_converter.application.result
        print("application-imports-ok")
    """
    result = _run(code)
    assert result.returncode == 0, result.stderr
    assert "application-imports-ok" in result.stdout


def test_only_ui_modules_import_pyside6() -> None:
    root = Path(kindle_converter.__file__).resolve().parent
    pattern = re.compile(r"^\s*(import PySide6|from PySide6\b)", re.MULTILINE)
    offenders: list[str] = []
    for module_path in root.rglob("*.py"):
        relative = module_path.relative_to(root)
        if relative.parts and relative.parts[0] == "ui":
            continue
        if pattern.search(module_path.read_text(encoding="utf-8")):
            offenders.append(str(relative))
    assert offenders == [], f"non-UI modules importing PySide6: {offenders}"


def test_conversion_worker_module_stays_qobject_only() -> None:
    """The M5.5 worker must never touch widgets or the main window.

    The worker is the Qt threading boundary for background conversion: it only
    imports ``PySide6.QtCore`` (``QObject``/``Signal``) and delegates all
    conversion work to the application layer. A widget or ``MainWindow``
    reference here would break the UI/gui-thread separation the background
    thread needs, and would also make the worker depend on the full Qt widget
    runtime that the module deliberately avoids.
    """
    root = Path(kindle_converter.__file__).resolve().parent
    source = (root / "ui" / "worker.py").read_text(encoding="utf-8")
    forbidden = (
        "QtWidgets",
        "QApplication",
        "QMainWindow",
        "MainWindow",
        "QWidget",
        "QDialog",
        "QLabel",
        "QPushButton",
    )
    offenders = [token for token in forbidden if token in source]
    assert offenders == [], f"worker.py references UI-widget symbols: {offenders}"
    assert "from PySide6.QtCore import QObject, Signal" in source