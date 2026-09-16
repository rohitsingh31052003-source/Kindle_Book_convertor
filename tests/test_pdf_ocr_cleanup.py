"""Deterministic OCR cleanup tests (Milestone 3.4).

These tests exercise the conservative, deterministic OCR text cleanup layer
against known raw OCR strings. They never invoke a real OCR engine, never
render PDFs, and never need an external OCR executable: cleanup operates on
already-recognized text only.
"""

from __future__ import annotations

import pytest

from kindle_converter.pdf import (
    MAX_CONSECUTIVE_BLANK_LINES,
    CleanedOCRResult,
    OCRResult,
    clean_ocr_pages,
    clean_ocr_result,
    clean_ocr_text,
)

# --------------------------------------------------------------------------- #
# CleanedOCRResult representation
# --------------------------------------------------------------------------- #


class TestCleanedOCRResult:
    def test_fields_and_values(self) -> None:
        result = CleanedOCRResult(page_number=3, text="hello")
        assert result.page_number == 3
        assert result.text == "hello"

    def test_frozen_and_slots(self) -> None:
        result = CleanedOCRResult(page_number=1, text="x")
        with pytest.raises(AttributeError):
            result.text = "changed"  # type: ignore[misc]
        assert CleanedOCRResult.__slots__ == ("page_number", "text")

    def test_equality(self) -> None:
        assert CleanedOCRResult(1, "a") == CleanedOCRResult(1, "a")
        assert CleanedOCRResult(1, "a") != CleanedOCRResult(2, "a")
        assert CleanedOCRResult(1, "a") != CleanedOCRResult(1, "b")

    def test_repr(self) -> None:
        assert "CleanedOCRResult" in repr(CleanedOCRResult(2, "text"))

    def test_not_equal_to_unrelated_type(self) -> None:
        assert CleanedOCRResult(1, "a") != "CleanedOCRResult(1, 'a')"


# --------------------------------------------------------------------------- #
# Newline normalization
# --------------------------------------------------------------------------- #


class TestNewlineNormalization:
    def test_crlf_becomes_lf(self) -> None:
        assert clean_ocr_text("a\r\nb\r\nc") == "a\nb\nc"

    def test_cr_becomes_lf(self) -> None:
        assert clean_ocr_text("a\rb\rc") == "a\nb\nc"

    def test_existing_lf_unchanged(self) -> None:
        assert clean_ocr_text("a\nb\nc") == "a\nb\nc"

    def test_mixed_newline_styles(self) -> None:
        # The final trailing newline is document-edge whitespace and is
        # removed by the edge-trim pass.
        assert clean_ocr_text("a\r\nb\rc\nd\r\n") == "a\nb\nc\nd"

    def test_crlf_pair_not_double_normalized(self) -> None:
        # "a\r\n\nb" -> "a\n\nb": the lone LF after CRLF is kept as a real
        # boundary, never consumed as part of the CRLF run.
        assert clean_ocr_text("a\r\n\nb") == "a\n\nb"

    def test_line_boundaries_preserved(self) -> None:
        # Meaningful line breaks survive; lines are not joined into prose.
        assert clean_ocr_text("line one\nline two") == "line one\nline two"


# --------------------------------------------------------------------------- #
# Unicode normalization (NFC)
# --------------------------------------------------------------------------- #


class TestUnicodeNormalization:
    def test_nfc_composition_applied(self) -> None:
        assert clean_ocr_text("cafe\u0301") == "caf\u00e9"
        assert clean_ocr_text("e\u0301\u0300") == "\u00e9\u0300"

    def test_already_normalized_unchanged(self) -> None:
        assert clean_ocr_text("caf\u00e9") == "caf\u00e9"

    def test_canonically_equivalent_inputs_agree(self) -> None:
        assert clean_ocr_text("cafe\u0301") == clean_ocr_text("caf\u00e9")

    def test_meaningful_unicode_preserved(self) -> None:
        for text in (
            "Привет, мир",
            "こんにちは世界",
            "Καλημέρα κόσμε",
            "🐍 яблоко",
        ):
            assert clean_ocr_text(text) == text

    def test_no_compatibility_conversion(self) -> None:
        # NFC is canonical only: compatibility characters (fullwidth, ligatures,
        # circled digits, roman numerals) must NOT be decomposed to ASCII.
        compatibility = "\u2460 \ufb01 \u2163 \uff21\uff22\uff23"
        assert clean_ocr_text(compatibility) == compatibility
        assert clean_ocr_text("\uff21") != "A"
        assert clean_ocr_text("\ufb01") != "fi"


