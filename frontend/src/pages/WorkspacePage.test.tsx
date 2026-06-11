import { describe, expect, it } from "vitest";

import type { Answer } from "../types";
import {
  answerSuggestsWebSearch,
  classifyDroppedFile,
  classifyPastedText,
  extractInlineWebToken,
  matchWorkspaceCommands
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
