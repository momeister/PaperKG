import { expect, test } from "@playwright/test";

test("project, upload, assistant evidence, quality, and settings flow", async ({ page }) => {
  const projectName = `e2e-${Date.now()}`;
  let globalNote: {
    id: string;
    project_id: string;
    title: string;
    markdown: string;
    citations: unknown[];
    assets: unknown[];
    citation_count: number;
    asset_count: number;
  } | null = null;
  const defaultGlobalNote = {
    id: "global-note",
    project_id: "__all_papers__",
    title: "Neue Notiz",
    markdown: "# Neue Notiz\n\n",
    citations: [],
    assets: [],
    citation_count: 0,
    asset_count: 0
  };

  await page.route("**/query/answer", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        question: "What connects graph transformers and citations?",
        answer: "Graph Transformer evidence is grounded in the local KG [p1].",
        sources: [{ paper_id: "p1", title: "Graph Transformer for Science", year: 2024 }],
        evidence: [{ paper_id: "p1", kind: "concept", text: "Graph Transformer", score: 1, field: "concepts" }]
      })
    });
  });

  await page.route("**/sources/verify-answer", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        sources: [
          {
            paper_id: "p1",
            title: "Graph Transformer for Science",
            pdf_available: false,
            evidence: [
              {
                paper_id: "p1",
                kind: "concept",
                field: "concepts",
                reference_text: "Graph Transformer evidence",
                pdf_excerpt: "Graph Transformer evidence in the parsed PDF text.",
                matched_terms: ["graph", "transformer"],
                found_in_pdf_text: true
              }
            ]
          }
        ],
        cited_paper_ids: ["p1"],
        missing_source_ids: []
      })
    });
  });

  await page.route("**/projects/__all_papers__/notes", async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as { title?: string; markdown?: string };
      globalNote = { ...defaultGlobalNote, title: payload.title ?? defaultGlobalNote.title, markdown: payload.markdown ?? defaultGlobalNote.markdown };
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ note: globalNote })
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: globalNote ? [globalNote] : [], total: globalNote ? 1 : 0 })
    });
  });

  await page.route("**/notes/global-note", async (route) => {
    globalNote = globalNote ?? defaultGlobalNote;
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON() as { title?: string; markdown?: string };
      globalNote = { ...globalNote, title: payload.title ?? globalNote.title, markdown: payload.markdown ?? globalNote.markdown };
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ note: globalNote })
    });
  });

  await page.goto("/");
  await expect(page.getByText("ScienceKG")).toBeVisible();

  await page.getByPlaceholder("Neues Projekt").fill(projectName);
  await page.getByRole("button", { name: /Anlegen/ }).click();
  await expect(page.getByRole("button", { name: new RegExp(projectName) })).toBeVisible();
  await page.getByLabel("Projekt").selectOption("");
  await expect(page.getByLabel("Projekt")).toHaveValue("");

  await page.getByRole("link", { name: /Notizen/ }).click();
  await expect(page.getByRole("heading", { name: "Notizen" })).toBeVisible();
  await page.getByRole("button", { name: "Neu" }).click();
  await expect(page.getByPlaceholder("Titel")).toHaveValue("Neue Notiz");
  const editor = page.getByPlaceholder("Markdown schreiben");
  await editor.fill("Alpha");
  await editor.press("Control+A");
  await editor.press("Control+B");
  await expect(editor).toHaveValue("**Alpha**");
  await editor.fill("- erster Punkt");
  await editor.press("End");
  await editor.press("Enter");
  await expect(editor).toHaveValue("- erster Punkt\n- ");
  await page.getByRole("button", { name: "Preview" }).click();
  const previewBlock = page.locator(".editable-preview-block").first();
  await previewBlock.fill("Direkt in Preview");
  await page.getByRole("button", { name: "Edit" }).click();
  await expect(editor).toHaveValue("- Direkt in Preview");

  await page.getByRole("link", { name: /Import/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "tiny.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n")
  });
  await expect(page.getByText("success")).toBeVisible();

  await page.getByRole("link", { name: /Assistant/ }).click();
  await page.getByPlaceholder("Frage an den lokalen KG").fill("What connects graph transformers and citations?");
  await page.getByRole("button", { name: "Senden" }).click();
  await expect(page.getByText("Graph Transformer evidence is grounded")).toBeVisible();
  await expect(page.getByText("Graph Transformer evidence in the parsed PDF text.")).toBeVisible();

  await page.getByRole("link", { name: /Quality/ }).click();
  await expect(page.getByRole("heading", { name: "Quality" })).toBeVisible();

  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByText("API Base URL")).toBeVisible();
});
