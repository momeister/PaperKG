import { WorkspaceHost } from "./workspace/WorkspaceHost";
import { GlossaryProvider, GlossaryPanel } from "./glossary/GlossaryProvider";
import { Fragment, lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  Code2,
  Columns3,
  FlaskConical,
  GitBranch,
  Library,
  Notebook,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Target,
  Telescope,
  Waypoints
} from "lucide-react";

import { api, API_BASE_URL } from "./api";
import { MotionProvider } from "./motion";
import {
  AppStateContext,
  clampCreativityLevel,
  clampFontScale,
  FONT_SCALE_STEP,
  loadWorkspaceMode,
  normalizeTheme,
  persistWorkspaceMode,
  THEME_META
} from "./state";
import type { LlmParams, Theme } from "./state";
import type { CreativityLevel, WorkspaceMode } from "./types";
import { ConstellationMark } from "./components/ConstellationMark";
import { CompactPopover } from "./components/CompactPopover";
import { isRemoteModel, LlmPicker } from "./components/LlmPicker";
import { Status } from "./components/Status";
import { ThemePicker } from "./components/ThemePicker";
import { useLlmProviders } from "./hooks/useLlmProviders";
// The overlay-family pages stay statically imported: they render in the four extra
// Tauri windows (early returns below, never through <Routes>) and must appear
// instantly — the control border/pointer ring can't wait for a chunk fetch.
import { OverlayPage } from "./pages/overlay/OverlayPage";
import { ControlBorderPage } from "./pages/ControlBorderPage";
import { PointerOverlayPage } from "./pages/PointerOverlayPage";
import { SnipOverlayPage } from "./pages/SnipOverlayPage";

