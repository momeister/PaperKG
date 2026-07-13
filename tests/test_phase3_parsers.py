import pytest
import re
from unittest.mock import MagicMock
from typing import Any

from parsing.parser_router import ParserRouter, ParserType, ParserCharacteristics
from parsing.marker_parser import (
    MarkerParser,
    _chars_to_spaced_text,
    _classify_and_split_words,
    _find_column_gutter,
    _join_hyphenated_linebreaks,
    _looks_better_spaced,
    _reconstruct_page_text,
    _repair_glued_parens,
    _text_needs_char_reconstruction,
)

class TestParserRouter:
    """Test intelligent parser selection."""

    def test_parser_characteristics_detect_formulas(self):
        """Test formula detection."""
        text_with_formulas = "The equation $$E=mc^2$$ shows energy equivalence. Also \\alpha and \\beta parameters."
        text_without = "Neural networks are good. They work well."

        assert ParserCharacteristics.has_heavy_formulas(text_with_formulas)
        assert not ParserCharacteristics.has_heavy_formulas(text_without)

    def test_parser_characteristics_detect_tables(self):
        """Test table detection."""
        text_with_tables = "Results:\nKey | Value | Count\n---|---|---\nA | 1 | 100\nB | 2 | 200"
        text_without = "The results show that the method works well."

        assert ParserCharacteristics.has_complex_tables(text_with_tables)
        assert not ParserCharacteristics.has_complex_tables(text_without)

    def test_parser_characteristics_detect_diagrams(self):
        """Test diagram detection."""
        text_with_diagrams = "Figure 1 shows the architecture. Figure 2 displays the workflow. Figure 3 presents the diagram."
        text_without = "The results indicate improvement."

        assert ParserCharacteristics.has_diagrams(text_with_diagrams)
        assert not ParserCharacteristics.has_diagrams(text_without)

    def test_parser_router_select_parser(self):
        """Test parser selection based on characteristics."""
        router = ParserRouter()

        # Mock parsers
        mock_marker = MagicMock()
        mock_nougat = MagicMock()

        router.register_parser(ParserType.MARKER, mock_marker)
        router.register_parser(ParserType.NOUGAT, mock_nougat)

        # Formula-heavy text should select Nougat
        preview = "The equation $$E=mc^2$$ is fundamental."
        selected = router.select_parser("/fake.pdf", preview)

        assert selected == ParserType.NOUGAT

    def test_parser_router_fallback_to_marker(self):
        """Test fallback to Marker when Nougat is not available."""
        router = ParserRouter()

        mock_marker = MagicMock()
        router.register_parser(ParserType.MARKER, mock_marker)
        router.available_parsers[ParserType.NOUGAT] = None

        # Formula-heavy text should still fall back to Marker when Nougat is unavailable
        preview = "The equation $$E=mc^2$$ is fundamental."
        selected = router.select_parser("/fake.pdf", preview)

        assert selected == ParserType.MARKER

    def test_parser_router_parse_uses_marker_preview_for_selection(self):
        """Test automatic parse selection uses a Marker text probe."""
        router = ParserRouter()
        marker_result = MagicMock(
            text="The equation $$E=mc^2$$ and \\alpha appear repeatedly.",
            parser=ParserType.MARKER,
        )
        nougat_result = MagicMock(text="nougat output", parser=ParserType.NOUGAT)
        mock_marker = MagicMock()
        mock_marker.parse.return_value = marker_result
        mock_nougat = MagicMock()
        mock_nougat.parse.return_value = nougat_result

        router.register_parser(ParserType.MARKER, mock_marker)
        router.register_parser(ParserType.NOUGAT, mock_nougat)

        result = router.parse("/fake.pdf", "paper_001")

        assert result is nougat_result
        assert result.parser == ParserType.NOUGAT
        assert mock_marker.parse.called
        assert mock_nougat.parse.called

    def test_parser_router_falls_back_when_selected_parser_raises(self):
        """Harvested PDFs should still parse when a specialized parser fails at runtime."""
        router = ParserRouter()
        marker_result = MagicMock(
            text="The equation $$E=mc^2$$ and \\alpha appear repeatedly.",
            parser=ParserType.MARKER,
            metadata={},
        )
        mock_marker = MagicMock()
        mock_marker.parse.return_value = marker_result
        mock_nougat = MagicMock()
        mock_nougat.parse.side_effect = RuntimeError("nougat service unavailable")

        router.register_parser(ParserType.MARKER, mock_marker)
        router.register_parser(ParserType.NOUGAT, mock_nougat)

        result = router.parse("/fake.pdf", "paper_001")

        assert result is marker_result
        assert result.parser == ParserType.MARKER
        assert result.metadata["parser_fallback_from"] == "nougat"
        assert "nougat service unavailable" in result.metadata["parser_fallback_error"]


