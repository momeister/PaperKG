import type { Paper } from "./types";

/** Link zur Originalquelle nach derselben Regel wie `/paper/meta` im Backend
 *  (api/routers/papers.py, `_external_paper_url` in api/routers/harvest.py):
 *  Landing-Page → DOI → direkter PDF-Link.
 *
 *  Bleibt auch dann nutzbar, wenn nie ein PDF geladen werden konnte — darüber
 *  kommt man später noch an das Paper (Bibliothek, Kauf). */
export function externalPaperUrl(paper: Paper): string | null {
  const landing = (paper.landing_page_url ?? "").trim();
  if (landing) return landing;
  const doi = (paper.doi ?? "").trim().replace("https://doi.org/", "");
  if (doi) return `https://doi.org/${doi}`;
  const pdfUrl = (paper.pdf_url ?? "").trim();
  return /^https?:\/\//i.test(pdfUrl) ? pdfUrl : null;
}
