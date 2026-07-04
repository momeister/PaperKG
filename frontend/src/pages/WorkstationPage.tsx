import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import {
  ChevronDown,
  ChevronRight,
  Eye,
  File as FileIcon,
  FilePlus,
  FolderGit2,
  FolderPlus,
  NotebookPen,
  PanelsTopLeft,
  RefreshCw,
  Save,
  Send,
  TerminalSquare,
  Trash2,
} from "lucide-react";
import Editor from "@monaco-editor/react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { NotesSidePanel } from "../components/NotesSidePanel";
import { TerminalTabs } from "../components/TerminalTabs";
import { PreviewPane, normalizePreviewUrl } from "../components/PreviewPane";
import { useAppState } from "../state";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { isTauri } from "../native";
import { languageForPath } from "../monaco-setup";
import type { CodeProject, FileTreeNode, GitStatus } from "../types";

const SELECTED_KEY = "sciencekg.werkstatt.project";
const MODE_KEY = "sciencekg.werkstatt.mode";
const NOTES_KEY = "sciencekg.werkstatt.notes";
type WerkstattMode = "manual" | "agent";

// Match the origin of a local dev server printed into the terminal
// (e.g. Vite's "Local:   http://localhost:5173/"). Stops at the first control
// char / whitespace so surrounding ANSI colour codes are not swallowed.
const DEV_URL_RE = /https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?/i;

/** One node in the project file tree (recursive). */
function TreeNode({
  node,
  depth,
  activePath,
  expanded,
  onToggle,
  onOpenFile,
}: {
  node: FileTreeNode;
  depth: number;
  activePath: string | null;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onOpenFile: (path: string) => void;
}) {
  const isDir = node.type === "dir";
  const isOpen = expanded.has(node.path);
  const isActive = !isDir && node.path === activePath;
  return (
    <div className="werkstatt-tree-node">
      <button
        type="button"
        className={`werkstatt-tree-row ${isActive ? "werkstatt-tree-row--active" : ""}`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={() => (isDir ? onToggle(node.path) : onOpenFile(node.path))}
        title={node.path || node.name}
      >
        {isDir ? (
          isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />
        ) : (
          <FileIcon size={14} />
        )}
        <span>{node.name}</span>
      </button>
      {isDir && isOpen
        ? (node.children ?? []).map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              expanded={expanded}
              onToggle={onToggle}
              onOpenFile={onOpenFile}
            />
          ))
        : null}
    </div>
  );
}

