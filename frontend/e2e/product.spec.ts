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
  const clinicalCitation = {
    id: "cite-clinical",
    note_id: "global-note",
    paper_id: "arxiv:2604.08226",
    title: "Grounding Clinical AI Competency in Human Cognition Through the Clinical World Model and Skill-Mix Framework",
    kind: "concept",
    reference_text: "Clinical AI competency evidence",
    pdf_excerpt: "Clinical AI evidence in PDF text for citation navigation.",
    evidence_index: 12
  };
  const defaultGlobalNote = {
    id: "global-note",
    project_id: "__all_papers__",
    title: "Neue Notiz",
    markdown: "# Neue Notiz\n\n",
    citations: [clinicalCitation],
    assets: [],
    citation_count: 1,
    asset_count: 0
  };
  const projects: Array<{ id: string; name: string; paper_ids: string[]; paper_count: number; year_min: number | null; year_max: number | null }> = [];
  const papers = [
    {
      id: "p1",
      title: "Graph Transformer for Science",
      year: 2024,
      has_full_text: true,
      project_ids: []
    }
  ];
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
  let followUpCount = 0;

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
            pdf_available: true,
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

  await page.route(/\/papers\/upload(?:\?.*)?$/, async (route) => {
    const title = new URL(route.request().url()).searchParams.get("title") || "tiny";
    const paper = {
      id: `uploaded-${papers.length + 1}`,
      title,
      year: null,
      has_full_text: true,
      project_ids: []
    };
    papers.push(paper);
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ paper, pdf_path: `library/${paper.id}.pdf` })
    });
  });

  await page.route(/\/papers(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: papers, total: papers.length, limit: 200, offset: 0 })
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

  await page.route("**/notes/global-note/append", async (route) => {
    globalNote = globalNote ?? defaultGlobalNote;
    const payload = route.request().postDataJSON() as { markdown?: string; citations?: unknown[] };
    const citations = Array.isArray(payload.citations) ? payload.citations : [];
    globalNote = {
      ...globalNote,
      markdown: `${globalNote.markdown}\n\n${payload.markdown ?? ""}`.trim(),
      citations: [...globalNote.citations, ...citations],
      citation_count: globalNote.citations.length + citations.length
    };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ note: globalNote })
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

  await page.route("**/paper/pdf**", async (route) => {
    await route.fulfill({
      contentType: "application/pdf",
      body: tinyPdf("Graph Transformer evidence in the parsed PDF text.")
    });
  });

  await page.route("**/notes/global-note/ai-threads**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const threadPatchMatch = /\/ai-threads\/([^/]+)$/.exec(pathname);
    if (request.method() === "PATCH" && threadPatchMatch) {
      const payload = request.postDataJSON() as { ui_state?: Record<string, unknown> };
      const thread = aiThreads.find((item) => item.id === threadPatchMatch[1]);
      if (thread) {
        thread.ui_state = { ...(thread.ui_state ?? {}), ...(payload.ui_state ?? {}) };
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ thread })
      });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith("/ai-threads")) {
      const payload = request.postDataJSON() as {
        selected_text?: string;
        instruction?: string;
        anchor_start?: number | null;
        anchor_end?: number | null;
        anchor_quote?: string | null;
      };
      const thread = {
        id: `thread-${aiThreads.length + 1}`,
        note_id: "global-note",
        selected_text: payload.selected_text ?? "",
        instruction: payload.instruction ?? "",
        response_text: "Das bedeutet in einfachen Worten: Es ist eine kurze Erklaerung.",
        replacement_text: "Das bedeutet in einfachen Worten: Es ist eine kurze Erklaerung.",
        answer_payload: {},
        anchor_start: payload.anchor_start ?? null,
        anchor_end: payload.anchor_end ?? null,
        anchor_quote: payload.anchor_quote ?? payload.selected_text ?? "",
        ui_state: {},
        messages: [
          {
            id: `msg-user-${aiThreads.length + 1}`,
            thread_id: `thread-${aiThreads.length + 1}`,
            note_id: "global-note",
            role: "user",
            content: payload.instruction ?? ""
          },
          {
            id: `msg-assistant-${aiThreads.length + 1}`,
            thread_id: `thread-${aiThreads.length + 1}`,
            note_id: "global-note",
            role: "assistant",
            content: "Das bedeutet in einfachen Worten: Es ist eine kurze Erklaerung."
          }
        ]
      };
      aiThreads = [thread, ...aiThreads];
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ replacement_text: thread.replacement_text, response_text: thread.response_text, thread })
      });
      return;
    }
    const messageMatch = /\/ai-threads\/([^/]+)\/messages$/.exec(pathname);
    if (request.method() === "POST" && messageMatch) {
      const payload = request.postDataJSON() as { message?: string };
      const thread = aiThreads.find((item) => item.id === messageMatch[1]);
      if (thread) {
        followUpCount += 1;
        thread.messages = [
          ...thread.messages,
          {
            id: `msg-followup-user-${followUpCount}`,
            thread_id: thread.id,
            note_id: "global-note",
            role: "user",
            content: payload.message ?? ""
          },
          {
            id: `msg-followup-assistant-${followUpCount}`,
            thread_id: thread.id,
            note_id: "global-note",
            role: "assistant",
            content: "Noch einfacher: Es ist eine Merkhilfe."
          }
        ];
        thread.response_text = "Noch einfacher: Es ist eine Merkhilfe.";
        thread.replacement_text = "Noch einfacher: Es ist eine Merkhilfe.";
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ replacement_text: thread?.replacement_text ?? "", response_text: thread?.response_text ?? "", thread })
      });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith(`/ai-threads/${aiThread.id}/delete`)) {
      aiThreads = aiThreads.filter((thread) => thread.id !== aiThread.id);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ deleted: true })
      });
      return;
    }
    const deleteMatch = /\/ai-threads\/([^/]+)\/delete$/.exec(pathname);
    if (request.method() === "POST" && deleteMatch) {
      aiThreads = aiThreads.filter((thread) => thread.id !== deleteMatch[1]);
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
  const formattedMarkdown =
    '**Bold** ==Mark== <span style="color:#2563eb">Blue</span> Quelle: [Z13 - Grounding Clinical AI Competency](sciencekg://citation/cite-clinical)';
  await editor.fill(formattedMarkdown);
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.locator(".markdown-preview strong", { hasText: "Bold" })).toBeVisible();
  await expect(page.locator(".markdown-preview mark", { hasText: "Mark" })).toBeVisible();
  await expect(page.locator(".citation-link", { hasText: "Z13 - Grounding Clinical AI Competency" })).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  await expect(editor).toHaveValue(formattedMarkdown);
  await page.getByRole("button", { name: "Split" }).click();
  await expect(editor).toBeVisible();
  await expect(page.locator(".markdown-preview", { hasText: "Z13 - Grounding Clinical AI Competency" })).toBeVisible();
  await editor.fill(`${formattedMarkdown}\n\nLive Split`);
  await expect(page.locator(".markdown-preview", { hasText: "Live Split" })).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  await editor.evaluate((node: HTMLTextAreaElement) => {
    const index = node.value.indexOf("Z13");
    node.focus();
    node.setSelectionRange(index + 2, index + 2);
    node.dispatchEvent(new Event("select", { bubbles: true }));
  });
  await expect(page.getByRole("button", { name: /Z13 öffnen/ })).toBeVisible();
  await page.getByRole("button", { name: /Quelle Z13 öffnen/ }).click();
  await expect(page.locator(".note-citation-row--active", { hasText: "Z13" })).toBeVisible();
  await expect(page.locator(".textarea-highlight-range--citation-active")).toBeVisible();
  await expect(page.locator(".excerpt-panel", { hasText: "Clinical AI evidence in PDF text for citation navigation." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Fett" })).toBeVisible();
  await page.getByRole("button", { name: "Keine Quelle aktiv" }).click();
  await expect(page.locator(".note-citation-row--active")).toHaveCount(0);
  await expect(page.locator(".textarea-highlight-range--citation-active")).toHaveCount(0);
  await page.locator(".note-context-toolbar").getByRole("button", { name: /KI/ }).click();
  await expect(page.locator(".note-history-panel")).toBeVisible();
  await page.locator(".note-context-toolbar").getByRole("button", { name: /Quellen/ }).click();
  const contextPanel = page.locator(".note-context-panel");
  const resizeHandle = page.getByRole("separator", { name: "Notizen und Quellen/PDF Breite anpassen" });
  const contextBefore = await contextPanel.boundingBox();
  const handleBox = await resizeHandle.boundingBox();
  if (!contextBefore || !handleBox) {
    throw new Error("Notes context resize handle was not measurable");
  }
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox.x + 90, handleBox.y + handleBox.height / 2, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => (await contextPanel.boundingBox())?.width ?? 0).toBeLessThan(contextBefore.width - 30);
  await editor.fill(`${Array.from({ length: 42 }, (_, index) => `Zeile ${index + 1}`).join("\n")}\nEndwort`);
  for (let index = 0; index < "Endwort".length; index += 1) {
    await editor.press("Shift+ArrowLeft");
  }
  await expect(page.getByPlaceholder("KI-Frage zu dieser Auswahl")).toBeVisible();
  const editorClientHeight = await editor.evaluate((node) => node.clientHeight);
  await expect
    .poll(() => editor.evaluate((node) => parseFloat(window.getComputedStyle(node).paddingBottom)))
    .toBeGreaterThan(120);
  await expect
    .poll(() => editor.evaluate((node) => parseFloat(window.getComputedStyle(node).paddingBottom)))
    .toBeLessThan(editorClientHeight);
  await page.getByPlaceholder("KI-Frage zu dieser Auswahl").fill("Was bedeutet das in einfachen Worten?");
  await page.getByRole("button", { name: "Fragen" }).click();
  const marker = page.getByRole("button", { name: /KI-Notiz N\d+ öffnen/ }).last();
  await expect(marker).toBeVisible();
  const markerLabel = (await marker.textContent())?.trim() || "N1";
  const inlineNote = page.getByRole("dialog", { name: new RegExp(`KI-Notiz ${markerLabel}`) });
  await expect(inlineNote).not.toBeVisible();
  await expect(page.locator(".textarea-highlight-range--thread-anchor-active")).toHaveCount(0);
  await expect(page.getByLabel("KI-Antwort")).toHaveValue(/Das bedeutet in einfachen Worten/);
  await expect(page.getByRole("button", { name: "Ersetzen" })).toBeVisible();
  await marker.click();
  await expect(inlineNote).toBeVisible();
  await expect(inlineNote).toContainText("Was bedeutet das in einfachen Worten?");
  await expect(inlineNote).toContainText("Das bedeutet in einfachen Worten");
  await expect(page.locator(".textarea-highlight-range--thread-anchor-active")).toBeVisible();
  await inlineNote.getByRole("button", { name: "KI-Notiz einklappen" }).click();
  await expect(inlineNote).not.toBeVisible();
  await expect(page.locator(".textarea-highlight-range--thread-anchor-active")).toHaveCount(0);
  await marker.click();
  await expect(inlineNote).toBeVisible();
  await expect(page.locator(".textarea-highlight-range--thread-anchor-active")).toBeVisible();
  await marker.click();
  await expect(inlineNote).not.toBeVisible();
  await expect(page.locator(".textarea-highlight-range--thread-anchor-active")).toHaveCount(0);
  await marker.click();
  await expect(inlineNote).toBeVisible();
  await inlineNote.getByPlaceholder("Weiterfragen").fill("Noch einfacher?");
  await inlineNote.getByRole("button", { name: "Fragen" }).click();
  await expect(inlineNote).toContainText("Noch einfacher: Es ist eine Merkhilfe.");
  await inlineNote.getByRole("button", { name: "KI-Notiz einklappen" }).click();
  await expect(inlineNote).not.toBeVisible();
  await page.locator(".note-context-toolbar").getByRole("button", { name: /KI/ }).click();
  await expect(page.locator(".ai-thread-anchor-badge", { hasText: markerLabel })).toBeVisible();
  const anchoredThread = page.locator(".ai-thread-card", { has: page.locator(".ai-thread-anchor-badge", { hasText: markerLabel }) });
  await anchoredThread.getByRole("button", { name: "Öffnen" }).click();
  await anchoredThread.getByPlaceholder("Folgefrage zu dieser Auswahl").fill("Aus der Übersicht?");
  await anchoredThread.getByRole("button", { name: "Fragen" }).click();
  await expect(inlineNote).not.toBeVisible();
  await expect(page.locator(".textarea-highlight-range--thread-anchor-active")).toHaveCount(0);
  await expect(anchoredThread).toContainText("Noch einfacher: Es ist eine Merkhilfe.");
  await anchoredThread.getByRole("button", { name: "KI-Antwort ausblenden" }).first().click();
  await expect(anchoredThread).not.toContainText("Das bedeutet in einfachen Worten: Es ist eine kurze Erklaerung.");
  await expect(anchoredThread).toContainText("Noch einfacher: Es ist eine Merkhilfe.");
  await page.locator(".note-context-toolbar").getByRole("button", { name: /Quellen/ }).click();
  await marker.click();
  await inlineNote.getByRole("button", { name: "KI-Notiz loeschen" }).click();
  await expect(page.getByRole("dialog", { name: new RegExp(`KI-Notiz ${markerLabel}`) })).not.toBeVisible();
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
  await expect(historyPanel.locator(".ai-thread-anchor-badge")).toHaveCount(0);
  await expect(historyPanel.locator(".ai-thread-preview p", { hasText: "Direkt" })).toHaveCount(1);
  const insertButton = page.getByRole("button", { name: "Einfügen", exact: true }).first();
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

  await expect(page.getByRole("link", { name: /Arbeitsplatz/ })).toBeVisible();
  await page.getByRole("link", { name: /Arbeitsplatz/ }).click();
  const workspace = page.locator(".workspace-page");
  const workspaceNav = page.locator(".workspace-nav-pane");
  const workspaceAssistant = page.locator(".workspace-assistant-pane");
  const workspaceNotes = page.locator(".workspace-notes-pane");
  const workspacePdf = page.locator(".workspace-page > .pdf-pane");
  const workspaceEditor = workspaceNotes.getByPlaceholder("Markdown schreiben");
  await expect(workspace).toBeVisible();
  await expect(page.getByRole("link", { name: /Assistant/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Notizen/ })).toBeVisible();
  await expect(workspaceNav.getByRole("button", { name: /^Notizen/ })).toBeVisible();
  await expect(workspaceNav.locator(".note-list-row").first()).toBeVisible();
  await expect(workspaceEditor).toBeVisible();
  await workspaceEditor.fill("Workspace Alpha\n\nQuelle: [Z13 - Grounding Clinical AI Competency](sciencekg://citation/cite-clinical)");
  await workspaceNotes.getByRole("button", { name: "Preview" }).click();
  await expect(workspaceNotes.locator(".markdown-preview", { hasText: "Workspace Alpha" })).toBeVisible();
  await workspaceNotes.getByRole("button", { name: "Split" }).click();
  await expect(workspaceEditor).toBeVisible();
  await expect(workspaceNotes.locator(".markdown-preview", { hasText: "Workspace Alpha" })).toBeVisible();
  await workspaceNotes.locator(".citation-link", { hasText: "Z13" }).click();
  await expect(workspacePdf).toContainText("Grounding Clinical AI Competency");
  await expect.poll(() => globalNote?.markdown ?? "").toContain("Workspace Alpha");

  await workspaceAssistant.getByPlaceholder("Frage an den lokalen KG").fill("What connects graph transformers and citations?");
  await workspaceAssistant.getByRole("button", { name: "Senden" }).click();
  await expect(workspaceAssistant.locator(".answer-text")).toContainText("Graph Transformer evidence is grounded");
  const workspaceAssistantCitation = workspaceAssistant.locator(".citation-link", { hasText: "Z1" });
  await workspaceAssistantCitation.hover();
  await expect(workspaceAssistant.locator(".citation-hover-card")).toContainText("Graph Transformer evidence in the parsed PDF text.");
  await workspaceAssistantCitation.click();
  await expect(workspacePdf).toContainText("Grounding Clinical AI Competency");
  await workspaceAssistant.getByRole("button", { name: "PDF-Nachweis öffnen" }).click();
  await expect(workspacePdf).toContainText("Graph Transformer for Science");
  await expect(workspacePdf.locator(".excerpt-panel")).toContainText("Graph Transformer evidence in the parsed PDF text.");
  await workspaceAssistant.getByRole("button", { name: "Antwort in Notiz" }).hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await workspaceAssistant.getByRole("button", { name: "Antwort in Notiz" }).click();
  await expect(workspaceAssistant.getByText("In Notiz gespeichert")).toBeVisible();
  await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence is grounded/);
  await workspaceAssistant.getByRole("button", { name: "Zitat Z1" }).hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await workspaceAssistant.getByRole("button", { name: "Zitat Z1" }).click();
  await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence/);

  await workspaceNav.getByRole("button", { name: /PDFs/ }).click();
  await expect(workspaceNav.locator(".workspace-active-source")).toContainText("Z1");
  await expect(workspaceNav.locator(".workspace-active-source")).toContainText("Graph Transformer for Science");
  await workspaceNav.locator(".note-citation-row", { hasText: "Z13" }).click();
  await expect(workspacePdf).toContainText("Grounding Clinical AI Competency");
  await workspaceNav.locator(".note-list-row", { hasText: "Graph Transformer for Science" }).click();
  await expect(workspacePdf).toContainText("Graph Transformer for Science");
  await workspacePdf.getByPlaceholder("In PDF suchen").fill("Graph");
  await workspacePdf.getByRole("button", { name: "Vergroessern" }).click();

  await workspaceEditor.fill("Workspace Auswahltext");
  await workspaceEditor.click();
  await workspaceEditor.press("End");
  for (let index = 0; index < "Auswahltext".length; index += 1) {
    await workspaceEditor.press("Shift+ArrowLeft");
  }
  await expect(workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl")).toBeVisible();
  await workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl").fill("Arbeitsplatz erklaeren");
  await workspaceNotes.getByRole("button", { name: "Fragen" }).click();
  await expect(workspaceNotes.getByLabel("KI-Antwort")).toHaveValue(/Das bedeutet in einfachen Worten/);
  await workspaceNav.getByRole("button", { name: /KI-Notizen/ }).click();
  const workspaceThread = workspaceNav.locator(".ai-thread-card", { hasText: "Arbeitsplatz erklaeren" }).first();
  await expect(workspaceThread).toBeVisible();
  await workspaceThread.getByRole("button", { name: "Oeffnen" }).click();
  await workspaceThread.getByPlaceholder("Folgefrage zu dieser Auswahl").fill("Noch kuerzer?");
  await workspaceThread.getByRole("button", { name: "Fragen" }).click();
  await expect(workspaceThread).toContainText("Noch einfacher: Es ist eine Merkhilfe.");
  const workspaceThreadInsert = workspaceThread.getByRole("button", { name: "Einfuegen" }).first();
  await workspaceThreadInsert.hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await workspaceThreadInsert.click();
  await expect(workspaceEditor).toHaveValue(/Noch einfacher: Es ist eine Merkhilfe/);
  const workspaceThreadMessage = workspaceThread.locator(".ai-thread-message--assistant", { hasText: "Noch einfacher: Es ist eine Merkhilfe." }).last();
  await expect(workspaceThreadMessage.getByRole("button", { name: "Einfuegen" })).toBeVisible();
  await workspaceThreadMessage.getByRole("button", { name: "KI-Antwort ausblenden" }).click();
  await expect(workspaceThreadMessage).not.toBeVisible();
  await workspaceThread.getByRole("button", { name: "KI-Verlauf loeschen" }).click();
  await expect(workspaceNav.getByText("Noch keine KI-Fragen")).toBeVisible();

  await workspaceNav.getByRole("button", { name: /KI-Sessions/ }).click();
  await expect(workspaceNav.locator(".workspace-session-item", { hasText: "What connects graph transformers and citations?" })).toBeVisible();
  const assistantBoxBefore = await workspaceAssistant.boundingBox();
  const assistantHandleBox = await page.getByRole("separator", { name: "Assistant Breite anpassen" }).boundingBox();
  if (!assistantBoxBefore || !assistantHandleBox) {
    throw new Error("Workspace assistant resize handle was not measurable");
  }
  await page.mouse.move(assistantHandleBox.x + assistantHandleBox.width / 2, assistantHandleBox.y + assistantHandleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(assistantHandleBox.x + 60, assistantHandleBox.y + assistantHandleBox.height / 2, { steps: 3 });
  await page.mouse.up();
  await expect.poll(async () => (await workspaceAssistant.boundingBox())?.width ?? 0).toBeGreaterThan(assistantBoxBefore.width + 20);
  await workspacePdf.getByRole("button", { name: "PDF einklappen" }).click();
  await expect(page.locator(".workspace-collapsed-pane", { hasText: "PDF" })).toBeVisible();
  await page.locator(".workspace-collapsed-pane", { hasText: "PDF" }).getByRole("button").click();
  await expect(workspacePdf).toBeVisible();
  await workspaceNav.getByRole("button", { name: "Arbeitsplatz einklappen" }).click();
  await expect(page.locator(".workspace-collapsed-pane", { hasText: "Navigator" })).toBeVisible();
  await page.locator(".workspace-collapsed-pane", { hasText: "Navigator" }).getByRole("button").click();
  await expect(workspaceNav).toBeVisible();

  await page.getByRole("link", { name: /Assistant/ }).click();
  await page.getByPlaceholder("Frage an den lokalen KG").fill("What connects graph transformers and citations?");
  await page.getByRole("button", { name: "Senden" }).click();
  await expect(page.locator(".answer-panel", { hasText: "Graph Transformer evidence is grounded" })).toBeVisible();
  await expect(page.locator(".excerpt-panel", { hasText: "Graph Transformer evidence in the parsed PDF text." }).first()).toBeVisible();

  await page.getByRole("link", { name: /Quality/ }).click();
  await expect(page.getByRole("heading", { name: "Quality" })).toBeVisible();

  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByText("API Base URL")).toBeVisible();
});

function tinyPdf(text: string) {
  const escaped = text.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  const stream = `BT /F1 12 Tf 24 120 Td (${escaped}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 180] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`
  ];
  let body = "%PDF-1.4\n";
  const offsets = objects.map((object, index) => {
    const offset = body.length;
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
    return offset;
  });
  const xrefOffset = body.length;
  body += `xref\n0 ${objects.length + 1}\n`;
  body += "0000000000 65535 f \n";
  body += offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  body += `trailer\n<< /Root 1 0 R /Size ${objects.length + 1} >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(body, "utf-8");
}