# --------------------------------------------------------------------------- #
# Control-character cleanup
# --------------------------------------------------------------------------- #


class TestControlCharacters:
    def test_nul_removed(self) -> None:
        assert clean_ocr_text("a\x00b") == "ab"
        assert clean_ocr_text("\x00") == ""

    @pytest.mark.parametrize(
        "control", ["\x01", "\x02", "\x0b", "\x0c", "\x1b", "\x1f", "\x7f"]
    )
    def test_c0_and_del_removed(self, control) -> None:
        assert control not in clean_ocr_text(f"a{control}b")

    @pytest.mark.parametrize("control", ["\x80", "\x85", "\x9d"])
    def test_c1_removed(self, control) -> None:
        assert control not in clean_ocr_text(f"a{control}b")

    def test_tabs_preserved(self) -> None:
        assert clean_ocr_text("a\tb") == "a\tb"

    def test_newlines_preserved(self) -> None:
        assert clean_ocr_text("a\n\nb") == "a\n\nb"

    def test_control_run_collapsed_away(self) -> None:
        assert clean_ocr_text("a\x00\x01\x02b") == "ab"

    def test_meaningful_chars_untouched(self) -> None:
        assert clean_ocr_text("héllo–world") == "héllo–world"


# --------------------------------------------------------------------------- #
# Whitespace policy
# --------------------------------------------------------------------------- #


class TestWhitespacePolicy:
    def test_trailing_spaces_removed(self) -> None:
        assert clean_ocr_text("one  \ntwo \nthree") == "one\ntwo\nthree"

    def test_trailing_tabs_removed(self) -> None:
        assert clean_ocr_text("one\t \n\ttwo\n\t") == "one\n\ttwo"

    def test_trailing_space_and_tab_on_same_line(self) -> None:
        assert clean_ocr_text("text \t \n") == "text"

    def test_leading_trailing_document_whitespace_removed(self) -> None:
        assert clean_ocr_text(" \n\thello world\n\t ") == "hello world"

    def test_internal_spaces_preserved(self) -> None:
        assert clean_ocr_text("a  b   c") == "a  b   c"

    def test_indentation_preserved(self) -> None:
        assert clean_ocr_text("para\n    indented\n\t tabbed") == (
            "para\n    indented\n\t tabbed"
        )

    def test_trailing_whitespace_only_document(self) -> None:
        assert clean_ocr_text("   \n\t \n") == ""

    def test_whitespace_only_line_becomes_blank(self) -> None:
        # A line solely of spaces counts as a blank line and is handled by
        # the blank-line policy, not kept as stray spacing.
        assert clean_ocr_text("one\n   \ntwo") == "one\n\ntwo"

    def test_pathological_blank_run_collapsed(self) -> None:
        raw = "paragraph\n\n\n \n \n \nparagraph"
        assert clean_ocr_text(raw) == "paragraph\n\n\nparagraph"

    def test_normal_paragraph_separation_preserved(self) -> None:
        assert clean_ocr_text("a\n\nb") == "a\n\nb"
        assert clean_ocr_text("a\nb") == "a\nb"

    def test_separation_at_maximum_preserved(self) -> None:
        assert clean_ocr_text("a\n\nb") == "a\n\nb"

    def test_maximum_constant_is_cap(self) -> None:
        over = "a" + "\n" * (MAX_CONSECUTIVE_BLANK_LINES + 5) + "b"
        expected = "a" + "\n\n\n" + "b"  # exactly MAX blank lines kept
        assert clean_ocr_text(over) == expected

    def test_maximum_constant_is_positive(self) -> None:
        assert MAX_CONSECUTIVE_BLANK_LINES >= 1


# --------------------------------------------------------------------------- #
# Safety: content preservation
# --------------------------------------------------------------------------- #


