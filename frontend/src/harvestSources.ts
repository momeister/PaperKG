import type { HarvestSource, HarvestSourceCatalog } from "./types";

/** Fallback, falls `GET /harvest/sources` (noch) nicht antwortet — muss zu
 *  `harvester/source_registry.py` passen. Die Live-Antwort gewinnt immer. */
export const FALLBACK_SOURCE_CATALOG: HarvestSourceCatalog = {
  groups: [
    { id: "aggregator", label: "Alle Fächer (Aggregatoren)" },
    { id: "mint", label: "MINT & Informatik" },
    { id: "life", label: "Medizin & Biologie" },
    { id: "social", label: "Sozial-, Geistes- & Bildungswissenschaften" }
  ],
  sources: [
    { id: "openalex", label: "OpenAlex", group: "aggregator", tier: "index", needs_key: false },
    { id: "crossref", label: "Crossref", group: "aggregator", tier: "journal", needs_key: false },
    { id: "semantic_scholar", label: "Semantic Scholar", group: "aggregator", tier: "index", needs_key: false },
    { id: "openaire", label: "OpenAIRE", group: "aggregator", tier: "index", needs_key: false },
    { id: "doaj", label: "DOAJ", group: "aggregator", tier: "journal", needs_key: false },
    { id: "core", label: "CORE", group: "aggregator", tier: "journal", needs_key: true },
    { id: "arxiv", label: "arXiv", group: "mint", tier: "preprint", needs_key: false },
    { id: "dblp", label: "DBLP", group: "mint", tier: "index", needs_key: false },
    { id: "ads", label: "NASA ADS", group: "mint", tier: "index", needs_key: true },
    { id: "europepmc", label: "Europe PMC / PubMed", group: "life", tier: "journal", needs_key: false },
    { id: "biorxiv", label: "bioRxiv / medRxiv", group: "life", tier: "preprint", needs_key: false },
    { id: "osf", label: "OSF Preprints", group: "social", tier: "preprint", needs_key: false },
    { id: "eric", label: "ERIC", group: "social", tier: "journal", needs_key: false },
    { id: "doab", label: "DOAB", group: "social", tier: "journal", needs_key: false },
    { id: "hal", label: "HAL", group: "social", tier: "journal", needs_key: false }
  ],
  default: ["arxiv", "openalex"]
};

let activeCatalog: HarvestSourceCatalog = FALLBACK_SOURCE_CATALOG;

/** Vom Backend geladenen Katalog uebernehmen, damit Badges ueberall gleich heissen. */
export function setSourceCatalog(catalog: HarvestSourceCatalog | undefined): void {
  if (catalog?.sources?.length) activeCatalog = catalog;
}

export function sourceCatalog(): HarvestSourceCatalog {
  return activeCatalog;
}

function findSource(sourceId: string): HarvestSource | undefined {
  return activeCatalog.sources.find((source) => source.id === sourceId);
}

/** Kurzes Label fuer das Quellen-Badge — unbekannte Quellen zeigen ihre ID. */
export function sourceLabel(sourceId: string | undefined | null): string {
  const id = (sourceId ?? "").trim();
  if (!id) return "—";
  return findSource(id)?.label ?? id;
}

/** Art der Quelle; steuert die Badge-Farbe (begutachtet / Preprint / Nachweis). */
export function sourceTier(sourceId: string | undefined | null): "journal" | "preprint" | "index" {
  return findSource((sourceId ?? "").trim())?.tier ?? "index";
}

export const TIER_LABEL: Record<string, string> = {
  journal: "begutachtete Quelle",
  preprint: "Preprint (nicht begutachtet)",
  index: "Nachweissystem"
};
