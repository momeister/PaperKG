import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AnswerText,
  citationContext,
  citationIds,
  citationMetasFor,
  citationQuoteFromParts,
  EvidenceVerificationBadge,
  formatAnswerForNote,
  isSentenceBoundary,
  meaningfulQuote,
  slimTurnForPersist,
  verificationSourcesFor
} from "./AssistantPage";
import type { AssistantTurn } from "./AssistantPage";
import type { Answer, VerificationSource } from "../types";

afterEach(() => cleanup());

describe("slimTurnForPersist (deep-analysis persistence size)", () => {
  it("drops heavy node fields so research turns persist instead of blowing the quota", () => {
    const turn: AssistantTurn = {
      id: "s1",
      question: "Frage",
      answer: null as unknown as AssistantTurn["answer"],
      verification: [],
      createdAt: "2026-06-24T00:00:00Z",
      type: "research_tree",
      researchNodes: [
        {
          id: "n1", parent_id: null, question: "Q", depth: 0, status: "done",
          answer: {
            question: "Q", answer: "Antwort [arxiv:1]", sources: [{ paper_id: "arxiv:1", title: "T" }],
            evidence: [], citation_links: [],
            context_diagnostics: { huge: "x".repeat(5000) },
            source_verification: { blob: "y".repeat(5000) },
          },
          verification: [{
            paper_id: "arxiv:1", title: "T", pdf_available: true,
            evidence: [{
              paper_id: "arxiv:1", kind: "quote", reference_text: "z".repeat(2000),
              pdf_excerpt: "w".repeat(5000), matched_terms: ["a", "b"], found_in_pdf_text: true,
            }],
          }],
        },
      ],
    };
    const slim = slimTurnForPersist(turn);
    const node = slim.researchNodes![0];
    // Heavy, re-derivable fields removed; essential ones (answer text, sources) kept.
    expect(node.answer?.context_diagnostics).toBeUndefined();
    expect(node.answer?.source_verification).toBeUndefined();
    expect(node.answer?.answer).toBe("Antwort [arxiv:1]");
    expect(node.answer?.sources).toHaveLength(1);
    expect(node.verification![0].evidence[0].pdf_excerpt).toBe("");
    expect(node.verification![0].evidence[0].matched_terms).toEqual([]);
    expect(node.verification![0].evidence[0].reference_text.length).toBeLessThanOrEqual(600);
    // Serialized turn is small enough to persist reliably.
    expect(JSON.stringify(slim).length).toBeLessThan(2000);
  });

  it("leaves non-research turns untouched", () => {
    const turn = { id: "c1", question: "Q", answer: null, verification: [], createdAt: "x", type: "chat" } as unknown as AssistantTurn;
    expect(slimTurnForPersist(turn)).toBe(turn);
  });
});

describe("sentence boundaries for note quotes", () => {
  it("does not treat abbreviations or parenthetical numbers as sentence ends", () => {
    const text = "Die Rate sank deutlich (8% vs. 5% vs. 8%). Danach folgt mehr Text.";
    const vsIndex = text.indexOf("vs.") + 2;
    expect(isSentenceBoundary(text, vsIndex)).toBe(false);
    const realEnd = text.indexOf(").") + 1;
    expect(isSentenceBoundary(text, realEnd)).toBe(true);
  });

  it("keeps the full sentence when extracting the quote around a citation", () => {
    const answer = "Die Reduktion war signifikant (8% vs. 5% vs. 8%) in allen Gruppen [p1]. Weiter geht es.";
    const parts = answer.split(/(\[[^\]]+\])/g);
    const quote = citationQuoteFromParts(parts, 1);
    expect(quote).toContain("Die Reduktion war signifikant");
    expect(quote).toContain("8% vs. 5%");
  });

  it("rejects meaningless quote fragments and accepts real sentences", () => {
    expect(meaningfulQuote("10).")).toBe("");
    expect(meaningfulQuote("8% vs. 5% vs. 8%).")).toBe("");
    expect(meaningfulQuote("Die Studie zeigt eine deutliche Reduktion der Ereignisse.")).toBe(
      "Die Studie zeigt eine deutliche Reduktion der Ereignisse."
    );
  });
});

