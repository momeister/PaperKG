/**
 * Der Kartenzustand: Ausschnitte holen, zusammenführen, Positionen berechnen.
 *
 * Zwei Regeln und ihre Gründe:
 *
 *   * Die beiden Richtungen werden **getrennt** geholt (siehe `fetchSide`) —
 *     einmal weil `direction=both` die Pfeile verdreht, und einmal weil erst die
 *     getrennten Antworten sagen, ob ein Knoten links oder rechts gehört.
 *   * Das Layout läuft **synchron**, nicht im Worker. Es ist eine Sortierung je
 *     Spalte, keine Kräftesimulation; ein Worker brächte nur eine Bildverzögerung.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../../api";
import type { CodeEdgeKind, CodeGraphSlice, CodeNodeId } from "../../types";
import { computeColumnLayout } from "./columnLayout";
import type { ColumnPoint } from "./columnLayout";
import { EMPTY_MAP, collapseNode, mergeSlice } from "./codeMap";
import type { MapState } from "./codeMap";

/**
 * Eine Richtung holen.
 *
 * **Nicht** durch ein einzelnes `direction: "both"` ersetzen, auch wenn es nach
 * einer Anfrage weniger aussieht: `Graph::neighbours` hängt im Both-Zweig Aus-
 * und Eingang flach aneinander (cs-graph/src/query.rs:341), und `Graph::slice`
 * trägt anschliessend *alles* als ausgehend ein (query.rs:411). Jede eingehende
 * Kante zeigte dann auf der Karte in die falsche Richtung — und die Seite, auf
 * die ein Knoten gehört, wäre gar nicht mehr feststellbar.
 */
function fetchSide(
  projectId: string,
  nodeId: CodeNodeId,
  direction: "in" | "out",
  depth: number,
  edges: CodeEdgeKind[],
): Promise<CodeGraphSlice> {
  return api.codegraph.slice(projectId, nodeId, { direction, depth, edges, budget: 400 });
}

/**
 * Dieselbe Karte, aber über *mehrere* Wurzeln — die Antwort auf „welche
 * Funktionen gehören zu diesem Feature".
 *
 * Bewusst ein zweiter Hook statt einer Verallgemeinerung von `useCodeMap`: der
 * Einzelfokus-Pfad ist getestet und wird von der ganzen Seite benutzt, und ein
 * gemeinsamer Hook mit zwei Betriebsarten wäre an jeder Stelle eine Fallunter-
 * scheidung. Die dreissig Zeilen Wiederholung sind der bessere Preis.
 *
 * Bewusst **kein** `graph_slice_multi` in Rust: `mergeSlice` besitzt die Regel,
 * dass eine Budgetsprengung *ganz* verworfen wird, und die zweimal richtig zu
 * halten wäre die teurere Doppelung.
 *
 * Alle Wurzeln liegen in Spalte 0. Das liest sich als: das sind die Funktionen
 * dieses Features, links wer sie auslöst, rechts was sie benutzen.
 */
