import type { ExtractionLibraryItem } from "./types";

/**
 * Eine einzige Quelle der Wahrheit fuer "wie viele Paper kann ich extrahieren?".
 *
 * Vorher rechneten die Extraktionsseite und die Pipeline-Kachel unabhaengig
 * voneinander — und mit *unterschiedlichen* Filtern. Die Kachel zaehlte nur Paper
 * mit lokalem PDF, der Batch-Knopf zusaetzlich alle Paper mit Abstract. Auf einem
 * echten Projekt standen so "94/297" und "Nicht extrahiert (400)" nebeneinander,
 * ohne dass erkennbar war, dass beide Zahlen stimmen und nur Verschiedenes zaehlen.
 *
 * Hier wird einmal gezaehlt und ueberall dieselbe Aufschluesselung angezeigt.
 */

/** Paper mit lokal auflösbarem PDF. */
export function hasPdf(item: ExtractionLibraryItem): boolean {
  return Boolean(item.pdf_path) && item.pdf_available !== false;
}

/**
 * Kann dieses Item in einen Batch?
 *
 * Ohne PDF reicht ein Abstract: `api/routers/extraction.py` faellt dann auf
 * `_abstract_only_extraction_text` zurueck (Titel + Abstract statt PDF-Volltext).
 * Graue Web-Quellen laufen ueber die Einzelextraktion, nicht ueber den Batch.
 */
export function isBatchable(item: ExtractionLibraryItem): boolean {
  if (item.source_type === "grey") {
    return false;
  }
  return hasPdf(item) || item.abstract_available === true;
}

export type ExtractionCounts = {
  /** Alles, was der Batch annehmen wuerde (PDF + nur-Abstract). */
  extractable: number;
  /** Davon bereits erfolgreich extrahiert. */
  extracted: number;
  /** Noch offen — die Zahl auf dem "Nicht extrahiert"-Knopf. */
  open: number;
  /** Aufschluesselung: mit lokalem PDF. */
  withPdf: number;
  /** Aufschluesselung: kein PDF, aber ein Abstract. */
  abstractOnly: number;
  /** Weder PDF noch Abstract — nicht extrahierbar, nur informativ. */
  noText: number;
  /** Offene Paper mit PDF (fuer den Untertitel des Knopfes). */
  openWithPdf: number;
  /** Offene Paper nur mit Abstract. */
  openAbstractOnly: number;
  /** Graue Web-Quellen in der Liste (nicht batchbar). */
  grey: number;
};

export function computeExtractionCounts(items: ExtractionLibraryItem[]): ExtractionCounts {
  const counts: ExtractionCounts = {
    extractable: 0,
    extracted: 0,
    open: 0,
    withPdf: 0,
    abstractOnly: 0,
    noText: 0,
    openWithPdf: 0,
    openAbstractOnly: 0,
    grey: 0
  };

  for (const item of items) {
    if (item.source_type === "grey") {
      counts.grey += 1;
      continue;
    }
    const pdf = hasPdf(item);
    if (!pdf && item.abstract_available !== true) {
      counts.noText += 1;
      continue;
    }
    counts.extractable += 1;
    if (pdf) {
      counts.withPdf += 1;
    } else {
      counts.abstractOnly += 1;
    }
    if (item.latest_extraction_status === "success") {
      counts.extracted += 1;
    } else {
      counts.open += 1;
      if (pdf) {
        counts.openWithPdf += 1;
      } else {
        counts.openAbstractOnly += 1;
      }
    }
  }
  return counts;
}

/** „297 PDF · 197 nur Abstract · 112 ohne Text" — die Zeile unter dem Zaehler. */
export function breakdownLabel(counts: ExtractionCounts): string {
  const parts = [`${counts.withPdf} PDF`];
  if (counts.abstractOnly) {
    parts.push(`${counts.abstractOnly} nur Abstract`);
  }
  if (counts.noText) {
    parts.push(`${counts.noText} ohne Text`);
  }
  return parts.join(" · ");
}

/** Quellen-Filter fuer die Batch-Auswahl. */
export type BatchScope = "all" | "pdf" | "abstract";

export function matchesScope(item: ExtractionLibraryItem, scope: BatchScope): boolean {
  if (scope === "pdf") {
    return hasPdf(item);
  }
  if (scope === "abstract") {
    // Bewusst dieselbe Bedingung wie `counts.abstractOnly`: „kein PDF" allein
    // wuerde auch Paper ohne jeden Text einschliessen — der Filter „Abstract (1)"
    // zeigte dann zwei Zeilen, und eine davon war gar nicht extrahierbar.
    return !hasPdf(item) && item.abstract_available === true;
  }
  return true;
}
