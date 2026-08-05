/**
 * Der Navigator: der Weg ins Projekt hinein.
 *
 * Die schmale Fassung in der Werkstatt hatte ein Suchfeld und sonst nichts —
 * wer nicht wusste, wonach er sucht, kam nicht hinein. Hier gibt es zwei
 * Zugänge: Suchen (mit Facetten über alle fünfzehn Symbolarten) und Blättern
 * (wichtigste Symbole je Art, heisse Dateien, dynamische Lücken, benutzte
 * Pakete). Bezeichner im Code sind fast immer englisch — das steht als Hinweis
 * da, weil es der häufigste Grund für „nichts gefunden" ist.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Boxes, FileWarning, Flame, Search } from "lucide-react";

import { api } from "../../api";
import { CODE_NODE_KINDS } from "../../types";
import type { CodeNodeId, CodeNodeKind, CodeSymbolHit } from "../../types";
import { Section, SymbolRow, kindLabel } from "./shared";

export type CodeNavigatorPanelProps = {
  projectId: string;
  ready: boolean;
  selectedId: CodeNodeId | null;
  term: string;
  onTermChange: (term: string) => void;
  onFocus: (hit: CodeSymbolHit) => void;
  onOpen: (path: string, line: number) => void;
};

/** Blätter-Arten mit Überschrift. Die Reihenfolge ist die Anzeigereihenfolge. */
const BROWSE_KINDS: { kind: CodeNodeKind; title: string; hint: string }[] = [
  { kind: "route", title: "Routen", hint: "was das Programm nach aussen anbietet" },
  { kind: "db_table", title: "Tabellen", hint: "welche Daten es anfasst" },
  { kind: "test", title: "Tests", hint: "was abgesichert ist" },
  { kind: "config_key", title: "Konfiguration", hint: "was von aussen geschaltet wird" },
];