// Every main-shell page is code-split: five webviews parse the entry chunk (main
// window + the lazily created overlay windows), so the heavyweights — Monaco+xterm
// (WorkstationPage), pdf.js (Library/Workspace), @xyflow (GraphPage) — must not sit
// in it. Fan-noise fix, together with the lazy window creation in the Tauri shell.
const BenchmarksPage = lazy(() => import("./pages/BenchmarksPage").then((m) => ({ default: m.BenchmarksPage })));
const GraphPage = lazy(() => import("./pages/GraphPage").then((m) => ({ default: m.GraphPage })));
const JobsPage = lazy(() => import("./pages/JobsPage").then((m) => ({ default: m.JobsPage })));
const LibraryPage = lazy(() => import("./pages/LibraryPage").then((m) => ({ default: m.LibraryPage })));
const QualityPage = lazy(() => import("./pages/QualityPage").then((m) => ({ default: m.QualityPage })));
const ResearchHubPage = lazy(() => import("./pages/ResearchHubPage").then((m) => ({ default: m.ResearchHubPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const WorkstationPage = lazy(() => import("./pages/WorkstationPage").then((m) => ({ default: m.WorkstationPage })));
const JupyterPage = lazy(() => import("./pages/JupyterPage").then((m) => ({ default: m.JupyterPage })));
const CodeGraphPage = lazy(() => import("./pages/codegraph/CodeGraphPage").then((m) => ({ default: m.CodeGraphPage })));

type NavEntry = {
  to: string;
  label: string;
  icon: typeof Telescope;
  group: string;
  /** Optional ausklappbare Sub-Einträge. Wenn gesetzt, wird der Parent als
   *  Gruppen-Kopf gerendert und die Sub-Einträge darunter eingeblendet. */
  children?: Array<{
    to: string;
    label: string;
    icon: typeof Telescope;
    /** Workspace-Modus, der beim Aktivieren gesetzt wird (Task-Focused Mode). */
    mode?: WorkspaceMode;
  }>;
};

const navigation: NavEntry[] = [
  { to: "/forschung", label: "Forschung", icon: Telescope, group: "Erkunden" },
  { to: "/library", label: "Library", icon: Library, group: "Erkunden" },
  {
    to: "/workspace",
    label: "Arbeitsplatz",
    icon: Columns3,
    group: "Arbeiten",
    // Task-Focused Mode: der Arbeitsplatz hat zwei Gesichter — die klassische
    // Forschungsansicht (Research) und den Task-Focused Mode (Kaggle/Hackathon/
    // Anweisung). Beide leben unter derselben Route /workspace; der Modus wird
    // pro Projekt in AppState gesetzt und persistiert.
    children: [
      { to: "/workspace", label: "Research", icon: NotebookPen, mode: "research" },
      { to: "/workspace", label: "Task", icon: Target, mode: "task" }
    ]
  },
  { to: "/werkstatt", label: "Werkstatt", icon: Code2, group: "Arbeiten" },
  // Gruppe "Arbeiten", nicht "Analyse": dort steht schon /graph, der Wissensgraph
  // über Papers. Zwei Einträge namens "Graph" untereinander wären das Erste, was
  // man verwechselt — hier steht der Code-Graph neben der Werkstatt, deren
  // Projekte er erklärt.
  { to: "/code", label: "Code-Graph", icon: Waypoints, group: "Arbeiten" },
  { to: "/jupyter", label: "Jupyter", icon: Notebook, group: "Arbeiten" },
  { to: "/graph", label: "Graph", icon: GitBranch, group: "Analyse" },
  { to: "/quality", label: "Quality", icon: BarChart3, group: "Analyse" },
  { to: "/benchmarks", label: "Benchmarks", icon: FlaskConical, group: "Analyse" },
  { to: "/jobs", label: "Jobs", icon: BrainCircuit, group: "Analyse" },
  { to: "/settings", label: "Settings", icon: Settings, group: "System" }
];

function loadStoredLlmParams(): LlmParams {
  try {
    const raw = localStorage.getItem("sciencekg.llmParams");
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as LlmParams;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function loadStoredTheme(): Theme {
  const stored = normalizeTheme(localStorage.getItem("sciencekg.theme"));
  if (stored) {
    return stored;
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "nacht";
  }
  return "tag";
}

function loadStoredFontScale(): number {
  return clampFontScale(Number(localStorage.getItem("sciencekg.fontScale") ?? "1"));
}

export default function App() {
  const [activeProject, setActiveProject] = useState<string | undefined>(() => localStorage.getItem("sciencekg.project") ?? undefined);
  const [{ provider, model }, setLlmChoice] = useState<{ provider?: string; model?: string }>(() => ({ provider: localStorage.getItem("sciencekg.provider") ?? undefined, model: localStorage.getItem("sciencekg.model") ?? undefined }));
  const setProvider = useCallback((provider?: string) => setLlmChoice(current => current.provider === provider ? current : { provider, model: undefined }), []);
  const setModel = useCallback((model?: string) => setLlmChoice(current => ({ ...current, model })), []);
  const [sidebarOpen, setSidebarOpen] = useState(() => localStorage.getItem("sciencekg.sidebar.open") !== "false");
  // Task-Focused Mode: welche Sidebar-Gruppen ausgeklappt sind. Default: alle
  // Gruppen mit Sub-Einträgen (aktuell nur "Arbeitsplatz") offen, damit der
  // Task-Modus direkt sichtbar ist. Persistiert in localStorage.
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem("sciencekg.sidebar.expandedGroups");
      if (raw) {
        const parsed = JSON.parse(raw) as string[];
        if (Array.isArray(parsed)) {
          return new Set(parsed);
        }
      }
    } catch {
      // ignore
    }
    // Default: alle Gruppen mit children ausgeklappt.
    return new Set(["Arbeitsplatz"]);
  });
  const [llmParams, setLlmParams] = useState<LlmParams>(loadStoredLlmParams);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(loadStoredTheme);
  const [fontScale, setFontScaleState] = useState<number>(loadStoredFontScale);
  // Task-Focused Mode: Workspace-Modus und Kreativitätsstufe sind projektgebunden.
  // Der Modus lebt in localStorage (UI-only), die Kreativitätsstufe in project_meta.json
  // (Backend-of-Record) — wir halten hier den geladenen Wert vor und patchen ihn
  // beim Setzen via ``PATCH /projects/{id}``.
  const [workspaceMode, setWorkspaceModeState] = useState<WorkspaceMode>(() => loadWorkspaceMode(activeProject));
  const [creativityLevel, setCreativityLevelState] = useState<CreativityLevel>(3);

  // The AI-Cursor overlay (R1) and the "AI has control" border both load the same app
  // in a separate Tauri window; each renders only its own compact view and skips the
  // heavy main-shell queries.
  const location = useLocation();
  const navigate = useNavigate();
  const isOverlay = window.__OVERLAY__ === true || location.pathname === "/overlay";
  const isControlBorder = window.__CONTROL_BORDER__ === true || location.pathname === "/control-border";
  const isPointerOverlay = window.__POINTER_OVERLAY__ === true || location.pathname === "/pointer";
  const isSnipOverlay = window.__SNIP_OVERLAY__ === true || location.pathname === "/snip";
  const skipHeavyQueries = isOverlay || isControlBorder || isPointerOverlay || isSnipOverlay;

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects, enabled: !skipHeavyQueries });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: api.getHealth, refetchInterval: 30000, enabled: !skipHeavyQueries });
  // Anbieter/Modelle: eine Quelle für die ganze App (siehe hooks/useLlmProviders).
  // Lokale Anbieter (LM Studio, Ollama) wissen am besten, was gerade geladen ist —
  // deshalb wird live erkannt statt der statischen Liste aus config.yaml zu glauben.
  const { defaultProvider, selectedProvider } = useLlmProviders(provider, model, {
    enabled: !skipHeavyQueries
  });

  useEffect(() => {
    if (!activeProject || !projectsQuery.data?.projects) {
      return;
    }
    const exists = projectsQuery.data.projects.some((project) => project.id === activeProject);
    if (!exists) {
      setActiveProject(undefined);
    }
  }, [activeProject, projectsQuery.data?.projects]);

  useEffect(() => {
    if (!provider && defaultProvider) {
      setProvider(defaultProvider);
    }
  }, [provider, defaultProvider]);

  useEffect(() => {
    activeProject ? localStorage.setItem("sciencekg.project", activeProject) : localStorage.removeItem("sciencekg.project");
  }, [activeProject]);

  useEffect(() => {
    provider ? localStorage.setItem("sciencekg.provider", provider) : localStorage.removeItem("sciencekg.provider");
  }, [provider]);

  useEffect(() => {
    model ? localStorage.setItem("sciencekg.model", model) : localStorage.removeItem("sciencekg.model");
  }, [model]);

  useEffect(() => {
    localStorage.setItem("sciencekg.sidebar.open", String(sidebarOpen));
  }, [sidebarOpen]);

  useEffect(() => {
    try {
      localStorage.setItem("sciencekg.sidebar.expandedGroups", JSON.stringify([...expandedGroups]));
    } catch {
      // ignore
    }
  }, [expandedGroups]);

  useEffect(() => {
    try {
      localStorage.setItem("sciencekg.llmParams", JSON.stringify(llmParams));
    } catch {
      // Storage unavailable — params just stay session-local.
    }
  }, [llmParams]);

  useEffect(() => {
    // data-theme wählt die Palette, data-scheme (dark/light) bedient die wenigen
    // Scheme-Selektoren in styles.css. Schreibt normalisiert zurück — migriert
    // damit auch alte "dark"/"light"-Werte aus localStorage.
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-scheme", THEME_META[theme].scheme);
    localStorage.setItem("sciencekg.theme", theme);
  }, [theme]);

  // Globaler UI-Zoom: skaliert Text, Icons und Layout proportional. ``zoom`` ist im
  // Tauri-/Chromium-Webview sicher (anders als ``transform: scale`` bricht es keine
  // 100vh-Panes). Der Boot-Script in index.html setzt denselben Wert flimmerfrei vorab.
  useEffect(() => {
    document.documentElement.style.zoom = String(fontScale);
    document.documentElement.style.setProperty("--app-font-scale", String(fontScale));
    localStorage.setItem("sciencekg.fontScale", String(fontScale));
  }, [fontScale]);

  // Task-Focused Mode: beim Projektwechsel Workspace-Modus neu laden. Der Modus
  // gilt pro Projekt, nicht global.
  useEffect(() => {
    setWorkspaceModeState(loadWorkspaceMode(activeProject));
  }, [activeProject]);

  // Task-Focused Mode: Kreativitätsstufe aus der Projektliste übernehmen (Backend
  // liefert ``creativity_level`` via ``project_meta.json``, Default 3).
  useEffect(() => {
    if (!activeProject || !projectsQuery.data?.projects) {
      setCreativityLevelState(3);
      return;
    }
    const project = projectsQuery.data.projects.find((p) => p.id === activeProject);
    setCreativityLevelState(clampCreativityLevel(project?.creativity_level));
  }, [activeProject, projectsQuery.data?.projects]);

  const setWorkspaceMode = (mode: WorkspaceMode) => {
    setWorkspaceModeState(mode);
    persistWorkspaceMode(activeProject, mode);
  };

  const setCreativityLevel = (level: CreativityLevel) => {
    const clamped = clampCreativityLevel(level);
    setCreativityLevelState(clamped);
    if (activeProject) {
      // Best-Effort-Persistenz; das Backend schreibt den Wert in project_meta.json.
      // Re-query der Projektliste würde Race-Conditions beim Tippen am Slider erzeugen,
      // deshalb opt-in: die Liste wird nur aktualisiert, wenn der Slider losgelassen
      // wird (aufrufende Komponente entscheidet via onBlur/onChange-Final).
      api.patchProject(activeProject, { creativity_level: clamped }).catch(() => {
        // Stumm — die UI zeigt schon den optimistischen Wert; schlägt das Patch fehl
        // (z.B. Backend gesperrt), bleibt der Wert session-lokal.
      });
    }
  };

  const toggleTheme = () => setTheme((current) => THEME_META[current].counterpart);
  const setFontScale = (scale: number) => setFontScaleState(clampFontScale(scale));
  const adjustFontScale = (delta: number) => setFontScaleState((current) => clampFontScale(current + delta));

  const state = useMemo(
    () => ({ activeProject, setActiveProject, provider, setProvider, model, setModel, llmParams, setLlmParams, theme, setTheme, toggleTheme, fontScale, setFontScale, workspaceMode, setWorkspaceMode, creativityLevel, setCreativityLevel }),
    [activeProject, provider, model, llmParams, theme, fontScale, workspaceMode, creativityLevel]
  );

  function updateLlmParam(key: keyof LlmParams, rawValue: string) {
    setLlmParams({
      ...llmParams,
      [key]: rawValue === "" ? undefined : Number(rawValue)
    });
  }

  // Overlay window: no sidebar/topbar — just the compact AI-Cursor. Wrapped in
  // AppStateContext so the embedded Notizen tab (NotesSurface) can use useAppState().
  if (isOverlay) {
    return (
      <AppStateContext.Provider value={state}>
        <GlossaryProvider><OverlayPage /></GlossaryProvider>
      </AppStateContext.Provider>
    );
  }
  // Control-border window: no sidebar/topbar — just the full-screen click-through frame.
  if (isControlBorder) {
    return <ControlBorderPage />;
  }
  // Pointer-overlay window: no sidebar/topbar — just the full-screen click-through highlight.
  if (isPointerOverlay) {
    return <PointerOverlayPage />;
  }
  // Snip window: no sidebar/topbar — just the full-screen frozen-frame region selector.
  if (isSnipOverlay) {
    return <SnipOverlayPage />;
  }

  const appearanceControls = <>
    <span className="font-scale-controls" role="group" aria-label="Schriftgröße">
      <button
        className="icon-button font-scale-button"
        type="button"
        aria-label="Schrift verkleinern"
        title="Schrift verkleinern"
        onClick={() => adjustFontScale(-FONT_SCALE_STEP)}
      >
        <span className="font-scale-glyph font-scale-glyph--small">A</span>
      </button>
      <button
        className="icon-button font-scale-button"
        type="button"
        aria-label="Schriftgröße zurücksetzen"
        title={`Schriftgröße: ${Math.round(fontScale * 100)}% – auf 100% zurücksetzen`}
        onClick={() => setFontScale(1)}
      >
        {Math.round(fontScale * 100)}%
      </button>
      <button
        className="icon-button font-scale-button"
        type="button"
        aria-label="Schrift vergrößern"
        title="Schrift vergrößern"
        onClick={() => adjustFontScale(FONT_SCALE_STEP)}
      >
        <span className="font-scale-glyph font-scale-glyph--large">A</span>
      </button>
    </span>
    <ThemePicker theme={theme} onSelect={setTheme} variant="inline" />
  </>;

  return (
    <AppStateContext.Provider value={state}>
      <GlossaryProvider><MotionProvider>
      <div className={`app-shell ${sidebarOpen ? "" : "app-shell--sidebar-collapsed"}`}>
        <aside className={`sidebar ${sidebarOpen ? "" : "sidebar--collapsed"}`}>
          <div className="brand">
            <ConstellationMark size={24} />
            <div>
              <strong>ScienceKG</strong>
              <span>Phase 5</span>
            </div>
            <button className="sidebar-toggle" type="button" aria-label="Navigation einklappen" onClick={() => setSidebarOpen((current) => !current)}>
              {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
            </button>
          </div>
          <nav>
            {navigation.map((item, index) => {
              const startsGroup = index === 0 || navigation[index - 1].group !== item.group;
              const hasChildren = !!item.children?.length;
              const expanded = expandedGroups.has(item.label);
              return (
                <Fragment key={item.to + item.label}>
                  {startsGroup ? <span className="sidebar-group-label">{item.group}</span> : null}
                  {hasChildren ? (
                    <div className="sidebar-group-parent">
                      <NavLink to={item.to} className={expanded ? "sidebar-group-parent-link" : "sidebar-group-parent-link"}>
                        <item.icon size={18} />
                        <span>{item.label}</span>
                        <button
                          type="button"
                          className="sidebar-group-chevron"
                          aria-label={expanded ? "Untermenü einklappen" : "Untermenü ausklappen"}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setExpandedGroups((current) => {
                              const next = new Set(current);
                              if (next.has(item.label)) {
                                next.delete(item.label);
                              } else {
                                next.add(item.label);
                              }
                              return next;
                            });
                          }}
                        >
                          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        </button>
                      </NavLink>
                      {expanded ? (
                        <div className="sidebar-subnav">
                          {item.children!.map((child) => {
                            const active = location.pathname === child.to && workspaceMode === child.mode;
                            return (
                              <button
                                key={child.label}
                                type="button"
                                className={`sidebar-subnav-link ${active ? "active" : ""}`}
                                onClick={() => {
                                  if (child.mode) {
                                    setWorkspaceMode(child.mode);
                                  }
                                  navigate(child.to);
                                }}
                              >
                                <child.icon size={15} />
                                <span>{child.label}</span>
                              </button>
                            );
                          })}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <NavLink to={item.to}>
                      <item.icon size={18} />
                      <span>{item.label}</span>
                    </NavLink>
                  )}
                </Fragment>
              );
            })}
          </nav>
        </aside>

        <main className="workspace">
          <header className="topbar topbar--compact">
              <label className="topbar-project">
                Projekt
                <select value={activeProject ?? ""} onChange={(event) => setActiveProject(event.target.value || undefined)}>
                  {/* Bei einem Ladefehler sonst nur "Alle Papers" — das sah aus, als
                      waeren alle Projekte geloescht. Deshalb ein eigener Eintrag. */}
                  <option value="">{projectsQuery.isError ? "⚠ Projekte nicht ladbar" : "Alle Papers"}</option>
                  {(projectsQuery.data?.projects ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
            <CompactPopover className="topbar-model-choice" open={paramsOpen} onOpenChange={setParamsOpen} label={<><span className="topbar-model-label" title={`${provider ?? defaultProvider ?? "Provider"} · ${model ?? selectedProvider?.default_model ?? "Modell"}`}>{provider ?? defaultProvider ?? "Provider"} · {model ?? selectedProvider?.default_model ?? "Modell"}</span>{isRemoteModel(model ?? selectedProvider?.default_model) && <span className="llm-picker-remote">☁ nicht lokal</span>}</>}>
              <LlmPicker
                variant="topbar"
                provider={provider}
                model={model}
                onProviderChange={setProvider}
                onModelChange={setModel}
                enabled={!skipHeavyQueries}
              />
                    <strong>LLM-Parameter</strong>
                    <p className="muted">Gelten für Assistant-Antworten; leer = Provider-Default.</p>
                    <label>
                      Temperatur
                      <input
                        type="number" min="0" max="2" step="0.05"
                        value={llmParams.temperature ?? ""}
                        placeholder={String(selectedProvider?.settings?.temperature ?? 0.2)}
                        onChange={(event) => updateLlmParam("temperature", event.target.value)}
                      />
                    </label>
                    <label>
                      Top-p
                      <input
                        type="number" min="0.05" max="1" step="0.05"
                        value={llmParams.top_p ?? ""}
                        placeholder={String(selectedProvider?.settings?.top_p ?? 0.95)}
                        onChange={(event) => updateLlmParam("top_p", event.target.value)}
                      />
                    </label>
                    <label>
                      Max. Tokens
                      <input
                        type="number" min="128" max="131072" step="128"
                        value={llmParams.max_tokens ?? ""}
                        placeholder={String(selectedProvider?.settings?.max_tokens ?? 2048)}
                        onChange={(event) => updateLlmParam("max_tokens", event.target.value)}
                      />
                    </label>
                    <label>
                      Kontext
                      <input
                        type="number" min="1024" max="262144" step="1024"
                        value={llmParams.context_size ?? ""}
                        placeholder={String(selectedProvider?.settings?.context_size ?? 32768)}
                        onChange={(event) => updateLlmParam("context_size", event.target.value)}
                      />
                    </label>
                    <div className="button-row">
                      <button className="button button-compact" type="button" onClick={() => setLlmParams({})}>
                        Zurücksetzen
                      </button>
                      <button className="button button-compact button-primary" type="button" onClick={() => setParamsOpen(false)}>
                        Fertig
                      </button>
                    </div>
            </CompactPopover>
            <CompactPopover label={<><Status value={healthQuery.data?.status ?? "loading"} /><span>{healthQuery.data?.warnings?.length ?? 0} Warnungen</span></>}>
              <strong>Backend-Status</strong><p>{API_BASE_URL}</p>
              {healthQuery.isError && <p role="alert">Backend nicht erreichbar</p>}
              {(healthQuery.data?.warnings ?? []).map((warning, index) => <p key={index}>{warning}</p>)}
            </CompactPopover>
            <div className="topbar-secondary">
              <CompactPopover label="Wörterbuch"><GlossaryPanel managementOnly /></CompactPopover>
              <CompactPopover label="Darstellung">{appearanceControls}</CompactPopover>
            </div>
            <CompactPopover className="topbar-overflow" label="Mehr">
              <GlossaryPanel managementOnly />
              <strong>Darstellung</strong>
              {appearanceControls}
            </CompactPopover>
          </header>

          <WorkspaceHost />
          <Suspense fallback={<div className="page-loading">Lade…</div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/forschung" replace />} />
              <Route path="/forschung" element={<ResearchHubPage />} />
              <Route path="/forschung/:stage" element={<ResearchHubPage />} />
              {/* Alt-Routen bleiben als Deep-Links auf die Hub-Stufen gültig. */}
              <Route path="/projects" element={<Navigate to="/forschung/projekte" replace />} />
              <Route path="/import" element={<Navigate to="/forschung/import" replace />} />
              <Route path="/extraction" element={<Navigate to="/forschung/extraktion" replace />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/assistant" element={<Navigate to="/workspace" replace />} />
              <Route path="/notes" element={<Navigate to="/workspace" replace />} />
              <Route path="/workspace" element={null} />
              <Route path="/werkstatt" element={<WorkstationPage />} />
              <Route path="/code" element={<CodeGraphPage />} />
              <Route path="/jupyter" element={<JupyterPage />} />
              <Route path="/overlay" element={<OverlayPage />} />
              <Route path="/control-border" element={<ControlBorderPage />} />
              <Route path="/pointer" element={<PointerOverlayPage />} />
              <Route path="/snip" element={<SnipOverlayPage />} />
              <Route path="/graph" element={<GraphPage />} />
              <Route path="/quality" element={<QualityPage />} />
              <Route path="/benchmarks" element={<BenchmarksPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </Suspense>
        </main>
      </div>
      </MotionProvider></GlossaryProvider>
    </AppStateContext.Provider>
  );
}
