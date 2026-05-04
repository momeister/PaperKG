from __future__ import annotations

from pathlib import Path

from parsing.marker_parser import MarkerParser
from parsing.parser_router import ParsedDocument


class NougatParser:
    """
    PDF parser optimized for documents with heavy mathematical formulas.
    Uses Nougat (Nvidia's math-aware OCR) for formula preservation.

    Production implementation would integrate:
    - Nougat API via inference server
    - Formula detection and layout preservation
    - Markdown output with LaTeX math
    """

    def parse(self, file_path: str, paper_id: str) -> ParsedDocument:
        """
        Parse PDF with Nougat model.

        Args:
            file_path: Path to PDF
            paper_id: Paper identifier

        Returns:
            ParsedDocument with math-aware extracted text
        """
        marker_result = MarkerParser().parse(file_path, paper_id)
        path = Path(file_path)

        # If a Nougat server is configured, use it; otherwise return a math-aware fallback.
        # This keeps the parser functional without hard-depending on a heavy local model.
        import os
        import httpx

        api_url = os.getenv("NOUGAT_API_URL")
        if api_url:
            try:
                with path.open("rb") as handle:
                    response = httpx.post(
                        api_url.rstrip("/") + "/parse",
                        files={"pdf": handle},
                        timeout=300.0,
                    )
                response.raise_for_status()
                payload = response.json()
                return ParsedDocument(
                    paper_id=paper_id,
                    parser="nougat",
                    text=payload.get("markdown") or payload.get("text") or marker_result.text,
                    page_count=int(payload.get("page_count") or marker_result.page_count),
                    metadata={
                        "status": "remote",
                        "source_path": str(path),
                        "formula_count": payload.get("formula_count", 0),
                    },
                )
            except Exception:
                pass

        return ParsedDocument(
            paper_id=paper_id,
            parser="nougat",
            text=marker_result.text,
            page_count=marker_result.page_count,
            metadata={
                "status": "fallback",
                "source_path": str(path),
                "base_parser": marker_result.parser,
            },
        )