/** Color a unified git diff line by line. */
function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return <EmptyState title="Keine Änderungen">Noch nichts geändert seit dem letzten Commit.</EmptyState>;
  }
  return (
    <pre className="werkstatt-diff">
      {diff.split("\n").map((line, index) => {
        let cls = "";
        if (line.startsWith("+") && !line.startsWith("+++")) cls = "werkstatt-diff--add";
        else if (line.startsWith("-") && !line.startsWith("---")) cls = "werkstatt-diff--del";
        else if (line.startsWith("@@")) cls = "werkstatt-diff--hunk";
        else if (line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("+++") || line.startsWith("---"))
          cls = "werkstatt-diff--meta";
        return (
          <span key={index} className={cls}>
            {line || " "}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

export function WorkstationPage() {
  const { activeProject } = useAppState();
  const queryClient = useQueryClient();

  const [projectId, setProjectId] = useState<string | null>(() => localStorage.getItem(SELECTED_KEY));
  const [mode, setMode] = useState<WerkstattMode>(
    () => (localStorage.getItem(MODE_KEY) === "agent" ? "agent" : "manual"),
  );
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [editorValue, setEditorValue] = useState<string>("");
  const [savedValue, setSavedValue] = useState<string>("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [resultTab, setResultTab] = useState<"changes" | "diff">("changes");
  const [showNewProject, setShowNewProject] = useState(false);
  const [showOpenFolder, setShowOpenFolder] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [openFolderPath, setOpenFolderPath] = useState("");
  const [newFilePath, setNewFilePath] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [notesOpen, setNotesOpen] = useState(() => localStorage.getItem(NOTES_KEY) === "1");
  const [toast, setToast] = useState<string | null>(null);
  const editorRef = useRef<unknown>(null);
  // URL auto-detection: rolling buffer of recent terminal output + a flag that
  // disables auto-fill once the user edits the address bar by hand.
  const urlBufferRef = useRef("");
  const userTouchedPreviewRef = useRef(false);

  const workspacesQuery = useQuery({ queryKey: ["werkstatt", "list"], queryFn: api.werkstatt.list });
  const projects = workspacesQuery.data?.projects ?? [];

  // Deep-Link (z.B. aus der Analyse-Werkstatt): /werkstatt?project=<id>&file=<pfad>
  // wählt das Projekt aus und öffnet die Datei samt aufgeklapptem Baumpfad.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const wanted = searchParams.get("project");
    if (!wanted || !projects.length) return;
    if (!projects.some((p) => p.id === wanted)) return;
    const file = searchParams.get("file");
    setProjectId(wanted);
    if (file) {
      setExpanded((current) => {
        const next = new Set(current);
        let acc = "";
        for (const part of file.split("/").slice(0, -1)) {
          acc = acc ? `${acc}/${part}` : part;
          next.add(acc);
        }
        return next;
      });
      void openFile(file, wanted);
    }
    setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, searchParams]);

  // Keep the selected project valid + persisted.
  useEffect(() => {
    if (!projects.length) return;
    if (!projectId || !projects.some((p) => p.id === projectId)) {
      setProjectId(projects[0].id);
    }
  }, [projects, projectId]);
  useEffect(() => {
    if (projectId) localStorage.setItem(SELECTED_KEY, projectId);
  }, [projectId]);
  useEffect(() => {
    localStorage.setItem(MODE_KEY, mode);
  }, [mode]);
  useEffect(() => {
    localStorage.setItem(NOTES_KEY, notesOpen ? "1" : "0");
  }, [notesOpen]);

  const activeCodeProject: CodeProject | undefined = projects.find((p) => p.id === projectId);

  const treeQuery = useQuery({
    queryKey: ["werkstatt", "tree", projectId],
    queryFn: () => api.werkstatt.tree(projectId as string),
    enabled: Boolean(projectId),
  });
  const gitStatusQuery = useQuery({
    queryKey: ["werkstatt", "git-status", projectId],
    queryFn: () => api.werkstatt.gitStatus(projectId as string),
    enabled: Boolean(projectId),
  });
  const diffQuery = useQuery({
    queryKey: ["werkstatt", "git-diff", projectId],
    queryFn: () => api.werkstatt.gitDiff(projectId as string),
    enabled: Boolean(projectId) && resultTab === "diff",
  });

  const dirty = openPath !== null && editorValue !== savedValue;

  function notify(message: string) {
    setToast(message);
    window.setTimeout(() => setToast((current) => (current === message ? null : current)), 2600);
  }

  function refreshProject() {
    queryClient.invalidateQueries({ queryKey: ["werkstatt", "tree", projectId] });
    queryClient.invalidateQueries({ queryKey: ["werkstatt", "git-status", projectId] });
    queryClient.invalidateQueries({ queryKey: ["werkstatt", "git-diff", projectId] });
  }

  // Auto-detect the dev-server URL from terminal output, unless the user took
  // manual control of the address bar.
  function handleTerminalOutput(text: string) {
    if (userTouchedPreviewRef.current) return;
    const buffer = (urlBufferRef.current + text).slice(-1000);
    urlBufferRef.current = buffer;
    const match = buffer.match(DEV_URL_RE);
    if (!match) return;
    const found = normalizePreviewUrl(match[0]);
    setPreviewUrl((current) => (current === found ? current : found));
  }

  function handlePreviewUrlChange(next: string) {
    userTouchedPreviewRef.current = true;
    setPreviewUrl(next);
  }

  async function openFile(path: string, pid: string | null = projectId) {
    if (!pid) return;
    try {
      const file = await api.werkstatt.readFile(pid, path);
      if (file.binary) {
        notify("Binärdatei — im Editor nicht darstellbar.");
        return;
      }
      if (file.too_large) {
        notify("Datei zu groß für den Editor.");
        return;
      }
      setOpenPath(path);
      setEditorValue(file.content ?? "");
      setSavedValue(file.content ?? "");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Datei konnte nicht geladen werden.");
    }
  }

  const saveMutation = useMutation({
    mutationFn: () => api.werkstatt.writeFile(projectId as string, openPath as string, editorValue),
    onSuccess: () => {
      setSavedValue(editorValue);
      refreshProject();
      notify("Gespeichert.");
    },
    onError: (error) => notify(error instanceof Error ? error.message : "Speichern fehlgeschlagen."),
  });

  const createProjectMutation = useMutation({
    mutationFn: (name: string) => api.werkstatt.create(name),
    onSuccess: (project) => {
      setShowNewProject(false);
      setNewProjectName("");
      queryClient.invalidateQueries({ queryKey: ["werkstatt", "list"] });
      setProjectId(project.id);
      notify(`Projekt „${project.name}" angelegt.`);
    },
    onError: (error) => notify(error instanceof Error ? error.message : "Projekt konnte nicht angelegt werden."),
  });

  const openFolderMutation = useMutation({
    mutationFn: (path: string) => api.werkstatt.open(path),
    onSuccess: (project) => {
      setShowOpenFolder(false);
      setOpenFolderPath("");
      queryClient.invalidateQueries({ queryKey: ["werkstatt", "list"] });
      setProjectId(project.id);
      notify(`Ordner „${project.name}" geöffnet.`);
    },
    onError: (error) => notify(error instanceof Error ? error.message : "Ordner konnte nicht geöffnet werden."),
  });

  const createFileMutation = useMutation({
    mutationFn: (path: string) => api.werkstatt.createFile(projectId as string, path),
    onSuccess: (_data, path) => {
      setNewFilePath("");
      refreshProject();
      void openFile(path);
    },
    onError: (error) => notify(error instanceof Error ? error.message : "Datei konnte nicht angelegt werden."),
  });

  const removeProjectMutation = useMutation({
    mutationFn: (id: string) => api.werkstatt.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["werkstatt", "list"] });
      setProjectId(null);
      setOpenPath(null);
      notify("Projekt aus PaperKG entfernt (Ordner bleibt auf der Platte).");
    },
  });

  // "In Workspace einfügen": current file/selection or diff → note in the active project.
  const insertMutation = useMutation({
    mutationFn: async (payload: { title: string; markdown: string }) =>
      api.createNote(noteProjectId(activeProject), payload),
    onSuccess: () => notify(`In Workspace eingefügt (Projekt: ${projectScopeLabel(activeProject)}).`),
    onError: (error) => notify(error instanceof Error ? error.message : "Einfügen fehlgeschlagen."),
  });

  function selectionOrFile(): string {
    const editor = editorRef.current as { getModel?: () => unknown; getSelection?: () => unknown } | null;
    try {
      const model = editor?.getModel?.() as { getValueInRange?: (r: unknown) => string } | null;
      const selection = editor?.getSelection?.() as { isEmpty?: () => boolean } | null;
      if (model && selection && selection.isEmpty && !selection.isEmpty()) {
        return model.getValueInRange?.(selection) ?? editorValue;
      }
    } catch {
      // fall through to the whole file
    }
    return editorValue;
  }

  function insertCodeIntoWorkspace() {
    if (!openPath) {
      notify("Erst eine Datei öffnen.");
      return;
    }
    const snippet = selectionOrFile();
    const lang = languageForPath(openPath);
    const markdown = `# Code: ${openPath}\n\nAus Code-Werkstatt-Projekt **${activeCodeProject?.name ?? ""}**.\n\n\`\`\`${lang}\n${snippet}\n\`\`\`\n`;
    insertMutation.mutate({ title: `Code: ${openPath.split("/").pop()}`, markdown });
  }

  function insertDiffIntoWorkspace() {
    const diff = diffQuery.data?.diff ?? "";
    if (!diff.trim()) {
      notify("Kein Diff vorhanden.");
      return;
    }
    const markdown = `# Änderungen: ${activeCodeProject?.name ?? ""}\n\n\`\`\`diff\n${diff}\n\`\`\`\n`;
    insertMutation.mutate({ title: `Diff: ${activeCodeProject?.name ?? "Projekt"}`, markdown });
  }

  function toggleFolder(path: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  // Ctrl/Cmd+S saves the open file.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (openPath && dirty && !saveMutation.isPending) saveMutation.mutate();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openPath, dirty, saveMutation]);

  const gitStatus: GitStatus | undefined = gitStatusQuery.data;
  const path = activeCodeProject?.path ?? "";

  // ---- Panel renderers (shared between the two layouts) ----

  function renderTreePane() {
    return (
      <div className="wk-pane">
        <div className="wk-pane-head">
          <strong className="wk-pane-title">Dateien</strong>
          <button className="wk-iconbtn" title="Aktualisieren" onClick={() => refreshProject()}>
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="werkstatt-newfile">
          <input
            placeholder="neue/datei.py"
            value={newFilePath}
            onChange={(event) => setNewFilePath(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && newFilePath.trim() && createFileMutation.mutate(newFilePath.trim())}
          />
          <button className="wk-iconbtn" title="Datei anlegen" disabled={!newFilePath.trim()} onClick={() => newFilePath.trim() && createFileMutation.mutate(newFilePath.trim())}>
            <FilePlus size={15} />
          </button>
        </div>
        <div className="werkstatt-tree">
          {treeQuery.data?.children?.length ? (
            treeQuery.data.children.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                depth={0}
                activePath={openPath}
                expanded={expanded}
                onToggle={toggleFolder}
                onOpenFile={openFile}
              />
            ))
          ) : (
            <p className="muted" style={{ padding: "8px 10px" }}>
              {treeQuery.isLoading ? "lädt…" : "Leeres Projekt."}
            </p>
          )}
        </div>
        <p className="werkstatt-path muted" title={path}>
          {path}
        </p>
      </div>
    );
  }

  function renderEditorPane() {
    return (
      <div className="wk-pane">
        <div className="wk-pane-head wk-pane-head--split">
          <span className="werkstatt-editor-path">
            {openPath ? openPath : "Keine Datei geöffnet"}
            {dirty ? <em className="werkstatt-dirty"> ●</em> : null}
          </span>
          <div className="werkstatt-editor-actions">
            <button
              className="wk-btn wk-btn--primary"
              disabled={!openPath || !dirty || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              <Save size={15} /> Speichern
            </button>
            <button
              className="wk-btn"
              disabled={!openPath || insertMutation.isPending}
              title="Aktuelle Datei/Selektion als Notiz in den Workspace einfügen"
              onClick={insertCodeIntoWorkspace}
            >
              <Send size={15} /> In Workspace
            </button>
          </div>
        </div>
        <div className="wk-editor-body">
          {openPath ? (
            <Editor
              height="100%"
              theme="vs-dark"
              path={openPath}
              language={languageForPath(openPath)}
              value={editorValue}
              onChange={(value) => setEditorValue(value ?? "")}
              onMount={(editor) => {
                editorRef.current = editor;
              }}
              options={{
                fontSize: 13,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 2,
              }}
            />
          ) : (
            <EmptyState title="Datei wählen">Öffne links eine Datei zum Bearbeiten.</EmptyState>
          )}
        </div>
      </div>
    );
  }

  function renderResultsPane() {
    return (
      <div className="wk-pane">
        <div className="werkstatt-results-tabs">
          <button className={resultTab === "changes" ? "active" : ""} onClick={() => setResultTab("changes")}>
            Änderungen
          </button>
          <button className={resultTab === "diff" ? "active" : ""} onClick={() => setResultTab("diff")}>
            Diff
          </button>
          <button className="wk-iconbtn" title="Aktualisieren" onClick={() => refreshProject()}>
            <RefreshCw size={14} />
          </button>
        </div>
        {resultTab === "changes" ? (
          <div className="werkstatt-changes">
            {!gitStatus?.available ? (
              <p className="muted">Git nicht verfügbar.</p>
            ) : !gitStatus.is_repo ? (
              <p className="muted">Kein Git-Repository.</p>
            ) : gitStatus.files.length ? (
              gitStatus.files.map((file) => (
                <button
                  key={file.path}
                  className="werkstatt-change-row"
                  title={file.untracked ? "neu" : file.staged ? "staged" : "geändert"}
                  onClick={() => (mode === "agent" ? setResultTab("diff") : openFile(file.path))}
                >
                  <span className={`werkstatt-change-code werkstatt-change-code--${file.untracked ? "new" : file.staged ? "staged" : "mod"}`}>
                    {file.code || "??"}
                  </span>
                  <span>{file.path}</span>
                </button>
              ))
            ) : (
              <p className="muted">Keine Änderungen.</p>
            )}
          </div>
        ) : (
          <div className="werkstatt-diff-wrap">
            <DiffView diff={diffQuery.data?.diff ?? ""} />
          </div>
        )}
        <div className="werkstatt-results-foot">
          <button
            className="wk-btn"
            disabled={insertMutation.isPending}
            title="Diff als Notiz in den Workspace einfügen"
            onClick={insertDiffIntoWorkspace}
          >
            <Send size={15} /> Diff in Workspace
          </button>
        </div>
      </div>
    );
  }

  function renderTerminalPane() {
    return (
      <div className="wk-pane wk-term-pane">
        <div className="wk-pane-head">
          <TerminalSquare size={14} />
          <strong className="wk-pane-title">Terminal</strong>
          <span className="wk-pane-sub" title={path}>
            {path}
          </span>
        </div>
        <TerminalTabs key={path} cwd={path} onOutput={handleTerminalOutput} />
      </div>
    );
  }

  return (
    <section className="page werkstatt-page">
      <header className="wk-toolbar">
        <div className="wk-toolbar-left">
          <FolderGit2 size={15} className="wk-toolbar-icon" />
          <select
            className="wk-project-select"
            value={projectId ?? ""}
            onChange={(event) => {
              setProjectId(event.target.value || null);
              setOpenPath(null);
            }}
          >
            <option value="">— Projekt wählen —</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name} {project.kind === "external" ? "(extern)" : ""}
                {project.exists === false ? " — fehlt" : ""}
              </option>
            ))}
          </select>
        </div>

        <div className="wk-segmented" role="tablist" aria-label="Modus">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "manual"}
            className={mode === "manual" ? "active" : ""}
            onClick={() => setMode("manual")}
          >
            <PanelsTopLeft size={14} /> Manuell
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "agent"}
            className={mode === "agent" ? "active" : ""}
            onClick={() => setMode("agent")}
          >
            <Eye size={14} /> Agent · Vorschau
          </button>
        </div>

        <div className="wk-toolbar-right">
          <button
            className={`wk-iconbtn wk-iconbtn--label ${notesOpen ? "wk-iconbtn--active" : ""}`}
            type="button"
            title="Projekt-Notizen ein-/ausklappen"
            onClick={() => setNotesOpen((v) => !v)}
          >
            <NotebookPen size={15} /> <span>Notizen</span>
          </button>
          <button
            className="wk-iconbtn wk-iconbtn--label"
            type="button"
            title="Neues Projekt anlegen"
            onClick={() => {
              setShowNewProject((v) => !v);
              setShowOpenFolder(false);
            }}
          >
            <FolderPlus size={15} /> <span>Neu</span>
          </button>
          <button
            className="wk-iconbtn wk-iconbtn--label"
            type="button"
            title="Bestehenden Ordner öffnen"
            onClick={() => {
              setShowOpenFolder((v) => !v);
              setShowNewProject(false);
            }}
          >
            <FolderGit2 size={15} /> <span>Ordner</span>
          </button>
          {activeCodeProject ? (
            <button
              className="wk-iconbtn wk-iconbtn--danger"
              type="button"
              title="Projekt aus PaperKG entfernen (Ordner bleibt erhalten)"
              onClick={() => removeProjectMutation.mutate(activeCodeProject.id)}
            >
              <Trash2 size={15} />
            </button>
          ) : null}
        </div>
      </header>

      {showNewProject ? (
        <div className="wk-inline-form">
          <input
            autoFocus
            placeholder="Projektname (z. B. mein-experiment)"
            value={newProjectName}
            onChange={(event) => setNewProjectName(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && newProjectName.trim() && createProjectMutation.mutate(newProjectName.trim())}
          />
          <button className="wk-btn wk-btn--primary" disabled={!newProjectName.trim() || createProjectMutation.isPending} onClick={() => createProjectMutation.mutate(newProjectName.trim())}>
            Anlegen
          </button>
          <span className="muted">→ {workspacesQuery.data?.base_dir}</span>
        </div>
      ) : null}
      {showOpenFolder ? (
        <div className="wk-inline-form">
          <input
            autoFocus
            placeholder="Absoluter Ordnerpfad (z. B. C:\\Users\\…\\mein-repo)"
            value={openFolderPath}
            onChange={(event) => setOpenFolderPath(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && openFolderPath.trim() && openFolderMutation.mutate(openFolderPath.trim())}
          />
          <button className="wk-btn wk-btn--primary" disabled={!openFolderPath.trim() || openFolderMutation.isPending} onClick={() => openFolderMutation.mutate(openFolderPath.trim())}>
            Öffnen
          </button>
          <span className="muted">Registriert einen bestehenden Ordner als externes Projekt.</span>
        </div>
      ) : null}

      {!projects.length && !workspacesQuery.isLoading ? (
        <div className="wk-body wk-body--empty">
          <EmptyState title="Noch kein Code-Projekt">
            Lege ein neues Projekt an oder öffne einen bestehenden Ordner. Projekte sind echte Git-Ordner
            unter <code>{workspacesQuery.data?.base_dir}</code> — auch von VS Code &amp; Co. zu öffnen.
          </EmptyState>
        </div>
      ) : null}

      {projectId && activeCodeProject ? (
        <div className="wk-body">
          {mode === "manual" ? (
            <PanelGroup direction="vertical" autoSaveId="wk-manual-v" className="wk-group">
              <Panel minSize={20} order={1} id="wk-m-main" className="wk-pane-slot">
                <PanelGroup direction="horizontal" autoSaveId="wk-manual-h" className="wk-group">
                  <Panel defaultSize={22} minSize={12} collapsible order={1} id="wk-m-tree" className="wk-pane-slot">
                    {renderTreePane()}
                  </Panel>
                  <PanelResizeHandle className="wk-resize wk-resize--v" />
                  <Panel minSize={25} order={2} id="wk-m-editor" className="wk-pane-slot">
                    {renderEditorPane()}
                  </Panel>
                  <PanelResizeHandle className="wk-resize wk-resize--v" />
                  <Panel defaultSize={26} minSize={12} collapsible order={3} id="wk-m-results" className="wk-pane-slot">
                    {renderResultsPane()}
                  </Panel>
                </PanelGroup>
              </Panel>
              <PanelResizeHandle className="wk-resize wk-resize--h" />
              <Panel defaultSize={34} minSize={10} collapsible order={2} id="wk-m-term" className="wk-pane-slot">
                {renderTerminalPane()}
              </Panel>
            </PanelGroup>
          ) : (
            <PanelGroup direction="horizontal" autoSaveId="wk-agent-h" className="wk-group">
              <Panel defaultSize={30} minSize={12} collapsible order={1} id="wk-a-term" className="wk-pane-slot">
                {renderTerminalPane()}
              </Panel>
              <PanelResizeHandle className="wk-resize wk-resize--v" />
              <Panel minSize={25} order={2} id="wk-a-preview" className="wk-pane-slot">
                <PreviewPane url={previewUrl} onUrlChange={handlePreviewUrlChange} />
              </Panel>
              <PanelResizeHandle className="wk-resize wk-resize--v" />
              <Panel defaultSize={26} minSize={12} collapsible order={3} id="wk-a-results" className="wk-pane-slot">
                {renderResultsPane()}
              </Panel>
            </PanelGroup>
          )}
          {notesOpen ? <NotesSidePanel onClose={() => setNotesOpen(false)} /> : null}
        </div>
      ) : null}

      {!isTauri() ? (
        <p className="muted werkstatt-hint">
          Hinweis: Das eingebettete Terminal und die Live-Vorschau (für KI-Coding-CLIs wie Claude Code,
          opencode, codex …) sind nur in der Desktop-App aktiv. Datei-Ansicht, Editor und Diff funktionieren
          auch im Browser.
        </p>
      ) : null}

      {toast ? <div className="werkstatt-toast">{toast}</div> : null}
    </section>
  );
}
