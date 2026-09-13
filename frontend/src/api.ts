import type {
  GlossaryEntry,
  AgentConfig,
  AgentHandoffResponse,
  Answer,
  CompanionAskResult,
  CompanionConfigInfo,
  CompanionGuideResult,
  SelfDriveStartResult,
  SelfDriveStepResult,
  CodeProject,
  WorkspaceList,
  FileTreeNode,
  FileContent,
  GitStatus,
  GitDiff,
  Checkpoint,
  CheckpointResult,
  CheckpointDiffEntry,
  CheckpointRestorePlan,
  CheckpointRestoreResult,
  Sandbox,
  SandboxRunResult,
  AnalysisRun,
  ClaimCheckResult,
  CreativityLevel,
  Dataset,
  DatasetDetails,
  DatasetDownloadRequest,
  DatasetDownloadResponse,
  DatasetHit,
  DatasetSource,
  DatasetSourceStatus,
  BenchmarkReport,
  BenchmarkRun,
  Dashboard,
  DeepResearchResponse,
  DiscoveryResponse,
  ExtractionHistoryItem,
  ExtractionQualityRow,
  ExtractionResultDetail,
  GreySource,
  ReferenceExtractResponse,
  ExtractionLibraryItem,
  ExtractionParseResponse,
  ExtractionRunResponse,
  GraphExplorer,
  HarvestDownloadResponse,
  HarvestSourceCatalog,
  HealthReport,
  Job,
  KaggleLoginRequest,
  KaggleLoginResponse,
  KaggleStatus,
  Note,
  NoteAiEditResponse,
  NoteAiThread,
  Paper,
  PaperMeta,
  PaperIngestResponse,
  PdfAnnotation,
  PdfAnnotationRect,
  ParallelSession,
  ParallelSessionSummary,
  ParallelStage,
  ParallelStep,
  ParallelVariant,
  ParallelEntry,
  Project,
  Provider,
  ResearchNode,
  ResearchSession,
  ResearchSessionSummary,
  RewriteResponse,
  ReviewEntity,
  Task,
  TaskDeepSearchEvent,
  TaskDeepSearchRequest,
  TaskGreySourceResponse,
  TaskImplementationPlan,
  TaskResearchDirection,
  TaskSuggestDirectionsResponse,
  TaskSpec,
  VerificationSource,
  VocabularyEntry
} from "./types";

declare global {
  interface Window {
    /** Backend origin injected by the native Tauri shell (dynamic localhost port). */
    __API_BASE__?: string;
    /** Set by the Tauri shell on the overlay window so the app renders the AI-Cursor (R1). */
    __OVERLAY__?: boolean;
    /** Set by the Tauri shell on the "AI has control" border window (Selbst-Steuerung). */
    __CONTROL_BORDER__?: boolean;
    /** Set by the Tauri shell on the Assistent "zeig mir" pointer overlay window. */
    __POINTER_OVERLAY__?: boolean;
    /** Set by the Tauri shell on the Desktop-Companion "Bereich erklären" snip window (R6). */
    __SNIP_OVERLAY__?: boolean;
  }
}

function resolveApiBaseUrl(): string {
  // 1. Native (Tauri) shell injects the backend origin at runtime via an
  //    initialization script, because the page is served from the Tauri asset
  //    protocol on a different origin than the Python sidecar's dynamic port.
  if (typeof window !== "undefined" && window.__API_BASE__) {
    return window.__API_BASE__;
  }
  // 2. Explicit build-time override.
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (fromEnv) {
    return fromEnv;
  }
  // 3. Web dev fallback (unchanged): Vite on :5173 talks to FastAPI on :8000.
  return "http://127.0.0.1:8000";
}

export const API_BASE_URL = resolveApiBaseUrl();

type RequestOptions = RequestInit & {
  query?: Record<string, string | number | boolean | null | undefined>;
};

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail ? String(detail.message) : `API request failed with ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

function url(path: string, query?: RequestOptions["query"]) {
  const target = new URL(path, API_BASE_URL);
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      target.searchParams.set(key, String(value));
    }
  });
  return target.toString();
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { query, headers, ...init } = options;
  let response: Response;
  try {
    response = await fetch(url(path, query), {
      ...init,
      headers: {
        ...(init.body && !(init.body instanceof FormData) ? { "content-type": "application/json" } : {}),
        ...headers
      }
    });
  } catch (error) {
    const reason = error instanceof Error && error.message ? ` ${error.message}` : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}).${reason}`);
  }
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload && "detail" in payload ? payload.detail : payload;
    throw new ApiError(response.status, detail || `Backend error ${response.status} for ${path}`);
  }
  return payload as T;
}

