import { useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BrainCircuit,
  Briefcase,
  Columns3,
  FileSearch,
  FileText,
  FlaskConical,
  GitBranch,
  Import,
  Library,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  SlidersHorizontal
} from "lucide-react";

import { api, API_BASE_URL } from "./api";
import { AppStateContext } from "./state";
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

const navigation = [
  { to: "/projects", label: "Projekte", icon: Briefcase },
  { to: "/import", label: "Import", icon: Import },
  { to: "/extraction", label: "Extraktion", icon: FileSearch },
  { to: "/library", label: "Library", icon: Library },
  { to: "/workspace", label: "Arbeitsplatz", icon: Columns3 },
  { to: "/graph", label: "Graph", icon: GitBranch },
  { to: "/quality", label: "Quality", icon: BarChart3 },
  { to: "/benchmarks", label: "Benchmarks", icon: FlaskConical },
  { to: "/jobs", label: "Jobs", icon: BrainCircuit },
  { to: "/settings", label: "Settings", icon: Settings }
];

export default function App() {
  const [activeProject, setActiveProject] = useState<string | undefined>(() => localStorage.getItem("sciencekg.project") ?? undefined);
  const [provider, setProvider] = useState<string | undefined>(() => localStorage.getItem("sciencekg.provider") ?? undefined);
  const [model, setModel] = useState<string | undefined>(() => localStorage.getItem("sciencekg.model") ?? undefined);
  const [sidebarOpen, setSidebarOpen] = useState(() => localStorage.getItem("sciencekg.sidebar.open") !== "false");

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: api.getHealth, refetchInterval: 30000 });
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.getProviders });

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

  const selectedProvider = providersQuery.data?.providers.find((item) => item.name === provider);
  const state = useMemo(
    () => ({ activeProject, setActiveProject, provider, setProvider, model, setModel }),
    [activeProject, provider, model]
  );

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
                  {(selectedProvider?.models ?? []).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="topbar-health">
              <Status value={healthQuery.data?.status ?? "loading"} />
              <span>{healthQuery.data?.warnings?.length ?? 0} Warnungen</span>
              <span>{API_BASE_URL}</span>
              <SlidersHorizontal size={17} />
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
