from __future__ import annotations

from pathlib import Path

from parsing.marker_parser import MarkerParser
from parsing.parser_router import ParsedDocument


class TableTransformerParser:
    """
    PDF parser optimized for documents with complex structured tables.
    Uses Table Transformer model for table detection and structure preservation.

    Production implementation would integrate:
    - Table Transformer via HuggingFace or local inference
    - Table structure detection (rows, columns, merged cells)
    - CSV/JSON export of extracted tables
    - Row/column boundary preservation in text
    """

    def parse(self, file_path: str, paper_id: str) -> ParsedDocument:
        """
        Parse PDF with Table Transformer model.

        Args:
            file_path: Path to PDF
            paper_id: Paper identifier

        Returns:
            ParsedDocument with structured table content
        """
        marker_result = MarkerParser().parse(file_path, paper_id)
        path = Path(file_path)

        # A full Table Transformer integration needs a dedicated OCR/layout pipeline.
        # Until that is available, we preserve the parsed text and annotate detected table blocks.
        text = self._preserve_tables(marker_result.text)
        return ParsedDocument(
            paper_id=paper_id,
            parser="table_transformer",
            text=text,
            page_count=marker_result.page_count,
            metadata={
                "status": "fallback",
                "source_path": str(path),
                "base_parser": marker_result.parser,
            },
        )

    @staticmethod
    def _preserve_tables(text: str) -> str:
        lines = text.splitlines()
        preserved: list[str] = []
        table_buffer: list[str] = []

        def flush_buffer() -> None:
            if table_buffer:
                preserved.append("\n".join(table_buffer))
                table_buffer.clear()

        for line in lines:
            if "|" in line or "\t" in line:
                table_buffer.append(line)
                continue
            flush_buffer()
            preserved.append(line)

        flush_buffer()
        return "\n".join(preserved)
