from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
	import pdfplumber
except Exception:  # pragma: no cover - optional dependency
	pdfplumber = None

try:
	from pypdf import PdfReader
except Exception:  # pragma: no cover - optional dependency
	PdfReader = None


@dataclass
class ParsedDocument:
	paper_id: str
	parser: str
	text: str
	page_count: int
	meta: dict[str, Any] = field(default_factory=dict)


class MarkerParser:
	"""
	Lightweight local parser interface.
	Tries pdfplumber first (best quality), falls back to PyPDF, then byte decoding.
	"""

	name = "marker"

	def parse(self, file_path: str | Path, paper_id: str) -> ParsedDocument:
		path = Path(file_path)
		text = ""
		page_count = 0
		metadata: dict[str, Any] = {"source_path": str(path)}

		if path.suffix.lower() == ".pdf":
			# Try pdfplumber first (best quality for text extraction)
			if pdfplumber is not None:
				try:
					with pdfplumber.open(str(path)) as pdf:
						page_texts: list[str] = []
						for page in pdf.pages:
							page_text = page.extract_text() or ""
							page_texts.append(page_text)
						text = "\n\n---PAGE BREAK---\n\n".join(page_texts).strip()
						page_count = len(pdf.pages)
						metadata["extraction_method"] = "pdfplumber"
						metadata["chars_extracted"] = len(text)
						if text and len(text) > 100:  # Good extraction
							return ParsedDocument(
								paper_id=paper_id,
								parser=self.name,
								text=text,
								page_count=page_count,
								meta=metadata,
							)
				except Exception as exc:
					metadata["pdfplumber_error"] = str(exc)
					# Fall through to PyPDF

			# Fall back to PyPDF
			if PdfReader is not None:
				try:
					reader = PdfReader(str(path))
					page_texts: list[str] = []
					for page in reader.pages:
						page_texts.append(page.extract_text() or "")
					text = "\n\n---PAGE BREAK---\n\n".join(page_texts).strip()
					page_count = len(reader.pages)
					metadata["extraction_method"] = "pypdf"
					metadata["chars_extracted"] = len(text)
					return ParsedDocument(
						paper_id=paper_id,
						parser=self.name,
						text=text,
						page_count=page_count,
						meta=metadata,
					)
				except Exception as exc:
					metadata["pypdf_error"] = str(exc)

			# Final fallback: byte decoding
			try:
				raw = path.read_bytes()
				text = self._decode(raw)
				page_count = max(1, text.count("\n\n") // 50 + 1)  # Estimate based on content
				metadata["extraction_method"] = "byte_decode_fallback"
				metadata["chars_extracted"] = len(text)
			except Exception as exc:
				metadata["extraction_error"] = str(exc)
				text = f"[Failed to extract text: {exc}]"
				page_count = 0
		else:
			# Not a PDF, try byte decoding
			raw = path.read_bytes()
			text = self._decode(raw)
			page_count = max(1, text.count("\n\n") // 50 + 1)
			metadata["extraction_method"] = "byte_decode_fallback"

		return ParsedDocument(
			paper_id=paper_id,
			parser=self.name,
			text=text,
			page_count=page_count,
			meta=metadata,
		)

	@staticmethod
	def _decode(raw: bytes) -> str:
		for encoding in ("utf-8", "latin-1", "utf-16"):
			try:
				return raw.decode(encoding, errors="ignore")
			except UnicodeDecodeError:
				continue
		return raw.decode("utf-8", errors="ignore")
