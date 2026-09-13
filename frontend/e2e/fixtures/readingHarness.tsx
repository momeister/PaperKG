import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { PdfPane } from "../../src/components/PdfPane";
import { NotesSurface, type NotesSurfaceActions } from "../../src/pages/NotesPage";
import { AppStateContext, type AppState } from "../../src/state";
import { pdfCitationPayload } from "../../src/components/pdfCitation";
import { textareaCaretTop } from "../../src/components/useWritingAnchor";
import { api } from "../../src/api";
import type { PdfSelection, PdfAnchor } from "../../src/types";
import "@fontsource-variable/inter";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource/jetbrains-mono/400.css";
import "../../src/styles/tokens.css";
import "../../src/styles/themes.css";
import "../../src/styles.css";

const testWindow = window as any;
testWindow.caretTop = textareaCaretTop;
const note = () => JSON.parse(localStorage.getItem("reading.note") || "null");
api.listNotes = async () => ({ items: note() ? [note()] : [], total: note() ? 1 : 0 });
api.getNote = async () => ({ note: note() });
api.listNoteAiThreads = async () => ({ items: [], total: 0 });
const save = async (payload: any) => {
  if (testWindow.failSave) throw Error("Speicherfehler");
  const n = { id: "reading-note", project_id: "__all_papers__", title: "Notiz", markdown: "", assets: [], ...note(), ...payload, citations: [...(note()?.citations || []), ...(payload.citations || [])] };
  localStorage.setItem("reading.note", JSON.stringify(n));
  return { note: n };
};
api.createNote = async (_project, payload) => save(payload);
api.updateNote = async (_id, payload) => save(payload);
api.paperMeta = async () => ({ title: "Fixture", paper_id: "fixture" }) as any;
api.pdfAnnotations.list = async () => ({ annotations: [] });
api.pdfAnnotations.create = async (_id, payload) => {
  if (testWindow.failSave) throw Error("Speicherfehler");
  return { annotation: { ...payload, id: crypto.randomUUID() } } as any;
};
api.rewriteNote = async payload => { testWindow.translationPayload = payload; return { text: "Deutsche Übersetzung" } as any; };
const evidences = [{ paper_id: "fixture", kind: "quote", reference_text: "AI wide WWW iii AI", pdf_excerpt: "AI wide WWW iii AI", matched_terms: [], found_in_pdf_text: true }];
function Harness() {
  const actionsRef = React.useRef<NotesSurfaceActions | null>(null);
  const [selection, setSelection] = React.useState<PdfSelection | null>(null);
  const [anchors, setAnchors] = React.useState<PdfAnchor[] | null>(null);
  testWindow.createReadingNote = () => actionsRef.current!.createNote();
  testWindow.openStoredCitation = () => setAnchors(note().citations[0].pdf_anchors);
  return <div style={{ display: "grid", gridTemplateColumns: "55% 45%", gridTemplateRows: "minmax(0, 1fr)", height: "100vh" }}>
    <PdfPane headerInPaneToolbar url="/reading-fixture.pdf" metaPaperId="fixture" title="Fixture" evidences={evidences} selection={selection} onSelectionChange={setSelection} anchors={anchors}
      onInsertSelection={async (sel, text, language) => { const p = pdfCitationPayload(sel, text, "Fixture", language); await actionsRef.current!.insertMarkdownAtCursor(p.markdown, p.citations); }} />
    <NotesSurface variant="workspace" actionsRef={actionsRef} onCitationOpen={c => setAnchors(c?.pdf_anchors ?? null)} />
  </div>;
}
createRoot(document.getElementById("root")!).render(<MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppStateContext.Provider value={{ provider: "deepseek", model: "deepseek-model", llmParams: {} } as AppState}><Harness /></AppStateContext.Provider></QueryClientProvider></MemoryRouter>);
