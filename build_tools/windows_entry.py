"""PyInstaller entry script for the Windows bundle (M6.5).

The bundle runs this plain module as ``__main__``; it imports the real
application entry point through absolute imports, which resolve correctly
from the frozen runtime tree regardless of package context (the checked-in
``ui/__main__.py`` relies on ``python -m`` semantics and cannot be executed
as a top-level script by PyInstaller).
"""

from __future__ import annotations

import sys

from kindle_converter.ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())