from __future__ import annotations

import re
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


def _find_column_gutter(
	words: list[dict[str, Any]],
	page_width: float,
	page_height: float,
	margin_frac: float = 0.08,
	band_lo_frac: float = 0.20,
	band_hi_frac: float = 0.80,
	tol: float = 1.0,
	min_band_width: float = 6.0,
	step: float = 1.0,
) -> float | None:
	"""
	Find a vertical x-band that no body word's bounding box touches, in the
	central portion of the page width. A wide-enough such band is the gutter
	between two columns. Header/footer words (running heads, copyright lines)
	are excluded first since they often span the full page width and would
	otherwise hide a real gutter that only exists in the body rows.

	Returns the gutter's x-midpoint, or None if no reliable gutter is found
	(single-column page, or layout too irregular for one stable vertical split).
	"""
	top_margin = page_height * margin_frac
	bottom_margin = page_height * (1 - margin_frac)
	body = [w for w in words if w.get("top", 0) >= top_margin and w.get("bottom", 0) <= bottom_margin]
	if not body:
		return None

	lo = page_width * band_lo_frac
	hi = page_width * band_hi_frac
	best_start: float | None = None
	best_width = 0.0
	run_start: float | None = None
	x = lo
	while x <= hi:
		occupied = any(w["x0"] < x + tol and w["x1"] > x - tol for w in body)
		if occupied:
			if run_start is not None:
				width = x - run_start
				if width > best_width:
					best_width = width
					best_start = run_start
				run_start = None
		elif run_start is None:
			run_start = x
		x += step
	if run_start is not None:
		width = hi - run_start
		if width > best_width:
			best_width = width
			best_start = run_start

	if best_start is not None and best_width >= min_band_width:
		return best_start + best_width / 2
	return None