export const api = {
  listGlossary: () => request<{ items: GlossaryEntry[] }>("/glossary"),
  createGlossary: (payload: { term: string; explanation: string }) => request<GlossaryEntry>("/glossary", { method: "POST", body: JSON.stringify(payload) }),
  updateGlossary: (id: string, payload: { term: string; explanation: string }) => request<GlossaryEntry>(`/glossary/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteGlossary: (id: string) => request<{ deleted: boolean }>(`/glossary/${encodeURIComponent(id)}`, { method: "DELETE" }),
  suggestGlossary: (payload: { term: string; selected_text: string; provider?: string; model?: string }) => request<{ suggestion: string }>("/glossary/suggest", { method: "POST", body: JSON.stringify(payload) }),
  getHealth: () => request<HealthReport>("/system/health-report"),
  // `degraded` ist gesetzt, wenn die Projektliste aus projects.json kommt, die
  // Metadaten-DB aber nicht lesbar war (z.B. zweites Backend haelt den Lock).
  // Die Projekte stimmen dann, nur Jahresspannen fehlen — die UI weist darauf hin.
  getProjects: () => request<{ projects: Project[]; degraded?: string }>("/projects"),
  createProject: (name: string) => request<{ project: Project }>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
  patchProject: (projectId: string, payload: { name?: string; pinned?: boolean; creativity_level?: CreativityLevel }) =>
    request<{ project: Project }>(`/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteProject: (projectId: string) =>
    request<{ deleted: boolean; project: Project }>(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }),
  addProjectPapers: (projectId: string, paperIds: string[]) =>
    request<{ project: Project }>(`/projects/${encodeURIComponent(projectId)}/papers`, {
      method: "POST",
      body: JSON.stringify({ paper_ids: paperIds })
    }),
  getDashboard: (projectId: string) => request<Dashboard>(`/projects/${encodeURIComponent(projectId)}/dashboard`),
  listPapers: (params: Record<string, string | number | boolean | null | undefined> = {}) =>
    request<{ items: Paper[]; total: number; limit: number; offset: number }>("/papers", { query: params }),
  paperMeta: (paperId: string) => request<PaperMeta>("/paper/meta", { query: { paper_id: paperId } }),
  paperIngest: (payload: { paper_id: string; project_id?: string | null; provider?: string; model?: string }) =>
    request<PaperIngestResponse>("/paper/ingest", { method: "POST", body: JSON.stringify(payload) }),
  uploadPdf: (file: File, params: { paper_id?: string; title?: string; project_id?: string }) =>
    request<{ paper: Paper; pdf_path: string; project_id?: string | null; attached?: boolean }>("/papers/upload", {
      method: "POST",
      query: params,
      headers: { "content-type": file.type || "application/pdf", "x-filename": file.name },
      body: file
    }),
  getHarvestSources: () => request<HarvestSourceCatalog>("/harvest/sources"),
  harvestSearch: (payload: { query: string; sources: string[]; max_results: number }) =>
    request<{ query: string; results: Paper[]; warnings: string[] }>("/harvest/search", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  harvestDownload: (papers: Paper[], downloadPdfs: boolean, projectId?: string) =>
    request<HarvestDownloadResponse>("/harvest/download", {
      method: "POST",
      body: JSON.stringify({ papers, download_pdfs: downloadPdfs, project_id: projectId || null })
    }),
  extractReferences: (payload: { paper_id: string; pdf_path?: string; parser?: string; max_references?: number }, signal?: AbortSignal) =>
    request<ReferenceExtractResponse>("/papers/references/extract", {
      method: "POST",
      body: JSON.stringify(payload),
      signal
    }),
  discoveryFromTopic: (payload: { topic: string; sources?: string[]; provider?: string; max_per_query?: number }, signal?: AbortSignal) =>
    request<DiscoveryResponse>("/discovery/from-topic", { method: "POST", body: JSON.stringify(payload), signal }),
  discoveryFromPaper: (payload: { paper_id: string; pdf_path?: string; sources?: string[]; provider?: string; max_per_query?: number }, signal?: AbortSignal) =>
    request<DiscoveryResponse>("/discovery/from-paper", { method: "POST", body: JSON.stringify(payload), signal }),
  deepResearch: (payload: {
    question: string;
    provider?: string;
    search_provider?: string;
    max_queries?: number;
    results_per_query?: number;
    max_sources?: number;
  }, signal?: AbortSignal) => request<DeepResearchResponse>("/research/deep", { method: "POST", body: JSON.stringify(payload), signal }),
  listGreySources: (projectId: string) =>
    request<{ project_id: string; grey_sources: GreySource[] }>(`/projects/${encodeURIComponent(projectId)}/grey-sources`),
  addGreySources: (projectId: string, sources: Record<string, unknown>[], query?: string) =>
    request<{ project_id: string; saved: GreySource[] }>(`/projects/${encodeURIComponent(projectId)}/grey-sources`, {
      method: "POST",
      body: JSON.stringify({ sources, query })
    }),
  addGreySourceFromUrl: (projectId: string, url: string) =>
    request<{ project_id: string; saved: GreySource }>(`/projects/${encodeURIComponent(projectId)}/grey-sources/from-url`, {
      method: "POST",
      body: JSON.stringify({ url })
    }),
  /** Notiz als zitierbare Projektquelle veroeffentlichen (Snapshot, idempotent je Notiz). */
  saveNoteAsSource: (noteId: string) =>
    request<{ saved: GreySource }>(`/notes/${encodeURIComponent(noteId)}/as-source`, {
      method: "POST",
      body: JSON.stringify({})
    }),
  /** Fertige Tiefenanalyse als zitierbare Projektquelle speichern (inkl. ihrer Quell-Paper). */
  saveResearchTreeAsSource: (payload: {
    project_id: string;
    root_question: string;
    document: string;
    nodes: unknown[];
    sources?: unknown[];
    session_id?: string;
  }) =>
    request<{ saved: GreySource; paper_count: number }>("/research/tree/as-source", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getWorkspaceSession: (projectId: string) =>
    request<{ project_id: string; payload: Record<string, unknown>; updated_timestamp?: string | null }>(
      `/workspace/sessions/${encodeURIComponent(projectId)}`
    ),
  saveWorkspaceSession: (projectId: string, payload: Record<string, unknown>, force = false) =>
    request<{ project_id: string; payload: Record<string, unknown> }>(`/workspace/sessions/${encodeURIComponent(projectId)}`, {
      method: "PUT",
      body: JSON.stringify({ payload, force })
    }),
  listWorkspaceSessionBackups: (projectId: string) =>
    request<{ project_id: string; backups: { saved_at: string; turn_count: number }[] }>(
      `/workspace/sessions/${encodeURIComponent(projectId)}/backups`
    ),
  restoreWorkspaceSession: (projectId: string, savedAt?: string) =>
    request<{ project_id: string; payload: Record<string, unknown> }>(
      `/workspace/sessions/${encodeURIComponent(projectId)}/restore`,
      { method: "POST", body: JSON.stringify({ saved_at: savedAt ?? null }) }
    ),
  deletePaper: (paperId: string) =>
    request<{ deleted: boolean; file_deleted: boolean; id: string }>(`/papers/${encodeURIComponent(paperId)}`, { method: "DELETE" }),
  deleteGreySource: (greyId: string) =>
    request<{ deleted: boolean; id: string }>(`/grey-sources/${encodeURIComponent(greyId)}`, { method: "DELETE" }),
  removeProjectPaper: (projectId: string, paperId: string) =>
    request<{ project: Project; removed: string }>(
      `/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}`,
      { method: "DELETE" }
    ),
  deleteNoteCitation: (noteId: string, citationId: string) =>
    request<{ deleted: boolean; id: string }>(
      `/notes/${encodeURIComponent(noteId)}/citations/${encodeURIComponent(citationId)}`,
      { method: "DELETE" }
    ),
  setPrimaryPaper: (projectId: string, paperId: string | null) =>
    request<{ project_id: string; primary_paper_id: string | null }>(
      `/projects/${encodeURIComponent(projectId)}/primary-paper`,
      { method: "PUT", body: JSON.stringify({ paper_id: paperId }) }
    ),
  // `degraded`: der Datei-Scan hat geklappt, aber die DB war nicht lesbar — dann
  // fehlen Grauquellen und Paper ohne PDF. Frueher wurde das stillschweigend
  // verschluckt, die Liste sah einfach kuerzer aus.
  getExtractionLibrary: (query = "", projectId?: string) =>
    request<{ items: ExtractionLibraryItem[]; total: number; degraded?: string }>("/extraction/library", {
      query: { query, project_id: projectId || undefined }
    }),
  parseExtractionPdf: (payload: { paper_id: string; pdf_path?: string; parser?: string }) =>
    request<ExtractionParseResponse>("/extraction/parse", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  runExtraction: (payload: {
    paper_id: string;
    text?: string;
    pdf_path?: string;
    parser?: string;
    provider?: string;
    model?: string;
    temperature?: number;
    top_p?: number;
    max_tokens?: number;
    context_size?: number;
    extraction_mode?: string;
    context_policy?: "auto" | "whole" | "chunk";
    allow_context_fallback?: boolean;
    link_concepts?: boolean;
  }) =>
    request<ExtractionRunResponse>("/extraction/extract", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  runExtractionBatch: (payload: {
    items: Array<{ paper_id: string; pdf_path?: string }>;
    job_id?: string;
    provider?: string;
    model?: string;
    temperature?: number;
    top_p?: number;
    max_tokens?: number;
    context_size?: number;
    extraction_mode?: string;
    context_policy?: "auto" | "whole" | "chunk";
    allow_context_fallback?: boolean;
    link_concepts?: boolean;
    resume?: boolean;
    force_reextract?: boolean;
  }) =>
    request<{ job: Job; items: Array<Record<string, unknown>> }>("/extraction/batch", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getExtractionBatchItems: (jobId: string) =>
    request<{ job_id: string; items: import("./types").BatchJobItem[] }>(`/extraction/batch/${jobId}/items`),
  cancelExtractionBatch: (jobId: string) =>
    request<{ job_id: string; status: string }>(`/extraction/batch/${jobId}/cancel`, { method: "POST", body: "{}" }),
  deleteExtractionResults: (payload: { paper_ids?: string[]; project_id?: string | null }) =>
    request<{ deleted: number; paper_ids: string[] }>("/extraction/delete", { method: "POST", body: JSON.stringify(payload) }),
  getExtractionHistory: (paperId = "") =>
    request<{ items: ExtractionHistoryItem[]; total: number }>("/extraction/history", { query: { paper_id: paperId } }),
  getExtractionQuality: (paperId = "", limit = 50) =>
    request<{ items: ExtractionQualityRow[]; total: number }>("/extraction/quality", { query: { paper_id: paperId, limit } }),
  getExtractionResult: (resultId: number) =>
    request<ExtractionResultDetail>(`/extraction/results/${resultId}`),
  getExtractionResultRaw: (resultId: number) =>
    request<{ result_id: number; raw_response: string | null }>(`/extraction/results/${resultId}/raw`),
  compareExtractions: (paperId: string, limit = 50) =>
    request<{ paper_id: string; groups: Record<string, ExtractionHistoryItem[]>; total: number }>("/extraction/compare", { query: { paper_id: paperId, limit } }),
  getExtractionVocabulary: () => request<{ items: VocabularyEntry[]; total: number }>("/extraction/vocabulary"),
  addExtractionVocabulary: (payload: { canonical_label: string; aliases: string[]; openalx_id?: string; domain?: string }) =>
    request<{ items: VocabularyEntry[]; total: number }>("/extraction/vocabulary", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  answer: (payload: {
    question: string;
    provider?: string;
    model?: string;
    limit?: number;
    paper_ids?: string[];
    priority_paper_ids?: string[];
    conversation_context?: Array<{ role: string; content: string }>;
    answer_context_mode?: "kg" | "pdf_if_fits";
    inline_context_texts?: string[];
    project_id?: string;
    grey_source_ids?: string[];
    include_project_grey?: boolean;
    llm_overrides?: Record<string, number | undefined>;
    answer_style?: "standard" | "kritisch";
  }) =>
    request<Answer>("/query/answer", { method: "POST", body: JSON.stringify(payload) }),
  claimCheck: (payload: {
    statement: string;
    paper_ids: string[];
    titles?: Record<string, string>;
    evidence_texts?: Record<string, string>;
    provider?: string | null;
    model?: string | null;
  }) =>
    request<{ statement: string; checks: ClaimCheckResult[] }>("/assistant/claim-check", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  verifyAnswer: (answer: Answer, options: { max_sources?: number; max_evidence_per_source?: number } = {}) =>
    request<{ sources: VerificationSource[]; cited_paper_ids: string[]; missing_source_ids: string[] }>("/sources/verify-answer", {
      method: "POST",
      body: JSON.stringify({
        answer,
        max_sources: options.max_sources ?? 12,
        max_evidence_per_source: options.max_evidence_per_source ?? 12
      })
    }),
  rewriteNote: (payload: { text: string; instruction: string; provider?: string; model?: string }) =>
    request<RewriteResponse>("/tools/rewrite", { method: "POST", body: JSON.stringify(payload) }),
  listNotes: (projectId: string) => request<{ items: Note[]; total: number }>(`/projects/${encodeURIComponent(projectId)}/notes`),
  createNote: (projectId: string, payload: { title: string; markdown?: string; citations?: Record<string, unknown>[] }) =>
    request<{ note: Note }>(`/projects/${encodeURIComponent(projectId)}/notes`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getNote: (noteId: string) => request<{ note: Note }>(`/notes/${encodeURIComponent(noteId)}`),
  updateNote: (noteId: string, payload: { title?: string; markdown?: string; citations?: Record<string, unknown>[] }) =>
    request<{ note: Note }>(`/notes/${encodeURIComponent(noteId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteNote: (noteId: string) => request<{ deleted: boolean }>(`/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
  appendNote: (noteId: string, payload: { markdown: string; title?: string; citations?: Record<string, unknown>[] }) =>
    request<{ note: Note }>(`/notes/${encodeURIComponent(noteId)}/append`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  restoreLatestNoteVersion: (noteId: string) =>
    request<{ note: Note }>(`/notes/${encodeURIComponent(noteId)}/versions/restore-latest`, { method: "POST" }),
  listNoteAiThreads: (noteId: string) => request<{ items: NoteAiThread[]; total: number }>(`/notes/${encodeURIComponent(noteId)}/ai-threads`),
  createNoteAiThread: (
    noteId: string,
    payload: {
      selected_text: string;
      instruction: string;
      provider?: string;
      model?: string;
      use_kg_evidence?: boolean;
      anchor_start?: number | null;
      anchor_end?: number | null;
      anchor_quote?: string | null;
    }
  ) =>
    request<NoteAiEditResponse>(`/notes/${encodeURIComponent(noteId)}/ai-threads`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  appendNoteAiMessage: (
    noteId: string,
    threadId: string,
    payload: { message: string; provider?: string; model?: string; use_kg_evidence?: boolean }
  ) =>
    request<NoteAiEditResponse>(`/notes/${encodeURIComponent(noteId)}/ai-threads/${encodeURIComponent(threadId)}/messages`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateNoteAiThread: (noteId: string, threadId: string, payload: { ui_state?: Record<string, unknown> }) =>
    request<{ thread: NoteAiThread }>(`/notes/${encodeURIComponent(noteId)}/ai-threads/${encodeURIComponent(threadId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteNoteAiThread: (noteId: string, threadId: string) =>
    request<{ deleted: boolean }>(`/notes/${encodeURIComponent(noteId)}/ai-threads/${encodeURIComponent(threadId)}/delete`, {
      method: "POST"
    }),
  deleteNoteAiThreads: (noteId: string) =>
    request<{ deleted: number }>(`/notes/${encodeURIComponent(noteId)}/ai-threads/delete-all`, {
      method: "POST"
    }),
  noteAiEdit: (noteId: string, payload: { selected_text: string; instruction: string; provider?: string; model?: string; use_kg_evidence?: boolean }) =>
    request<NoteAiEditResponse>(`/notes/${encodeURIComponent(noteId)}/ai-edit`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  askNote: (noteId: string, payload: { question: string; provider?: string; model?: string; use_kg_evidence?: boolean }) =>
    request<NoteAiEditResponse>(`/notes/${encodeURIComponent(noteId)}/ask`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  uploadNoteAsset: (noteId: string, file: File) =>
    request<{ asset: { id: string; url: string; filename: string } }>(`/notes/${encodeURIComponent(noteId)}/assets`, {
      method: "POST",
      headers: { "content-type": file.type || "image/png", "x-filename": file.name },
      body: file
    }),
  noteAssetUrl: (assetId: string) => url(`/notes/assets/${encodeURIComponent(assetId)}`),
  paperPdfUrl: (paperId: string, title = "") =>
    url("/paper/pdf", { paper_id: paperId, title }),
  getGraph: (params: { project_id?: string; query?: string; edge_types?: string; limit?: number }) =>
    request<GraphExplorer>("/graph/explorer", { query: params }),
  getBenchmark: () => request<BenchmarkReport>("/quality/benchmark"),
  getReview: (status = "pending", query = "") => request<{ items: ReviewEntity[]; total: number }>("/review/entities", { query: { status, query } }),
  reviewAction: (ids: number[], action: "approve" | "reject") =>
    request<{ updated: number; status: string }>("/review/entities/actions", { method: "POST", body: JSON.stringify({ ids, action }) }),
  getProviders: () => request<{ default_provider: string; providers: Provider[] }>("/models/providers"),
  discoverModels: (provider: string) => request<{ provider: string; models: string[] }>(`/models/${encodeURIComponent(provider)}/discover`, { method: "POST" }),
  checkProvider: (provider: string, model?: string) =>
    request<{ provider: string; model: string; ok: boolean; error?: string | null }>(`/models/${encodeURIComponent(provider)}/check`, {
      method: "POST",
      query: { model }
    }),
  getJobs: () => request<{ jobs: Job[] }>("/jobs"),
  runHealthRepair: () => request<{ status: string; actions: Record<string, unknown>[]; after: HealthReport }>("/jobs/health-repair", { method: "POST", body: JSON.stringify({}) }),
  runBenchmarkJob: () =>
    request<{ status: string; report: BenchmarkReport; run: BenchmarkRun }>("/jobs/benchmark", { method: "POST", body: JSON.stringify({}) }),
  runBenchmarkSuite: (payload: Record<string, unknown> = {}) =>
    request<{ status: string; report: BenchmarkReport }>("/jobs/benchmark-suite", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getBenchmarkSuiteLatest: () => request<{ status: string; report: Partial<BenchmarkReport> }>("/quality/benchmark-suite/latest"),
  runGraphRebuild: () => request<{ status: string; result: Record<string, unknown> }>("/jobs/graph-rebuild", { method: "POST" }),
  runEvalJob: (provider: string, model?: string) =>
    request<{ status: string; report: Record<string, unknown>; run: BenchmarkRun }>("/jobs/eval", {
      method: "POST",
      body: JSON.stringify({ provider, model })
    }),
  getBenchmarkRuns: (kind?: "extraction" | "qa") =>
    request<{ items: BenchmarkRun[]; total: number }>("/benchmark/runs", { query: { kind } }),
  deleteBenchmarkRun: (runId: string) =>
    request<{ deleted: boolean; id: string }>(`/benchmark/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
  clarifyQuestion: (question: string, provider?: string | null, model?: string | null) =>
    request<{ directions: string[] }>("/research/clarify", {
      method: "POST",
      body: JSON.stringify({ question, provider: provider ?? null, model: model ?? null })
    }),

  // --- Deep-research sessions (server-persisted trees) ---
  listResearchSessions: (projectId: string) =>
    request<{ sessions: ResearchSessionSummary[] }>(`/research/sessions/${encodeURIComponent(projectId)}`),
  getResearchSession: (sessionId: string) =>
    request<{ session: ResearchSession }>(`/research/session/${encodeURIComponent(sessionId)}`),
  upsertResearchSession: (
    sessionId: string,
    payload: { project_id?: string | null; question?: string; status?: string; nodes: ResearchNode[] },
  ) =>
    request<{ session: ResearchSession }>(`/research/session/${encodeURIComponent(sessionId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteResearchSession: (sessionId: string) =>
    request<{ deleted: boolean }>(`/research/session/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  // --- Parallel Research mode ---
  createParallelSession: (
    projectId: string,
    payload: { question: string; variant_count?: number; paper_ids?: string[]; provider?: string | null; model?: string | null; task_id?: string | null; creativity_level?: CreativityLevel | null },
  ) =>
    request<{ session: ParallelSession }>(`/projects/${encodeURIComponent(projectId)}/parallel`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listParallelSessions: (projectId: string) =>
    request<{ sessions: ParallelSessionSummary[] }>(`/projects/${encodeURIComponent(projectId)}/parallel`),
  getParallelSession: (sessionId: string) =>
    request<{ session: ParallelSession }>(`/parallel/${encodeURIComponent(sessionId)}`),
  deleteParallelSession: (sessionId: string) =>
    request<{ deleted: boolean }>(`/parallel/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  generateParallelVariants: (
    sessionId: string,
    payload: { variant_count?: number; stage_id?: string | null; paper_ids?: string[]; provider?: string | null; model?: string | null; task_id?: string | null; creativity_level?: CreativityLevel | null },
  ) =>
    request<{ session: ParallelSession }>(`/parallel/${encodeURIComponent(sessionId)}/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addParallelVariant: (
    sessionId: string,
    payload: { name: string; approach?: string; rationale?: string; suggested_prompt?: string; stage_id?: string | null },
  ) =>
    request<{ variant: ParallelVariant }>(`/parallel/${encodeURIComponent(sessionId)}/variants`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addParallelStage: (
    sessionId: string,
    payload: { name?: string; goal?: string; propose?: boolean; paper_ids?: string[]; provider?: string | null; model?: string | null; task_id?: string | null; creativity_level?: CreativityLevel | null },
  ) =>
    request<{ session: ParallelSession }>(`/parallel/${encodeURIComponent(sessionId)}/stages`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateParallelStage: (
    stageId: string,
    payload: Partial<{ name: string; goal: string; status: string; position: number }>,
  ) =>
    request<{ stage: ParallelStage; session: ParallelSession }>(`/parallel/stages/${encodeURIComponent(stageId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteParallelStage: (stageId: string) =>
    request<{ deleted: boolean }>(`/parallel/stages/${encodeURIComponent(stageId)}`, { method: "DELETE" }),
  reviewParallelStage: (
    stageId: string,
    payload: { paper_ids?: string[]; provider?: string | null; model?: string | null } = {},
  ) =>
    request<{ session: ParallelSession; answer: Answer }>(`/parallel/stages/${encodeURIComponent(stageId)}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateParallelVariant: (
    variantId: string,
    payload: Partial<{ name: string; approach: string; rationale: string; suggested_prompt: string; status: string; position: number; rejection_reason: string }>,
  ) =>
    request<{ variant: ParallelVariant }>(`/parallel/variants/${encodeURIComponent(variantId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteParallelVariant: (variantId: string) =>
    request<{ deleted: boolean }>(`/parallel/variants/${encodeURIComponent(variantId)}`, { method: "DELETE" }),
  addParallelEntry: (
    variantId: string,
    payload: { content: string; request_feedback?: boolean; paper_ids?: string[]; provider?: string | null; model?: string | null },
  ) =>
    request<{ session: ParallelSession; user_entry: ParallelEntry; feedback_entry: ParallelEntry | null }>(
      `/parallel/variants/${encodeURIComponent(variantId)}/entries`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  deleteParallelEntry: (entryId: string) =>
    request<{ deleted: boolean }>(`/parallel/entries/${encodeURIComponent(entryId)}`, { method: "DELETE" }),
  synthesizeParallelSession: (
    sessionId: string,
    payload: { paper_ids?: string[]; provider?: string | null; model?: string | null },
  ) =>
    request<{ session: ParallelSession; answer: Answer }>(`/parallel/${encodeURIComponent(sessionId)}/synthesize`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  askParallelFollowup: (
    sessionId: string,
    payload: {
      question: string;
      variant_count?: number;
      paper_ids?: string[];
      provider?: string | null;
      model?: string | null;
      task_id?: string | null;
      creativity_level?: CreativityLevel | null;
    },
  ) =>
    request<{ session: ParallelSession; answer: Answer }>(`/parallel/${encodeURIComponent(sessionId)}/ask`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getAgentConfig: () => request<AgentConfig>("/agent/config"),
  parallelVariantHandoff: (
    variantId: string,
    payload: { with_research_context?: boolean; paper_ids?: string[]; provider?: string | null; model?: string | null } = {},
  ) =>
    request<AgentHandoffResponse>(`/parallel/variants/${encodeURIComponent(variantId)}/handoff`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // --- Parallel: "Wie umsetzen"-Steps + Professor-Interaktion ---
  // Status-Übergänge pro Step: vorgeschlagen → in_progress → done|rejected.
  addParallelStep: (
    variantId: string,
    payload: { text: string; rationale?: string; citation?: string; origin?: "user" | "ai" },
  ) =>
    request<{ variant: ParallelVariant }>(`/parallel/variants/${encodeURIComponent(variantId)}/steps`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateParallelStep: (
    variantId: string,
    stepId: string,
    payload: Partial<{ text: string; status: "vorgeschlagen" | "in_progress" | "done" | "rejected" }>,
  ) =>
    request<{ variant: ParallelVariant }>(`/parallel/variants/${encodeURIComponent(variantId)}/steps/${encodeURIComponent(stepId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteParallelStep: (variantId: string, stepId: string) =>
    request<{ deleted: boolean }>(`/parallel/variants/${encodeURIComponent(variantId)}/steps/${encodeURIComponent(stepId)}`, { method: "DELETE" }),
  /** "Ergebnis zeigen" — Step auf done setzen, Ergebnis als parallel_entry posten,
   *  optional Professor-Feedback anfordern. */
  submitParallelStepResult: (
    variantId: string,
    stepId: string,
    payload: { result: string; request_feedback?: boolean; paper_ids?: string[]; provider?: string | null; model?: string | null },
  ) =>
    request<{ variant: ParallelVariant; answer: Answer | null }>(
      `/parallel/variants/${encodeURIComponent(variantId)}/steps/${encodeURIComponent(stepId)}/result`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  /** "Frage an Professor" — Follow-up scoped to a variant (optional step_id). */
  askParallelProfessor: (
    variantId: string,
    payload: { question: string; step_id?: string | null; paper_ids?: string[]; provider?: string | null; model?: string | null },
  ) =>
    request<{ variant: ParallelVariant; answer: Answer }>(`/parallel/variants/${encodeURIComponent(variantId)}/ask`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** "Weg nichts für mich" — Variante mit Begründung ablehnen. */
  rejectParallelVariant: (variantId: string, payload: { reason?: string } = {}) =>
    request<{ variant: ParallelVariant }>(`/parallel/variants/${encodeURIComponent(variantId)}/reject`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** Implementationsplan einer Session (Summe der akzeptierten Steps). */
  getParallelImplementationPlan: (sessionId: string) =>
    request<{ plan_markdown: string; steps: { text: string; rationale: string; citation: string }[] }>(
      `/parallel/${encodeURIComponent(sessionId)}/implementation-plan`,
    ),

  // --- Code-Werkstatt (coding projects, file tree, editor, git) ---
  werkstatt: {
    list: () => request<WorkspaceList>("/workspaces"),
    create: (name: string) =>
      request<CodeProject>("/workspaces", { method: "POST", body: JSON.stringify({ name }) }),
    open: (path: string, name?: string) =>
      request<CodeProject>("/workspaces/open", { method: "POST", body: JSON.stringify({ path, name }) }),
    remove: (projectId: string) =>
      request<{ deleted: boolean; id: string }>(`/workspaces/${encodeURIComponent(projectId)}`, { method: "DELETE" }),
    tree: (projectId: string) =>
      request<FileTreeNode>(`/workspaces/${encodeURIComponent(projectId)}/tree`),
    readFile: (projectId: string, path: string) =>
      request<FileContent>(`/workspaces/${encodeURIComponent(projectId)}/file`, { query: { path } }),
    writeFile: (projectId: string, path: string, content: string) =>
      request<{ path: string; size: number }>(`/workspaces/${encodeURIComponent(projectId)}/file`, {
        method: "PUT",
        body: JSON.stringify({ path, content }),
      }),
    createFile: (projectId: string, path: string) =>
      request<{ path: string; type: string }>(`/workspaces/${encodeURIComponent(projectId)}/file`, {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    createDir: (projectId: string, path: string) =>
      request<{ path: string; type: string }>(`/workspaces/${encodeURIComponent(projectId)}/dir`, {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    deleteFile: (projectId: string, path: string) =>
      request<{ path: string; deleted: boolean }>(`/workspaces/${encodeURIComponent(projectId)}/file`, {
        method: "DELETE",
        query: { path },
      }),
    gitStatus: (projectId: string) =>
      request<GitStatus>(`/workspaces/${encodeURIComponent(projectId)}/git/status`),
    gitDiff: (projectId: string, path?: string) =>
      request<GitDiff>(`/workspaces/${encodeURIComponent(projectId)}/git/diff`, { query: { path } }),
    listCheckpoints: (projectId: string) =>
      request<{ project_id: string; checkpoints: Checkpoint[] }>(
        `/workspaces/${encodeURIComponent(projectId)}/checkpoints`,
      ),
    createCheckpoint: (projectId: string, label: string) =>
      request<CheckpointResult>(`/workspaces/${encodeURIComponent(projectId)}/checkpoints`, {
        method: "POST",
        body: JSON.stringify({ label }),
      }),
    checkpointRestorePreview: (projectId: string, checkpointId: string) =>
      request<{ project_id: string; checkpoint: Checkpoint; plan: CheckpointRestorePlan }>(
        `/workspaces/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(checkpointId)}/restore/preview`,
      ),
    restoreCheckpoint: (projectId: string, checkpointId: string, planHash: string) =>
      request<CheckpointRestoreResult>(
        `/workspaces/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(checkpointId)}/restore`,
        { method: "POST", body: JSON.stringify({ plan_hash: planHash }) },
      ),
    checkpointDiff: (projectId: string, checkpointId: string) =>
      request<{ available: boolean; entries: CheckpointDiffEntry[]; error?: string }>(
        `/workspaces/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(checkpointId)}/diff`,
      ),
    deleteCheckpoint: (projectId: string, checkpointId: string) =>
      request<{ project_id: string; checkpoint_id: string; removed: boolean }>(
        `/workspaces/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(checkpointId)}`,
        { method: "DELETE" },
      ),
    listSandboxes: (projectId: string) =>
      request<{ project_id: string; sandboxes: Sandbox[] }>(
        `/workspaces/${encodeURIComponent(projectId)}/sandboxes`,
      ),
    createSandbox: (projectId: string, checkpointId: string, testCommand?: string) =>
      request<{ project_id: string; sandbox: Sandbox }>(
        `/workspaces/${encodeURIComponent(projectId)}/sandboxes`,
        { method: "POST", body: JSON.stringify({ checkpoint_id: checkpointId, test_command: testCommand }) },
      ),
    runSandbox: (projectId: string, sandboxId: string, command?: string, timeout = 300) =>
      request<{ project_id: string; sandbox_id: string; run: SandboxRunResult }>(
        `/workspaces/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/run`,
        { method: "POST", body: JSON.stringify({ command, timeout }) },
      ),
    sandboxDiff: (projectId: string, sandboxId: string) =>
      request<{ available: boolean; entries: CheckpointDiffEntry[]; note?: string }>(
        `/workspaces/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/diff`,
      ),
    applySandbox: (projectId: string, sandboxId: string) =>
      request<{ applied: boolean; entries?: { path: string; status: string; action: string }[] }>(
        `/workspaces/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/apply`,
        { method: "POST" },
      ),
    deleteSandbox: (projectId: string, sandboxId: string) =>
      request<{ project_id: string; sandbox_id: string; removed: boolean }>(
        `/workspaces/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}`,
        { method: "DELETE" },
      ),
  },

  // --- Code-Graph (CodeSearch: Symbole, Beziehungen, Belege) ---
  // Knoten-IDs sind immer Hex-Strings — siehe types.ts, CodeNodeId.
  codegraph: {
    status: (projectId: string) =>
      request<import("./types").CodeIndexStatus>(`/codegraph/${encodeURIComponent(projectId)}`),
    dropIndex: (projectId: string) =>
      request<{ code_project_id: string; removed: boolean }>(
        `/codegraph/${encodeURIComponent(projectId)}/index`,
        { method: "DELETE" },
      ),
    overview: (projectId: string) =>
      request<import("./types").CodeOverview>(`/codegraph/${encodeURIComponent(projectId)}/overview`),
    /**
     * Woraus besteht das Projekt — eine Ebene der Bereichskarte.
     *
     * `prefix` ist leer für die oberste Ebene und geht dann Pfadsegment für
     * Pfadsegment tiefer. Die Struktur kommt aus dem Index, nicht aus einem
     * Modell; der Name kann von einem stammen und sagt dann, dass er es tut.
     */
    clusters: (projectId: string, prefix = "", edges?: import("./types").CodeEdgeKind[]) =>
      request<import("./types").CodeClusterLevel>(
        `/codegraph/${encodeURIComponent(projectId)}/clusters`,
        { query: { prefix, edges: edges?.length ? edges.join(",") : undefined } },
      ),
    /** Die echten Kanten hinter einer aufsummierten — der Beleg zur Zahl am Pfeil. */
    clusterEdge: (
      projectId: string,
      from: string,
      to: string,
      edges?: import("./types").CodeEdgeKind[],
      limit = 60,
    ) =>
      request<import("./types").CodeNeighbour[]>(
        `/codegraph/${encodeURIComponent(projectId)}/clusters/edge`,
        { query: { from, to, edges: edges?.length ? edges.join(",") : undefined, limit } },
      ),
    /** Die wichtigsten Symbole eines Bereichs — der Übergang zur Symbolkarte. */
    clusterMembers: (projectId: string, prefix: string, limit = 30) =>
      request<import("./types").CodeSymbolHit[]>(
        `/codegraph/${encodeURIComponent(projectId)}/clusters/members`,
        { query: { prefix, limit } },
      ),
    // --- Gespräche über den Code ---
    chats: (projectId: string, limit = 50) =>
      request<{ code_project_id: string; chats: import("./types").CodeChat[] }>(
        `/codegraph/${encodeURIComponent(projectId)}/chats`,
        { query: { limit } },
      ),
    createChat: (projectId: string, payload: { title?: string; project_id?: string | null }) =>
      request<import("./types").CodeChat>(`/codegraph/${encodeURIComponent(projectId)}/chats`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    chat: (projectId: string, chatId: string) =>
      request<{ chat: import("./types").CodeChat; turns: import("./types").CodeChatTurn[] }>(
        `/codegraph/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}`,
      ),
    removeChat: (projectId: string, chatId: string) =>
      request<{ id: string; deleted: boolean }>(
        `/codegraph/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}`,
        { method: "DELETE" },
      ),
    search: (projectId: string, q: string, kind?: string, limit = 40) =>
      request<import("./types").CodeSymbolHit[]>(`/codegraph/${encodeURIComponent(projectId)}/search`, {
        query: { q, kind, limit },
      }),
    searchText: (projectId: string, q: string, limit = 20) =>
      request<import("./types").CodeTextHit[]>(`/codegraph/${encodeURIComponent(projectId)}/search/text`, {
        query: { q, limit },
      }),
    node: (projectId: string, nodeId: string) =>
      request<import("./types").CodeNodeDetail>(
        `/codegraph/${encodeURIComponent(projectId)}/node/${encodeURIComponent(nodeId)}`,
      ),
    blueprint: (projectId: string, nodeId: string) =>
      request<import("./types").CodeBlueprint>(
        `/codegraph/${encodeURIComponent(projectId)}/blueprint/${encodeURIComponent(nodeId)}`,
      ),
    /**
     * Ausschnitt um ein Symbol, für die Karte.
     *
     * `direction` steht bewusst auf `"out"` statt `"both"`: `Graph::slice` trägt
     * im Both-Zweig auch eingehende Kanten als ausgehende ein
     * (cs-graph/src/query.rs:411) — in einer Liste unauffällig, auf einer
     * Pfeilkarte falsch. Wer beide Richtungen braucht, fragt zweimal.
     */
    slice: (
      projectId: string,
      nodeId: string,
      opts: {
        depth?: number;
        direction?: import("./types").CodeDirection;
        budget?: number;
        edges?: import("./types").CodeEdgeKind[];
      } = {},
    ) =>
      request<import("./types").CodeGraphSlice>(
        `/codegraph/${encodeURIComponent(projectId)}/slice/${encodeURIComponent(nodeId)}`,
        {
          query: {
            depth: opts.depth ?? 1,
            direction: opts.direction ?? "out",
            budget: opts.budget ?? 400,
            edges: opts.edges?.length ? opts.edges.join(",") : undefined,
          },
        },
      ),
    /** Ein Sprung über *alle* Kantenarten — `blueprint` kennt nur drei. */
    neighbours: (
      projectId: string,
      nodeId: string,
      opts: {
        direction?: import("./types").CodeDirection;
        edges?: import("./types").CodeEdgeKind[] | "all";
      } = {},
    ) =>
      request<import("./types").CodeNeighbour[]>(
        `/codegraph/${encodeURIComponent(projectId)}/neighbours/${encodeURIComponent(nodeId)}`,
        {
          query: {
            direction: opts.direction ?? "both",
            edges: opts.edges === "all" ? "all" : opts.edges?.length ? opts.edges.join(",") : undefined,
          },
        },
      ),
    /** Kürzester **Aufruf**pfad. `path: null` heißt nicht „keine Beziehung". */
    path: (projectId: string, fromId: string, toId: string, maxDepth = 12) =>
      request<import("./types").CodePathResult>(`/codegraph/${encodeURIComponent(projectId)}/path`, {
        query: { from: fromId, to: toId, max_depth: maxDepth },
      }),
    /** Auswirkungsanalyse — wer wird von einer Änderung mitrissen (vor der Ausführung). */
    impact: (
      projectId: string,
      nodeId: string,
      opts: { depth?: number; budget?: number; edges?: import("./types").CodeEdgeKind[] } = {},
    ) =>
      request<import("./types").CodeImpact>(
        `/codegraph/${encodeURIComponent(projectId)}/impact/${encodeURIComponent(nodeId)}`,
        {
          query: {
            depth: opts.depth ?? 3,
            budget: opts.budget ?? 400,
            edges: opts.edges?.length ? opts.edges.join(",") : undefined,
          },
        },
      ),
    /** Spaghetti-Hotspots — Symbole, die eine gemessene Regel reißen. Ohne LLM. */
    hotspots: (
      projectId: string,
      opts: {
        loc?: number; complexity?: number; max_nesting?: number;
        fan_in?: number; fan_out?: number; churn?: number; limit?: number;
      } = {},
    ) =>
      request<import("./types").Hotspot[]>(
        `/codegraph/${encodeURIComponent(projectId)}/hotspots`,
        {
          query: {
            loc: opts.loc ?? 200, complexity: opts.complexity ?? 15,
            max_nesting: opts.max_nesting ?? 5, fan_in: opts.fan_in ?? 30,
            fan_out: opts.fan_out ?? 25, churn: opts.churn ?? 0, limit: opts.limit ?? 50,
          },
        },
      ),
    /** Ringe im Abhängigkeitsgraph — Tarjan-SCC mit Belegkanten. Ohne LLM. */
    cycles: (
      projectId: string,
      opts: { level?: "file" | "symbol"; edges?: import("./types").CodeEdgeKind[] } = {},
    ) =>
      request<{ code_project_id: string; level: string; cycles: import("./types").Cycle[] }>(
        `/codegraph/${encodeURIComponent(projectId)}/cycles`,
        {
          query: {
            level: opts.level ?? "file",
            edges: opts.edges?.length ? opts.edges.join(",") : undefined,
          },
        },
      ),
    /** Wichtigste Symbole je Art — auch die zwölf, die `overview` weglässt. */
    top: (projectId: string, kinds?: import("./types").CodeNodeKind[], limit = 30) =>
      request<import("./types").CodeSymbolHit[]>(`/codegraph/${encodeURIComponent(projectId)}/top`, {
        query: { kind: kinds?.length ? kinds.join(",") : undefined, limit },
      }),
    diagram: (projectId: string, nodeId: string, kind: "class" | "sequence", depth = 2) =>
      request<import("./types").CodeDiagram>(
        `/codegraph/${encodeURIComponent(projectId)}/diagram/${encodeURIComponent(nodeId)}`,
        { query: { kind, depth } },
      ),
    source: (projectId: string, path: string) =>
      request<import("./types").CodeSourceText>(`/codegraph/${encodeURIComponent(projectId)}/source`, {
        query: { path },
      }),
    /** Die aufgezeichnete Hälfte von „warum so gebaut" — git und eigene Notizen. */
    recordedRationale: (projectId: string, nodeId: string) =>
      request<import("./types").CodeRecordedRationale>(
        `/codegraph/${encodeURIComponent(projectId)}/why/${encodeURIComponent(nodeId)}`,
      ),
    addRationale: (
      projectId: string,
      payload: { text: string; symbol_id?: string; rel_path?: string; author?: string },
    ) =>
      request<import("./types").CodeRationale>(
        `/codegraph/${encodeURIComponent(projectId)}/rationale`,
        { method: "POST", body: JSON.stringify(payload) },
      ),
    removeRationale: (projectId: string, rationaleId: string) =>
      request<{ id: string; deleted: boolean }>(
        `/codegraph/${encodeURIComponent(projectId)}/rationale/${encodeURIComponent(rationaleId)}`,
        { method: "DELETE" },
      ),
    /** Nur die Zeilen einer Funktion — samt Hash als optimistischer Sperre. */
    symbolSource: (projectId: string, nodeId: string) =>
      request<import("./types").CodeSymbolSource>(
        `/codegraph/${encodeURIComponent(projectId)}/symbol/${encodeURIComponent(nodeId)}/source`,
      ),
    /**
     * Dieselben Zeilen zurückschreiben.
     *
     * Der Zeilenbereich wird **nicht** mitgeschickt: er kommt im Backend aus dem
     * Graphen. Ein Panel, das eine Weile offen stand, kennt womöglich einen
     * alten Bereich und schriebe an die falsche Stelle.
     */
    writeSymbolSource: (
      projectId: string,
      nodeId: string,
      payload: { text: string; content_hash: string },
    ) =>
      request<import("./types").CodeSymbolWrite>(
        `/codegraph/${encodeURIComponent(projectId)}/symbol/${encodeURIComponent(nodeId)}/source`,
        { method: "PATCH", body: JSON.stringify(payload) },
      ),
    positions: (projectId: string, text: string, limit = 8) =>
      request<import("./types").CodeTerminalPosition[]>(
        `/codegraph/${encodeURIComponent(projectId)}/positions`,
        { method: "POST", body: JSON.stringify({ text, limit }) },
      ),
    cite: (
      projectId: string,
      payload: {
        note_id: string;
        rel_path: string;
        start_line: number;
        end_line?: number;
        title?: string;
        reference_text?: string;
      },
    ) =>
      request<import("./types").CodeNoteCitation>(
        `/codegraph/${encodeURIComponent(projectId)}/cite`,
        { method: "POST", body: JSON.stringify(payload) },
      ),
    citations: (projectId: string, noteId: string) =>
      request<{ code_project_id: string; note_id: string; citations: import("./types").CodeNoteCitation[] }>(
        `/codegraph/${encodeURIComponent(projectId)}/citations`,
        { query: { note_id: noteId } },
      ),
    answers: (projectId: string, limit = 30) =>
      request<{ code_project_id: string; answers: import("./types").CodeAnswerRecord[] }>(
        `/codegraph/${encodeURIComponent(projectId)}/answers`,
        { query: { limit } },
      ),
    removeAnswer: (projectId: string, answerId: string) =>
      request<{ id: string; deleted: boolean }>(
        `/codegraph/${encodeURIComponent(projectId)}/answers/${encodeURIComponent(answerId)}`,
        { method: "DELETE" },
      ),
    links: (projectId: string, researchProjectId?: string | null) =>
      request<{ code_project_id: string; links: import("./types").CodePaperLink[] }>(
        `/codegraph/${encodeURIComponent(projectId)}/links`,
        { query: { project_id: researchProjectId ?? undefined } },
      ),
    addLink: (projectId: string, payload: Partial<import("./types").CodePaperLink>) =>
      request<import("./types").CodePaperLink>(`/codegraph/${encodeURIComponent(projectId)}/links`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    removeLink: (projectId: string, linkId: string) =>
      request<{ id: string; deleted: boolean }>(
        `/codegraph/${encodeURIComponent(projectId)}/links/${encodeURIComponent(linkId)}`,
        { method: "DELETE" },
      ),
  },

  // --- Analyse-Werkstatt (reproduzierbare, provenance-tragende Skript-Läufe) ---
  analysis: {
    list: (projectId?: string | null) =>
      request<{ runs: AnalysisRun[] }>("/analysis/runs", { query: { project_id: projectId ?? undefined } }),
    get: (runId: string) =>
      request<{ run: AnalysisRun }>(`/analysis/runs/${encodeURIComponent(runId)}`),
    create: (payload: AnalysisRunRequest) =>
      request<{ run: AnalysisRun }>("/analysis/runs", { method: "POST", body: JSON.stringify(payload) }),
    revise: (runId: string, payload: AnalysisReviseRequest) =>
      request<{ run: AnalysisRun }>(`/analysis/runs/${encodeURIComponent(runId)}/revise`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    verify: (runId: string) =>
      request<{ verification: { reproducible: boolean; expected: string; actual: string; ok: boolean; stderr: string } }>(
        `/analysis/runs/${encodeURIComponent(runId)}/verify`,
        { method: "POST" }
      ),
    remove: (runId: string) =>
      request<{ deleted: boolean; id: string }>(`/analysis/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
    /** Absolute URL of one generated artifact file (figure/table/data/log). */
    artifactUrl: (artifactId: string) => url(`/analysis/artifacts/${encodeURIComponent(artifactId)}`),
  },

  // --- Datensätze (freie Registries: Zenodo/Figshare/Dryad/ClinicalTrials/PWC) ---
  datasets: {
    sources: () => request<{ sources: DatasetSource[]; default: string[] }>("/datasets/sources"),
    /** Login-Badges pro Quelle (Kaggle authenticated? username? hint?). */
    sourcesStatus: () => request<{ sources: DatasetSourceStatus[] }>("/datasets/sources/status"),
    search: (payload: { query: string; sources?: string[]; per_source?: number }) =>
      request<{ results: DatasetHit[]; warnings: string[] }>("/datasets/search", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    import: (datasets: DatasetHit[], projectId?: string | null, linkedPaperId?: string | null) =>
      request<{ imported: Dataset[]; count: number }>("/datasets/import", {
        method: "POST",
        body: JSON.stringify({ datasets, project_id: projectId ?? null, linked_paper_id: linkedPaperId ?? null }),
      }),
    list: (projectId?: string | null) =>
      request<{ datasets: Dataset[] }>("/datasets", { query: { project_id: projectId ?? undefined } }),
    details: (source: string, externalId: string) =>
      request<DatasetDetails>("/datasets/details", { query: { source, external_id: externalId } }),
    /** Download einer einzelnen Datei (Kaggle auth-pflichtig — schlägt ohne Login fehl). */
    download: (payload: DatasetDownloadRequest) =>
      request<DatasetDownloadResponse>("/datasets/download", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<{ deleted: boolean; id: string }>(`/datasets/${encodeURIComponent(id)}`, { method: "DELETE" }),
  },

  // --- Task-Focused Mode: Task-Specs pro Projekt ---
  tasks: {
    list: (projectId: string) =>
      request<{ project_id: string; tasks: Task[] }>(`/projects/${encodeURIComponent(projectId)}/tasks`),
    get: (taskId: string) => request<Task>(`/tasks/${encodeURIComponent(taskId)}`),
    create: (
      projectId: string,
      payload: { title?: string; task_json?: Partial<TaskSpec>; source_kind?: string; source_url?: string | null; source_pdf_path?: string | null },
    ) =>
      request<Task>(`/projects/${encodeURIComponent(projectId)}/tasks`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (taskId: string, payload: { title?: string; task_json?: Partial<TaskSpec> }) =>
      request<Task>(`/tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (taskId: string) =>
      request<{ deleted: boolean; id: string }>(`/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" }),
    /** LLM-Extraktion ohne Speichern (Vorschau). */
    extract: (payload: { source_kind: string; source_url?: string | null; source_text?: string | null; source_pdf_path?: string | null; title?: string | null; provider?: string | null; model?: string | null }) =>
      request<TaskSpec>("/tasks/extract", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    /** LLM-Extraktion + sofortiges Speichern im Projekt. */
    ingest: (
      projectId: string,
      payload: { source_kind: string; source_url?: string | null; source_text?: string | null; source_pdf_path?: string | null; title?: string | null; provider?: string | null; model?: string | null },
    ) =>
      request<Task>(`/projects/${encodeURIComponent(projectId)}/tasks/ingest`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    /** Forschungsrichtungen vorschlagen (Kreativitätsstufe 1–5). */
    suggestDirections: (
      taskId: string,
      payload: { creativity_level?: CreativityLevel; provider?: string | null; model?: string | null },
    ) =>
      request<TaskSuggestDirectionsResponse>(`/tasks/${encodeURIComponent(taskId)}/suggest-directions`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    /** Implementationsplan für eine Richtung erzeugen. */
    plan: (
      taskId: string,
      payload: { direction: TaskResearchDirection; kg_context?: string; creativity_level?: CreativityLevel; provider?: string | null; model?: string | null },
    ) =>
      request<TaskImplementationPlan>(`/tasks/${encodeURIComponent(taskId)}/plan`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    /** Task-Spec als zitierfähige Grey-Source veröffentlichen (``grey::task_{id}``). */
    publishAsGreySource: (taskId: string) =>
      request<TaskGreySourceResponse>(`/tasks/${encodeURIComponent(taskId)}/as-grey-source`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
    /** Stream POST /tasks/{id}/deep-search: per-research-direction Tiefensuche (SSE).
     * Erntet viele Paper + Web-Quellen und synthetisiert ein geerdetes
     * Möglichkeitsprinzip (Machbarkeit/Ansätze/Risiken/Fazit). Selbe SSE-Reader-
     * Bauweise wie streamResearchTree — fetch POST + getReader() + TextDecoder
     * + split auf "\n" + parse "data:"-Zeilen. */
    streamDeepSearch: async (
      taskId: string,
      payload: TaskDeepSearchRequest,
      onEvent: (event: TaskDeepSearchEvent) => void,
      signal?: AbortSignal,
    ): Promise<void> => {
      const target = new URL(`/tasks/${encodeURIComponent(taskId)}/deep-search`, API_BASE_URL);
      let response: Response;
      try {
        response = await fetch(target.toString(), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
          signal,
        });
      } catch (error) {
        const reason = error instanceof Error ? error.message : "";
        throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
      }
      if (!response.ok) {
        const text = await response.text();
        throw new ApiError(response.status, text || `Backend error ${response.status}`);
      }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              onEvent(JSON.parse(line.slice(6)) as TaskDeepSearchEvent);
            } catch {
              // malformed SSE line – skip
            }
          }
        }
      }
    },
  },

  // --- Kaggle-Login (Credentials in .env, gitignored) ---
  getKaggleStatus: () => request<KaggleStatus>("/settings/kaggle"),
  loginKaggle: (payload: KaggleLoginRequest) =>
    request<KaggleLoginResponse>("/settings/kaggle", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logoutKaggle: () =>
    request<{ ok: boolean; hint: string }>("/settings/kaggle", { method: "DELETE" }),

  // --- PDF-Notizen (an Textstelle/Punkt im PDF verankert, persistent pro Paper) ---
  pdfAnnotations: {
    list: (paperId: string) =>
      request<{ annotations: PdfAnnotation[] }>(`/papers/${encodeURIComponent(paperId)}/annotations`),
    create: (
      paperId: string,
      payload: {
        page_number: number;
        kind: "highlight" | "point";
        rects: PdfAnnotationRect[];
        quote?: string | null;
        body?: string;
        color?: string | null;
      }
    ) =>
      request<{ annotation: PdfAnnotation }>(`/papers/${encodeURIComponent(paperId)}/annotations`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (
      annotationId: string,
      patch: { body?: string; color?: string | null; kind?: string; quote?: string | null; rects?: PdfAnnotationRect[] }
    ) =>
      request<{ annotation: PdfAnnotation }>(`/pdf-annotations/${encodeURIComponent(annotationId)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    remove: (annotationId: string) =>
      request<{ deleted: boolean; id: string }>(`/pdf-annotations/${encodeURIComponent(annotationId)}`, {
        method: "DELETE",
      }),
  }
};

export interface AnalysisRunRequest {
  request: string;
  project_id?: string | null;
  provider?: string | null;
  model?: string | null;
  paper_ids?: string[];
  dataset_ids?: string[];
  context?: string | null;
  /** Werkstatt-Projekt, dessen Code-Graph als Planer-Kontext dazukommt. */
  code_project_id?: string | null;
}

export interface AnalysisReviseRequest {
  request?: string | null;
  annotation?: string | null;
  provider?: string | null;
  model?: string | null;
  context?: string | null;
}

export interface ResearchTreeRequest {
  question: string;
  project_id?: string | null;
  depth?: number;
  branches?: number;
  provider?: string | null;
  model?: string | null;
  paper_ids?: string[];
  grey_source_ids?: string[];
  include_project_grey?: boolean;
  auto_harvest?: boolean;
  initial_nodes?: import("./types").ResearchNode[];
  session_id?: string | null;
}

export interface AutoAnswerRequest {
  question: string;
  /** Clean question used for paper/web search + related-topic analysis (no verbosity hint). */
  search_question?: string;
  project_id?: string | null;
  provider?: string;
  model?: string;
  limit?: number;
  paper_ids?: string[];
  priority_paper_ids?: string[];
  answer_context_mode?: "kg" | "pdf_if_fits";
  conversation_context?: Array<{ role: string; content: string }>;
  grey_source_ids?: string[];
  include_project_grey?: boolean;
  llm_overrides?: Record<string, number | undefined>;
  force?: boolean;
  max_related_topics?: number;
}

/** Stream POST /query/auto-answer: answer, then auto-harvest papers+web if weak, then re-answer. */
export async function streamAutoAnswer(
  payload: AutoAnswerRequest,
  onEvent: (event: import("./types").AutoAnswerEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const target = new URL("/query/auto-answer", API_BASE_URL);
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `Backend error ${response.status}`);
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)) as import("./types").AutoAnswerEvent);
        } catch {
          // malformed SSE line – skip
        }
      }
    }
  }
}

export async function streamResearchTree(
  payload: ResearchTreeRequest,
  onNode: (node: ResearchNode) => void,
  signal?: AbortSignal,
): Promise<void> {
  const target = new URL("/research/tree", API_BASE_URL);
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `Backend error ${response.status}`);
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          onNode(JSON.parse(line.slice(6)) as ResearchNode);
        } catch {
          // malformed SSE line – skip
        }
      }
    }
  }
}

/** Stream POST /agent/dispatch: forward a task brief to the local desktop-agent bridge and
 * receive its run progress as events. Best-effort — a disabled/unreachable bridge yields a
 * single `{status:"error"}` event rather than throwing. */
export async function streamAgentDispatch(
  payload: { task: string; variant_id?: string | null; bridge_url?: string | null },
  onEvent: (event: import("./types").AgentDispatchEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const target = new URL("/agent/dispatch", API_BASE_URL);
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `Backend error ${response.status}`);
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)) as import("./types").AgentDispatchEvent);
        } catch {
          // malformed SSE line – skip
        }
      }
    }
  }
}

/** Stream POST /agent/observe/start: begin an Assistent (helper) session — periodic
 * screen descriptions from the bridge, relayed as SSE. Screenshots never leave the
 * bridge process; only short text descriptions arrive here. */
export async function streamObserve(
  payload: {
    session_id?: string | null;
    interval_ms?: number | null;
    primer?: string;
    bridge_base?: string | null;
  },
  onEvent: (event: import("./types").ObserveEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const target = new URL("/agent/observe/start", API_BASE_URL);
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `Backend error ${response.status}`);
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)) as import("./types").ObserveEvent);
        } catch {
          // malformed SSE line – skip
        }
      }
    }
  }
}

/** POST /agent/observe/ask: ask a live question against an active Assistent session. */
export const askObserve = (payload: { session_id: string; question: string; bridge_base?: string | null }) =>
  request<{ answer?: string; error?: string }>("/agent/observe/ask", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /agent/observe/point: locate a UI element for the Assistent's pointer overlay
 * ("zeig mir wo ich klicken kann"). Returns real screen coordinates — never dispatches
 * mouse/keyboard input, the overlay only draws a highlight there. */
export const askObservePoint = (payload: { session_id: string; question: string; bridge_base?: string | null }) =>
  request<import("./types").ObservePointResult>("/agent/observe/point", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /agent/observe/stop: stop an active Assistent observation session. */
export const stopObserve = (payload: { session_id: string; bridge_base?: string | null }) =>
  request<{ ok: boolean; error?: string }>("/agent/observe/stop", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /agent/cancel: gracefully abort an in-flight Selbst-Steuerung run. */
export const cancelAgent = (payload: { run_id: string; bridge_base?: string | null }) =>
  request<{ ok: boolean; error?: string }>("/agent/cancel", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /companion/guide: Desktop-Companion answer + optional click-guidance steps for
 * a full screenshot. Step coordinates come back in physical monitor pixels — the
 * pointer overlay only *shows* them; nothing ever clicks. */
export const guideCompanion = (payload: {
  question: string;
  image_base64: string;
  history?: { role: string; content: string }[];
  provider?: string | null;
  model?: string | null;
  use_papers?: boolean;
  use_web?: boolean;
  /** Code-Graph eines Werkstatt-Projekts als dritte Quelle. */
  use_code?: boolean;
  code_project_id?: string | null;
  session_id?: string | null;
}) =>
  request<CompanionGuideResult>("/companion/guide", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /companion/ask: free-form Desktop-Companion screen Q&A — used for
 * "Bereich erklären" snips (`region: true`) and text-only follow-up questions. */
export const askCompanion = (payload: {
  question: string;
  image_base64?: string | null;
  history?: { role: string; content: string }[];
  region?: boolean;
  provider?: string | null;
  model?: string | null;
  use_papers?: boolean;
  use_web?: boolean;
  /** Code-Graph eines Werkstatt-Projekts als dritte Quelle. */
  use_code?: boolean;
  code_project_id?: string | null;
  session_id?: string | null;
}) =>
  request<CompanionAskResult>("/companion/ask", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** GET /companion/config: companion defaults + selectable vision providers/models. */
export const getCompanionConfig = () => request<CompanionConfigInfo>("/companion/config");

/** Companion/Selbst-Steuerung sessions: durable chat + step log (DuckDB). */
export const listCompanionSessions = (kind?: "companion" | "selfdrive") =>
  request<{ sessions: import("./types").CompanionSessionSummary[] }>(
    `/companion/sessions${kind ? `?kind=${kind}` : ""}`,
  );

export const getCompanionSession = (sessionId: string) =>
  request<import("./types").CompanionSessionDetail>(`/companion/sessions/${sessionId}`);

export const createCompanionSession = (payload: {
  kind: "companion" | "selfdrive";
  title?: string;
  goal?: string;
  provider?: string | null;
  model?: string | null;
  monitor?: number | null;
}) =>
  request<import("./types").CompanionSessionDetail>("/companion/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateCompanionSession = (sessionId: string, payload: { title?: string; status?: string }) =>
  request<import("./types").CompanionSessionDetail>(`/companion/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteCompanionSession = (sessionId: string) =>
  request<{ deleted: boolean }>(`/companion/sessions/${sessionId}`, { method: "DELETE" });

/** Incremental guided sequences (auto-advance): start → step (per screenshot) → stop. */
export const startGuide = (payload: {
  goal: string;
  provider?: string | null;
  model?: string | null;
  monitor?: number | null;
  use_papers?: boolean;
  use_web?: boolean;
  /** Code-Graph eines Werkstatt-Projekts als dritte Quelle. */
  use_code?: boolean;
  code_project_id?: string | null;
  session_id?: string | null;
}) =>
  request<import("./types").GuideStartResult>("/companion/guide/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const stepGuide = (payload: {
  guide_id: string;
  image_base64: string;
  event: "start" | "click" | "skip";
  click_x?: number | null;
  click_y?: number | null;
}) =>
  request<import("./types").GuideStepResult>("/companion/guide/step", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const stopGuide = (payload: { guide_id: string }) =>
  request<{ stopped: boolean }>("/companion/guide/stop", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /selfdrive/start: open a native Selbst-Steuerung session (R7). Gated on
 * companion.self_drive.enabled; the shell still needs an explicit arm (autopilot
 * or per-action confirmation). */
export const startSelfDrive = (payload: {
  goal: string;
  monitor?: number | null;
  provider?: string | null;
  model?: string | null;
  session_id?: string | null;
}) =>
  request<SelfDriveStartResult>("/selfdrive/start", { method: "POST", body: JSON.stringify(payload) });

/** POST /selfdrive/step: plan the next action from the current screenshot
 * (verify → stall-check → plan → refine pipeline; lookups resolve server-side). */
export const stepSelfDrive = (payload: { session_id: string; image_base64: string }) =>
  request<SelfDriveStepResult>("/selfdrive/step", { method: "POST", body: JSON.stringify(payload) });

/** POST /selfdrive/answer: reply to an `ask` action; resume via /selfdrive/step. */
export const answerSelfDrive = (payload: { session_id: string; answer: string }) =>
  request<{ ok?: boolean; error?: string }>("/selfdrive/answer", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** POST /selfdrive/stop: drop a session (idempotent). */
export const stopSelfDrive = (payload: { session_id: string }) =>
  request<{ stopped: boolean }>("/selfdrive/stop", { method: "POST", body: JSON.stringify(payload) });

export interface ResearchTreeExportOptions {
  tikz_tree: boolean;
  charts: boolean;
  tables: boolean;
  comfyui_images: boolean;
}

export interface ResearchTreeExportRequest {
  root_question: string;
  document: string;
  nodes: ResearchNode[];
  sources: import("./types").VerificationSource[];
  format: "pdf" | "zip" | "tex";
  options: ResearchTreeExportOptions;
  provider?: string | null;
  model?: string | null;
}

export interface ResearchTreeExportResult {
  blob: Blob;
  filename: string;
  /** The format actually produced — may be "zip" even if "pdf" was requested (fallback). */
  format: string;
  warnings: string[];
}

/** Build & download the Tiefenanalyse synthesis as a LaTeX PDF / .tex / .zip. */
export async function exportResearchTree(
  payload: ResearchTreeExportRequest,
  signal?: AbortSignal,
): Promise<ResearchTreeExportResult> {
  const target = new URL("/research/tree/export", API_BASE_URL);
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `Backend error ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] ?? "tiefenanalyse";
  const format = response.headers.get("X-Export-Format") ?? filename.split(".").pop() ?? "";
  let warnings: string[] = [];
  try {
    warnings = JSON.parse(response.headers.get("X-Export-Warnings") ?? "[]") as string[];
  } catch {
    warnings = [];
  }
  return { blob, filename, format, warnings };
}

// --------------------------------------------------------------------------- //
// Projekt-Bundles: ein Projekt exportieren / importieren                       //
// --------------------------------------------------------------------------- //

export type BundlePreview = {
  project: string;
  exported_at: string;
  app_version: string;
  bundle_version: number;
  includes_pdfs: boolean;
  counts: Record<string, number>;
  papers_existing: number;
  papers_new: number;
  project_exists: boolean;
  warnings: string[];
};

export type BundleImportReport = {
  project: string;
  mode: string;
  papers_imported: number;
  papers_skipped: number;
  extractions_imported: number;
  grey_sources_imported: number;
  embeddings_imported: number;
  pdfs_imported: number;
  paper_count: number;
  warnings: string[];
};

/** Lädt das Projekt-Bundle als ZIP herunter (inkl. Dateiname aus dem Header). */
export async function exportProjectBundle(
  projectId: string,
  includePdfs = false,
): Promise<{ blob: Blob; filename: string }> {
  const target = new URL(`/projects/${encodeURIComponent(projectId)}/export`, API_BASE_URL);
  target.searchParams.set("include_pdfs", String(includePdfs));
  let response: Response;
  try {
    response = await fetch(target.toString());
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text()) || `Backend error ${response.status}`);
  }
  const blob = await response.blob();
  const match = /filename="?([^"]+)"?/.exec(response.headers.get("Content-Disposition") ?? "");
  return { blob, filename: match?.[1] ?? `paperkg-export_${projectId}.zip` };
}

/** Schickt das ZIP als Rohkörper — der Router streamt es in eine Temp-Datei. */
async function postBundle<T>(path: string, file: File, query: Record<string, string>): Promise<T> {
  const target = new URL(path, API_BASE_URL);
  Object.entries(query).forEach(([key, value]) => value && target.searchParams.set(key, value));
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      headers: { "content-type": "application/zip" },
      body: file,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  const payload = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : payload;
    throw new ApiError(response.status, detail || `Backend error ${response.status}`);
  }
  return payload as T;
}

export function previewProjectBundle(file: File) {
  return postBundle<{ preview: BundlePreview }>("/bundles/preview", file, {});
}

export function importProjectBundle(file: File, mode: "merge" | "replace", targetProject?: string) {
  return postBundle<{ report: BundleImportReport }>("/bundles/import", file, {
    mode,
    ...(targetProject ? { target_project: targetProject } : {}),
  });
}

/** Stream POST /codegraph/{id}/index: Indizieren mit Fortschritt.
 *
 * Ein großes Repository braucht Sekunden bis Minuten. Ohne die Zwischenmeldungen
 * wäre das von einem Hänger nicht zu unterscheiden — deshalb SSE und nicht ein
 * langes POST, das irgendwann antwortet. */
export async function streamCodeGraphIndex(
  projectId: string,
  onEvent: (event: import("./types").CodeIndexEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(`/codegraph/${encodeURIComponent(projectId)}/index`, undefined, onEvent, signal);
}

/** Erklärung zu einem Symbol. Wie `streamCodeAsk`, nur ohne Frage. */
export async function streamCodeExplain(
  projectId: string,
  nodeId: string,
  payload: { provider?: string | null; model?: string | null },
  onEvent: (event: import("./types").CodeExplainEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(
    `/codegraph/${encodeURIComponent(projectId)}/explain/${encodeURIComponent(nodeId)}`,
    payload,
    onEvent as (event: unknown) => void,
    signal,
  );
}

/** Stream POST /codegraph/{id}/refactor/{nodeId}: ein Refactor-Vorschlag (geprüft). */
export async function streamCodeRefactorPropose(
  projectId: string,
  nodeId: string,
  payload: { provider?: string | null; model?: string | null },
  onEvent: (event: import("./types").CodeRefactorEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(
    `/codegraph/${encodeURIComponent(projectId)}/refactor/${encodeURIComponent(nodeId)}`,
    payload,
    onEvent as (event: unknown) => void,
    signal,
  );
}

/** Stream POST /codegraph/{id}/refactor/{nodeId}/try: Vorschlag in Sandbox anwenden + testen. */
export async function streamCodeRefactorTry(
  projectId: string,
  nodeId: string,
  payload: {
    proposal: import("./types").RefactorProposal;
    sandbox_id?: string | null;
    test_command?: string | null;
    timeout?: number;
  },
  onEvent: (event: import("./types").CodeRefactorTryEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(
    `/codegraph/${encodeURIComponent(projectId)}/refactor/${encodeURIComponent(nodeId)}/try`,
    payload,
    onEvent as (event: unknown) => void,
    signal,
  );
}

/**
 * Die *hergeleitete* Hälfte von „warum wurde das so gebaut".
 *
 * Die aufgezeichnete Hälfte holt `api.codegraph.recordedRationale` — sie braucht
 * kein Modell und steht deshalb sofort da, auch ohne Netz. Getrennt zu halten
 * ist der Punkt der ganzen Funktion: eine Herleitung darf nie aussehen wie ein
 * Beleg.
 */
export async function streamCodeWhy(
  projectId: string,
  nodeId: string,
  payload: { provider?: string | null; model?: string | null },
  onEvent: (event: import("./types").CodeWhyEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(
    `/codegraph/${encodeURIComponent(projectId)}/why/${encodeURIComponent(nodeId)}`,
    payload,
    onEvent as (event: unknown) => void,
    signal,
  );
}

/** Stream POST /codegraph/{id}/ask: der Begleiter, während er nachschlägt.
 *
 * Die Aktivitätszeilen sind der Grund für den Strom. Beim Zusehen, *wo* der
 * Begleiter nachsieht, entsteht das Vertrauen in die Antwort — ein Spinner sagt
 * „warte", diese Zeilen sagen „ich habe hier nachgesehen". */
export async function streamCodeAsk(
  projectId: string,
  payload: {
    question: string;
    session?: string;
    provider?: string | null;
    model?: string | null;
    project_id?: string | null;
    paper_ids?: string[] | null;
    use_papers?: boolean;
  },
  onEvent: (event: import("./types").CodeAskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(`/codegraph/${encodeURIComponent(projectId)}/ask`, payload, onEvent, signal);
}

/** Stream POST /codegraph/{id}/chats/{chatId}/ask: ein Zug im Gespräch.
 *
 * Wie `streamCodeAsk`, nur dass der Verlauf serverseitig aus den bisherigen
 * Zügen kommt — der Client schickt ihn nicht mit, damit „was durfte zitiert
 * werden" nicht vom Client behauptet werden kann. */
export async function streamCodeChat(
  projectId: string,
  chatId: string,
  payload: {
    question: string;
    provider?: string | null;
    model?: string | null;
    project_id?: string | null;
    paper_ids?: string[] | null;
    use_papers?: boolean;
  },
  onEvent: (event: import("./types").CodeAskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(
    `/codegraph/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/ask`,
    payload,
    onEvent,
    signal,
  );
}

/** Stream POST /codegraph/{id}/clusters/name: eine Ebene benennen lassen.
 *
 * Die Karte wartet nicht darauf. Sie steht mit Ordnernamen, und die Namen des
 * Modells kommen nach — fällt das Modell aus, fehlt nur die Beschriftung. */
export async function streamClusterNames(
  projectId: string,
  payload: { prefix?: string; provider?: string | null; model?: string | null; force?: boolean },
  onEvent: (event: import("./types").CodeClusterNameEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamCodeGraphSse(
    `/codegraph/${encodeURIComponent(projectId)}/clusters/name`,
    payload,
    onEvent,
    signal,
  );
}

/** Gemeinsames SSE-Lesen für beide Code-Graph-Ströme. */
async function streamCodeGraphSse<T>(
  path: string,
  body: unknown | undefined,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const target = new URL(path, API_BASE_URL);
  let response: Response;
  try {
    response = await fetch(target.toString(), {
      method: "POST",
      signal,
      ...(body === undefined
        ? {}
        : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    throw new ApiError(0, `API nicht erreichbar (${API_BASE_URL}). ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `Backend error ${response.status}`);
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as T);
      } catch {
        // Eine unlesbare Zeile darf den Rest des Laufs nicht mitnehmen.
      }
    }
  }
}

/** Receives progress and exactly one checked answer, never draft tokens. */
export async function streamAnswer(
  payload: Parameters<typeof api.answer>[0],
  onProgress: (message: string) => void,
  signal?: AbortSignal,
): Promise<Answer> {
  const response = await fetch(new URL("/query/answer/stream", API_BASE_URL), {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload), signal,
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  if (!response.body) throw new Error("Antwortstream fehlt");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer: Answer | undefined;
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const event = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const data = event.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const parsed = JSON.parse(data);
        if (parsed.type === "error") throw new Error(parsed.message);
        if (parsed.type === "progress") onProgress(parsed.message);
        if (parsed.type === "answer") answer = parsed.answer;
      }
      if (done) break;
    }
  } finally { reader.releaseLock(); }
  if (!answer) throw new Error("Antwortstream endete ohne geprüfte Antwort");
  return answer;
}
