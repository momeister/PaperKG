import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnswerText, AnswerView } from "./CodeAskPanel";
import type { CodeAnswer, CodeCitation, CodeTrailStep } from "../types";

/** Eine Minimalantwort — nur die Spur ist hier der Gegenstand. */
function answerWith(trail: CodeTrailStep[]): CodeAnswer {
  return {
    question: "wo fliesst das hin?",
    answer: "Von A nach B.",
    trail_text: "",
    citations: [],
    trail,
    verdict: "sound",
    verdict_label: "Alle Belege halten",
    is_clean: true,
    quote_mismatches: [],
    uncited_sentences: 0,
    tool_calls: 1,
    truncated: false,
    tool_calling_fallback: false,
    provider: "lm_studio",
    model: "qwen",
    papers: [],
    paper_citations: [],
    bare_citations: 0,
  };
}

/**
 * Der Antworttext ist die Stelle, an der die Beleg-Prüfung sichtbar wird — oder
 * eben nicht. Zwei Dinge dürfen hier nicht schiefgehen: der Chip muss genau die
 * zitierte Stelle umfassen (sonst markiert er Prosa als Beleg), und sein Status
 * muss mitkommen (sonst sieht ein erfundenes Zitat aus wie ein geprüftes).
 */
function citation(text: string, needle: string, status: CodeCitation["status"]): CodeCitation {
  const start = text.indexOf(needle);
  return {
    path: needle.split(":")[0],
    from_line: Number(needle.split(":")[1]),
    to_line: Number(needle.split(":")[1]),
    status,
    start,
    end: start + needle.length,
  };
}

// Die Suite hat kein globales Auto-Cleanup (die anderen Specs rendern je einmal);
// ohne das hier sammelten sich die Chips aller Fälle im selben Body.
afterEach(cleanup);

describe("AnswerText", () => {
  it("schneidet den Beleg-Chip genau an der zitierten Stelle aus", () => {
    const text = "Der Rabatt wird in src/pricing.py:2 gerechnet.";
    render(<AnswerText text={text} citations={[citation(text, "src/pricing.py:2", "verified")]} />);

    const chip = screen.getByRole("button");
    expect(chip).toHaveTextContent("src/pricing.py:2");
    expect(chip.className).toContain("cg-cite--verified");
    // Die Prosa daneben darf nicht mit im Chip landen.
    expect(chip.textContent).not.toContain("gerechnet");
  });

  it("verschiebt sich nicht, wenn vor dem Beleg Umlaute stehen", () => {
    // Genau der Fall, für den das Backend die Byte-Versätze umrechnet: „ä"/„ö"
    // sind in UTF-8 zwei Bytes, in JavaScript eine Zeichenposition.
    const text = "Für schöne Beträge zählt src/pricing.py:2.";
    render(<AnswerText text={text} citations={[citation(text, "src/pricing.py:2", "verified")]} />);

    expect(screen.getByRole("button")).toHaveTextContent("src/pricing.py:2");
  });

  it("zeigt einem nicht nachgeschlagenen Beleg seinen Status an", () => {
    const text = "Angeblich in src/pricing.py:400.";
    render(
      <AnswerText text={text} citations={[citation(text, "src/pricing.py:400", "not_retrieved")]} />,
    );

    const chip = screen.getByRole("button");
    expect(chip.className).toContain("cg-cite--not_retrieved");
    expect(chip).toHaveTextContent("nicht nachgeschlagen");
  });

  it("überspringt Belege, deren Versätze sich überlappen, statt Text zu verdoppeln", () => {
    const text = "Siehe src/a.py:1 dort.";
    const good = citation(text, "src/a.py:1", "verified");
    const overlapping: CodeCitation = { ...good, start: good.start + 2, end: good.end + 2 };
    render(<AnswerText text={text} citations={[good, overlapping]} />);

    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByText(/Siehe/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("dort.");
  });
});

/**
 * Die Spur ist der eigentliche Ertrag einer Antwort. Auf der grossen Seite wird
 * sie begehbar — aber nur dort, wo das Backend den Schritt an ein Symbol binden
 * konnte. Ohne `node_id` gäbe es auf der Karte nichts anzuspringen, und ein
 * Knopf, der nichts tut, wäre schlimmer als keiner.
 */
describe("CodeAskPanel — Spur auf der Karte", () => {
  const steps: CodeTrailStep[] = [
    { path: "src/a.py", line: 12, reason: "Eingang", node_id: "a1b2c3d4e5f60718", verified: true },
    { path: "src/b.py", line: 40, reason: "dort weiter", node_id: null, verified: true },
  ];

  function renderAnswer(props: { onShowOnMap?: (nodeId: string) => void }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <AnswerView
          answer={answerWith(steps)}
          projectId="cp_1"
          notesProjectId="__all_papers__"
          {...props}
        />
      </QueryClientProvider>,
    );
  }

  it("bietet den Kartensprung nur für Schritte mit node_id an", () => {
    renderAnswer({ onShowOnMap: () => {} });
    expect(screen.getAllByRole("button", { name: "auf der Karte" })).toHaveLength(1);
  });

  it("bietet ihn gar nicht an, wo es keine Karte gibt (Werkstatt-Panel)", () => {
    renderAnswer({});
    expect(screen.queryByRole("button", { name: "auf der Karte" })).toBeNull();
  });
});