const verification: VerificationSource[] = [
  {
    paper_id: "p1",
    title: "Earth Shape Study",
    pdf_available: true,
    evidence: [
      {
        paper_id: "p1",
        kind: "claim",
        reference_text: "The Earth is round.",
        pdf_excerpt: "The Earth is round.",
        matched_terms: ["earth", "round"],
        found_in_pdf_text: true
      }
    ]
  },
  {
    paper_id: "p2",
    title: "Solar Companion Study",
    pdf_available: true,
    evidence: [
      {
        paper_id: "p2",
        kind: "claim",
        reference_text: "Earth orbits the Sun.",
        pdf_excerpt: "Earth orbits the Sun.",
        matched_terms: ["earth", "sun"],
        found_in_pdf_text: true
      }
    ]
  }
];

describe("assistant grouped citations", () => {
  it("parses comma, semicolon, and language-joined citation groups", () => {
    expect(citationIds("p1, p2; p3 und p4 and p5")).toEqual(["p1", "p2", "p3", "p4", "p5"]);
  });

  it("maps grouped paper IDs to multiple citation targets", () => {
    const metas = citationMetasFor(verification, "p1, p2", "Earth is round and orbits the Sun.");

    expect(metas.map((meta) => meta.source.paper_id)).toEqual(["p1", "p2"]);
    expect(metas.map((meta) => meta.evidenceIndex)).toEqual([0, 0]);
  });

  it("exports grouped answer citations as separate note citation links", () => {
    const answer: Answer = {
      question: "What supports Earth facts?",
      answer: "The Earth is round and orbits the Sun [p1, p2].",
      sources: [
        { paper_id: "p1", title: "Earth Shape Study" },
        { paper_id: "p2", title: "Solar Companion Study" }
      ],
      evidence: []
    };

    const formatted = formatAnswerForNote(answer, verification);

    expect(formatted.citations).toHaveLength(2);
    expect(formatted.markdown).toContain("sciencekg://citation/cite_");
    expect(formatted.markdown).toContain("Z1 - Earth Shape Study");
    expect(formatted.markdown).toContain("Z1 - Solar Companion Study");
    expect(formatted.markdown).not.toContain("[p1, p2]");
  });

  it("renders grouped citation markers as separate clickable chips", () => {
    render(
      <AnswerText
        answer="The Earth is round and orbits the Sun [p1, p2]."
        getCitationMeta={(citation, context) => citationMetasFor(verification, citation, context)}
        onCitationClick={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: /Z1 Earth Shape Study/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Z1 Solar Companion Study/ })).toBeVisible();
  });

  it("highlights the cited answer span while hovering a citation chip", () => {
    const { container } = render(
      <AnswerText
        answer="The Earth is round [p1]."
        getCitationMeta={(citation, context) => citationMetasFor(verification, citation, context)}
        onCitationClick={vi.fn()}
      />
    );

    fireEvent.pointerEnter(screen.getByRole("button", { name: /Z1 Earth Shape Study/ }));

    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("The Earth is round");
  });

  it("pins, switches, and clears the cited answer span from citation clicks", () => {
    const { container } = render(
      <AnswerText
        answer="The Earth is round [p1]. Earth orbits the Sun [p2]."
        getCitationMeta={(citation, context) => citationMetasFor(verification, citation, context)}
        onCitationClick={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /Z1 Earth Shape Study/ }));
    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("The Earth is round");

    fireEvent.click(screen.getByRole("button", { name: /Z1 Solar Companion Study/ }));
    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("Earth orbits the Sun");
    expect(container.querySelector(".citation-context-highlight")?.textContent).not.toContain("The Earth is round");

    fireEvent.click(screen.getByRole("button", { name: /Z1 Solar Companion Study/ }));
    expect(container.querySelector(".citation-context-highlight")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Z1 Earth Shape Study/ }));
    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("The Earth is round");
    fireEvent.click(container.querySelector(".answer-text-content") as HTMLElement);
    expect(container.querySelector(".citation-context-highlight")).toBeNull();
  });

  it("returns one meta per fragment link at the same citation offset", () => {
    // A claim synthesized from two PDF passages ships two links at the same
    // citation_start — both Belegstellen must surface as separate chips.
    const fragmentVerification: VerificationSource[] = [
      {
        paper_id: "p1",
        title: "Fragment Study",
        pdf_available: true,
        evidence: [
          {
            evidence_id: "ev-frag-a",
            paper_id: "p1",
            kind: "pdf",
            reference_text: "Survival improved while side effects increased.",
            pdf_excerpt: "The median overall survival was 16.8 months in the treatment group.",
            matched_terms: [],
            found_in_pdf_text: true
          },
          {
            evidence_id: "ev-frag-b",
            paper_id: "p1",
            kind: "pdf",
            reference_text: "Survival improved while side effects increased.",
            pdf_excerpt: "Patients in the treatment group reported more fatigue and headaches.",
            matched_terms: [],
            found_in_pdf_text: true
          }
        ]
      }
    ];
    const links = [
      { citation: "p1", citation_start: 48, citation_end: 52, paper_id: "p1", evidence_id: "ev-frag-a" },
      { citation: "p1", citation_start: 48, citation_end: 52, paper_id: "p1", evidence_id: "ev-frag-b" }
    ];

    const metas = citationMetasFor(
      fragmentVerification,
      "p1",
      "Survival improved while side effects increased.",
      links,
      48
    );

    expect(metas.map((meta) => meta.evidenceId)).toEqual(["ev-frag-a", "ev-frag-b"]);
    expect(metas.map((meta) => meta.evidenceIndex)).toEqual([0, 1]);
  });

  it("uses citation links to map repeated paper citations to distinct evidence", () => {
    const repeatedVerification: VerificationSource[] = [
      {
        paper_id: "arxiv:2507.16947",
        title: "AI-based Clinical Decision Support",
        pdf_available: true,
        evidence: [
          {
            evidence_id: "ev-abstract",
            paper_id: "arxiv:2507.16947",
            kind: "paper",
            reference_text: "These results demonstrate potential for advancing responsible adoption.",
            pdf_excerpt: "These results demonstrate potential for advancing responsible adoption.",
            matched_terms: ["responsible", "adoption"],
            found_in_pdf_text: true
          },
          {
            evidence_id: "ev-errors",
            paper_id: "arxiv:2507.16947",
            kind: "claim",
            reference_text: "Clinicians with access to AI Consult made 16% fewer diagnostic errors and 13% fewer treatment errors.",
            pdf_excerpt: "Clinicians with access to AI Consult made 16% fewer diagnostic errors and 13% fewer treatment errors.",
            matched_terms: ["16%", "13%", "diagnostic", "treatment"],
            found_in_pdf_text: true
          }
        ]
      }
    ];
    const answerText =
      "The study argues the system can support responsible adoption [arxiv:2507.16947]. " +
      "It also reports 16% fewer diagnostic errors and 13% fewer treatment errors [arxiv:2507.16947].";
    const firstStart = answerText.indexOf("[arxiv:2507.16947]");
    const secondStart = answerText.lastIndexOf("[arxiv:2507.16947]");
    const answer: Answer = {
      question: "What did AI Consult show?",
      answer: answerText,
      sources: [{ paper_id: "arxiv:2507.16947", title: "AI-based Clinical Decision Support" }],
      evidence: [],
      citation_links: [
        {
          citation: "arxiv:2507.16947",
          citation_start: firstStart,
          citation_end: firstStart + "[arxiv:2507.16947]".length,
          paper_id: "arxiv:2507.16947",
          evidence_id: "ev-abstract"
        },
        {
          citation: "arxiv:2507.16947",
          citation_start: secondStart,
          citation_end: secondStart + "[arxiv:2507.16947]".length,
          paper_id: "arxiv:2507.16947",
          evidence_id: "ev-errors"
        }
      ]
    };

    const first = citationMetasFor(repeatedVerification, "arxiv:2507.16947", answerText.slice(0, firstStart), answer.citation_links, firstStart);
    const second = citationMetasFor(repeatedVerification, "arxiv:2507.16947", answerText.slice(firstStart), answer.citation_links, secondStart);
    const formatted = formatAnswerForNote(answer, repeatedVerification);

    expect(first[0].evidenceIndex).toBe(0);
    expect(second[0].evidenceIndex).toBe(1);
    expect(formatted.citations.map((citation) => citation.evidence_id)).toEqual(["ev-abstract", "ev-errors"]);
  });

  it("quotes only the preceding claim when the citation sits at a clean sentence boundary", () => {
    const answerText = "Claim A holds true. [p1] Claim B is unrelated.";
    const parts = answerText.split(/(\[[^\]]+\])/g);
    const citationIndex = parts.findIndex((part) => part === "[p1]");

    const quote = citationQuoteFromParts(parts, citationIndex);

    expect(quote).toBe("Claim A holds true.");
    expect(quote).not.toContain("Claim B is unrelated");
  });

  it("still combines a fragment from after the marker when it interrupts a sentence mid-claim", () => {
    const answerText = "Researchers observed that survival [p1] improved by 20% over the trial period.";
    const parts = answerText.split(/(\[[^\]]+\])/g);
    const citationIndex = parts.findIndex((part) => part === "[p1]");

    const quote = citationQuoteFromParts(parts, citationIndex);

    expect(quote).toContain("Researchers observed that survival");
    expect(quote).toContain("improved by 20% over the trial period.");
  });

  it("weights the citation context toward the preceding claim rather than the trailing text", () => {
    const before = `${"Earlier background. ".repeat(40)}Survival improved by 20% in the treatment arm`;
    const after = ` according to the trial.${"x".repeat(900)}`;
    const parts = [before, "[p1]", after];

    const context = citationContext(parts, 1);

    expect(context).toContain("Survival improved by 20% in the treatment arm");
    expect(context).toContain("according to the trial");
  });
});

