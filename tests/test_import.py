"""Basic sanity tests for the package foundation (Milestone 1)."""

import kindle_converter
from kindle_converter import document, epub, pdf


def test_package_importable() -> None:
    assert kindle_converter is not None


def test_subpackages_importable() -> None:
    assert pdf.__name__ == "kindle_converter.pdf"
    assert document.__name__ == "kindle_converter.document"
    assert epub.__name__ == "kindle_converter.epub"


def test_package_has_version() -> None:
    assert isinstance(kindle_converter.__version__, str)
    assert kindle_converter.__version__