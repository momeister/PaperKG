import { useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BrainCircuit,
  Briefcase,
  Code2,
  Columns3,
  FileSearch,
  FileText,
  FlaskConical,
  GitBranch,
  Import,
  Library,
  Notebook,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  SlidersHorizontal
} from "lucide-react";

import { api, API_BASE_URL } from "./api";
import { AppStateContext } from "./state";
import type { LlmParams } from "./state";
import { Status } from "./components/Status";
import { BenchmarksPage } from "./pages/BenchmarksPage";
import { ExtractionPage } from "./pages/ExtractionPage";
import { GraphPage } from "./pages/GraphPage";
import { ImportPage } from "./pages/ImportPage";
import { JobsPage } from "./pages/JobsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { QualityPage } from "./pages/QualityPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { WorkstationPage } from "./pages/WorkstationPage";
import { JupyterPage } from "./pages/JupyterPage";
import { OverlayPage } from "./pages/OverlayPage";

const navigation = [
  { to: "/projects", label: "Projekte", icon: Briefcase },
  { to: "/import", label: "Import", icon: Import },
  { to: "/extraction", label: "Extraktion", icon: FileSearch },
  { to: "/library", label: "Library", icon: Library },
  { to: "/workspace", label: "Arbeitsplatz", icon: Columns3 },
  { to: "/werkstatt", label: "Werkstatt", icon: Code2 },
  { to: "/jupyter", label: "Jupyter", icon: Notebook },
  { to: "/graph", label: "Graph", icon: GitBranch },
  { to: "/quality", label: "Quality", icon: BarChart3 },
  { to: "/benchmarks", label: "Benchmarks", icon: FlaskConical },
  { to: "/jobs", label: "Jobs", icon: BrainCircuit },
  { to: "/settings", label: "Settings", icon: Settings }
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

export default function App() {
  const [activeProject, setActiveProject] = useState<string | undefined>(() => localStorage.getItem("sciencekg.project") ?? undefined);
  const [provider, setProvider] = useState<string | undefined>(() => localStorage.getItem("sciencekg.provider") ?? undefined);
  const [model, setModel] = useState<string | undefined>(() => localStorage.getItem("sciencekg.model") ?? undefined);
  const [sidebarOpen, setSidebarOpen] = useState(() => localStorage.getItem("sciencekg.sidebar.open") !== "false");
  const [llmParams, setLlmParams] = useState<LlmParams>(loadStoredLlmParams);
  const [paramsOpen, setParamsOpen] = useState(false);

  // The AI-Cursor overlay (R1) loads the same app in a separate Tauri window; it
  // renders only the compact overlay and skips the heavy main-shell queries.
  const location = useLocation();
  const isOverlay = window.__OVERLAY__ === true || location.pathname === "/overlay";

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects, enabled: !isOverlay });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: api.getHealth, refetchInterval: 30000, enabled: !isOverlay });
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.getProviders, enabled: !isOverlay });
  // Local providers (LM Studio, Ollama) know their loaded models best — discover them
  // live instead of relying on the static list in config.yaml.
  const activeProviderInfo = providersQuery.data?.providers.find((item) => item.name === provider);
  const supportsDiscovery = ["lm_studio", "ollama", "openai_compatible", "openai", "nvidia"].includes(
    activeProviderInfo?.provider_type ?? ""
  );
  const discoveredModelsQuery = useQuery({
    queryKey: ["models-discovered", provider],
    queryFn: () => api.discoverModels(provider as string),
    enabled: Boolean(provider) && supportsDiscovery && !isOverlay,
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
    () => ({ activeProject, setActiveProject, provider, setProvider, model, setModel, llmParams, setLlmParams }),
    [activeProject, provider, model, llmParams]
  );

  function updateLlmParam(key: keyof LlmParams, rawValue: string) {
    setLlmParams({
      ...llmParams,
      [key]: rawValue === "" ? undefined : Number(rawValue)
    });
  }

  // Overlay window: no sidebar/topbar — just the compact AI-Cursor.
  if (isOverlay) {
    return <OverlayPage />;
  }

  return (
    <AppStateContext.Provider value={state}>
      <div className={`app-shell ${sidebarOpen ? "" : "app-shell--sidebar-collapsed"}`}>
        <aside className={`sidebar ${sidebarOpen ? "" : "sidebar--collapsed"}`}>
          <div className="brand">
            <FileText size={22} />
            <div>
              <strong>ScienceKG</strong>
              <span>Phase 5</span>
            </div>
            <button className="sidebar-toggle" type="button" aria-label="Navigation einklappen" onClick={() => setSidebarOpen((current) => !current)}>
              {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
            </button>
          </div>
          <nav>
            {navigation.map((item) => (
              <NavLink key={item.to} to={item.to}>
                <item.icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            ))}
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
              <label>
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

          <Routes>
            <Route path="/" element={<Navigate to="/projects" replace />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/import" element={<ImportPage />} />
            <Route path="/extraction" element={<ExtractionPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/assistant" element={<Navigate to="/workspace" replace />} />
            <Route path="/notes" element={<Navigate to="/workspace" replace />} />
            <Route path="/workspace" element={<WorkspacePage />} />
            <Route path="/werkstatt" element={<WorkstationPage />} />
            <Route path="/jupyter" element={<JupyterPage />} />
            <Route path="/overlay" element={<OverlayPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/quality" element={<QualityPage />} />
            <Route path="/benchmarks" element={<BenchmarksPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </AppStateContext.Provider>
  );
}
