/**
 * Gemeinsame Bausteine des Code-Graphen — Vokabular und Verhalten an einer Stelle.
 *
 * Das Werkstatt-Panel und die grosse Seite zeigen dieselben Dinge in
 * unterschiedlicher Breite. Was sie *sagen*, muss identisch sein: dieselben
 * Marker ●◐○◆, dieselben Bezeichnungen, dieselbe Regel — **keine Beziehung ohne
 * Sicherheitsstufe und Belegstelle**. Zwei Kopien davon würden auseinanderdriften,
 * und die Abweichung fiele erst auf, wenn eine geratene Kante irgendwo wie eine
 * belegte aussieht.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network } from "lucide-react";

import { api, streamCodeGraphIndex } from "../../api";
import type {
  CodeConfidence,
  CodeEdgeKind,
  CodeIndexEvent,
  CodeNeighbour,
  CodeNodeKind,
  CodeSymbolHit,
} from "../../types";

/** Sicherheitsstufen. Marker und Wortlaut wie im Original — sie sind Vokabular. */
export const CONFIDENCE_META: Record<CodeConfidence, { marker: string; label: string; hint: string }> = {
  measured: { marker: "◆", label: "gemessen", hint: "zur Laufzeit beobachtet" },
  verified: { marker: "●", label: "verifiziert", hint: "vom Language Server bestätigt" },
  resolved: { marker: "◐", label: "aufgelöst", hint: "über Import, Sichtbarkeit oder Vererbung" },
  guessed: { marker: "○", label: "vermutet", hint: "nur über Namensgleichheit geraten" },
};

export const KIND_LABEL: Partial<Record<CodeNodeKind, string>> = {
  file: "Datei",
  module: "Modul",
  class: "Klasse",
  interface: "Schnittstelle",
  function: "Funktion",
  method: "Methode",
  field: "Feld",
  global: "Global",
  route: "Route",
  db_table: "Tabelle",
  db_column: "Spalte",
  test: "Test",
  config_key: "Konfig",
  external_package: "Paket",
  dynamic_gap: "Lücke",
};

export const EDGE_KIND_LABEL: Record<CodeEdgeKind, string> = {
  calls: "ruft auf",
  reads: "liest",
  writes: "schreibt",
  contains: "enthält",
  imports: "importiert",
  inherits: "erbt von",
  implements: "implementiert",
  param_type: "Parametertyp",
  returns_type: "Rückgabetyp",
  throws: "wirft",
  tested_by: "getestet von",
  touches_table: "fasst Tabelle an",
  handles_route: "bedient Route",
  gated_by: "geschaltet durch",
};

/**
 * Die vierzehn Kantenarten in vier Gruppen — so viele Filterchips nebeneinander
 * sind eine Wand; gruppiert sind sie eine Auswahl.
 */
export const EDGE_GROUPS: { title: string; kinds: CodeEdgeKind[] }[] = [
  { title: "Referenzen", kinds: ["calls", "reads", "writes"] },
  { title: "Struktur", kinds: ["contains", "imports", "inherits", "implements"] },
  { title: "Typen", kinds: ["param_type", "returns_type", "throws"] },
  { title: "Betrieb", kinds: ["tested_by", "touches_table", "handles_route", "gated_by"] },
];

export const PHASE_LABEL: Record<string, string> = {
  scanning: "Dateien durchsuchen",
  parsing: "Code lesen",
  resolving: "Verbindungen auflösen",
  history: "Historie auswerten",
  ranking: "Relevanz berechnen",
  done: "fertig",
};

export function kindLabel(kind: CodeNodeKind): string {
  return KIND_LABEL[kind] ?? kind;
}

export function edgeKindLabel(kind: CodeEdgeKind): string {
  return EDGE_KIND_LABEL[kind] ?? kind;
}

export function Confidence({ value, candidates }: { value: CodeConfidence; candidates?: number }) {
  const meta = CONFIDENCE_META[value] ?? CONFIDENCE_META.guessed;
  const ambiguity = candidates && candidates > 1 ? ` · ${candidates} Kandidaten` : "";
  return (
    <span className={`cg-conf cg-conf--${value}`} title={`${meta.label} — ${meta.hint}${ambiguity}`}>
      {meta.marker}
    </span>
  );
}

export function Relevance({ value }: { value: number }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <span className="cg-relevance" title={`Relevanz ${pct}%`}>
      <span className="cg-relevance-bar" style={{ width: `${Math.max(2, pct)}%` }} />
    </span>
  );
}

export function Stat({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="cg-stat">
      <span className="cg-stat-value">{value.toLocaleString("de-DE")}</span>
      <span className="cg-stat-label">{label}</span>
      {sub && <span className="cg-stat-sub">{sub}</span>}
    </div>
  );
}

export function Section({
  icon,
  title,
  hint,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="cg-section">
      <h4 title={hint}>
        {icon} {title}
      </h4>
      {children}
    </section>
  );
}