describe("evidence verification badge", () => {
  const baseEvidence = {
    paper_id: "p1",
    kind: "pdf",
    reference_text: "Der zitierte Satz",
    pdf_excerpt: "The located excerpt.",
    matched_terms: [],
    found_in_pdf_text: true
  };

  it("flags sources without a local PDF", () => {
    const source: VerificationSource = { paper_id: "p1", title: "Study", pdf_available: false, evidence: [baseEvidence] };

    render(<EvidenceVerificationBadge source={source} evidence={baseEvidence} />);

    expect(screen.getByText(/Kein lokales PDF/)).toBeTruthy();
  });

  it("flags excerpts that were not verified in the PDF text", () => {
    const source: VerificationSource = { paper_id: "p1", title: "Study", pdf_available: true, evidence: [] };
    const evidence = { ...baseEvidence, found_in_pdf_text: false };

    render(<EvidenceVerificationBadge source={source} evidence={evidence} />);

    expect(screen.getByText(/nicht im PDF verifiziert/)).toBeTruthy();
  });

  it("labels whole-pdf fallback evidence as approximate", () => {
    const source: VerificationSource = { paper_id: "p1", title: "Study", pdf_available: true, evidence: [] };
    const evidence = { ...baseEvidence, metadata: { context_policy: "whole" } };

    render(<EvidenceVerificationBadge source={source} evidence={evidence} />);

    expect(screen.getByText(/Ungefähre Stelle/)).toBeTruthy();
  });

  it("labels approximate regions from claim anchoring and verification", () => {
    const source: VerificationSource = { paper_id: "p1", title: "Study", pdf_available: true, evidence: [] };
    const fromClaim = { ...baseEvidence, metadata: { context_policy: "approx_region" } };
    const fromVerification = { ...baseEvidence, metadata: { located: "approx_region" } };

    render(<EvidenceVerificationBadge source={source} evidence={fromClaim} />);
    expect(screen.getByText(/Ungefährer Bereich/)).toBeTruthy();
    cleanup();
    render(<EvidenceVerificationBadge source={source} evidence={fromVerification} />);
    expect(screen.getByText(/Ungefährer Bereich/)).toBeTruthy();
  });

  it("reuses the verification report embedded in the answer without a second verify call", async () => {
    const payload: Answer = {
      question: "q",
      answer: "Claim [p1].",
      sources: [{ paper_id: "p1", title: "Study" }],
      evidence: [],
      source_verification: {
        sources: [{ paper_id: "p1", title: "Study", pdf_available: true, evidence: [] }],
        cited_paper_ids: ["p1"],
        missing_source_ids: []
      }
    };

    const sources = await verificationSourcesFor(payload);

    expect(sources).toHaveLength(1);
    expect(sources[0].paper_id).toBe("p1");
  });

  it("renders nothing for verified excerpts and grey sources", () => {
    const verifiedSource: VerificationSource = { paper_id: "p1", title: "Study", pdf_available: true, evidence: [] };
    const greySource: VerificationSource = { paper_id: "grey::g1", title: "Web", pdf_available: false, evidence: [] };

    const verified = render(<EvidenceVerificationBadge source={verifiedSource} evidence={baseEvidence} />);
    expect(verified.container.innerHTML).toBe("");
    cleanup();
    const grey = render(<EvidenceVerificationBadge source={greySource} evidence={baseEvidence} />);
    expect(grey.container.innerHTML).toBe("");
  });
});