def _word(text: str, x0: float, x1: float, top: float, bottom: float) -> dict[str, Any]:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}


class TestColumnAwareExtraction:
    """Unit tests for the column-gutter detection / reconstruction helpers in marker_parser.

    These operate on plain word-dicts (the shape pdfplumber's extract_words returns),
    so they're testable without any PDF fixtures.
    """

    PAGE_WIDTH = 600.0
    PAGE_HEIGHT = 800.0

    def _two_column_words(self, left_x=(50, 200), right_x=(350, 500), tops=(100, 200, 300, 400, 500, 600)):
        words = []
        for i, top in enumerate(tops):
            words.append(_word(f"L{i}", left_x[0], left_x[1], top, top + 12))
            words.append(_word(f"R{i}", right_x[0], right_x[1], top, top + 12))
        return words

    def test_find_column_gutter_detects_clean_two_column_split(self):
        words = self._two_column_words()
        gutter = _find_column_gutter(words, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        assert gutter is not None
        assert 200.0 < gutter < 350.0

    def test_find_column_gutter_returns_none_for_single_column(self):
        # Tile overlapping words across the whole scan band [120, 480] - no zero-occupancy
        # run remains, so a single-column page must not produce a (false) gutter.
        words = []
        x = 120.0
        top = 100.0
        i = 0
        while x < 480.0:
            words.append(_word(f"w{i}", x, x + 12.0, top, top + 10.0))
            x += 10.0
            top += 15.0
            i += 1
        assert _find_column_gutter(words, self.PAGE_WIDTH, self.PAGE_HEIGHT) is None

    def test_find_column_gutter_excludes_header_and_footer_margins(self):
        # A full-width running head/footer would otherwise mask the real body gutter.
        words = self._two_column_words()
        words.append(_word("Running Head", 50, 550, 10, 30))   # top margin = 0.08 * 800 = 64
        words.append(_word("Page 7", 50, 550, 770, 790))        # bottom margin = 736
        gutter = _find_column_gutter(words, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        assert gutter is not None
        assert 200.0 < gutter < 350.0

    def test_find_column_gutter_detects_asymmetric_split_near_71_percent(self):
        # Regression guard for band_hi_frac=0.80: the bug page's gutter sits at ~71% of the
        # page width (asymmetric abstract/sidebar layout), which a narrower band would miss.
        words = self._two_column_words(left_x=(50, 400), right_x=(450, 560))
        gutter = _find_column_gutter(words, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        assert gutter is not None
        assert 400.0 < gutter < 450.0
        assert abs(gutter - 0.71 * self.PAGE_WIDTH) < 40.0

    def test_find_column_gutter_returns_none_when_gap_too_narrow(self):
        words = self._two_column_words(left_x=(50, 250), right_x=(253, 490))
        assert _find_column_gutter(words, self.PAGE_WIDTH, self.PAGE_HEIGHT) is None

    def test_classify_and_split_words_partitions_by_margin_then_gutter(self):
        words = self._two_column_words(tops=(100, 200))
        words.append(_word("HEADER", 50, 550, 10, 30))
        words.append(_word("FOOTER", 50, 550, 770, 790))
        header, left, right, footer = _classify_and_split_words(words, gutter_x=275.0, page_height=self.PAGE_HEIGHT)
        assert [w["text"] for w in header] == ["HEADER"]
        assert [w["text"] for w in footer] == ["FOOTER"]
        assert [w["text"] for w in left] == ["L0", "L1"]
        assert [w["text"] for w in right] == ["R0", "R1"]

    def test_reconstruct_page_text_orders_whole_columns_not_interleaved_rows(self):
        # Both columns share the same vertical positions - naive top-to-bottom reading
        # would interleave "Left1 Right1 Left2 Right2"; reconstruction must keep each
        # column intact and emit the left column before the right one.
        words = [
            _word("Left1", 50, 200, 100, 112),
            _word("Right1", 350, 500, 100, 112),
            _word("Left2", 50, 200, 150, 162),
            _word("Right2", 350, 500, 150, 162),
        ]
        text = _reconstruct_page_text(words, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        assert text is not None
        assert text.index("Left1") < text.index("Left2") < text.index("Right1") < text.index("Right2")

    def test_reconstruct_page_text_returns_none_without_gutter(self):
        words = []
        x = 120.0
        top = 100.0
        i = 0
        while x < 480.0:
            words.append(_word(f"w{i}", x, x + 12.0, top, top + 10.0))
            x += 10.0
            top += 15.0
            i += 1
        assert _reconstruct_page_text(words, self.PAGE_WIDTH, self.PAGE_HEIGHT) is None

    def test_reconstruct_page_text_places_header_first_and_footer_last(self):
        words = self._two_column_words()
        words.append(_word("HEADER", 50, 550, 10, 30))
        words.append(_word("FOOTER", 50, 550, 770, 790))
        text = _reconstruct_page_text(words, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        assert text is not None
        assert text.index("HEADER") < text.index("L0")
        assert text.index("R5") < text.index("FOOTER")


class TestReconstructPageTextCharRepair:
    """The two-column path must fall back to char-gap reconstruction when the column-ordered
    word text is itself glued (extract_words inherits extract_text()'s dropped-space glyphs)."""

    PAGE_WIDTH = 600.0
    PAGE_HEIGHT = 800.0

    def _char(self, text: str, x0: float, x1: float, top: float, size: float = 10.0) -> dict[str, Any]:
        return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + size, "size": size}

    def _phrase_chars(self, phrase: str, start_x: float, top: float, cw: float = 3.0, gap: float = 3.0) -> list[dict]:
        """Chars for a space-separated phrase: 0 gap within a word, >2.24pt gap between words
        (the effective floor is 0.28 * max(size, 8)), and no glyph emitted for the space itself.
        Widths stay narrow so every char's center remains on its own side of the gutter."""
        chars: list[dict] = []
        x = start_x
        for word in phrase.split(" "):
            for ch in word:
                chars.append(self._char(ch, x, x + cw, top))
                x += cw
            x += gap
        return chars

    def test_glued_two_column_words_are_repaired_from_chars(self):
        left = "vision language framework for industrial anomaly understanding"
        right = "Right column note"
        words: list[dict] = []
        chars: list[dict] = []
        for top in (100.0, 150.0, 200.0):
            words.append(_word(left.replace(" ", ""), 50, 200, top, top + 10))   # one glued token
            words.append(_word(right.replace(" ", ""), 350, 500, top, top + 10))
            chars.extend(self._phrase_chars(left, 50, top))
            chars.extend(self._phrase_chars(right, 350, top))

        result = _reconstruct_page_text(words, self.PAGE_WIDTH, self.PAGE_HEIGHT, chars=chars)
        assert result is not None
        assert left in result                              # de-glued via char gaps
        assert left.replace(" ", "") not in result         # the glued token is gone

    def test_well_spaced_two_column_words_ignore_chars(self):
        # extract_words already produced clean tokens → the check never fires → chars are ignored
        # and the word-join is returned verbatim (passing chars must not change a good page).
        words: list[dict] = []
        for i, top in enumerate((100.0, 150.0, 200.0, 250.0)):
            words.append(_word(f"Leftword{i}", 50, 200, top, top + 10))
            words.append(_word(f"Rightword{i}", 350, 500, top, top + 10))
        chars = [self._char("Z", 50, 53, 100.0)]  # would corrupt output if wrongly applied
        with_chars = _reconstruct_page_text(words, self.PAGE_WIDTH, self.PAGE_HEIGHT, chars=chars)
        without = _reconstruct_page_text(words, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        assert with_chars == without
        assert with_chars is not None and "Leftword0" in with_chars


class TestCharsToSpacedText:
    """Tests for _chars_to_spaced_text char-level space insertion."""

    def _char(self, text: str, x0: float, x1: float, top: float = 100.0, size: float = 10.0) -> dict:
        return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + size, "size": size}

    def test_inserts_space_at_word_boundary(self):
        # Two words separated by a gap larger than 0.28 * font_size
        # font_size=10, threshold = 0.28 * 10 = 2.8 — gap of 12 should insert space
        chars = [
            *[self._char(c, 10 * i, 10 * i + 8) for i, c in enumerate("Hello")],
            *[self._char(c, 60 + 10 * i, 60 + 10 * i + 8) for i, c in enumerate("World")],
        ]
        result = _chars_to_spaced_text(chars)
        assert result == "Hello World"

    def test_no_space_within_word(self):
        # Characters directly adjacent (gap = 0) should not get spaces
        chars = [self._char("A", 0, 8), self._char("B", 8, 16), self._char("C", 16, 24)]
        result = _chars_to_spaced_text(chars)
        assert result == "ABC"

    def test_new_line_on_y_gap(self):
        chars = [
            self._char("L", 10, 18, top=100.0),
            self._char("1", 18, 24, top=100.0),
            self._char("L", 10, 18, top=115.0),
            self._char("2", 18, 24, top=115.0),
        ]
        result = _chars_to_spaced_text(chars)
        assert result == "L1\nL2"

    def test_empty_input(self):
        assert _chars_to_spaced_text([]) == ""

    def test_fixes_merged_tokens(self):
        # Simulate a PDF where two words have no space between them in extract_text
        # but the chars have a measurable x-gap > 0.28 * size
        size = 10.0
        gap = 3.0  # 3pt > 0.28 * 10 = 2.8 → should insert space
        chars = [
            self._char("I", 0, 5, size=size),
            self._char("n", 5, 11, size=size),
            self._char("a", 11 + gap, 17 + gap, size=size),
            self._char("n", 17 + gap, 23 + gap, size=size),
        ]
        result = _chars_to_spaced_text(chars)
        assert " " in result  # space was inserted at the gap

    def test_no_space_for_normal_kerning_gap(self):
        # Kerned/justified text often has small inter-char gaps well below a word
        # boundary; the old 0.22 threshold split words apart ("ass essing").
        size = 10.0
        gap = 2.5  # 2.5pt < 0.28 * 10 = 2.8 → stays one word
        chars = [
            self._char("a", 0, 5, size=size),
            self._char("s", 5, 10, size=size),
            self._char("s", 10 + gap, 15 + gap, size=size),
        ]
        assert _chars_to_spaced_text(chars) == "ass"


class TestParseTextCleanup:
    """Tests for naive-text preference and de-hyphenation helpers."""

    def test_join_hyphenated_linebreaks_joins_lowercase_continuation(self):
        assert _join_hyphenated_linebreaks("for assess-\ning model quality") == "for assessing model quality"
        # Column reconstruction flattens line breaks to spaces before this runs.
        assert _join_hyphenated_linebreaks("the bevaci- zumab group") == "the bevacizumab group"

    def test_join_hyphenated_linebreaks_keeps_capitalized_compounds(self):
        # "Wilcoxon-\nMann" is a real hyphenated compound, not a broken word.
        assert _join_hyphenated_linebreaks("the Wilcoxon-\nMann test") == "the Wilcoxon-\nMann test"

    def test_join_hyphenated_linebreaks_keeps_suspended_hyphens(self):
        # Suspended hyphenation ("pre- and post-") must not be glued into "preand".
        assert _join_hyphenated_linebreaks("pre- and post-treatment") == "pre- and post-treatment"
        assert _join_hyphenated_linebreaks("mid- to long-term effects") == "mid- to long-term effects"

    def test_text_needs_char_reconstruction_false_for_normal_text(self):
        text = ("This page has perfectly ordinary spacing between all of its words. " * 5)
        assert _text_needs_char_reconstruction(text) is False

    def test_text_needs_char_reconstruction_true_for_glued_text(self):
        text = (
            "Thispagelacksanywordseparationatallbecausethecontentstreamencodesnospaces"
            "andthereforeneedsthecharlevelgapreconstructiontobecomereadableagain" * 3
        )
        assert _text_needs_char_reconstruction(text) is True

    def test_text_needs_char_reconstruction_false_for_short_text(self):
        assert _text_needs_char_reconstruction("Shortpagefooter") is False

    def test_text_needs_char_reconstruction_true_for_single_extreme_run(self):
        # A lone glued title/sentence ("...vision-languageframeworkforindustrialAnomaly...")
        # is glue even when the rest of the page is well spaced. Only two 16+ runs here, so the
        # >=3 long_runs/paren_glue thresholds never fire — the extreme-run (>=25) trigger must.
        text = (
            "To address this gap we present GenAU a Generalist "
            "visionlanguageframeworkforindustrialAnomalyUnderstanding that works well here. "
        ) * 2
        assert len(re.findall(r"[A-Za-z]{16,}", text)) < 3  # thresholds alone would say False
        assert _text_needs_char_reconstruction(text) is True

    def test_text_needs_char_reconstruction_true_for_localized_paren_gluing(self):
        # Otherwise well-spaced page, but extract_text() dropped spaces around parentheses.
        text = (
            "The finite element analysis is performed using the Multiphysics Object "
            "Oriented Simulation Environment(MOOSE)framework which is widely used. " * 3
        )
        assert _text_needs_char_reconstruction(text) is True

    def test_repair_glued_parens_inserts_spaces_around_parentheses(self):
        assert (
            _repair_glued_parens("Simulation Environment(MOOSE)framework")
            == "Simulation Environment (MOOSE) framework"
        )
        assert _repair_glued_parens("the results(2020)show") == "the results (2020) show"

    def test_repair_glued_parens_keeps_single_letter_math_and_punctuation(self):
        # single-letter functions and a closing paren before non-letters stay intact
        assert _repair_glued_parens("f(x) and H(t).") == "f(x) and H(t)."
        assert _repair_glued_parens("group (10.6 months).") == "group (10.6 months)."

    def test_looks_better_spaced_accepts_real_fix_and_rejects_over_splitting(self):
        glued = "TheresultshighlighttheutilityofDMD asaframework"
        fixed = "The results highlight the utility of DMD as a framework"
        over_split = "T h e r e s u l t s h i g h l i g h t"
        assert _looks_better_spaced(glued, fixed) is True
        assert _looks_better_spaced(glued, over_split) is False


class TestParserImplementations:
    """Test actual parser fallbacks."""

    def test_marker_parser_reads_pdf_text_or_falls_back(self, tmp_path):
        from pypdf import PdfWriter

        pdf_path = tmp_path / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with pdf_path.open("wb") as handle:
            writer.write(handle)

        parser = MarkerParser()
        result = parser.parse(pdf_path, "paper_001")

        assert result.paper_id == "paper_001"
        assert result.page_count == 1
        assert result.meta.get("extraction_method") == "pypdf"

    def test_marker_parser_two_column_pdf_uses_column_aware_reconstruction(self):
        import re
        from pathlib import Path

        pdf_path = Path("data/pdfs/upload__files__files/upload__files__files_v1.pdf")
        if not pdf_path.exists():
            pytest.skip("bug-report corpus PDF not present in this checkout")

        parser = MarkerParser()
        result = parser.parse(pdf_path, "nejm_bevacizumab")

        assert result.meta.get("extraction_method") == "pdfplumber_columns"
        assert result.meta.get("columns_pages", 0) > 0

        # The exact end-to-end regression check for the reported bug: this sentence was
        # previously split across two interleaved columns ("Sub- The median
        # progression-free survival was group analyses of ... and 10.6 months ...").
        # After column-aware reconstruction it must appear as one contiguous sentence -
        # the same whitespace-normalized substring match that best_excerpt performs.
        # De-hyphenation additionally joins the line-broken "bevaci-\nzumab".
        normalized = re.sub(r"\s+", " ", result.text)
        target = (
            "The median progression-free survival was longer in the bevacizumab "
            "group than in the placebo group (10.6 months vs. 6.2 months"
        )
        assert target in normalized

    def test_marker_parser_single_column_pdf_keeps_naive_extraction_method(self):
        from pathlib import Path

        pdf_path = Path(
            "data/pdfs/europe_pmc__the-potential-of-polymeric-micelles-in-the-context-of-glioblastoma-therapy"
            "__doaj_19f65c896f46443e8854d1adcea653d7"
            "/europe_pmc__the-potential-of-polymeric-micelles-in-the-context-of-glioblastoma-therapy"
            "__doaj_19f65c896f46443e8854d1adcea653d7_v1.pdf"
        )
        if not pdf_path.exists():
            pytest.skip("single-column corpus PDF not present in this checkout")

        parser = MarkerParser()
        result = parser.parse(pdf_path, "single_column_paper")

        assert result.meta.get("extraction_method") == "pdfplumber"
        assert result.meta.get("columns_pages") == 0

    def test_nougat_parser_returns_real_fallback_text(self, tmp_path):
        pdf_path = tmp_path / "input.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        from parsing.nougat_parser import NougatParser

        parser = NougatParser()
        result = parser.parse(str(pdf_path), "paper_002")

        assert result.paper_id == "paper_002"
        assert "not yet implemented" not in result.text.lower()
        assert result.metadata.get("status") in {"fallback", "remote"}


