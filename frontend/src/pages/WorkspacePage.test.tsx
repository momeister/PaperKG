import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Answer } from "../types";
import { saveAssistantSession, turnBlocks } from "./AssistantPage";
import {
  answerSuggestsWebSearch,
  classifyDroppedFile,
  classifyPastedText,
  extractInlineWebToken,
  matchWorkspaceCommands,
  restoredActiveTurnFor
} from "./WorkspacePage";

function file(name: string, type: string) {
  return new File(["x"], name, { type });
}

describe("dropped/pasted source classification", () => {
  it("classifies PDFs by MIME type and by extension when the type is missing", () => {
    expect(classifyDroppedFile(file("paper.pdf", "application/pdf"))).toBe("pdf");
    expect(classifyDroppedFile(file("paper.pdf", ""))).toBe("pdf");
    expect(classifyDroppedFile(file("PAPER.PDF", ""))).toBe("pdf");
  });

  it("classifies images by MIME type and by extension", () => {
    expect(classifyDroppedFile(file("figure.png", "image/png"))).toBe("image");
    expect(classifyDroppedFile(file("figure.jpg", ""))).toBe("image");
    expect(classifyDroppedFile(file("scan.WEBP", ""))).toBe("image");
  });

  it("treats anything else as unsupported", () => {
    expect(classifyDroppedFile(file("notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))).toBe(
      "unsupported"
    );
    expect(classifyDroppedFile(file("data.xlsx", ""))).toBe("unsupported");
    expect(classifyDroppedFile(file("noextension", ""))).toBe("unsupported");
  });

  it("recognizes a single bare http(s) link as a URL", () => {
    expect(classifyPastedText("https://arxiv.org/abs/1234.5678")).toBe("url");
    expect(classifyPastedText("  http://example.com/paper  ")).toBe("url");
  });

  it("does not classify ordinary text or multi-line/mixed content as a URL", () => {
    expect(classifyPastedText("What does this paper say about widgets?")).toBeNull();
    expect(classifyPastedText("See https://example.com for details")).toBeNull();
    expect(classifyPastedText("https://example.com/a\nhttps://example.com/b")).toBeNull();
    expect(classifyPastedText("")).toBeNull();
  });
});

describe("inline /web token", () => {
  it("detects /web anywhere in the prompt and strips it", () => {
    expect(extractInlineWebToken("/web Was sagt die Studie?")).toEqual({ text: "Was sagt die Studie?", web: true });
    expect(extractInlineWebToken("Was sagt die Studie? /web")).toEqual({ text: "Was sagt die Studie?", web: true });
    expect(extractInlineWebToken("Was sagt /web die Studie?")).toEqual({ text: "Was sagt die Studie?", web: true });
  });

  it("leaves prompts without the token untouched", () => {
    expect(extractInlineWebToken("Wie funktioniert ein Webserver?")).toEqual({ text: "Wie funktioniert ein Webserver?", web: false });
    expect(extractInlineWebToken("/website example.com")).toEqual({ text: "/website example.com", web: false });
  });
});

describe("command palette matching", () => {
  it("matches commands by prefix on name and alias", () => {
    expect(matchWorkspaceCommands("su").map((command) => command.name)).toContain("suche");
    expect(matchWorkspaceCommands("auswahl").map((command) => command.name)).toContain("selected");
    expect(matchWorkspaceCommands("").length).toBeGreaterThan(5);
  });

  it("returns nothing for unknown commands", () => {
    expect(matchWorkspaceCommands("zzz")).toHaveLength(0);
  });
});

describe("web search offer heuristic", () => {
  const baseAnswer: Answer = { question: "q", answer: "", sources: [], evidence: [] };

  it("offers web search for explicit no-answer responses", () => {
    expect(answerSuggestsWebSearch({ ...baseAnswer, no_answer: true })).toBe(true);
    expect(answerSuggestsWebSearch({ ...baseAnswer, answer: "The local KG does not contain enough evidence." })).toBe(true);
    expect(answerSuggestsWebSearch({ ...baseAnswer, answer: "Es gibt nicht genug Evidenz im lokalen Bestand." })).toBe(true);
    expect(
      answerSuggestsWebSearch({ ...baseAnswer, answer: "x", context_diagnostics: { fallback_reason: "no_traceable_citations" } })
    ).toBe(true);
  });

  it("stays quiet for substantive answers", () => {
    expect(answerSuggestsWebSearch({ ...baseAnswer, answer: "Die Studie zeigt einen Effekt von 12% [arxiv:1]." })).toBe(false);
    expect(answerSuggestsWebSearch(null)).toBe(false);
  });
});

describe("restored deep-analysis session (white-screen regression)", () => {
  const projectId = "proj-deep";

  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, String(value)),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
      key: () => null,
      get length() {
        return store.size;
      }
    });
  });

  // A research_tree turn carries no answer (answer: null). turnBlocks fabricates a block
  // straight from that null answer — rendering it through the normal answer-block path
  // dereferenced block.answer.answer and white-screened the whole workspace. The fix keys
  // off the turn type, so detection of a persisted deep-analysis session must be reliable.
  it("recognizes a persisted research_tree turn so it never renders as an answer block", () => {
    const treeTurn = {
      id: "turn-1",
      question: "Tiefenfrage?",
      answer: null,
      verification: [],
      createdAt: new Date().toISOString(),
      type: "research_tree" as const,
      researchNodes: [{ id: "n1", parent_id: null, question: "Tiefenfrage?", depth: 0, status: "done", answer: null }]
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    saveAssistantSession(projectId, { history: [treeTurn as any], activeTurnId: "turn-1" });

    const restored = restoredActiveTurnFor(projectId);
    expect(restored?.type).toBe("research_tree");
    // The phantom block from such a turn has a null answer — exactly the value that crashed.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(turnBlocks(treeTurn as any)[0].answer).toBeNull();
  });

  it("returns a normal turn unchanged", () => {
    const normalTurn = {
      id: "turn-2",
      question: "Normale Frage?",
      answer: { question: "Normale Frage?", answer: "Antwort", sources: [], evidence: [] },
      verification: [],
      createdAt: new Date().toISOString()
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    saveAssistantSession(projectId, { history: [normalTurn as any], activeTurnId: "turn-2" });

    expect(restoredActiveTurnFor(projectId)?.type).toBeUndefined();
  });
});