export function CodeNavigatorPanel({
  projectId,
  ready,
  selectedId,
  term,
  onTermChange,
  onFocus,
  onOpen,
}: CodeNavigatorPanelProps) {
  const [debounced, setDebounced] = useState(term.trim());
  const [kinds, setKinds] = useState<CodeNodeKind[]>([]);
  const [browseKind, setBrowseKind] = useState<CodeNodeKind | null>(null);

  // 140 ms: kurz genug, dass es sich wie Tippen anfühlt, lang genug, dass ein
  // Wort nicht acht Abfragen auslöst.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(term.trim()), 140);
    return () => window.clearTimeout(timer);
  }, [term]);

  const kindParam = kinds.length ? kinds.join(",") : undefined;

  const symbols = useQuery({
    queryKey: ["codegraph", "search", projectId, debounced, kindParam],
    queryFn: () => api.codegraph.search(projectId, debounced, kindParam, 60),
    enabled: ready && debounced.length > 0,
  });

  const texts = useQuery({
    queryKey: ["codegraph", "search-text", projectId, debounced],
    queryFn: () => api.codegraph.searchText(projectId, debounced, 25),
    enabled: ready && debounced.length >= 3,
  });

  const overview = useQuery({
    queryKey: ["codegraph", "overview", projectId],
    queryFn: () => api.codegraph.overview(projectId),
    enabled: ready,
  });

  const browse = useQuery({
    queryKey: ["codegraph", "top", projectId, browseKind],
    queryFn: () => api.codegraph.top(projectId, browseKind ? [browseKind] : undefined, 40),
    enabled: ready && Boolean(browseKind),
  });

  const searching = debounced.length > 0;

  function toggleKind(kind: CodeNodeKind) {
    setKinds((current) =>
      current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind],
    );
  }

  const hits = useMemo(() => symbols.data ?? [], [symbols.data]);

  return (
    <div className="cgp-pane cgp-navigator">
      <div className="cgp-nav-search">
        <span className="search-field">
          <Search size={14} />
          <input
            autoFocus
            value={term}
            placeholder="Symbol oder Text suchen …"
            onChange={(event) => onTermChange(event.target.value)}
          />
        </span>
        <div className="cgp-facets">
          {CODE_NODE_KINDS.map((kind) => (
            <button
              key={kind}
              className={`cgp-facet${kinds.includes(kind) ? " cgp-facet--on" : ""}`}
              onClick={() => toggleKind(kind)}
              title={`Nur ${kindLabel(kind)}`}
            >
              {kindLabel(kind)}
            </button>
          ))}
        </div>
      </div>

      <div className="cgp-pane-body">
        {searching ? (
          <>
            {hits.length > 0 && (
              <Section icon={<Boxes size={13} />} title="Symbole" hint="nach Relevanz sortiert">
                {hits.map((hit) => (
                  <SymbolRow
                    key={hit.id}
                    hit={hit}
                    onFocus={onFocus}
                    onOpen={onOpen}
                    active={hit.id === selectedId}
                  />
                ))}
              </Section>
            )}
            {(texts.data?.length ?? 0) > 0 && (
              <Section icon={<Search size={13} />} title="Im Text">
                {texts.data!.map((hit, index) => (
                  <button
                    key={`${hit.path}:${hit.line}:${index}`}
                    className="cg-row"
                    onClick={() => onOpen(hit.path, hit.line)}
                  >
                    <span className="cg-row-main cgp-mono">{hit.text.trim().slice(0, 140)}</span>
                    <span className="cg-row-meta">
                      {hit.path}:{hit.line}
                    </span>
                  </button>
                ))}
              </Section>
            )}
            {!symbols.isLoading && hits.length === 0 && (texts.data?.length ?? 0) === 0 && (
              <p className="muted cg-fineprint">
                Nichts gefunden für „{debounced}". Bezeichner im Code sind fast immer englisch —
                such nach <code>total</code>, nicht nach „Endbetrag". Facetten oben schränken
                zusätzlich ein.
              </p>
            )}
          </>
        ) : (
          <>
            {overview.data && (
              <>
                <Section
                  icon={<Boxes size={13} />}
                  title="Wichtigste Symbole"
                  hint="nach PageRank, Reichweite, Änderungshäufigkeit und Risiko"
                >
                  {overview.data.important.map((hit) => (
                    <SymbolRow
                      key={hit.id}
                      hit={hit}
                      onFocus={onFocus}
                      onOpen={onOpen}
                      active={hit.id === selectedId}
                    />
                  ))}
                </Section>

                <section className="cg-section">
                  <h4 title="Arten, die der Überblick nicht zeigt — hier sind sie blätterbar.">
                    <Boxes size={13} /> Blättern
                  </h4>
                  <div className="cgp-facets">
                    {BROWSE_KINDS.map((entry) => (
                      <button
                        key={entry.kind}
                        className={`cgp-facet${browseKind === entry.kind ? " cgp-facet--on" : ""}`}
                        title={entry.hint}
                        onClick={() =>
                          setBrowseKind((current) => (current === entry.kind ? null : entry.kind))
                        }
                      >
                        {entry.title}
                      </button>
                    ))}
                  </div>
                  {browseKind &&
                    (browse.isLoading ? (
                      <p className="muted">wird geladen …</p>
                    ) : (browse.data?.length ?? 0) === 0 ? (
                      <p className="muted cg-fineprint">
                        Nichts dieser Art im Index — dieses Projekt hat davon keine.
                      </p>
                    ) : (
                      browse.data!.map((hit) => (
                        <SymbolRow
                          key={hit.id}
                          hit={hit}
                          onFocus={onFocus}
                          onOpen={onOpen}
                          active={hit.id === selectedId}
                        />
                      ))
                    ))}
                </section>

                <Section
                  icon={<Flame size={13} />}
                  title="Heisse Dateien"
                  hint="am häufigsten geändert — dort sitzt meist auch der Ärger"
                >
                  {overview.data.hot_files.map((file) => (
                    <button
                      key={file.path}
                      className="cg-row"
                      onClick={() => onOpen(file.path, 1)}
                      title={`${file.churn} Änderungen · ${file.risk} Fehlerbehebungen`}
                    >
                      <span className="cg-row-main">{file.path}</span>
                      <span className="cg-row-meta">
                        {file.churn}× · {file.risk} Fixes
                      </span>
                    </button>
                  ))}
                </Section>

                {overview.data.gaps.length > 0 && (
                  <Section
                    icon={<FileWarning size={13} />}
                    title="Dynamische Lücken"
                    hint="Hier endet die statische Analyse — Reflection, eval, Dependency Injection. Sichtbar gemacht statt weggelassen."
                  >
                    {overview.data.gaps.map((gap) => (
                      <SymbolRow
                        key={gap.id}
                        hit={gap}
                        onFocus={onFocus}
                        onOpen={onOpen}
                        active={gap.id === selectedId}
                      />
                    ))}
                  </Section>
                )}

                <Section
                  icon={<Boxes size={13} />}
                  title="Benutzte Pakete"
                  hint="was der Code tatsächlich importiert — nicht, was in der Paketliste steht"
                >
                  <div className="cg-chips">
                    {overview.data.dependencies.map((dep) => (
                      <span key={dep.id} className="cg-chip">
                        {dep.name}
                      </span>
                    ))}
                  </div>
                </Section>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default CodeNavigatorPanel;
