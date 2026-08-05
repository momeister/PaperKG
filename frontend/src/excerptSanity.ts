// Frontend mirror of query/excerpt_sanity.py — a defensive sanity check used
// in reformulateForSource before a garbled PDF excerpt is fed to the rewrite
// LLM. PDF table-column extraction occasionally produces reversed tokens
// ("detanoitcarfopyH" = Hypofractionated, "esahP" = Phase, "yG" = Gy, "AN" = NA)
// that small rewrite models echo verbatim plus a chain-of-thought preamble.
// When detected we return "" (no rewrite) so the original statement stays
// unchanged rather than being corrupted.

const REVERSABLE_STEMS = [
  // EN
  "the", "and", "for", "that", "this", "with", "from", "were", "have",
  "which", "their", "about", "phase", "study", "patient", "treatment",
  "survival", "radiotherapy", "dose", "fraction", "stereotactic",
  "hypofractionated", "bevacizumab", "glioblastoma", "tumor", "cancer",
  "brain", "therapy", "clinical", "result", "conclusion", "method",
  "background", "objective", "increase", "decrease", "compared",
  "significant", "respect", "analysis", "data", "table", "figure",
  "reference", "copyright", "published", "journal",
  // DE
  "der", "die", "und", "mit", "von", "auf", "nicht", "ist", "eine",
  "einem", "patienten", "studie", "behandlung", "ergebnis", "methode",
  "schluss", "folgerung", "vergleich", "signifikant", "analyse",
  // units / common abbreviations whose reversal is a strong garble signal
  "Gy", "NA", "MGy",
];

const REVERSED_LONG = new Set(
  REVERSABLE_STEMS.filter((s) => s.length >= 4).map((s) => s.split("").reverse().join(""))
);
const REVERSED_SHORT = new Set(
  REVERSABLE_STEMS.filter((s) => s.length >= 2 && s.length < 4).map((s) => s.split("").reverse().join(""))
);

const MIDWORD_CAPITAL = /[a-z]{2,}[A-Z]/;

function tokenize(text: string): string[] {
  return (text ?? "").trim().split(/\s+/).filter(Boolean);
}

export function isGarbledExcerpt(text: string): boolean {
  const tokens = tokenize(text);
  if (tokens.length < 4) return false;

  const lower = tokens.map((t) => t.toLowerCase());
  let longHits = 0;
  for (const frag of REVERSED_LONG) {
    if (lower.some((t) => t.includes(frag))) {
      longHits += 1;
      if (longHits >= 2) return true;
    }
  }

  let shortHits = 0;
  for (const frag of REVERSED_SHORT) {
    if (lower.some((t) => t === frag)) {
      shortHits += 1;
      if (shortHits >= 3) return true;
    }
  }

  const alpha = tokens.filter((t) => /[A-Za-z]/.test(t));
  if (alpha.length >= 8) {
    const midwordCap = alpha.filter((t) => MIDWORD_CAPITAL.test(t)).length;
    const density = midwordCap / alpha.length;
    if (density >= 0.25 && shortHits >= 1) return true;
  }

  return false;
}