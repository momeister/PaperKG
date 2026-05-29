import type {
  Answer,
  BenchmarkReport,
  Dashboard,
  GraphExplorer,
  HealthReport,
  Job,
  Note,
  NoteAiEditResponse,
  NoteAiThread,
  Paper,
  Project,
  Provider,
  RewriteResponse,
  ReviewEntity,
  VerificationSource
} from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

type RequestOptions = RequestInit & {
  query?: Record<string, string | number | boolean | null | undefined>;
};

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `API request failed with ${status}`);
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
  const response = await fetch(url(path, query), {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData) ? { "content-type": "application/json" } : {}),
      ...headers
    }
  });
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new ApiError(response.status, typeof payload === "object" && payload && "detail" in payload ? payload.detail : payload);
  }
  return payload as T;
}

export const api = {
  getHealth: () => request<HealthReport>("/system/health-report"),
  getProjects: () => request<{ projects: Project[] }>("/projects"),
  createProject: (name: string) => request<{ project: Project }>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
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
  uploadPdf: (file: File, params: { paper_id?: string; title?: string }) =>
    request<{ paper: Paper; pdf_path: string }>("/papers/upload", {
      method: "POST",
      query: params,
      headers: { "content-type": file.type || "application/pdf", "x-filename": file.name },
      body: file
    }),
  harvestSearch: (payload: { query: string; sources: string[]; max_results: number }) =>
    request<{ query: string; results: Paper[]; warnings: string[] }>("/harvest/search", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  harvestDownload: (papers: Paper[], downloadPdfs: boolean) =>
    request<{ inserted: number; downloaded: number; failed_downloads: string[] }>("/harvest/download", {
      method: "POST",
      body: JSON.stringify({ papers, download_pdfs: downloadPdfs })
    }),
  answer: (payload: { question: string; provider?: string; model?: string; limit?: number; conversation_context?: Array<{ role: string; content: string }> }) =>
    request<Answer>("/query/answer", { method: "POST", body: JSON.stringify(payload) }),
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
  createNote: (projectId: string, payload: { title: string; markdown?: string }) =>
    request<{ note: Note }>(`/projects/${encodeURIComponent(projectId)}/notes`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getNote: (noteId: string) => request<{ note: Note }>(`/notes/${encodeURIComponent(noteId)}`),
  updateNote: (noteId: string, payload: { title?: string; markdown?: string }) =>
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
  runBenchmarkJob: () => request<{ status: string; report: BenchmarkReport }>("/jobs/benchmark", { method: "POST", body: JSON.stringify({}) }),
  runGraphRebuild: () => request<{ status: string; result: Record<string, unknown> }>("/jobs/graph-rebuild", { method: "POST" }),
  runEvalJob: (provider: string, model?: string) =>
    request<{ status: string; report: Record<string, unknown> }>("/jobs/eval", { method: "POST", body: JSON.stringify({ provider, model }) })
};