def _classify_and_split_words(
	words: list[dict[str, Any]],
	gutter_x: float,
	page_height: float,
	margin_frac: float = 0.08,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
	"""
	Partition words into (header, left, right, footer): running heads/footers
	by vertical margin, remaining "body" words by which side of the gutter
	their bounding-box center falls on. Each group is sorted by (top, x0) —
	plain top-to-bottom, left-to-right reading order within that region.
	"""
	top_margin = page_height * margin_frac
	bottom_margin = page_height * (1 - margin_frac)
	header: list[dict[str, Any]] = []
	left: list[dict[str, Any]] = []
	right: list[dict[str, Any]] = []
	footer: list[dict[str, Any]] = []
	for word in words:
		top = word.get("top", 0)
		bottom = word.get("bottom", 0)
		if top < top_margin:
			header.append(word)
		elif bottom > bottom_margin:
			footer.append(word)
		else:
			center = (word["x0"] + word["x1"]) / 2
			(left if center < gutter_x else right).append(word)

	def _sort(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
		return sorted(group, key=lambda w: (w.get("top", 0), w.get("x0", 0)))

	return _sort(header), _sort(left), _sort(right), _sort(footer)


def _words_to_text(words: list[dict[str, Any]]) -> str:
	"""Join extracted words into properly-spaced lines, preserving line breaks."""
	if not words:
		return ""
	lines: list[list[str]] = []
	current: list[str] = []
	prev_bottom: float | None = None
	for w in sorted(words, key=lambda w: (w.get("top", 0), w.get("x0", 0))):
		top = float(w.get("top", 0))
		if prev_bottom is not None and top > prev_bottom + 2:
			if current:
				lines.append(current)
			current = []
		current.append(str(w.get("text", "")))
		prev_bottom = float(w.get("bottom", top))
	if current:
		lines.append(current)
	return "\n".join(" ".join(line) for line in lines)


def _chars_to_spaced_text(chars: list[dict[str, Any]], gap_ratio: float = 0.28) -> str:
	"""Rebuild text from pdfplumber char data, inserting spaces based on inter-character gaps.

	Uses the gap between consecutive characters relative to font size to detect word boundaries.
	This handles PDFs that don't encode explicit space characters in their content stream.
	"""
	if not chars:
		return ""
	sorted_chars = sorted(chars, key=lambda c: (round(float(c.get("top", 0)), 1), float(c.get("x0", 0))))
	lines: list[list[str]] = []
	current_line: list[str] = []
	prev_bottom: float | None = None
	prev_x1: float | None = None
	prev_size: float = 10.0
	for ch in sorted_chars:
		text = ch.get("text", "")
		if not text or not text.strip():
			continue
		top = float(ch.get("top", 0))
		x0 = float(ch.get("x0", 0))
		x1 = float(ch.get("x1", x0 + 5))
		size = float(ch.get("size", prev_size) or prev_size or 10.0)
		if prev_bottom is not None and top > prev_bottom + 2:
			if current_line:
				lines.append(current_line)
			current_line = []
			prev_x1 = None
		if prev_x1 is not None and (x0 - prev_x1) > gap_ratio * max(size, prev_size, 8):
			current_line.append(" ")
		current_line.append(text)
		prev_bottom = float(ch.get("bottom", top + size))
		prev_x1 = x1
		prev_size = size
	if current_line:
		lines.append(current_line)
	return "\n".join("".join(line) for line in lines)


def _text_needs_char_reconstruction(text: str) -> bool:
	"""True when extract_text() lacks word spacing (glued words), so the char-gap
	reconstruction is worth attempting. For normally spaced text the reconstruction
	is a net loss: its gap heuristic injects spurious mid-word spaces ("ass essing")
	that defeat verbatim quote matching downstream — so callers still guard the result
	with _looks_better_spaced() before adopting it.

	Besides fully unspaced pages (very low space ratio), extract_text() also drops
	individual space glyphs at font/style changes and around parentheses on otherwise
	well-spaced pages ("SimulationEnvironment(MOOSE)framework"). Long letter runs and
	missing spaces around parentheses flag that localized gluing."""
	if len(text) < 200:
		return False
	space_ratio = text.count(" ") / len(text)
	if space_ratio < 0.04:
		return True
	long_runs = len(re.findall(r"[A-Za-z]{16,}", text))
	paren_glue = len(re.findall(r"[A-Za-z0-9]\(|\)[A-Za-z]", text))
	# A single extreme run ("vision-languageframeworkforindustrialAnomalyUnderstanding")
	# is glue even when the rest of the page is fine — the >=3 count never fires for a
	# lone glued title/sentence. Real words rarely exceed 25 letters; a legit long German
	# compound stays intact because _looks_better_spaced rejects reconstructions that
	# don't actually reduce glued runs.
	extreme_run = re.search(r"[A-Za-z]{25,}", text) is not None
	return long_runs >= 3 or paren_glue >= 3 or extreme_run


# extract_text() frequently drops the space glyph on the boundary between a word and an
# adjacent parenthesis at a font/style change, gluing e.g. "Environment(MOOSE)framework".
# Inserting a space there is near-zero risk in prose. Requiring at least two letters (or a
# digit) before "(" leaves single-letter math like "f(x)" / "H(t)" untouched.
_GLUED_OPEN_PAREN_RE = re.compile(r"([A-Za-z]{2,}|[0-9])\(")
_GLUED_CLOSE_PAREN_RE = re.compile(r"\)([A-Za-z])")


def _repair_glued_parens(text: str) -> str:
	text = _GLUED_OPEN_PAREN_RE.sub(r"\1 (", text)
	return _GLUED_CLOSE_PAREN_RE.sub(r") \1", text)


def _looks_better_spaced(naive: str, recon: str) -> bool:
	"""Adopt char-gap reconstruction only when it actually fixes glued words without
	over-splitting. It must reduce the number of long glued runs, and it must not flood
	the text with spurious single-letter tokens (the "ass essing" failure mode)."""
	if not recon:
		return False
	naive_glue = len(re.findall(r"[A-Za-z]{16,}", naive))
	recon_glue = len(re.findall(r"[A-Za-z]{16,}", recon))
	if recon_glue >= naive_glue:
		return False
	singleton_re = re.compile(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])")
	naive_singletons = len(singleton_re.findall(naive))
	recon_singletons = len(singleton_re.findall(recon))
	token_count = max(1, len(re.findall(r"\S+", recon)))
	if recon_singletons > naive_singletons + max(5, int(0.02 * token_count)):
		return False
	return True


# Joins words hyphenated across a line break ("assess-\ning" -> "assessing"). Column
# reconstruction flattens line breaks to spaces first, so "- " is matched as well.
# The lowercase-continuation requirement keeps capitalized compounds ("Wilcoxon-\nMann")
# intact, and the conjunction stoplist protects suspended hyphens ("pre- and
# post-treatment"). Genuine lowercase compounds broken at the line end lose their
# hyphen, which is acceptable for matching purposes.
_HYPHEN_BREAK_RE = re.compile(
	r"([A-Za-zÄÖÜäöüß])-[ \n](?!(?:and|or|to|as|und|oder|bis|als|sowie)\b)([a-zäöüß])"
)


def _join_hyphenated_linebreaks(text: str) -> str:
	return _HYPHEN_BREAK_RE.sub(r"\1\2", text)


def _classify_chars_by_region(
	chars: list[dict[str, Any]],
	gutter_x: float,
	page_height: float,
	margin_frac: float = 0.08,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
	"""Partition chars into (header, left, right, footer) by position, mirroring _classify_and_split_words."""
	top_margin = page_height * margin_frac
	bottom_margin = page_height * (1 - margin_frac)
	header: list[dict[str, Any]] = []
	left: list[dict[str, Any]] = []
	right: list[dict[str, Any]] = []
	footer: list[dict[str, Any]] = []
	for ch in chars:
		top = float(ch.get("top", 0))
		bottom = float(ch.get("bottom", top))
		x0 = float(ch.get("x0", 0))
		x1 = float(ch.get("x1", x0))
		center_x = (x0 + x1) / 2
		if top < top_margin:
			header.append(ch)
		elif bottom > bottom_margin:
			footer.append(ch)
		elif center_x < gutter_x:
			left.append(ch)
		else:
			right.append(ch)
	return header, left, right, footer


def _reconstruct_page_text(
	words: list[dict[str, Any]],
	page_width: float,
	page_height: float,
	*,
	chars: list[dict[str, Any]] | None = None,
) -> str | None:
	"""
	Rebuild a two-column page's text in correct reading order: running head,
	then the entire left column top-to-bottom, then the entire right column
	top-to-bottom, then the running footer. Returns None when no reliable
	column gutter is found or the split is degenerate (all body words on one
	side) — callers should fall back to naive whole-page extraction in that
	case (single-column pages, the corpus majority, are unaffected).

	Word bounding boxes drive column detection; text join uses word-level tokens
	(reliable for two-column PDFs where extract_words produces clean word segments).
	"""
	if not words:
		return None
	gutter_x = _find_column_gutter(words, page_width, page_height)
	if gutter_x is None:
		return None
	header_w, left_w, right_w, footer_w = _classify_and_split_words(words, gutter_x, page_height)
	if not left_w or not right_w:
		return None
	parts = [
		" ".join(str(w.get("text", "")) for w in group)
		for group in (header_w, left_w, right_w, footer_w)
		if group
	]
	joined = "\n\n".join(p for p in parts if p)
	# Word tokens inherit extract_text()'s glue problem (dropped space glyphs → words
	# concatenated inside one token). When the column-ordered text still lacks word
	# spacing, rebuild each region from char gaps — same guard as the single-column path.
	if chars and _text_needs_char_reconstruction(joined):
		regions = _classify_chars_by_region(chars, gutter_x, page_height)
		rebuilt_parts = [_chars_to_spaced_text(group) for group in regions if group]
		rebuilt = "\n\n".join(p for p in rebuilt_parts if p)
		if rebuilt and _looks_better_spaced(joined, rebuilt):
			return rebuilt
	return joined


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
						columns_used = 0
						char_reconstructed = 0
						for page in pdf.pages:
							naive_text = page.extract_text() or ""
							words: list[dict[str, Any]] | None = None
							recon_text: str | None = None
							chars: list[dict[str, Any]] = []
							try:
								words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
								chars = list(page.chars or [])
								recon_text = _reconstruct_page_text(words, float(page.width), float(page.height), chars=chars)
							except Exception:
								recon_text = None
							if recon_text is not None and naive_text and len(recon_text) < 0.9 * len(naive_text):
								recon_text = None
							if recon_text is not None:
								page_texts.append(_repair_glued_parens(_join_hyphenated_linebreaks(recon_text)))
								columns_used += 1
							else:
								# Prefer pdfplumber's own text: the char-gap reconstruction
								# only helps when the naive text lacks word spacing, and it
								# injects spurious mid-word spaces otherwise — so adopt it only
								# when _looks_better_spaced confirms it fixes gluing.
								spaced = ""
								if not naive_text or _text_needs_char_reconstruction(naive_text):
									candidate = _chars_to_spaced_text(chars) if chars else ""
									if candidate and (not naive_text or _looks_better_spaced(naive_text, candidate)):
										spaced = candidate
										char_reconstructed += 1
								page_text = spaced or naive_text or (_words_to_text(words) if words else "")
								page_texts.append(_repair_glued_parens(_join_hyphenated_linebreaks(page_text)))
						text = "\n\n---PAGE BREAK---\n\n".join(page_texts).strip()
						page_count = len(pdf.pages)
						metadata["extraction_method"] = "pdfplumber_columns" if columns_used else "pdfplumber"
						metadata["chars_extracted"] = len(text)
						metadata["columns_pages"] = columns_used
						metadata["char_reconstructed_pages"] = char_reconstructed
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
						page_texts.append(_join_hyphenated_linebreaks(page.extract_text() or ""))
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
