import { describe, expect, it } from "vitest";
import { findGlossaryMatches } from "./matching";
import type { GlossaryEntry } from "../types";
const entries = (...terms: string[]) => terms.map((term, index) => ({ id: String(index), term, explanation: "Meaning" } as GlossaryEntry));
const words = (text: string, terms: string[]) => findGlossaryMatches(text, entries(...terms)).map(m => text.slice(m.start, m.end));

describe("global glossary matching", () => {
  it("matches whole terms, case-insensitively, including Unicode and whitespace in phrases", () => {
    expect(words("AI AIs xAI AI_test ai; ÜBER übergang. Neural\nNetwork", ["AI", "über", "neural network"])).toEqual(["AI", "ai", "ÜBER", "Neural\nNetwork"]);
  });
  it("prefers the longest overlap even when it starts later", () => {
    expect(words("deep neural network", ["deep neural", "neural network", "network"])).toEqual(["neural network"]);
    expect(words("neural network network", ["network", "neural network"])).toEqual(["neural network", "network"]);
  });
  it("does not infer synonyms or inflections and escapes regex characters", () => {
    expect(words("C++ C++x a.b axb networks", ["C++", "a.b", "network"])).toEqual(["C++", "a.b"]);
  });
  it("excludes fenced/inline code, links, citations, URLs and syntax", () => {
    const text = 'AI `AI`\n```python\nAI\n\nAI\n```\n~~~\nAI\n~~~\n[AI](sciencekg://citation/AI) [AI](https://x/AI) https://x/AI www.AI.org [AI][ref]\n[ref]: https://AI.org\n**AI** <span title="AI">AI</span> [arxiv:AI]';
    expect(words(text, ["AI", "**", "https", "span"])).toEqual(["AI", "AI", "AI"]);
  });
  it("does not span formatting or URLs", () => {
    expect(words("neural **network** neural https://x network", ["neural network"])).toEqual([]);
  });
  it("preserves source offsets with emoji and case-expanding Unicode", () => {
    const text = "😀 İ AI";
    expect(findGlossaryMatches(text, entries("AI"))[0]).toMatchObject({ start: 5, end: 7 });
  });
});

it("keeps code with nested backticks and long closing fences opaque", () => {
  expect(words('AI ``AI `AI` AI`` AI\n~~~lang\nAI\n\nAI\n~~~~\nAI\n<code>AI</code> [1]', ["AI", "1"])).toEqual(["AI", "AI", "AI"]);
});
