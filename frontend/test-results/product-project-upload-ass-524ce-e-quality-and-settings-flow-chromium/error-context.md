# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: product.spec.ts >> project, upload, assistant evidence, quality, and settings flow
- Location: e2e\product.spec.ts:3:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('.workspace-assistant-pane').locator('.ai-thread-card').filter({ hasText: 'Arbeitsplatz erklaeren' }).first().locator('.ai-thread-info-block--selection')
Expected substring: "Volltext-Endmarke-7"
Received string:    "Markierter BereichWorkspace Auswahltext mit einem langen markierten Bereich, der im Notiz-Assistenten nicht gekuerzt werden darf. Er enthaelt mehrere Saetze, damit die Vorschau nicht nur den"
Timeout: 5000ms

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('.workspace-assistant-pane').locator('.ai-thread-card').filter({ hasText: 'Arbeitsplatz erklaeren' }).first().locator('.ai-thread-info-block--selection')
    14 × locator resolved to <section class="ai-thread-info-block ai-thread-info-block--selection">…</section>
       - unexpected value "Markierter BereichWorkspace Auswahltext mit einem langen markierten Bereich, der im Notiz-Assistenten nicht gekuerzt werden darf. Er enthaelt mehrere Saetze, damit die Vorschau nicht nur den"