export function useMultiFocusMap(
  projectId: string | null,
  rootIds: CodeNodeId[],
  edgeKinds: CodeEdgeKind[],
  enabled: boolean,
): UseCodeMapResult {
  const [map, setMap] = useState<MapState>(EMPTY_MAP);
  const [isLoading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runRef = useRef(0);

  const kindKey = edgeKinds.join(",");
  const rootKey = rootIds.join(",");

  useEffect(() => {
    if (!projectId || !rootIds.length || !enabled) {
      setMap(EMPTY_MAP);
      return;
    }
    const run = ++runRef.current;
    setLoading(true);
    setError(null);

    Promise.all(
      rootIds.flatMap((id) => [
        fetchSide(projectId, id, "out", 1, edgeKinds),
        fetchSide(projectId, id, "in", 1, edgeKinds),
      ]),
    )
      .then((slices) => {
        if (runRef.current !== run) return;
        let next = EMPTY_MAP;
        // Erst alle Wurzeln in Spalte 0, damit sie die Mitte behalten, auch wenn
        // eine von ihnen zufällig Nachbarin einer anderen ist.
        rootIds.forEach((id, index) => {
          const slice = slices[index * 2] ?? slices[index * 2 + 1];
          const hit = slice?.nodes.find((node) => node.id === id);
          if (hit) {
            next = mergeSlice(next, { nodes: [hit], edges: [], truncated: false }, {
              hop: 0,
              column: 0,
            });
          }
        });
        rootIds.forEach((_id, index) => {
          const out = slices[index * 2];
          const incoming = slices[index * 2 + 1];
          if (out) next = mergeSlice(next, out, { hop: 1, column: 1 });
          if (incoming) next = mergeSlice(next, incoming, { hop: 1, column: -1 });
        });
        setMap(next);
      })
      .catch((cause: unknown) => {
        if (runRef.current !== run) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setMap(EMPTY_MAP);
      })
      .finally(() => {
        if (runRef.current === run) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, rootKey, kindKey, enabled]);

  const positions = useMemo(
    () =>
      computeColumnLayout(
        [...map.nodes.values()].map((node) => ({
          id: node.id,
          column: node.column,
          weight: node.relevance,
        })),
      ),
    [map.nodes],
  );

  const noop = useCallback(() => {}, []);
  return {
    map,
    positions,
    isLoading,
    error,
    expand: noop,
    collapse: noop,
    reset: useCallback(() => {
      runRef.current++;
      setMap(EMPTY_MAP);
    }, []),
    dismissBudgetWarning: useCallback(
      () => setMap((current) => ({ ...current, atBudget: false })),
      [],
    ),
  };
}

export type UseCodeMapResult = {
  map: MapState;
  positions: Map<string, ColumnPoint>;
  isLoading: boolean;
  error: string | null;
  expand: (nodeId: CodeNodeId, side?: "in" | "out") => void;
  collapse: (nodeId: CodeNodeId) => void;
  reset: () => void;
  dismissBudgetWarning: () => void;
};

export function useCodeMap(
  projectId: string | null,
  focusId: CodeNodeId | null,
  edgeKinds: CodeEdgeKind[],
  depth: number,
  enabled: boolean,
): UseCodeMapResult {
  const [map, setMap] = useState<MapState>(EMPTY_MAP);
  const [isLoading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Damit eine langsam zurückkommende alte Anfrage die neue nicht überschreibt.
  const runRef = useRef(0);

  const kindKey = edgeKinds.join(",");

  useEffect(() => {
    if (!projectId || !focusId || !enabled) {
      setMap(EMPTY_MAP);
      return;
    }
    const run = ++runRef.current;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchSide(projectId, focusId, "out", depth, edgeKinds),
      fetchSide(projectId, focusId, "in", depth, edgeKinds),
    ])
      .then(([out, incoming]) => {
        if (runRef.current !== run) return;
        // Der Fokus selbst sitzt in Spalte 0 — er steckt in beiden Antworten,
        // und der erste Merge legt seine Spalte fest.
        let next = mergeSlice(EMPTY_MAP, { nodes: [], edges: [], truncated: false });
        const focusHit =
          out.nodes.find((node) => node.id === focusId) ??
          incoming.nodes.find((node) => node.id === focusId);
        if (focusHit) {
          next = mergeSlice(next, { nodes: [focusHit], edges: [], truncated: false }, {
            hop: 0,
            column: 0,
          });
        }
        next = mergeSlice(next, out, { hop: 1, column: 1 });
        next = mergeSlice(next, incoming, { hop: 1, column: -1 });
        setMap(next);
      })
      .catch((cause: unknown) => {
        if (runRef.current !== run) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setMap(EMPTY_MAP);
      })
      .finally(() => {
        if (runRef.current === run) setLoading(false);
      });
    // kindKey statt edgeKinds: das Array ist bei jedem Render neu.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, focusId, depth, kindKey, enabled]);

  const expand = useCallback(
    (nodeId: CodeNodeId, side?: "in" | "out") => {
      if (!projectId) return;
      const node = map.nodes.get(nodeId);
      if (!node || node.expanded) return;
      // Ein Knoten links wird nach links weitergeklappt, einer rechts nach
      // rechts. Beides zu holen zöge die Gegenrichtung in dieselbe Spalte und
      // machte aus dem Fluss wieder ein Knäuel.
      const direction: "in" | "out" = side ?? (node.column < 0 ? "in" : "out");
      const columnStep = direction === "in" ? -1 : 1;
      const run = runRef.current;
      setLoading(true);
      fetchSide(projectId, nodeId, direction, 1, edgeKinds)
        .then((slice) => {
          if (runRef.current !== run) return;
          setMap((current) =>
            mergeSlice(current, slice, {
              hop: node.hop + 1,
              column: node.column + columnStep,
              expandedFrom: nodeId,
            }),
          );
        })
        .catch((cause: unknown) =>
          setError(cause instanceof Error ? cause.message : String(cause)),
        )
        .finally(() => {
          if (runRef.current === run) setLoading(false);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId, map.nodes, kindKey],
  );

  const collapse = useCallback(
    (nodeId: CodeNodeId) => {
      if (!focusId) return;
      setMap((current) => collapseNode(current, focusId, nodeId));
    },
    [focusId],
  );

  const reset = useCallback(() => {
    runRef.current++;
    setMap(EMPTY_MAP);
  }, []);

  const dismissBudgetWarning = useCallback(
    () => setMap((current) => ({ ...current, atBudget: false })),
    [],
  );

  // Die Kantenfilter gehen absichtlich nicht in die Rechnung ein: ein
  // ausgeschalteter Kantentyp soll die Knoten nicht umsortieren, sonst wandert
  // beim Filtern das ganze Bild.
  const positions = useMemo(
    () =>
      computeColumnLayout(
        [...map.nodes.values()].map((node) => ({
          id: node.id,
          column: node.column,
          weight: node.relevance,
        })),
      ),
    [map.nodes],
  );

  return { map, positions, isLoading, error, expand, collapse, reset, dismissBudgetWarning };
}
