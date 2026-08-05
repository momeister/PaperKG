/**
 * Der Steckbrief: was man über ein Symbol sagen kann, ohne ein Modell zu fragen.
 *
 * Vorher stand an dieser Stelle der Docstring — und wo keiner im Code steht,
 * stand nichts. Das las sich, als wüsste das Werkzeug nichts über die Funktion,
 * dabei kennt es Signatur, Nebenwirkungen, Aufrufer, Komplexität und Historie.
 * Diese Sätze sind aus genau diesen Zahlen gebaut: keine Vermutung, keine
 * Erfindung, nur zusammengefasst, was im Index steht.
 *
 * Reine Funktionen, damit sie prüfbar sind — die Aussagen dürfen nicht kippen,
 * wenn jemand ein Feld umbenennt.
 */
import type { CodeNeighbour, CodeNodeDetail } from "../../types";

const EFFECT_LABEL: Record<string, string> = {
  file_read: "liest Dateien",
  file_write: "schreibt Dateien",
  network: "geht ins Netz",
  database: "spricht mit der Datenbank",
  process_spawn: "startet Prozesse",
  global_write: "ändert globalen Zustand",
  console: "schreibt auf die Konsole",
  randomness: "verwendet Zufall",
  time: "liest die Uhr",
  environment: "liest die Umgebung",
};

export function sideEffectLabel(effect: string): string {
  return EFFECT_LABEL[effect] ?? effect;
}

/**
 * Zwei bis vier Sätze über das Symbol, aus Fakten gebaut.
 *
 * Bewusst zurückhaltend formuliert: „ruft nichts weiter auf" ist eine Aussage
 * über den Index, nicht über die Wirklichkeit — bei dynamischen Aufrufen weiss
 * die statische Analyse es schlicht nicht. Deshalb steht dort, wo es zählt, ein
 * „laut Index".
 */
export function factSummary(detail: CodeNodeDetail): string[] {
  const facts = detail.facts;
  const metrics = detail.metrics;
  const lines: string[] = [];

  const kindWord =
    detail.kind === "method"
      ? "Methode"
      : detail.kind === "class"
        ? "Klasse"
        : detail.kind === "test"
          ? "Test"
          : detail.kind === "interface"
            ? "Schnittstelle"
            : "Funktion";

  if (facts) {
    const inputs = facts.params.length;
    const returns = facts.returns?.trim();
    const parts: string[] = [`${kindWord} mit ${inputs === 0 ? "keinem Parameter" : inputs === 1 ? "einem Parameter" : `${inputs} Parametern`}`];
    if (returns) parts.push(`gibt ${returns} zurück`);
    else parts.push("ohne angegebenen Rückgabetyp");
    parts.push(`${facts.loc} Zeilen`);
    lines.push(`${parts.join(", ")}.`);

    if (facts.side_effects.length) {
      lines.push(
        `Wirkt nach aussen: ${facts.side_effects.map(sideEffectLabel).join(", ")}.`,
      );
    } else if (facts.pure === true) {
      lines.push("Ohne Nebenwirkungen — arbeitet nur mit dem, was hereinkommt.");
    }

    if (facts.throws.length) {
      lines.push(`Kann ${facts.throws.join(", ")} werfen.`);
    }

    if (facts.complexity >= 15) {
      lines.push(
        `Verzweigt stark (Komplexität ${facts.complexity}, Verschachtelung ${facts.max_nesting}) — hier steckt Entscheidungslogik.`,
      );
    }
  }

  const callers = metrics.fan_in;
  const callees = metrics.fan_out;
  if (callers === 0 && callees === 0) {
    lines.push("Laut Index weder Aufrufer noch Aufgerufene — womöglich ein Einstiegspunkt oder dynamisch erreicht.");
  } else {
    lines.push(
      `Laut Index ${callers === 1 ? "ein Aufrufer" : `${callers} Aufrufer`} und ${
        callees === 1 ? "ein aufgerufenes Symbol" : `${callees} aufgerufene Symbole`
      }.`,
    );
  }

  if (metrics.risk > 0) {
    const changes = metrics.churn === 1 ? "eine Änderung" : `${metrics.churn} Änderungen`;
    const fixes = metrics.risk === 1 ? "eine Fehlerbehebung" : `${metrics.risk} Fehlerbehebungen`;
    lines.push(
      metrics.churn === 1
        ? `${changes}, und die war ${fixes} — die Zahlen gelten für die ganze Datei, nicht für dieses Symbol.`
        : `${changes}, davon ${fixes} — die Zahlen gelten für die ganze Datei, nicht für dieses Symbol.`,
    );
  }

  return lines;
}

/**
 * Nachbarn, die relevanter sind als das betrachtete Symbol.
 *
 * Der Fall, für den das gedacht ist: man klickt eine Hilfsfunktion an, und das
 * Interessante liegt eine Ebene tiefer. Ohne diesen Hinweis müsste man das aus
 * den Relevanzbalken selbst herauslesen.
 */
export function strongerNeighbours(
  detail: CodeNodeDetail,
  groups: { title: string; items: CodeNeighbour[] }[],
  margin = 0.1,
): { title: string; item: CodeNeighbour }[] {
  const threshold = (detail.metrics.relevance ?? 0) + margin;
  const found: { title: string; item: CodeNeighbour }[] = [];
  for (const group of groups) {
    for (const item of group.items) {
      if ((item.node.relevance ?? 0) > threshold) found.push({ title: group.title, item });
    }
  }
  return found
    .sort((a, b) => (b.item.node.relevance ?? 0) - (a.item.node.relevance ?? 0))
    .slice(0, 6);
}

/** Ist das hier eher ein Durchgangsposten als ein Ort mit Inhalt? */
export function looksLikeHelper(detail: CodeNodeDetail): boolean {
  const facts = detail.facts;
  if (!facts) return false;
  return facts.loc <= 12 && facts.complexity <= 3;
}
