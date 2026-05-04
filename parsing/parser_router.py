from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ParserType(str, Enum):
    """Parser types for different PDF characteristics."""

    MARKER = "marker"
    NOUGAT = "nougat"
    TABLE_TRANSFORMER = "table_transformer"
    VLM = "vlm"


@dataclass
class ParsedDocument:
    """Document parsing result."""

    paper_id: str
    parser: ParserType | str
    text: str
    page_count: int
    metadata: dict = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class DocumentParser(Protocol):
    """Protocol for document parsers."""

    def parse(self, file_path: str, paper_id: str) -> ParsedDocument:
        """Parse document and return extracted text."""
        ...


class ParserCharacteristics:
    """Analyze PDF characteristics to select optimal parser."""

    @staticmethod
    def has_heavy_formulas(text: str, sample_size: int = 5000) -> bool:
        """Check if text likely contains heavy mathematical formulas."""
        sample = text[:sample_size]
        formula_indicators = ["$$", "\\[", "\\]", "\\alpha", "\\beta"]
        count = sum(sample.count(ind) for ind in formula_indicators)
        return count > 1

    @staticmethod
    def has_complex_tables(text: str, sample_size: int = 5000) -> bool:
        """Check if text likely contains complex tables."""
        sample = text[:sample_size]
        # Simple heuristic: multiple consecutive lines with pipe symbols or aligned columns
        lines = sample.split("\n")
        table_lines = sum(1 for line in lines if "|" in line or "\t" in line)
        return table_lines > 2

    @staticmethod
    def has_diagrams(text: str, sample_size: int = 5000) -> bool:
        """Check if text likely contains important diagrams."""
        sample = text[:sample_size]
        # Markers like "Figure:", "Diagram:", repeated mentions of visual elements
        visual_markers = ["Figure", "Diagram", "Visualization", "Schematic", "Architecture"]
        count = sum(sample.count(marker) for marker in visual_markers)
        return count > 1


class ParserRouter:
    """
    Intelligent parser router that selects optimal parser based on PDF characteristics.
    Falls back gracefully when specialized parsers unavailable.
    """

    def __init__(self) -> None:
        """Initialize router with default available parsers."""
        self.available_parsers: dict[ParserType, DocumentParser | None] = {
            ParserType.MARKER: None,
            ParserType.NOUGAT: None,
            ParserType.TABLE_TRANSFORMER: None,
            ParserType.VLM: None,
        }
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in parser implementations when available."""
        try:
            from parsing.marker_parser import MarkerParser

            self.available_parsers[ParserType.MARKER] = MarkerParser()
        except Exception:
            pass

        try:
            from parsing.nougat_parser import NougatParser

            self.available_parsers[ParserType.NOUGAT] = NougatParser()
        except Exception:
            pass

        try:
            from parsing.table_transformer import TableTransformerParser

            self.available_parsers[ParserType.TABLE_TRANSFORMER] = TableTransformerParser()
        except Exception:
            pass

        try:
            from parsing.vlm_parser import VLMParser

            self.available_parsers[ParserType.VLM] = VLMParser()
        except Exception:
            pass

    def register_parser(self, parser_type: ParserType, parser: DocumentParser) -> None:
        """Register parser implementation."""
        self.available_parsers[parser_type] = parser

    def select_parser(self, file_path: str, preview_text: str | None = None) -> ParserType:
        """
        Select best parser based on file characteristics.

        Args:
            file_path: Path to PDF file
            preview_text: Optional preview of first page text for analysis

        Returns:
            Selected ParserType
        """
        if preview_text is None:
            preview_text = ""

        # Priority order for parser selection
        if ParserCharacteristics.has_heavy_formulas(preview_text):
            if self.available_parsers[ParserType.NOUGAT]:
                return ParserType.NOUGAT

        if ParserCharacteristics.has_complex_tables(preview_text):
            if self.available_parsers[ParserType.TABLE_TRANSFORMER]:
                return ParserType.TABLE_TRANSFORMER

        if ParserCharacteristics.has_diagrams(preview_text):
            if self.available_parsers[ParserType.VLM]:
                return ParserType.VLM

        # Fallback to Marker
        return ParserType.MARKER

    def parse(
        self, file_path: str, paper_id: str, force_parser: ParserType | None = None
    ) -> ParsedDocument:
        """
        Parse document with automatic or forced parser selection.

        Args:
            file_path: Path to PDF
            paper_id: Paper identifier
            force_parser: Optional parser type override

        Returns:
            ParsedDocument with extracted text

        Raises:
            ValueError: If selected parser unavailable and no fallback possible
        """
        selected = force_parser or self.select_parser(file_path)
        parser = self.available_parsers.get(selected)

        if parser is None:
            # Try fallback to Marker
            parser = self.available_parsers[ParserType.MARKER]
            if parser is None:
                raise ValueError(f"No parser available for {file_path}")
            selected = ParserType.MARKER

        result = parser.parse(file_path, paper_id)
        result.parser = selected
        return result