export function SymbolRow({
  hit,
  onFocus,
  onOpen,
  active,
}: {
  hit: CodeSymbolHit;
  onFocus: (hit: CodeSymbolHit) => void;
  onOpen: (path: string, line: number) => void;
  active?: boolean;
}) {
  return (
    <div className={`cg-row cg-row--symbol${active ? " cg-row--active" : ""}`}>
      <button className="cg-row-main" onClick={() => onFocus(hit)} title="Bauplan öffnen">
        <Relevance value={hit.relevance} />
        <span className="cg-kind">{kindLabel(hit.kind)}</span>
        <span className="cg-qualified">{hit.qualified}</span>
      </button>
      <button
        className="cg-row-meta cg-row-link"
        onClick={() => onOpen(hit.path, hit.line)}
        title="Im Editor öffnen"
      >
        {hit.path}:{hit.line}
      </button>
    </div>
  );
}

/**
 * Eine Nachbarliste. Jede Zeile trägt ihre Sicherheitsstufe und die Zeile, an
 * der die Beziehung belegt ist — ohne beides dürfte sie hier nicht stehen.
 *
 * `extra` hängt je Zeile eine zusätzliche Aktion an (die grosse Seite nutzt das
 * für „auf der Karte zeigen"); ohne die Angabe sieht die Liste aus wie immer.
 */
export function NeighbourList({
  title,
  items,
  onFocus,
  onOpen,
  extra,
}: {
  title: string;
  items: CodeNeighbour[];
  onFocus: (hit: CodeSymbolHit) => void;
  onOpen: (path: string, line: number) => void;
  extra?: (item: CodeNeighbour) => React.ReactNode;
}) {
  if (!items.length) return null;
  return (
    <section className="cg-section">
      <h4>
        <Network size={13} /> {title} <span className="cg-count">{items.length}</span>
      </h4>
      {items.map((item, index) => (
        <div key={`${item.node.id}-${item.kind}-${index}`} className="cg-row cg-row--symbol">
          <button className="cg-row-main" onClick={() => onFocus(item.node)}>
            <Confidence value={item.confidence} candidates={item.candidates} />
            <span className="cg-qualified">{item.node.qualified}</span>
          </button>
          {extra?.(item)}
          <button
            className="cg-row-meta cg-row-link"
            onClick={() => onOpen(item.evidence_path, item.evidence_line)}
            title="Belegstelle im Editor öffnen"
          >
            {item.evidence_path}:{item.evidence_line}
            {item.candidates > 1 && <em> · {item.candidates} Kandidaten</em>}
          </button>
        </div>
      ))}
    </section>
  );
}

/** Die dauerhafte Legende. Gehört unter jede Anzeige von Kanten, nie in ein Menü. */
export function ConfidenceLegend({ className }: { className?: string }) {
  return (
    <p className={className ?? "cg-legend"}>
      ● verifiziert &nbsp; ◐ aufgelöst &nbsp; ○ vermutet &nbsp; ◆ gemessen
    </p>
  );
}

/**
 * Indexzustand eines Code-Projekts: Status, Indizieren als SSE, Verwerfen.
 *
 * Beide Oberflächen brauchen exakt dieselben Knöpfe mit exakt denselben
 * Fortschrittstexten — deshalb liegt das hier und nicht zweimal daneben.
 */
export function useCodeIndex(projectId: string | null) {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const status = useQuery({
    queryKey: ["codegraph", "status", projectId],
    queryFn: () => api.codegraph.status(projectId!),
    enabled: Boolean(projectId),
  });

  const indexMutation = useMutation({
    mutationFn: async () => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setProgress("wird vorbereitet …");
      await streamCodeGraphIndex(
        projectId!,
        (event: CodeIndexEvent) => {
          if (event.event === "progress") {
            const phase = PHASE_LABEL[event.phase] ?? event.phase;
            setProgress(event.total > 0 ? `${phase} ${event.done}/${event.total}` : `${phase} …`);
          } else if (event.event === "failed") {
            setProgress(`Fehlgeschlagen: ${event.error}`);
          } else if (event.event === "done") {
            const reused = event.report.files_reused;
            setProgress(
              `${event.report.files_parsed} geparst, ${reused} wiederverwendet · ${event.report.duration_ms} ms`,
            );
          }
        },
        controller.signal,
      );
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["codegraph"] });
      window.setTimeout(() => setProgress(null), 6000);
    },
  });

  const dropMutation = useMutation({
    mutationFn: () => api.codegraph.dropIndex(projectId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["codegraph"] }),
  });

  useEffect(() => () => abortRef.current?.abort(), []);

  /** Anteil geratener Kanten. Steht dauerhaft da, nicht in einem Menü versteckt. */
  const guessShare = useMemo(() => {
    const stats = status.data?.stats;
    if (!stats || !stats.edges) return null;
    return Math.round((stats.guessed_edges / stats.edges) * 100);
  }, [status.data]);

  return {
    status,
    ready: status.data?.status === "ready" && (status.data?.stats.nodes ?? 0) > 0,
    progress,
    guessShare,
    indexMutation,
    dropMutation,
    busy: indexMutation.isPending,
  };
}
