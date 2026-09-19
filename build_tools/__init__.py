"""M6.5 Windows packaging build/verification tooling.

This package contains the repository's Windows packaging tooling:

* :mod:`build_tools.common` -- pure helpers (project metadata, version
  resource generation, PE inspection, development-path scanning);
* :mod:`build_tools.build_windows` -- the reproducible build (clean venv +
  PyInstaller + post-process);
* :mod:`build_tools.verify_windows_package` -- static + isolated execution
  verification of the frozen artifact.

It is not part of the ``kindle_converter`` distribution and is never shipped
with it.
"""

__all__ = ["common"]