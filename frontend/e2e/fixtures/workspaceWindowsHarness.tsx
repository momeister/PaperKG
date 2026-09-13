import { windowPdf } from "./windowPdf";
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { Panel, PanelGroup, type ImperativePanelHandle } from "react-resizable-panels";
import { PortablePane, WorkspaceOwnerContext, WorkspaceWindowsContext } from "../../src/workspace/PortablePane";
import { WorkspaceWindows } from "../../src/workspace/windows";
import { PANE_IDS, type PaneId } from "../../src/workspace/protocol";
import { AppStateContext, type AppState } from "../../src/state";
import { NotesSurface } from "../../src/pages/NotesPage";
import { AssistantComposer } from "../../src/pages/AssistantComposer";
import { PdfPane } from "../../src/components/PdfPane";
import { api } from "../../src/api";
import { nativeInvoke, nativeListen } from "../../src/native";
import "../../src/styles/tokens.css";
import "../../src/styles/themes.css";
import "../../src/styles.css";

const probe = window as any;
const nativeProbe = new URLSearchParams(location.search).has("native");
const childWindows = new Map<PaneId, Window>();
probe.children = childWindows;
probe.work = { started: 0, completed: 0, mounts: 0 };
let note = { id: "window-note", project_id: "window-test", title: "Fensternotiz", markdown: "Entwurf", citations: [], assets: [] };
probe.saved = [];
api.listNotes = async () => ({ items: [note], total: 1 }) as any;
api.getNote = async () => ({ note }) as any;
api.listNoteAiThreads = async () => ({ items: [], total: 0 });
api.updateNote = async (_id, value) => { note = { ...note, ...value } as typeof note; probe.saved.push(value); return { note } as any; };
api.paperMeta = async () => ({ title: "Fenster-PDF", paper_id: "fixture" }) as any;
api.pdfAnnotations.list = async () => ({ annotations: [] });
const manager = new WorkspaceWindows({
  enabled: true,
  open: url => {
    if (probe.failOpen) return null;
    const child = window.open(url.href, "_blank", "popup,width=750,height=750");
    if (child) childWindows.set(url.searchParams.get("pane") as PaneId, child);
    return child;
  },
  command: async (pane, action) => {
    const child = childWindows.get(pane);
    if (nativeProbe) {
      await nativeInvoke("workspace_window_action", { pane, action });
      if (action === "destroy") childWindows.delete(pane);
      return;
    }
    if (action === "destroy") { child?.close(); childWindows.delete(pane); }
    if (action === "show") child?.focus();
  }
});
probe.manager = manager;
function Work() {
  const [result, setResult] = React.useState("");
  React.useEffect(() => { probe.work.mounts++; }, []);
  const run = async () => {
    probe.work.started++;
    await new Promise<void>(resolve => { probe.finish = resolve; });
    probe.work.completed++; setResult("Antwort fertig");
  };
  return <div><AssistantComposer papers={[]} disabled={false} onSubmit={run} onSelectPaper={() => {}} />
    <button onClick={run}>Arbeit starten</button><output>{result}</output></div>;
}
function Pane({ pane }: { pane: PaneId }) {
  const ref = React.useRef<ImperativePanelHandle>(null);
  return <Panel id={pane} defaultSize={25} minSize={2} collapsible collapsedSize={2} ref={ref}>
    <PortablePane pane={pane} panelRef={ref}>
      {pane === "navigator" ? <input placeholder="Navigator suchen" /> : null}
      {pane === "center" ? <PdfPane headerInPaneToolbar url={nativeProbe ? `data:application/pdf;base64,${btoa(windowPdf())}` : "/window-fixture.pdf"} title="Fenster-PDF" metaPaperId="fixture" /> : null}
      {pane === "assistant" ? <Work /> : null}
      {pane === "notes" ? <NotesSurface variant="workspace" /> : null}
    </PortablePane>
  </Panel>;
}
function Harness() {
  const [hidden, setHidden] = React.useState(false);
  React.useLayoutEffect(() => manager.setOwner("window-test"), []);
  React.useEffect(() => manager.start(), []);
  React.useEffect(() => {
    if (!nativeProbe) return;
    let stopped = false, off: (() => void) | undefined;
    void nativeListen<PaneId>("workspace-dock-request", pane => { void manager.dock(pane); })
      .then(unlisten => { if (stopped) unlisten(); else off = unlisten; });
    return () => { stopped = true; off?.(); };
  }, []);
  return <>
    <button onClick={() => setHidden(v => !v)}>Seite wechseln</button>
    <div hidden={hidden} style={{ height: "85vh" }}>
      <PanelGroup direction="horizontal">{PANE_IDS.map(pane => <Pane key={pane} pane={pane} />)}</PanelGroup>
    </div>
  </>;
}
createRoot(document.getElementById("root")!).render(
  <MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AppStateContext.Provider value={{ activeProject: "window-test", provider: "test", model: "test", llmParams: {} } as AppState}>
      <WorkspaceWindowsContext.Provider value={manager}><WorkspaceOwnerContext.Provider value="window-test"><Harness /></WorkspaceOwnerContext.Provider></WorkspaceWindowsContext.Provider>
    </AppStateContext.Provider>
  </QueryClientProvider></MemoryRouter>
);
