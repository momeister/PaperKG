import { describe, expect, it } from "vitest";

import { classifyDroppedFile, classifyPastedText } from "./WorkspacePage";

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
