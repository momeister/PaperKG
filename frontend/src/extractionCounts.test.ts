import { describe, expect, it } from "vitest";

import { breakdownLabel, computeExtractionCounts, isBatchable, matchesScope } from "./extractionCounts";
import type { ExtractionLibraryItem } from "./types";

function item(partial: Partial<ExtractionLibraryItem>): ExtractionLibraryItem {
  return {
    paper_id: partial.paper_id ?? "p",
    title: "T",
    filename: "",
    pdf_path: "",
    ...partial
  } as ExtractionLibraryItem;
}

const withPdf = (id: string, status?: string) =>
  item({ paper_id: id, pdf_path: `/pdfs/${id}.pdf`, pdf_available: true, latest_extraction_status: status });
const abstractOnly = (id: string, status?: string) =>
  item({ paper_id: id, pdf_available: false, abstract_available: true, latest_extraction_status: status });
const noText = (id: string) => item({ paper_id: id, pdf_available: false, abstract_available: false });
const grey = (id: string) => item({ paper_id: `grey::${id}`, source_type: "grey", pdf_available: true });

describe("computeExtractionCounts", () => {
  it("zählt PDF- und Abstract-only-Paper in einer Gesamtmenge", () => {
    // Genau die Konstellation, die vorher zwei widersprüchliche Zahlen ergab.
    const counts = computeExtractionCounts([
      withPdf("a", "success"),
      withPdf("b"),
      abstractOnly("c"),
      abstractOnly("d", "success"),
      noText("e"),
      grey("f")
    ]);

    expect(counts.extractable).toBe(4);
    expect(counts.extracted).toBe(2);
    expect(counts.open).toBe(2);
    expect(counts.withPdf).toBe(2);
    expect(counts.abstractOnly).toBe(2);
    expect(counts.noText).toBe(1);
    expect(counts.grey).toBe(1);
  });

  it("schlüsselt die offenen Paper nach Quelle auf", () => {
    const counts = computeExtractionCounts([withPdf("a"), abstractOnly("b"), abstractOnly("c", "success")]);
    expect(counts.openWithPdf).toBe(1);
    expect(counts.openAbstractOnly).toBe(1);
  });

  it("zählt gescheiterte Extraktionen als offen, nicht als erledigt", () => {
    const counts = computeExtractionCounts([withPdf("a", "failed")]);
    expect(counts.extracted).toBe(0);
    expect(counts.open).toBe(1);
  });

  it("behandelt pdf_available === false als 'kein PDF', auch mit gesetztem Pfad", () => {
    const counts = computeExtractionCounts([
      item({ paper_id: "a", pdf_path: "/weg.pdf", pdf_available: false, abstract_available: true })
    ]);
    expect(counts.withPdf).toBe(0);
    expect(counts.abstractOnly).toBe(1);
  });

  it("liefert bei leerer Bibliothek überall Null", () => {
    expect(computeExtractionCounts([])).toMatchObject({ extractable: 0, extracted: 0, open: 0 });
  });
});

describe("isBatchable", () => {
  it("nimmt Paper mit PDF und Paper mit Abstract", () => {
    expect(isBatchable(withPdf("a"))).toBe(true);
    expect(isBatchable(abstractOnly("b"))).toBe(true);
  });

  it("lehnt Paper ohne Text und graue Quellen ab", () => {
    expect(isBatchable(noText("c"))).toBe(false);
    // Graue Web-Quellen laufen über die Einzelextraktion.
    expect(isBatchable(grey("d"))).toBe(false);
  });
});

describe("matchesScope", () => {
  it("trennt PDF- von Abstract-Quellen", () => {
    expect(matchesScope(withPdf("a"), "pdf")).toBe(true);
    expect(matchesScope(withPdf("a"), "abstract")).toBe(false);
    expect(matchesScope(abstractOnly("b"), "abstract")).toBe(true);
    expect(matchesScope(abstractOnly("b"), "pdf")).toBe(false);
    expect(matchesScope(withPdf("a"), "all")).toBe(true);
    expect(matchesScope(abstractOnly("b"), "all")).toBe(true);
  });

  it("zeigt unter 'Abstract' nur, was der Zähler auch als Abstract zählt", () => {
    // Vorher hiess der Filter schlicht „kein PDF": neben dem einen Abstract-Paper
    // stand dann auch das Paper ganz ohne Text in der Liste — „Abstract (1)" mit
    // zwei Zeilen. Die Filtermenge muss zu `counts.abstractOnly` passen.
    const items = [withPdf("a"), abstractOnly("b"), noText("c")];
    const counts = computeExtractionCounts(items);
    expect(items.filter((entry) => matchesScope(entry, "abstract"))).toHaveLength(counts.abstractOnly);
    expect(matchesScope(noText("c"), "abstract")).toBe(false);
  });
});

describe("breakdownLabel", () => {
  it("nennt nur die Kategorien, die es gibt", () => {
    expect(breakdownLabel(computeExtractionCounts([withPdf("a"), abstractOnly("b"), noText("c")]))).toBe(
      "1 PDF · 1 nur Abstract · 1 ohne Text"
    );
    expect(breakdownLabel(computeExtractionCounts([withPdf("a")]))).toBe("1 PDF");
  });
});