class TestSafety:
    def test_empty_string(self) -> None:
        assert clean_ocr_text("") == ""

    def test_whitespace_only_string(self) -> None:
        assert clean_ocr_text(" \t\n ") == ""

    def test_normal_prose(self) -> None:
        prose = "The quick brown fox jumps over the lazy dog."
        assert clean_ocr_text(prose) == prose

    def test_punctuation(self) -> None:
        assert clean_ocr_text("Hello, world! How are you?") == (
            "Hello, world! How are you?"
        )

    def test_numbers(self) -> None:
        assert clean_ocr_text("12,345.67 and 1,000 items") == (
            "12,345.67 and 1,000 items"
        )

    def test_url_and_email(self) -> None:
        text = "Email a.b+c@example.com or visit https://example.com/x?v=1&r=2"
        assert clean_ocr_text(text) == text

    def test_non_ascii_text(self) -> None:
        text = "Mañana, déjà vu, Doppelgänger."
        assert clean_ocr_text(text) == text

    def test_quotes_and_dashes(self) -> None:
        text = "“quoted”—she said —‘single’ and -- dashes."
        assert clean_ocr_text(text) == text

    def test_legitimate_repeated_spaces_preserved(self) -> None:
        assert clean_ocr_text("a  b") == "a  b"
        assert clean_ocr_text("col A    col B") == "col A    col B"

    def test_no_ocr_character_substitution(self) -> None:
        # Cleanup must never 'correct' lookalike OCR characters.
        assert clean_ocr_text("teh 0 1 I 5 S rn") == "teh 0 1 I 5 S rn"

    def test_dashed_line_left_untouched(self) -> None:
        # Dehyphenation is deliberately not performed by M3.4.
        assert clean_ocr_text("conver-\nsion") == "conver-\nsion"
        assert clean_ocr_text("well-\nknown") == "well-\nknown"


# --------------------------------------------------------------------------- #
# Idempotence
# --------------------------------------------------------------------------- #


class TestIdempotence:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "\n\n\n",
            "hello\r\n\r\n\r\nworld \t",
            "a\x00b\n\n\n\n\nc",
            "cafe\u0301 \r\n text ",
            "paragraph\n\n\n \n \n \nparagraph",
            "conver-\nsion",
            "a  b\n    indented\nc  ",
            "mixed\r\nnewlines\rcr-only\nlf\n\r\n",
            "① ﬁ Ⅳ ＡＢＣ",
        ],
    )
    def test_text_cleanup_is_idempotent(self, text) -> None:
        once = clean_ocr_text(text)
        assert clean_ocr_text(once) == once

    def test_result_cleanup_is_idempotent(self) -> None:
        # clean_ocr_result accepts OCRResult (M3.3) values. Idempotence at the
        # page level means the cleaned text is already a fixed point of the
        # text cleanup pipeline, and the same source cleans identically twice.
        result = OCRResult(3, "  raw\r\n\r\n\r\n text \t")
        once = clean_ocr_result(result)
        assert clean_ocr_text(once.text) == once.text
        assert clean_ocr_result(result) == once

    def test_pages_cleanup_is_idempotent(self) -> None:
        pages = [OCRResult(1, " a\r\n"), OCRResult(2, "  \n\n b ")]
        cleaned = list(clean_ocr_pages(pages))
        assert [clean_ocr_text(c.text) for c in cleaned] == [
            c.text for c in cleaned
        ]
        assert list(clean_ocr_pages(pages)) == cleaned


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    @pytest.mark.parametrize(
        "text",
        [
            "hello\r\nworld",
            "a\x00\x01b\n\n\n\n\nc",
            "cafe\u0301 \t",
            "  \nlead\ntrail  \n  ",
            "",
        ],
    )
    def test_same_input_same_output(self, text) -> None:
        assert clean_ocr_text(text) == clean_ocr_text(text)

    def test_exact_expected_output(self) -> None:
        # Determinism also means a fixed, byte-for-byte expected result for a
        # representative dirty OCR string: newlines normalized, control/blank
        # artifacts removed, a capped blank-line run, edges stripped, and
        # internal indentation preserved.
        raw = "  x  \r\n\r\n\r\n y \r\nz\t"
        assert clean_ocr_text(raw) == "x\n\n\n y\nz"


# --------------------------------------------------------------------------- #
# Result-level cleanup API
# --------------------------------------------------------------------------- #


class TestCleanOCRResult:
    def test_page_number_preserved(self) -> None:
        cleaned = clean_ocr_result(OCRResult(17, "  text  "))
        assert cleaned.page_number == 17

    def test_text_is_cleaned(self) -> None:
        cleaned = clean_ocr_result(OCRResult(1, "  a\r\n\r\n\r\n b \t "))
        assert cleaned.text == clean_ocr_text("  a\r\n\r\n\r\n b \t ")

    def test_delegates_to_text_cleanup(self) -> None:
        result = OCRResult(2, " x\r\n\r\n\r\n y ")
        assert clean_ocr_result(result).text == clean_ocr_text(result.text)

    def test_source_ocr_result_unmodified(self) -> None:
        raw = "  raw \r\n\r\n text \t "
        source = OCRResult(5, raw)
        clean_ocr_result(source)
        assert source.page_number == 5
        assert source.text == raw

    def test_immutable_output_type(self) -> None:
        cleaned = clean_ocr_result(OCRResult(1, "text"))
        assert isinstance(cleaned, CleanedOCRResult)

    @pytest.mark.parametrize("bad", [None, "result", 7, object(), [OCRResult(1, "x")]])
    def test_non_ocr_result_rejected(self, bad) -> None:
        with pytest.raises(TypeError):
            clean_ocr_result(bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "page_number", [0, -1, True, False, "3", 3.0]
    )
    def test_invalid_page_number_rejected(self, page_number) -> None:
        with pytest.raises(ValueError):
            clean_ocr_result(
                OCRResult(page_number=page_number, text="x")  # type: ignore[arg-type]
            )

    def test_valid_page_number_accepted(self) -> None:
        assert clean_ocr_result(OCRResult(1, "x")).page_number == 1


