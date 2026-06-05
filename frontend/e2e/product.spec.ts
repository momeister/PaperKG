import { expect, test, type Locator } from "@playwright/test";

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
    evidence_index: 1
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
    },
    {
      id: "p2",
      title: "   ",
      pdf_filename: "AI-based Clinical Decision Support.pdf",
      year: 2025,
      has_full_text: true,
      project_ids: []
    }
  ];
  let extractionHistory = [
    {
      id: 1,
      paper_id: "p1",
      llm_provider: "fake",
      llm_model: "fake-model",
      extraction_status: "success",
      extraction_timestamp: "2026-06-04T10:00:00",
      concepts: [{ label: "Graph Transformer", confidence: 0.95 }],
      methods: [{ label: "Attention", confidence: 0.9 }],
      claims: [{ statement: "Graph Transformer evidence is grounded." }]
    }
  ];
  let vocabulary = [{ canonical_label: "Graph Transformer", aliases: ["GT"], domain: "ML", openalx_id: null, confidence: 1 }];
  const workspaceLongAnswer =
    "Das bedeutet in einfachen Worten: Es ist eine kurze Erklaerung. Diese Antwort bleibt im Notiz-Assistenten voll sichtbar, damit laengere KI-Notizen als Chat gelesen werden koennen. Sie enthaelt mehrere Saetze, eine zweite Erklaerungsebene und einen eindeutigen Schluss: vollstaendig sichtbarer Langtext endet hier.";
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
  let lastAnswerPayload: { paper_ids?: string[] } | null = null;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.route("**/query/answer", async (route) => {
    lastAnswerPayload = route.request().postDataJSON() as { paper_ids?: string[] };
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

  await page.route(/\/extraction\/library(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: papers.map((paper, index) => ({
          paper_id: paper.id,
          title: paper.title.trim() || paper.pdf_filename?.replace(/\.pdf$/i, "") || paper.id,
          filename: paper.pdf_filename ?? `${paper.id}.pdf`,
          pdf_path: `library/${paper.id}.pdf`,
          size_bytes: 2048 + index,
          modified_timestamp: "2026-06-04T10:00:00",
          latest_extraction_status: index === 0 ? "success" : null,
          known_paper: true
        })),
        total: papers.length
      })
    });
  });

  await page.route("**/extraction/parse", async (route) => {
    const payload = route.request().postDataJSON() as { paper_id?: string; pdf_path?: string };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        paper_id: payload.paper_id ?? "p1",
        pdf_path: payload.pdf_path ?? "library/p1.pdf",
        text: "Graph Transformer methods produce grounded science claims.",
        page_count: 2,
        parser: "marker",
        metadata: { extraction_method: "fake" },
        excerpt: "Graph Transformer methods produce grounded science claims."
      })
    });
  });

  await page.route("**/extraction/extract", async (route) => {
    const payload = route.request().postDataJSON() as { paper_id?: string };
    const item = {
      id: extractionHistory.length + 1,
      paper_id: payload.paper_id ?? "p1",
      llm_provider: "fake",
      llm_model: "fake-model",
      extraction_status: "success",
      extraction_timestamp: "2026-06-04T10:01:00",
      concepts: [{ label: "Graph Transformer", confidence: 0.95, review_status: "approved" }],
      methods: [{ label: "Attention", confidence: 0.9, review_status: "approved" }],
      claims: [{ statement: "Graph Transformer methods produce grounded science claims." }]
    };
    extractionHistory = [item, ...extractionHistory];
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        result_id: item.id,
        paper_id: item.paper_id,
        status: "success",
        duration_seconds: 1.2,
        result: {
          paper_id: item.paper_id,
          paper_type: "research",
          concepts: item.concepts,
          methods: item.methods,
          concept_candidates: [],
          method_candidates: [],
          relations: [],
          claims: item.claims,
          cross_domain_hints: [],
          terminology_conflicts: [],
          temporal_coverage: { paper_year: 2026 },
          mathematical_content: { has_formulas: false },
          language_detected: "en",
          quality_warnings: [],
          metadata_status: "valid",
          blocking_errors: [],
          candidate_count: 0,
          extraction_diagnostics: { mode: "fake" },
          raw_response: "{}"
        }
      })
    });
  });

  await page.route(/\/extraction\/history(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: extractionHistory, total: extractionHistory.length })
    });
  });

  await page.route(/\/extraction\/vocabulary(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as { canonical_label: string; aliases?: string[]; domain?: string; openalx_id?: string };
      vocabulary = [
        ...vocabulary,
        {
          canonical_label: payload.canonical_label,
          aliases: payload.aliases ?? [],
          domain: payload.domain ?? "",
          openalx_id: payload.openalx_id ?? null,
          confidence: 1
        }
      ];
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: vocabulary, total: vocabulary.length })
    });
  });

  await page.route("**/extraction/batch", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        job: {
          job_id: "extract-job-1",
          status: "completed",
          papers_total: 1,
          papers_processed: 1,
          papers_failed: 0
        },
        items: [{ paper_id: "p1", status: "completed" }]
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
    const paperId = new URL(route.request().url()).searchParams.get("paper_id") ?? "";
    const pdfText = paperId === "arxiv:2604.08226" ? "Clinical AI evidence in PDF text for citation navigation." : "Graph Transformer evidence in the parsed PDF text.";
    await route.fulfill({
      contentType: "application/pdf",
      body: tinyPdf(pdfText)
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
      const responseText = payload.instruction === "Arbeitsplatz erklaeren" ? workspaceLongAnswer : "Das bedeutet in einfachen Worten: Es ist eine kurze Erklaerung.";
      const thread = {
        id: `thread-${aiThreads.length + 1}`,
        note_id: "global-note",
        selected_text: payload.selected_text ?? "",
        instruction: payload.instruction ?? "",
        response_text: responseText,
        replacement_text: responseText,
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
            content: responseText
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

  await page.getByRole("link", { name: /Arbeitsplatz/ }).click();
  await expect(page.locator(".workspace-notes-pane")).toBeVisible();
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
    '**Bold** ==Mark== <span style="color:#2563eb">Blue</span> Quelle: [Z2 - Grounding Clinical AI Competency](sciencekg://citation/cite-clinical)';
  await editor.fill(formattedMarkdown);
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.locator(".markdown-preview strong", { hasText: "Bold" })).toBeVisible();
  await expect(page.locator(".markdown-preview mark", { hasText: "Mark" })).toBeVisible();
  await expect(page.locator(".citation-link", { hasText: "Z2 - Grounding Clinical AI Competency" })).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  await expect(editor).toHaveValue(formattedMarkdown);
  await page.getByRole("button", { name: "Split" }).click();
  await expect(editor).toBeVisible();
  await expect(page.locator(".markdown-preview", { hasText: "Z2 - Grounding Clinical AI Competency" })).toBeVisible();
  await editor.fill(`${formattedMarkdown}\n\nLive Split`);
  await expect(page.locator(".markdown-preview", { hasText: "Live Split" })).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  await expect(page.getByRole("button", { name: "Fett" })).toBeVisible();
  const spellButton = page.getByRole("button", { name: "Rechtschreibkontrolle ausschalten" });
  await expect(spellButton).toHaveAttribute("aria-pressed", "true");
  await spellButton.click();
  await expect(page.getByRole("button", { name: "Rechtschreibkontrolle einschalten" })).toHaveAttribute("aria-pressed", "false");

  await page.getByRole("link", { name: /Import/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "tiny.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n")
  });
  await expect(page.getByText("success")).toBeVisible();

  await page.getByRole("link", { name: /Extraktion/ }).click();
  await expect(page.getByRole("heading", { name: "Extraktion" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Ausführen/ }).first()).toHaveClass(/active/);
  await page.getByLabel("PDF").selectOption("library/p1.pdf");
  await page.getByRole("button", { name: "Parsen" }).click();
  await expect(page.getByPlaceholder("Paper-Text")).toHaveValue(/Graph Transformer methods/);
  await page.locator(".extraction-input-panel").getByRole("button", { name: "Ausführen" }).click();
  await expect(page.locator(".extraction-result-panel")).toContainText("Graph Transformer");
  await expect(page.locator(".extraction-result-panel")).toContainText("Attention");
  await page.getByRole("button", { name: /PDFs/ }).click();
  await expect(page.locator(".extraction-library-table")).toContainText("Graph Transformer for Science");
  await page.getByRole("button", { name: /Ausführen/ }).first().click();
  await page.locator(".extraction-batch-panel .extraction-library-table input[type='checkbox']").first().check();
  await page.getByRole("button", { name: "Auswahl ausführen" }).click();
  await expect(page.locator(".status-strip", { hasText: "1/1" })).toBeVisible();
  await page.getByRole("button", { name: /Vokabular/ }).click();
  await page.getByLabel("Canonical Label").fill("Citation Network");
  await page.getByLabel("Aliases").fill("citation graph");
  await page.getByRole("button", { name: "Hinzufuegen" }).click();
  await expect(page.locator(".extraction-vocabulary-table")).toContainText("Citation Network");
  await page.getByRole("button", { name: /Historie/ }).click();
  await expect(page.locator(".extraction-history-table")).toContainText("p1");

  await expect(page.getByRole("link", { name: /Arbeitsplatz/ })).toBeVisible();
  await page.getByRole("link", { name: /Arbeitsplatz/ }).click();
  const workspace = page.locator(".workspace-page");
  const workspaceNav = page.locator(".workspace-nav-pane");
  const workspaceAssistant = page.locator(".workspace-assistant-pane");
  const workspaceNotes = page.locator(".workspace-notes-pane");
  const workspacePdf = page.locator(".workspace-page > .pdf-pane");
  const workspaceEditor = workspaceNotes.getByPlaceholder("Markdown schreiben");
  await expect(workspace).toBeVisible();
  await expect(page.getByRole("link", { name: /Assistant/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Notizen/ })).toHaveCount(0);
  await expect(workspaceNav.getByRole("button", { name: /^Notizen/ })).toBeVisible();
  await expect(workspaceNav.locator(".note-list-row").first()).toBeVisible();
  await expect(workspaceEditor).toBeVisible();
  await workspaceEditor.fill(
    "Workspace Alpha\n\nErste Quelle: [Z2 - Grounding Clinical AI Competency](sciencekg://citation/cite-clinical)\n\nZweite Quelle: [Z2 - Grounding Clinical AI Competency](sciencekg://citation/cite-clinical)"
  );
  await workspaceNotes.getByRole("button", { name: "Preview" }).click();
  await expect(workspaceNotes.locator(".markdown-preview", { hasText: "Workspace Alpha" })).toBeVisible();
  await workspaceNotes.getByRole("button", { name: "Split" }).click();
  await expect(workspaceEditor).toBeVisible();
  await expect(workspaceNotes.locator(".markdown-preview", { hasText: "Workspace Alpha" })).toBeVisible();
  const workspaceClickedCitation = workspaceNotes.locator(".citation-link", { hasText: "Z2" }).nth(1);
  await workspaceClickedCitation.click();
  const workspaceCitationHighlight = workspaceNotes.locator(".textarea-highlight-range--citation-active");
  await expect(workspaceCitationHighlight).toHaveCount(1);
  await expect(workspacePdf).toContainText("Grounding Clinical AI Competency");
  await expect(workspacePdf.locator(".excerpt-panel")).toContainText("Clinical AI evidence in PDF text for citation navigation.");
  await expect(workspacePdf.locator(".pdf-highlight--active").first()).toBeVisible();
  const workspaceCitationColor = await evidenceColor(workspaceClickedCitation);
  expect(await evidenceColor(workspaceCitationHighlight.first())).toBe(workspaceCitationColor);
  expect(await evidenceColor(workspacePdf.locator(".excerpt-panel"))).toBe(workspaceCitationColor);
  expect(await evidenceColor(workspacePdf.locator(".pdf-highlight--active").first())).toBe(workspaceCitationColor);
  await expect.poll(() => globalNote?.markdown ?? "").toContain("Workspace Alpha");

  await workspaceNav.getByRole("button", { name: /PDFs/ }).click();
  expect(pageErrors).toEqual([]);
  await expect(workspaceNav.locator(".workspace-paper-row", { hasText: "AI based Clinical Decision Support" })).toBeVisible();
  const graphPdfRow = workspaceNav.locator(".workspace-paper-row", { hasText: "Graph Transformer for Science" });
  await expect(graphPdfRow.locator(".workspace-paper-select input")).toBeVisible();
  await expect(graphPdfRow.locator(".workspace-paper-main")).toContainText("p1 - 2024");
  await graphPdfRow.press("Enter");
  await expect(workspacePdf).toContainText("Graph Transformer for Science");
  await graphPdfRow.locator(".workspace-paper-select").click();
  await workspaceAssistant.getByRole("button", { name: "Auswahl" }).click();
  await workspaceAssistant.getByPlaceholder("Frage an den lokalen KG").fill("What connects graph transformers and citations?");
  await workspaceAssistant.getByRole("button", { name: "Senden" }).click();
  await expect(workspaceAssistant.locator(".answer-text")).toContainText("Graph Transformer evidence is grounded");
  expect(lastAnswerPayload?.paper_ids).toEqual(["p1"]);
  const workspaceAssistantCitation = workspaceAssistant.locator(".citation-link", { hasText: "Z1" });
  await workspaceAssistantCitation.hover();
  await expect(workspaceAssistant.locator(".citation-hover-card")).toContainText("Graph Transformer evidence in the parsed PDF text.");
  await expect(workspaceAssistant.locator(".citation-context-highlight", { hasText: "Graph Transformer evidence is grounded" })).toBeVisible();
  const hoverCardInsert = workspaceAssistant.locator(".citation-hover-card").getByRole("button", { name: "In Notiz" });
  await expect(hoverCardInsert).toBeVisible();
  await hoverCardInsert.hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await expect
    .poll(() => workspaceNotes.locator(".textarea-highlight-layer").evaluate((node) => node.scrollWidth <= node.clientWidth + 1))
    .toBe(true);
  await workspaceAssistantCitation.click();
  await expect(workspaceAssistantCitation).toHaveClass(/citation-link--active/);
  await expect(workspaceAssistant.locator(".citation-context-highlight", { hasText: "Graph Transformer evidence is grounded" })).toBeVisible();
  await expect(workspaceAssistant.locator(".evidence-dock:not(.evidence-dock--collapsed)")).toBeVisible();
  await expect(workspaceAssistant.locator(".evidence-row.list-row--active", { hasText: "Graph Transformer evidence" })).toBeVisible();
  await expect(workspacePdf).toContainText("Graph Transformer for Science");
  await expect(workspacePdf.locator(".excerpt-panel")).toHaveCount(0);
  await workspaceAssistant.getByRole("button", { name: "Antwort in Notiz" }).hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await workspaceAssistant.getByRole("button", { name: "Antwort in Notiz" }).click();
  await expect(workspaceAssistant.getByText("In Notiz gespeichert")).toBeVisible();
  await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence is grounded/);
  await workspaceAssistant.getByRole("button", { name: "Zitat Z1" }).hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await workspaceAssistant.getByRole("button", { name: "Zitat Z1" }).click();
  await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence/);
  const longScrolledNote = Array.from({ length: 180 }, (_, index) => `Scroll-Zeile ${index + 1}: Workspace bleibt an dieser Stelle.`).join("\n");
  await workspaceEditor.fill(longScrolledNote);
  const beforePdfInsertViewport = await workspaceEditor.evaluate((node: HTMLTextAreaElement) => {
    node.focus();
    const cursor = node.value.length;
    node.setSelectionRange(cursor, cursor);
    node.scrollTop = node.scrollHeight;
    node.dispatchEvent(new Event("select", { bubbles: true }));
    node.dispatchEvent(new Event("scroll", { bubbles: true }));
    return { scrollTop: node.scrollTop, cursor };
  });
  expect(beforePdfInsertViewport.scrollTop).toBeGreaterThan(40);
  await workspaceAssistant.getByRole("button", { name: "PDF Z1" }).click();
  await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence in the parsed PDF text/);
  const afterPdfInsertViewport = await workspaceEditor.evaluate((node: HTMLTextAreaElement) => ({
    scrollTop: node.scrollTop,
    selectionStart: node.selectionStart,
    selectionEnd: node.selectionEnd
  }));
  expect(afterPdfInsertViewport.scrollTop).toBeGreaterThanOrEqual(beforePdfInsertViewport.scrollTop - 24);
  expect(afterPdfInsertViewport.selectionStart).toBeGreaterThan(beforePdfInsertViewport.cursor);
  expect(afterPdfInsertViewport.selectionStart).toBe(afterPdfInsertViewport.selectionEnd);

  await workspaceNav.getByRole("button", { name: /PDFs/ }).click();
  await expect(workspaceNav.locator(".workspace-active-source")).toContainText("Z2");
  await expect(workspaceNav.locator(".workspace-active-source")).toContainText("Grounding Clinical AI Competency");
  await workspaceNav.locator(".note-citation-row", { hasText: "Z2" }).click();
  const activeWorkspaceCitationRow = workspaceNav.locator(".note-citation-row--active", { hasText: "Z2" });
  await expect(activeWorkspaceCitationRow).toBeVisible();
  await expect(workspacePdf).toContainText("Grounding Clinical AI Competency");
  await expect(workspacePdf.locator(".excerpt-panel")).toContainText("Clinical AI evidence in PDF text for citation navigation.");
  expect(await evidenceColor(activeWorkspaceCitationRow)).toBe(workspaceCitationColor);
  expect(await evidenceColor(workspacePdf.locator(".excerpt-panel"))).toBe(workspaceCitationColor);
  await workspaceNav.locator(".workspace-paper-row", { hasText: "Graph Transformer for Science" }).click();
  await expect(workspacePdf).toContainText("Graph Transformer for Science");
  await workspacePdf.getByPlaceholder("In PDF suchen").fill("Graph");
  await workspacePdf.getByRole("button", { name: "Vergroessern" }).click();

  const workspaceLongSelection =
    "Auswahltext mit einem langen markierten Bereich, der im Notiz-Assistenten nicht gekuerzt werden darf. Er enthaelt mehrere Saetze, damit die Vorschau nicht nur den Anfang zeigt, und endet mit Volltext-Endmarke-7.";
  await workspaceEditor.fill(`Workspace ${workspaceLongSelection}`);
  await workspaceEditor.evaluate(
    (node: HTMLTextAreaElement, selectedText) => {
      const start = node.value.indexOf(selectedText);
      if (start < 0) {
        throw new Error("Workspace selection text was not found");
      }
      node.focus();
      node.setSelectionRange(start, start + selectedText.length);
      node.dispatchEvent(new Event("select", { bubbles: true }));
      document.dispatchEvent(new Event("selectionchange", { bubbles: true }));
    },
    workspaceLongSelection
  );
  await expect(workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl")).toBeVisible();
  await workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl").fill("Arbeitsplatz erklaeren");
  await workspaceNotes.getByRole("button", { name: "Fragen" }).click();
  await expect(workspaceNotes.getByLabel("KI-Antwort")).toHaveValue(/Das bedeutet in einfachen Worten/);
  await workspaceNotes.getByRole("button", { name: "Schliessen" }).click();
  await workspaceEditor.click();
  await workspaceEditor.press("Home");
  for (let index = 0; index < "Workspace".length; index += 1) {
    await workspaceEditor.press("Shift+ArrowRight");
  }
  await expect(workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl")).toBeVisible();
  await workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl").fill("Zweite Notiz");
  await workspaceNotes.getByRole("button", { name: "Fragen" }).click();
  await expect(workspaceNotes.getByLabel("KI-Antwort")).toHaveValue(/Das bedeutet in einfachen Worten/);
  await expect(workspaceNav.getByRole("button", { name: /KI-Notizen/ })).toHaveCount(0);
  await workspaceAssistant.getByRole("button", { name: "Notiz-Assistent" }).click();
  await expect(workspaceAssistant.getByRole("button", { name: "Notiz-Assistent" })).toHaveClass(/active/);
  await expect(workspaceAssistant.getByText("Grounded KG")).toHaveCount(0);
  const workspaceThread = workspaceAssistant.locator(".ai-thread-card", { hasText: "Arbeitsplatz erklaeren" }).first();
  await expect(workspaceThread).toBeVisible();
  await expect(workspaceThread.locator(".ai-thread-preview")).not.toContainText("Deine Frage");
  await expect(workspaceThread.locator(".ai-thread-preview")).not.toContainText("KI-Antwort");
  await expect(workspaceAssistant.locator(".workspace-notes-assistant-panel")).toBeVisible();
  await expect(workspaceAssistant.locator(".ai-thread-card", { hasText: "Arbeitsplatz erklaeren" })).toBeVisible();
  await expect(workspaceEditor).toBeVisible();
  await expect.poll(() => workspaceAssistant.locator(".workspace-notes-assistant-list").evaluate((node) => window.getComputedStyle(node).overflowY)).toBe("auto");
  await expect(workspaceAssistant.locator(".ai-thread-card").first()).toContainText("Zweite Notiz");
  await workspaceThread.getByRole("button", { name: "KI-Notiz anpinnen" }).click();
  await expect(workspaceThread).toHaveClass(/ai-thread-card--pinned/);
  await expect(workspaceAssistant.locator(".ai-thread-card").first()).toContainText("Arbeitsplatz erklaeren");
  await workspaceThread.getByRole("button", { name: "Öffnen" }).click();
  await expect(workspaceNotes.locator(".thread-anchor-popover")).toHaveCount(0);
  await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active").count()).toBeGreaterThan(0);
  await expect(workspaceThread).toContainText("Markierter Bereich");
  await expect(workspaceThread.locator(".ai-thread-info-block--selection")).toContainText("Volltext-Endmarke-7");
  await expect(workspaceThread).toContainText("vollstaendig sichtbarer Langtext endet hier.");
  await workspaceThread.getByRole("button", { name: "KI-Notiz gross anzeigen" }).click();
  const focusedWorkspaceThread = workspaceAssistant.locator(".workspace-notes-assistant-card--focused", { hasText: "Arbeitsplatz erklaeren" });
  await expect(focusedWorkspaceThread).toBeVisible();
  await expect(workspaceAssistant.locator(".workspace-notes-assistant-panel--focused")).toBeVisible();
  await expect.poll(() => focusedWorkspaceThread.locator(".ai-thread-messages").evaluate((node) => window.getComputedStyle(node).overflowY)).toBe("auto");
  await expect.poll(() => workspaceAssistant.locator(".workspace-notes-assistant-list").evaluate((node) => window.getComputedStyle(node).overflowY)).toBe("hidden");
  const focusedFollowUp = focusedWorkspaceThread.locator(".ai-follow-up-row");
  await expect(focusedFollowUp).toBeVisible();
  await focusedWorkspaceThread.locator(".ai-thread-messages").evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await expect(focusedFollowUp).toBeVisible();
  await focusedWorkspaceThread.getByPlaceholder("Folgefrage zu dieser Auswahl").fill("Noch kuerzer?");
  await focusedWorkspaceThread.getByRole("button", { name: "Fragen" }).click();
  await expect(focusedWorkspaceThread).toContainText("Noch einfacher: Es ist eine Merkhilfe.");
  const workspaceThreadInsert = focusedWorkspaceThread.getByRole("button", { name: "Einfügen" }).first();
  await workspaceThreadInsert.hover();
  await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  await workspaceThreadInsert.click();
  await expect(workspaceEditor).toHaveValue(/Noch einfacher: Es ist eine Merkhilfe/);
  const workspaceMarker = workspaceNotes.getByRole("button", { name: /KI-Notiz N\d+ öffnen/ }).last();
  await expect(workspaceMarker).toBeVisible();
  await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active").count()).toBeGreaterThan(0);
  const insertedText = await workspaceEditor.inputValue();
  const manualInsertIndex = insertedText.indexOf("Merkhilfe") + "Merk".length;
  await workspaceEditor.evaluate(
    (node, index) => {
      node.focus();
      node.setSelectionRange(index, index);
    },
    manualInsertIndex
  );
  await page.keyboard.type(" manuell");
  await expect(workspaceMarker).toBeVisible();
  await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active").count()).toBeGreaterThan(0);
  await expect(workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active", { hasText: "manuell" })).toHaveCount(0);
  await workspaceMarker.hover();
  await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor").count()).toBeGreaterThan(0);
  await expect(page.locator(".textarea-thread-anchor-tooltip")).toHaveCount(0);
  await expect(page.locator(".textarea-thread-anchor-tooltip--portal")).toHaveCount(0);
  await expect(workspaceMarker).not.toHaveAttribute("title", /.+/);
  const markerBox = await workspaceMarker.boundingBox();
  const editorWrapBox = await workspaceNotes.locator(".markdown-editor-wrap").boundingBox();
  if (!markerBox || !editorWrapBox) {
    throw new Error("Workspace marker was not measurable");
  }
  expect(markerBox.x).toBeGreaterThanOrEqual(editorWrapBox.x - 1);
  expect(markerBox.x + markerBox.width).toBeLessThanOrEqual(editorWrapBox.x + editorWrapBox.width + 1);
  await workspaceMarker.click();
  const workspaceInlineNote = page.getByRole("dialog", { name: /KI-Notiz N\d+/ }).last();
  await expect(workspaceInlineNote).toBeVisible();
  const inlineNoteBox = await workspaceInlineNote.boundingBox();
  const viewport = page.viewportSize();
  if (!inlineNoteBox || !viewport) {
    throw new Error("Workspace inline KI note was not measurable");
  }
  expect(inlineNoteBox.x).toBeGreaterThanOrEqual(0);
  expect(inlineNoteBox.x + inlineNoteBox.width).toBeLessThanOrEqual(viewport.width + 1);
  await workspaceInlineNote.getByRole("button", { name: "KI-Notiz einklappen" }).click();
  await workspaceAssistant.getByRole("button", { name: "PDF-Assistent" }).click();
  await expect(workspaceAssistant.getByRole("button", { name: "PDF-Assistent" })).toHaveClass(/active/);
  await workspaceAssistant.getByRole("button", { name: "Notiz-Assistent" }).click();
  await expect(workspaceAssistant.locator(".workspace-notes-assistant-panel")).toContainText("Arbeitsplatz erklaeren");
  await expect(workspaceEditor).toBeVisible();
  await expect(workspaceNotes).toBeVisible();
  const workspaceThreadMessage = workspaceAssistant.locator(".ai-thread-message--assistant", { hasText: "Noch einfacher: Es ist eine Merkhilfe." }).last();
  await expect(workspaceThreadMessage.getByRole("button", { name: "Einfügen" })).toBeVisible();
  await workspaceThreadMessage.getByRole("button", { name: "KI-Antwort ausblenden" }).click();
  await expect(workspaceThreadMessage).not.toBeVisible();
  await workspaceAssistant.getByRole("button", { name: "Liste" }).first().click();
  await workspaceThread.getByRole("button", { name: "KI-Verlauf loeschen" }).click();
  await workspaceAssistant.getByRole("button", { name: "Alle" }).click();
  await expect(workspaceAssistant.getByText("Noch keine KI-Fragen")).toBeVisible();

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

  await page.goto("/assistant");
  await expect(page).toHaveURL(/\/workspace$/);

  await page.getByRole("link", { name: /Quality/ }).click();
  await expect(page.getByRole("heading", { name: "Quality" })).toBeVisible();

  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByText("API Base URL")).toBeVisible();
});

async function evidenceColor(locator: Locator) {
  return locator.evaluate((node) => window.getComputedStyle(node).getPropertyValue("--evidence-color").trim());
}

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
