"""Module entry point: ``python -m kindle_converter.ui``.

Launches the desktop application shell (M5.2). Requires the optional ``ui``
extra (PySide6).
"""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())