```

```yaml
- text: Markierter Bereich
- paragraph: Workspace Auswahltext mit einem langen markierten Bereich, der im Notiz-Assistenten nicht gekuerzt werden darf. Er enthaelt mehrere Saetze, damit die Vorschau nicht nur den
```

# Test source

```ts
  726 |   expect(await evidenceColor(workspaceCitationHighlight.first())).toBe(workspaceCitationColor);
  727 |   expect(await evidenceColor(workspacePdf.locator(".excerpt-panel"))).toBe(workspaceCitationColor);
  728 |   expect(await evidenceColor(workspacePdf.locator(".pdf-highlight--active").first())).toBe(workspaceCitationColor);
  729 |   await expect.poll(() => globalNote?.markdown ?? "").toContain("Workspace Alpha");
  730 | 
  731 |   await workspaceNav.getByRole("button", { name: /PDFs/ }).click();
  732 |   expect(pageErrors).toEqual([]);
  733 |   await expect(workspaceNav.locator(".workspace-paper-row", { hasText: "AI based Clinical Decision Support" })).toBeVisible();
  734 |   const graphPdfRow = workspaceNav.locator(".workspace-paper-row", { hasText: "Graph Transformer for Science" });
  735 |   await expect(graphPdfRow.locator(".workspace-paper-select input")).toBeVisible();
  736 |   await expect(graphPdfRow.locator(".workspace-paper-main")).toContainText("p1 - 2024");
  737 |   await graphPdfRow.press("Enter");
  738 |   await expect(workspacePdf).toContainText("Graph Transformer for Science");
  739 |   await graphPdfRow.locator(".workspace-paper-select").click();
  740 |   await workspaceAssistant.getByRole("button", { name: "Auswahl" }).click();
  741 |   await workspaceAssistant.getByPlaceholder("Frage an den lokalen KG").fill("What connects graph transformers and citations?");
  742 |   await workspaceAssistant.getByRole("button", { name: "Senden" }).click();
  743 |   await expect(workspaceAssistant.locator(".answer-text")).toContainText("Graph Transformer evidence is grounded");
  744 |   expect(lastAnswerPayload?.paper_ids).toEqual(["p1"]);
  745 |   const workspaceAssistantCitation = workspaceAssistant.locator(".citation-link", { hasText: "Z1" });
  746 |   await workspaceAssistantCitation.hover();
  747 |   await expect(workspaceAssistant.locator(".citation-hover-card")).toContainText("Graph Transformer evidence in the parsed PDF text.");
  748 |   const hoverCardInsert = workspaceAssistant.locator(".citation-hover-card").getByRole("button", { name: "In Notiz" });
  749 |   await expect(hoverCardInsert).toBeVisible();
  750 |   await hoverCardInsert.hover();
  751 |   await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  752 |   await expect
  753 |     .poll(() => workspaceNotes.locator(".textarea-highlight-layer").evaluate((node) => node.scrollWidth <= node.clientWidth + 1))
  754 |     .toBe(true);
  755 |   await workspaceAssistantCitation.click();
  756 |   await expect(workspaceAssistantCitation).toHaveClass(/citation-link--active/);
  757 |   await expect(workspaceAssistant.locator(".evidence-dock:not(.evidence-dock--collapsed)")).toBeVisible();
  758 |   await expect(workspaceAssistant.locator(".evidence-row.list-row--active", { hasText: "Graph Transformer evidence" })).toBeVisible();
  759 |   await expect(workspacePdf).toContainText("Graph Transformer for Science");
  760 |   await expect(workspacePdf.locator(".excerpt-panel")).toContainText("Graph Transformer evidence in the parsed PDF text.");
  761 |   await workspaceAssistant.getByRole("button", { name: "Antwort in Notiz" }).hover();
  762 |   await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  763 |   await workspaceAssistant.getByRole("button", { name: "Antwort in Notiz" }).click();
  764 |   await expect(workspaceAssistant.getByText("In Notiz gespeichert")).toBeVisible();
  765 |   await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence is grounded/);
  766 |   await workspaceAssistant.getByRole("button", { name: "Zitat Z1" }).hover();
  767 |   await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  768 |   await workspaceAssistant.getByRole("button", { name: "Zitat Z1" }).click();
  769 |   await expect(workspaceEditor).toHaveValue(/Graph Transformer evidence/);
  770 | 
  771 |   await workspaceNav.getByRole("button", { name: /PDFs/ }).click();
  772 |   await expect(workspaceNav.locator(".workspace-active-source")).toContainText("Z1");
  773 |   await expect(workspaceNav.locator(".workspace-active-source")).toContainText("Graph Transformer for Science");
  774 |   await workspaceNav.locator(".note-citation-row", { hasText: "Z2" }).click();
  775 |   const activeWorkspaceCitationRow = workspaceNav.locator(".note-citation-row--active", { hasText: "Z2" });
  776 |   await expect(activeWorkspaceCitationRow).toBeVisible();
  777 |   await expect(workspacePdf).toContainText("Grounding Clinical AI Competency");
  778 |   await expect(workspacePdf.locator(".excerpt-panel")).toContainText("Clinical AI evidence in PDF text for citation navigation.");
  779 |   expect(await evidenceColor(activeWorkspaceCitationRow)).toBe(workspaceCitationColor);
  780 |   expect(await evidenceColor(workspacePdf.locator(".excerpt-panel"))).toBe(workspaceCitationColor);
  781 |   await workspaceNav.locator(".workspace-paper-row", { hasText: "Graph Transformer for Science" }).click();
  782 |   await expect(workspacePdf).toContainText("Graph Transformer for Science");
  783 |   await workspacePdf.getByPlaceholder("In PDF suchen").fill("Graph");
  784 |   await workspacePdf.getByRole("button", { name: "Vergroessern" }).click();
  785 | 
  786 |   const workspaceLongSelection =
  787 |     "Auswahltext mit einem langen markierten Bereich, der im Notiz-Assistenten nicht gekuerzt werden darf. Er enthaelt mehrere Saetze, damit die Vorschau nicht nur den Anfang zeigt, und endet mit Volltext-Endmarke-7.";
  788 |   await workspaceEditor.fill(`Workspace ${workspaceLongSelection}`);
  789 |   await workspaceEditor.click();
  790 |   await workspaceEditor.press("End");
  791 |   for (let index = 0; index < workspaceLongSelection.length; index += 1) {
  792 |     await workspaceEditor.press("Shift+ArrowLeft");
  793 |   }
  794 |   await expect(workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl")).toBeVisible();
  795 |   await workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl").fill("Arbeitsplatz erklaeren");
  796 |   await workspaceNotes.getByRole("button", { name: "Fragen" }).click();
  797 |   await expect(workspaceNotes.getByLabel("KI-Antwort")).toHaveValue(/Das bedeutet in einfachen Worten/);
  798 |   await workspaceNotes.getByRole("button", { name: "Schliessen" }).click();
  799 |   await workspaceEditor.click();
  800 |   await workspaceEditor.press("Home");
  801 |   for (let index = 0; index < "Workspace".length; index += 1) {
  802 |     await workspaceEditor.press("Shift+ArrowRight");
  803 |   }
  804 |   await expect(workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl")).toBeVisible();
  805 |   await workspaceNotes.getByPlaceholder("KI-Frage zu dieser Auswahl").fill("Zweite Notiz");
  806 |   await workspaceNotes.getByRole("button", { name: "Fragen" }).click();
  807 |   await expect(workspaceNotes.getByLabel("KI-Antwort")).toHaveValue(/Das bedeutet in einfachen Worten/);
  808 |   await expect(workspaceNav.getByRole("button", { name: /KI-Notizen/ })).toHaveCount(0);
  809 |   await workspaceAssistant.getByRole("button", { name: "Notiz-Assistent" }).click();
  810 |   await expect(workspaceAssistant.getByRole("button", { name: "Notiz-Assistent" })).toHaveClass(/active/);
  811 |   await expect(workspaceAssistant.getByText("Grounded KG")).toHaveCount(0);
  812 |   const workspaceThread = workspaceAssistant.locator(".ai-thread-card", { hasText: "Arbeitsplatz erklaeren" }).first();
  813 |   await expect(workspaceThread).toBeVisible();
  814 |   await expect(workspaceAssistant.locator(".workspace-notes-assistant-panel")).toBeVisible();
  815 |   await expect(workspaceAssistant.locator(".ai-thread-card", { hasText: "Arbeitsplatz erklaeren" })).toBeVisible();
  816 |   await expect(workspaceEditor).toBeVisible();
  817 |   await expect.poll(() => workspaceAssistant.locator(".workspace-notes-assistant-list").evaluate((node) => window.getComputedStyle(node).overflowY)).toBe("auto");
  818 |   await expect(workspaceAssistant.locator(".ai-thread-card").first()).toContainText("Zweite Notiz");
  819 |   await workspaceThread.getByRole("button", { name: "KI-Notiz anpinnen" }).click();
  820 |   await expect(workspaceThread).toHaveClass(/ai-thread-card--pinned/);
  821 |   await expect(workspaceAssistant.locator(".ai-thread-card").first()).toContainText("Arbeitsplatz erklaeren");
  822 |   await workspaceThread.getByRole("button", { name: "Öffnen" }).click();
  823 |   await expect(workspaceNotes.locator(".thread-anchor-popover")).toHaveCount(0);
  824 |   await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active").count()).toBeGreaterThan(0);
  825 |   await expect(workspaceThread).toContainText("Markierter Bereich");
> 826 |   await expect(workspaceThread.locator(".ai-thread-info-block--selection")).toContainText("Volltext-Endmarke-7");
      |                                                                             ^ Error: expect(locator).toContainText(expected) failed
  827 |   await expect(workspaceThread).toContainText("Deine Frage");
  828 |   await expect(workspaceThread).toContainText("KI-Antwort");
  829 |   await expect(workspaceThread).toContainText("vollstaendig sichtbarer Langtext endet hier.");
  830 |   await workspaceThread.getByRole("button", { name: "KI-Notiz gross anzeigen" }).click();
  831 |   const focusedWorkspaceThread = workspaceAssistant.locator(".workspace-notes-assistant-card--focused", { hasText: "Arbeitsplatz erklaeren" });
  832 |   await expect(focusedWorkspaceThread).toBeVisible();
  833 |   await expect(workspaceAssistant.locator(".workspace-notes-assistant-panel--focused")).toBeVisible();
  834 |   await expect.poll(() => focusedWorkspaceThread.locator(".ai-thread-messages").evaluate((node) => window.getComputedStyle(node).overflowY)).toBe("visible");
  835 |   await expect.poll(() => workspaceAssistant.locator(".workspace-notes-assistant-list").evaluate((node) => window.getComputedStyle(node).overflowY)).toBe("auto");
  836 |   await focusedWorkspaceThread.getByPlaceholder("Folgefrage zu dieser Auswahl").fill("Noch kuerzer?");
  837 |   await focusedWorkspaceThread.getByRole("button", { name: "Fragen" }).click();
  838 |   await expect(focusedWorkspaceThread).toContainText("Noch einfacher: Es ist eine Merkhilfe.");
  839 |   const workspaceThreadInsert = focusedWorkspaceThread.getByRole("button", { name: "Einfügen" }).first();
  840 |   await workspaceThreadInsert.hover();
  841 |   await expect(workspaceNotes.locator(".markdown-editor-wrap")).toHaveAttribute("data-insert-preview", "true");
  842 |   await workspaceThreadInsert.click();
  843 |   await expect(workspaceEditor).toHaveValue(/Noch einfacher: Es ist eine Merkhilfe/);
  844 |   const workspaceMarker = workspaceNotes.getByRole("button", { name: /KI-Notiz N\d+ öffnen/ }).last();
  845 |   await expect(workspaceMarker).toBeVisible();
  846 |   await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active").count()).toBeGreaterThan(0);
  847 |   const insertedText = await workspaceEditor.inputValue();
  848 |   const manualInsertIndex = insertedText.indexOf("Merkhilfe") + "Merk".length;
  849 |   await workspaceEditor.evaluate(
  850 |     (node, index) => {
  851 |       node.focus();
  852 |       node.setSelectionRange(index, index);
  853 |     },
  854 |     manualInsertIndex
  855 |   );
  856 |   await page.keyboard.type(" manuell");
  857 |   await expect(workspaceMarker).toBeVisible();
  858 |   await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active").count()).toBeGreaterThan(0);
  859 |   await expect(workspaceNotes.locator(".textarea-highlight-range--thread-anchor-active", { hasText: "manuell" })).toHaveCount(0);
  860 |   await workspaceMarker.hover();
  861 |   await expect.poll(() => workspaceNotes.locator(".textarea-highlight-range--thread-anchor").count()).toBeGreaterThan(0);
  862 |   await expect(page.locator(".textarea-thread-anchor-tooltip")).toHaveCount(0);
  863 |   await expect(page.locator(".textarea-thread-anchor-tooltip--portal")).toHaveCount(0);
  864 |   await expect(workspaceMarker).not.toHaveAttribute("title", /.+/);
  865 |   const markerBox = await workspaceMarker.boundingBox();
  866 |   const editorWrapBox = await workspaceNotes.locator(".markdown-editor-wrap").boundingBox();
  867 |   if (!markerBox || !editorWrapBox) {
  868 |     throw new Error("Workspace marker was not measurable");
  869 |   }
  870 |   expect(markerBox.x).toBeGreaterThanOrEqual(editorWrapBox.x - 1);
  871 |   expect(markerBox.x + markerBox.width).toBeLessThanOrEqual(editorWrapBox.x + editorWrapBox.width + 1);
  872 |   await workspaceMarker.click();
  873 |   const workspaceInlineNote = page.getByRole("dialog", { name: /KI-Notiz N\d+/ }).last();
  874 |   await expect(workspaceInlineNote).toBeVisible();
  875 |   const inlineNoteBox = await workspaceInlineNote.boundingBox();
  876 |   const viewport = page.viewportSize();
  877 |   if (!inlineNoteBox || !viewport) {
  878 |     throw new Error("Workspace inline KI note was not measurable");
  879 |   }
  880 |   expect(inlineNoteBox.x).toBeGreaterThanOrEqual(0);
  881 |   expect(inlineNoteBox.x + inlineNoteBox.width).toBeLessThanOrEqual(viewport.width + 1);
  882 |   await workspaceInlineNote.getByRole("button", { name: "KI-Notiz einklappen" }).click();
  883 |   await workspaceAssistant.getByRole("button", { name: "PDF-Assistent" }).click();
  884 |   await expect(workspaceAssistant.getByRole("button", { name: "PDF-Assistent" })).toHaveClass(/active/);
  885 |   await workspaceAssistant.getByRole("button", { name: "Notiz-Assistent" }).click();
  886 |   await expect(workspaceAssistant.locator(".workspace-notes-assistant-panel")).toContainText("Arbeitsplatz erklaeren");
  887 |   await expect(workspaceEditor).toBeVisible();
  888 |   await expect(workspaceNotes).toBeVisible();
  889 |   const workspaceThreadMessage = workspaceAssistant.locator(".ai-thread-message--assistant", { hasText: "Noch einfacher: Es ist eine Merkhilfe." }).last();
  890 |   await expect(workspaceThreadMessage.getByRole("button", { name: "Einfügen" })).toBeVisible();
  891 |   await workspaceThreadMessage.getByRole("button", { name: "KI-Antwort ausblenden" }).click();
  892 |   await expect(workspaceThreadMessage).not.toBeVisible();
  893 |   await workspaceAssistant.getByRole("button", { name: "Liste" }).first().click();
  894 |   await workspaceThread.getByRole("button", { name: "KI-Verlauf loeschen" }).click();
  895 |   await workspaceAssistant.getByRole("button", { name: "Alle" }).click();
  896 |   await expect(workspaceAssistant.getByText("Noch keine KI-Fragen")).toBeVisible();
  897 | 
  898 |   await workspaceNav.getByRole("button", { name: /KI-Sessions/ }).click();
  899 |   await expect(workspaceNav.locator(".workspace-session-item", { hasText: "What connects graph transformers and citations?" })).toBeVisible();
  900 |   const assistantBoxBefore = await workspaceAssistant.boundingBox();
  901 |   const assistantHandleBox = await page.getByRole("separator", { name: "Assistant Breite anpassen" }).boundingBox();
  902 |   if (!assistantBoxBefore || !assistantHandleBox) {
  903 |     throw new Error("Workspace assistant resize handle was not measurable");
  904 |   }
  905 |   await page.mouse.move(assistantHandleBox.x + assistantHandleBox.width / 2, assistantHandleBox.y + assistantHandleBox.height / 2);
  906 |   await page.mouse.down();
  907 |   await page.mouse.move(assistantHandleBox.x + 60, assistantHandleBox.y + assistantHandleBox.height / 2, { steps: 3 });
  908 |   await page.mouse.up();
  909 |   await expect.poll(async () => (await workspaceAssistant.boundingBox())?.width ?? 0).toBeGreaterThan(assistantBoxBefore.width + 20);
  910 |   await workspacePdf.getByRole("button", { name: "PDF einklappen" }).click();
  911 |   await expect(page.locator(".workspace-collapsed-pane", { hasText: "PDF" })).toBeVisible();
  912 |   await page.locator(".workspace-collapsed-pane", { hasText: "PDF" }).getByRole("button").click();
  913 |   await expect(workspacePdf).toBeVisible();
  914 |   await workspaceNav.getByRole("button", { name: "Arbeitsplatz einklappen" }).click();
  915 |   await expect(page.locator(".workspace-collapsed-pane", { hasText: "Navigator" })).toBeVisible();
  916 |   await page.locator(".workspace-collapsed-pane", { hasText: "Navigator" }).getByRole("button").click();
  917 |   await expect(workspaceNav).toBeVisible();
  918 | 
  919 |   await page.getByRole("link", { name: /Assistant/ }).click();
  920 |   await page.getByPlaceholder("Frage an den lokalen KG").fill("What connects graph transformers and citations?");
  921 |   await page.getByRole("button", { name: "Senden" }).click();
  922 |   await expect(page.locator(".answer-panel", { hasText: "Graph Transformer evidence is grounded" })).toBeVisible();
  923 |   await expect(page.locator(".excerpt-panel", { hasText: "Graph Transformer evidence in the parsed PDF text." }).first()).toBeVisible();
  924 | 
  925 |   await page.getByRole("link", { name: /Quality/ }).click();
  926 |   await expect(page.getByRole("heading", { name: "Quality" })).toBeVisible();
```