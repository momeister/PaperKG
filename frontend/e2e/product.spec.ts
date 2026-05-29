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
  const projects: Array<{ id: string; name: string; paper_ids: string[]; paper_count: number; year_min: number | null; year_max: number | null }> = [];
  const aiThread = {
    id: "thread-1",
    note_id: "global-note",
    selected_text: "Direkt",
    instruction: "Fasse kurz zusammen",
    response_text: "Ausfuehrliche Antwort, die im kompakten Verlauf nicht sofort sichtbar sein soll.",
    replacement_text: "Kompakte Antwort",
    answer_payload: {},
    anchor_start: 2,
    anchor_end: 8,
    anchor_quote: "Direkt",
    ui_state: {},
    messages: [
      {
        id: "msg-user",
        thread_id: "thread-1",
        note_id: "global-note",
        role: "user",
        content: "Fasse kurz zusammen"
      },
      {
        id: "msg-assistant",
        thread_id: "thread-1",
        note_id: "global-note",
        role: "assistant",
        content: "Kompakte Antwort"
      }
    ]
  };
  let aiThreads = [aiThread];

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

  await page.route(/\/projects(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as { name: string; paper_ids?: string[] };
      const project = {
        id: payload.name,
        name: payload.name,
        paper_ids: payload.paper_ids ?? [],
        paper_count: payload.paper_ids?.length ?? 0,
        year_min: null,
        year_max: null
      };
      projects.push(project);
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ project }) });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ projects }) });
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

  await page.route("**/notes/global-note/ai-threads**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "POST" && pathname.endsWith(`/ai-threads/${aiThread.id}/delete`)) {
      aiThreads = aiThreads.filter((thread) => thread.id !== aiThread.id);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ deleted: true })
      });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/ai-threads/delete-all")) {
      const deleted = aiThreads.length;
      aiThreads = [];
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ deleted })
      });
      return;
    }
    if (request.method() === "GET" && pathname.endsWith("/ai-threads")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: aiThreads, total: aiThreads.length })
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: aiThreads, total: aiThreads.length })
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
  await editor.press("Control+Z");
  await expect(editor).toHaveValue("Alpha");
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
  const spellButton = page.getByRole("button", { name: "Rechtschreibkontrolle ausschalten" });
  await expect(spellButton).toHaveAttribute("aria-pressed", "true");
  await spellButton.click();
  await expect(page.getByRole("button", { name: "Rechtschreibkontrolle einschalten" })).toHaveAttribute("aria-pressed", "false");
  await editor.click();
  await editor.press("Control+A");
  await page.getByPlaceholder("KI-Frage zu dieser Auswahl").click();
  await expect(page.locator(".textarea-highlight-range--selection").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "KI-Verlauf", exact: true }).click();
  await expect(page.getByText("Fasse kurz zusammen")).toBeVisible();
  await expect(page.getByText("Ausfuehrliche Antwort, die im kompakten Verlauf nicht sofort sichtbar sein soll.")).not.toBeVisible();
  const historyPanel = page.locator(".note-history-panel");
  await expect(historyPanel.locator(".ai-thread-header span")).toHaveCount(0);
  await expect(historyPanel.locator(".ai-thread-preview p", { hasText: "Direkt" })).toHaveCount(1);
  const insertButton = page.getByRole("button", { name: "Einfuegen", exact: true }).first();
  await insertButton.hover();
  await expect(page.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await expect(page.locator(".textarea-ghost-insertion--ai")).toBeVisible();
  await insertButton.click();
  await expect(editor).toHaveValue(/Kompakte Antwort/);
  await page.getByRole("button", { name: "KI-Verlauf loeschen" }).click();
  await expect(page.getByText("Noch keine KI-Fragen")).toBeVisible();
  await expect(page.getByText("Fasse kurz zusammen")).not.toBeVisible();

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
