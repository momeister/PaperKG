import type { PdfSelection } from "../types";

export function pdfCitationPayload(selection: PdfSelection, text: string, title?: string, language?: string) {
  const citation = { id: `cite_${crypto.randomUUID()}`, paper_id: selection.paperId, title, kind: "pdf_selection", reference_text: selection.originalText, pdf_excerpt: selection.originalText, pdf_anchors: selection.anchors };
  const label = (title || selection.paperId).replace(/[\[\]\n]/g, " ");
  const markdown = `${language ? `Übersetzung (${language}):\n\n` : ""}${text.split("\n").map(line => `> ${line}`).join("\n")}\n\nQuelle: [Z1 - ${label}](sciencekg://citation/${citation.id})`;
  return { markdown, citations: [citation] };
}
