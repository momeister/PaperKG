import { Fragment, lazy, Suspense, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BrainCircuit,
  Code2,
  Columns3,
  FlaskConical,
  GitBranch,
  Library,
  Notebook,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  SlidersHorizontal,
  Telescope
} from "lucide-react";

import { api, API_BASE_URL } from "./api";
import { MotionProvider } from "./motion";
import { AppStateContext, clampFontScale, FONT_SCALE_STEP, normalizeTheme, THEME_META } from "./state";
import type { LlmParams, Theme } from "./state";
import { ConstellationMark } from "./components/ConstellationMark";
import { Status } from "./components/Status";
import { ThemePicker } from "./components/ThemePicker";
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
const WorkspacePage = lazy(() => import("./pages/WorkspacePage").then((m) => ({ default: m.WorkspacePage })));
const WorkstationPage = lazy(() => import("./pages/WorkstationPage").then((m) => ({ default: m.WorkstationPage })));
const JupyterPage = lazy(() => import("./pages/JupyterPage").then((m) => ({ default: m.JupyterPage })));

const navigation = [
  { to: "/forschung", label: "Forschung", icon: Telescope, group: "Erkunden" },
  { to: "/library", label: "Library", icon: Library, group: "Erkunden" },
  { to: "/workspace", label: "Arbeitsplatz", icon: Columns3, group: "Arbeiten" },
  { to: "/werkstatt", label: "Werkstatt", icon: Code2, group: "Arbeiten" },
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
    return "observatorium";
  }
  return "manuskript";
}

function loadStoredFontScale(): number {
  return clampFontScale(Number(localStorage.getItem("sciencekg.fontScale") ?? "1"));
}

export default function App() {
  const [activeProject, setActiveProject] = useState<string | undefined>(() => localStorage.getItem("sciencekg.project") ?? undefined);
  const [provider, setProvider] = useState<string | undefined>(() => localStorage.getItem("sciencekg.provider") ?? undefined);
  const [model, setModel] = useState<string | undefined>(() => localStorage.getItem("sciencekg.model") ?? undefined);
  const [sidebarOpen, setSidebarOpen] = useState(() => localStorage.getItem("sciencekg.sidebar.open") !== "false");
  const [llmParams, setLlmParams] = useState<LlmParams>(loadStoredLlmParams);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(loadStoredTheme);
  const [fontScale, setFontScaleState] = useState<number>(loadStoredFontScale);

  // The AI-Cursor overlay (R1) and the "AI has control" border both load the same app
  // in a separate Tauri window; each renders only its own compact view and skips the
  // heavy main-shell queries.
  const location = useLocation();
  const isOverlay = window.__OVERLAY__ === true || location.pathname === "/overlay";
  const isControlBorder = window.__CONTROL_BORDER__ === true || location.pathname === "/control-border";
  const isPointerOverlay = window.__POINTER_OVERLAY__ === true || location.pathname === "/pointer";
  const isSnipOverlay = window.__SNIP_OVERLAY__ === true || location.pathname === "/snip";
  const skipHeavyQueries = isOverlay || isControlBorder || isPointerOverlay || isSnipOverlay;

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects, enabled: !skipHeavyQueries });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: api.getHealth, refetchInterval: 30000, enabled: !skipHeavyQueries });
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.getProviders, enabled: !skipHeavyQueries });
  // Local providers (LM Studio, Ollama) know their loaded models best — discover them
  // live instead of relying on the static list in config.yaml.
  const activeProviderInfo = providersQuery.data?.providers.find((item) => item.name === provider);
  const supportsDiscovery = ["lm_studio", "ollama", "openai_compatible", "openai", "nvidia"].includes(
    activeProviderInfo?.provider_type ?? ""
  );
  const discoveredModelsQuery = useQuery({
    queryKey: ["models-discovered", provider],
    queryFn: () => api.discoverModels(provider as string),
    enabled: Boolean(provider) && supportsDiscovery && !skipHeavyQueries,
    staleTime: 60_000,
    retry: false
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
    if (!provider && providersQuery.data?.default_provider) {
      setProvider(providersQuery.data.default_provider);
    }
  }, [provider, providersQuery.data?.default_provider]);

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
    localStorage.setItem("sciencekg.fontScale", String(fontScale));
  }, [fontScale]);

  const toggleTheme = () => setTheme((current) => THEME_META[current].counterpart);
  const setFontScale = (scale: number) => setFontScaleState(clampFontScale(scale));
  const adjustFontScale = (delta: number) => setFontScaleState((current) => clampFontScale(current + delta));

  const selectedProvider = providersQuery.data?.providers.find((item) => item.name === provider);
  const modelOptions = useMemo(() => {
    const merged = [...(discoveredModelsQuery.data?.models ?? []), ...(selectedProvider?.models ?? [])];
    if (selectedProvider?.default_model) {
      merged.push(selectedProvider.default_model);
    }
    if (model) {
      merged.push(model);
    }
    return Array.from(new Set(merged.filter(Boolean)));
  }, [discoveredModelsQuery.data?.models, selectedProvider, model]);
  const state = useMemo(
    () => ({ activeProject, setActiveProject, provider, setProvider, model, setModel, llmParams, setLlmParams, theme, setTheme, toggleTheme, fontScale, setFontScale }),
    [activeProject, provider, model, llmParams, theme, fontScale]
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
        <OverlayPage />
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

  return (
    <AppStateContext.Provider value={state}>
      <MotionProvider>
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
              return (
                <Fragment key={item.to}>
                  {startsGroup ? <span className="sidebar-group-label">{item.group}</span> : null}
                  <NavLink to={item.to}>
                    <item.icon size={18} />
                    <span>{item.label}</span>
                  </NavLink>
                </Fragment>
              );
            })}
          </nav>
        </aside>

        <main className="workspace">
          <header className="topbar">
            <div className="topbar-group">
              <label>
                Projekt
                <select value={activeProject ?? ""} onChange={(event) => setActiveProject(event.target.value || undefined)}>
                  <option value="">Alle Papers</option>
                  {(projectsQuery.data?.projects ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Provider
                <select value={provider ?? ""} onChange={(event) => setProvider(event.target.value || undefined)}>
                  {(providersQuery.data?.providers ?? []).map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="topbar-model">
                Modell
                <select value={model ?? selectedProvider?.default_model ?? ""} onChange={(event) => setModel(event.target.value || undefined)}>
                  {modelOptions.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              {discoveredModelsQuery.isFetching ? <span className="topbar-hint">erkenne Modelle…</span> : null}
            </div>
            <div className="topbar-health">
              <Status value={healthQuery.data?.status ?? "loading"} />
              <span>{healthQuery.data?.warnings?.length ?? 0} Warnungen</span>
              <span>{API_BASE_URL}</span>
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
              <ThemePicker theme={theme} onSelect={setTheme} />
              <span className="llm-params-wrap">
                <button
                  className={`icon-button ${paramsOpen || Object.values(llmParams).some((value) => value !== undefined) ? "icon-button--active" : ""}`}
                  type="button"
                  aria-label="LLM-Parameter anpassen"
                  title="LLM-Parameter anpassen"
                  onClick={() => setParamsOpen((current) => !current)}
                >
                  <SlidersHorizontal size={17} />
                </button>
                {paramsOpen ? (
                  <div className="llm-params-popover">
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
                  </div>
                ) : null}
              </span>
            </div>
          </header>

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
              <Route path="/workspace" element={<WorkspacePage />} />
              <Route path="/werkstatt" element={<WorkstationPage />} />
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
      </MotionProvider>
    </AppStateContext.Provider>
  );
}
