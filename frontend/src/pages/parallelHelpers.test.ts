import { describe, expect, it } from "vitest";

import type { ParallelSession, ParallelStage, ParallelVariant } from "../types";
import {
  buildParallelSessionMarkdown,
  groupVariantsByStage,
  STAGE_STATUS_LABEL,
  stageGroupLabel,
} from "./parallelHelpers";

function stage(over: Partial<ParallelStage> = {}): ParallelStage {
  return {
    id: "s1",
    session_id: "sess",
    name: "Etappe A",
    goal: "Grundlagen klären",
    status: "aktiv",
    position: 0,
    ...over,
  };
}

function variant(over: Partial<ParallelVariant> = {}): ParallelVariant {
  return {
    id: "v1",
    session_id: "sess",
    name: "Variante 1",
    approach: "Ansatz [arxiv:1]",
    rationale: "Weil",
    suggested_prompt: "mach was",
    origin: "ai",
    status: "vorgeschlagen",
    position: 0,
    stage_id: "s1",
    entries: [],
    ...over,
  };
}

function session(over: Partial<ParallelSession> = {}): ParallelSession {
  return {
    id: "sess",
    question: "Wie X bauen?",
    status: "active",
    variants: [],
    stages: [],
    followups: [],
    ...over,
  };
}

describe("groupVariantsByStage", () => {
  it("groups variants under their stage in stage order", () => {
    const s = session({
      stages: [stage(), stage({ id: "s2", name: "Etappe B", status: "offen", position: 1 })],
      variants: [
        variant({ id: "v2", stage_id: "s2" }),
        variant({ id: "v1", stage_id: "s1" }),
      ],
    });
    const groups = groupVariantsByStage(s);
    expect(groups).toHaveLength(2);
    expect(groups[0].stage?.id).toBe("s1");
    expect(groups[0].variants.map((v) => v.id)).toEqual(["v1"]);
    expect(groups[1].variants.map((v) => v.id)).toEqual(["v2"]);
  });

  it("puts legacy variants without stages into an orphan group labelled Etappe 1", () => {
    const s = session({ variants: [variant({ stage_id: null })] });
    const groups = groupVariantsByStage(s);
    expect(groups).toHaveLength(1);
    expect(groups[0].stage).toBeNull();
    expect(groups[0].variants).toHaveLength(1);
    expect(stageGroupLabel(groups[0], 0)).toBe("Etappe 1");
  });

  it("appends unknown-stage variants as a trailing orphan group", () => {
    const s = session({
      stages: [stage()],
      variants: [variant(), variant({ id: "vX", stage_id: "does-not-exist" })],
    });
    const groups = groupVariantsByStage(s);
    expect(groups).toHaveLength(2);
    expect(groups[1].stage).toBeNull();
    expect(stageGroupLabel(groups[1], 1)).toBe("Weitere Varianten");
  });
});

describe("buildParallelSessionMarkdown", () => {
  it("renders question, stages, variants, results, reviews and end review", () => {
    const s = session({
      overview_markdown: "Worum es geht.",
      synthesis_markdown: "### Finale Antwort\nNimm Variante 1 [arxiv:1].",
      stages: [
        stage({ review_markdown: "### Verständnis\nEtappe ok." }),
        stage({ id: "s2", name: "Etappe B", status: "offen", position: 1, goal: "" }),
      ],
      variants: [
        variant({
          entries: [
            { id: "e1", variant_id: "v1", session_id: "sess", role: "user", content: "80% erreicht" },
            {
              id: "e2",
              variant_id: "v1",
              session_id: "sess",
              role: "assistant",
              content: "### Verständnis\nGut [arxiv:1].",
              answer_payload: {
                question: "",
                answer: "### Verständnis\nGut [arxiv:1].",
                sources: [],
                evidence: [],
              },
            },
          ],
        }),
      ],
      followups: [{ id: "f1", session_id: "sess", question: "Und weiter?" }],
    });
    const md = buildParallelSessionMarkdown(s);
    expect(md).toContain("# Parallel-Recherche: Wie X bauen?");
    expect(md).toContain("## Überblick");
    expect(md).toContain("## Etappe 1: Etappe A — Aktiv");
    expect(md).toContain("Ziel: Grundlagen klären");
    expect(md).toContain("### Variante: Variante 1 (Vorgeschlagen)");
    expect(md).toContain("> 80% erreicht");
    expect(md).toContain("**Professor-Review:**");
    expect(md).toContain("### Etappen-Review: Etappe A");
    expect(md).toContain("## Etappe 2: Etappe B — Offen");
    expect(md).toContain("## Folgefragen");
    expect(md).toContain("## End-Review");
    expect(md).toContain("[arxiv:1]");
  });

  it("keeps working for legacy sessions without stages", () => {
    const s = session({ variants: [variant({ stage_id: null })] });
    const md = buildParallelSessionMarkdown(s);
    expect(md).toContain("## Etappe 1: Etappe 1");
    expect(md).toContain("### Variante: Variante 1");
  });
});

describe("STAGE_STATUS_LABEL", () => {
  it("covers all backend statuses", () => {
    expect(Object.keys(STAGE_STATUS_LABEL).sort()).toEqual(["abgeschlossen", "aktiv", "offen"]);
  });
});
