import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnswerText, citationIds, citationMetasFor, formatAnswerForNote } from "./AssistantPage";
import type { Answer, VerificationSource } from "../types";

afterEach(() => cleanup());

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

    expect(screen.getByRole("button", { name: /Z1 p1/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Z1 p2/ })).toBeVisible();
  });

  it("highlights the cited answer span while hovering a citation chip", () => {
    const { container } = render(
      <AnswerText
        answer="The Earth is round [p1]."
        getCitationMeta={(citation, context) => citationMetasFor(verification, citation, context)}
        onCitationClick={vi.fn()}
      />
    );

    fireEvent.pointerEnter(screen.getByRole("button", { name: /Z1 p1/ }));

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

    fireEvent.click(screen.getByRole("button", { name: /Z1 p1/ }));
    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("The Earth is round");

    fireEvent.click(screen.getByRole("button", { name: /Z1 p2/ }));
    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("Earth orbits the Sun");
    expect(container.querySelector(".citation-context-highlight")?.textContent).not.toContain("The Earth is round");

    fireEvent.click(screen.getByRole("button", { name: /Z1 p2/ }));
    expect(container.querySelector(".citation-context-highlight")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Z1 p1/ }));
    expect(container.querySelector(".citation-context-highlight")?.textContent).toContain("The Earth is round");
    fireEvent.click(container.querySelector(".answer-text-content") as HTMLElement);
    expect(container.querySelector(".citation-context-highlight")).toBeNull();
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
});