# --------------------------------------------------------------------------- #
# Multi-page cleanup API
# --------------------------------------------------------------------------- #


class TestCleanOCRPages:
    def test_preserves_order_and_page_numbers(self) -> None:
        pages = [OCRResult(10, " a\r\n"), OCRResult(11, " b "), OCRResult(12, " c ")]
        cleaned = list(clean_ocr_pages(pages))
        assert [c.page_number for c in cleaned] == [10, 11, 12]

    def test_pages_cleaned_independently(self) -> None:
        pages = [OCRResult(1, " a\r\n\r\n\r\n"), OCRResult(2, " \t b ")]
        cleaned = list(clean_ocr_pages(pages))
        assert [c.text for c in cleaned] == ["a", "b"]

    def test_pages_are_not_merged(self) -> None:
        pages = [OCRResult(1, "one"), OCRResult(2, "two"), OCRResult(3, "three")]
        cleaned = list(clean_ocr_pages(pages))
        assert len(cleaned) == 3
        assert [c.text for c in cleaned] == ["one", "two", "three"]

    def test_accepts_a_generator(self) -> None:
        gen = (OCRResult(n, f" page {n} \r\n") for n in (4, 5))
        cleaned = list(clean_ocr_pages(gen))
        assert [c.page_number for c in cleaned] == [4, 5]
        assert [c.text for c in cleaned] == ["page 4", "page 5"]

    def test_is_lazy_generator(self) -> None:
        # clean_ocr_pages returns a generator; nothing is cleaned before
        # iteration starts.
        stream = clean_ocr_pages([OCRResult(1, " a "), OCRResult(2, " b ")])
        first = next(stream)
        assert first == CleanedOCRResult(1, "a")
        assert list(stream) == [CleanedOCRResult(2, "b")]

    @pytest.mark.parametrize("bad", [None, 7, "results"])
    def test_non_iterable_rejected(self, bad) -> None:
        with pytest.raises(TypeError):
            list(clean_ocr_pages(bad))  # type: ignore[arg-type]

    def test_bad_element_rejected(self) -> None:
        with pytest.raises(TypeError):
            list(clean_ocr_pages([object()]))  # type: ignore[list-item]

    def test_separation_of_pages_checked(self) -> None:
        # Even when a page starts/ends with edge whitespace, its neighbours
        # are unaffected: no cross-page concatenation ever happens.
        pages = [OCRResult(1, "\nlead"), OCRResult(2, "tail\n")]
        cleaned = list(clean_ocr_pages(pages))
        assert [c.text for c in cleaned] == ["lead", "tail"]


# --------------------------------------------------------------------------- #
# Public API surface
# --------------------------------------------------------------------------- #


class TestPublicAPI:
    def test_cleanup_api_is_exported(self) -> None:
        import kindle_converter.pdf

        for name in (
            "clean_ocr_text",
            "clean_ocr_result",
            "clean_ocr_pages",
            "CleanedOCRResult",
            "MAX_CONSECUTIVE_BLANK_LINES",
        ):
            assert name in kindle_converter.pdf.__all__
            assert hasattr(kindle_converter.pdf, name)

    def test_internal_helpers_are_not_exported(self) -> None:
        import kindle_converter.pdf as pdf_api
        from kindle_converter.pdf import ocr_cleanup

        for name in (
            "_clean_text",
            "_normalize_newlines",
            "_remove_control_characters",
            "_strip_trailing_line_whitespace",
            "_collapse_blank_lines",
            "_validate_result",
        ):
            assert name not in pdf_api.__all__
            assert hasattr(ocr_cleanup, name)

    def test_max_blank_lines_constant_value(self) -> None:
        assert MAX_CONSECUTIVE_BLANK_LINES == 2

    def test_ocr_result_still_exported(self) -> None:
        import kindle_converter.pdf

        assert kindle_converter.pdf.OCRResult is OCRResult