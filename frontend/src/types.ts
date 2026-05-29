export type Project = {
  id: string;
  name: string;
  paper_ids: string[];
  paper_count: number;
  year_min?: number | null;
  year_max?: number | null;
};

export type Paper = {
  id: string;
  title: string;
  abstract?: string;
  source?: string;
  source_id?: string;
  year?: number | null;
  doi?: string | null;
  pdf_url?: string | null;
  has_full_text?: boolean;
  latest_extraction_status?: string | null;
  project_ids?: string[];
};

export type HealthReport = {
  status: string;
  metadata_db?: { paper_count?: number };
  graph_db?: { exists?: boolean; backend?: string; kuzu_available?: boolean };
  pdf_library?: { pdf_count?: number };
  papers?: Record<string, unknown>;
  extractions?: Record<string, unknown>;
  review_queue?: { pending?: number; total?: number };
  embeddings?: { total?: number; model_count?: number; latest_version?: number };
  batch_jobs?: { by_status?: Record<string, number>; latest?: Job[] };
  quality_telemetry?: Record<string, unknown>;
  warnings?: string[];
  action_items?: Array<{ kind: string; severity: string; message: string }>;
};

export type Dashboard = {
  project: Project;
  metrics: {
    papers: number;
    pdfs: number;
    extraction_coverage: number;
    pending_review: number;
    embeddings: number;
    warnings: number;
  };
  health: HealthReport;
  latest_jobs: Job[];
};

export type Source = {
  paper_id: string;
  title: string;
  year?: number | null;
  doi?: string | null;
  url?: string | null;
};

export type Evidence = {
  paper_id: string;
  kind: string;
  text: string;
  score: number;
  field?: string | null;
  metadata?: Record<string, unknown>;
};

export type Answer = {
  question: string;
  answer: string;
  no_answer?: boolean;
  generation_error?: string | null;
  sources: Source[];
  evidence: Evidence[];
};

export type VerificationEvidence = {
  paper_id: string;
  kind: string;
  field?: string | null;
  reference_text: string;
  pdf_excerpt: string;
  matched_terms: string[];
  found_in_pdf_text: boolean;
};

export type VerificationSource = {
  paper_id: string;
  title: string;
  pdf_available: boolean;
  pdf_filename?: string | null;
  pdf_error?: string | null;
  evidence: VerificationEvidence[];
};

export type Provider = {
  name: string;
  provider_type: string;
  base_url: string;
  default_model: string;
  models: string[];
  settings: {
    temperature?: number;
    top_p?: number;
    max_tokens?: number;
    context_size?: number;
  };
  auth_configured: boolean;
};

export type ReviewEntity = {
  id: number;
  paper_id: string;
  label: string;
  entity_type?: string | null;
  canonical_id?: string | null;
  suggested_canonical?: string | null;
  review_status: string;
  evidence?: string | null;
  merge_candidates?: unknown[];
  source_field?: string | null;
};

export type GraphNode = {
  id: string;
  label: string;
  type: "paper" | "concept" | "method" | string;
  year?: number | null;
  metadata?: Record<string, unknown>;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  score?: number | null;
};

export type GraphExplorer = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: Record<string, unknown>;
};

export type Job = {
  job_id: string;
  status: string;
  papers_total: number;
  papers_processed: number;
  papers_failed: number;
  error_message?: string | null;
  updated_timestamp?: string;
};

export type BenchmarkReport = {
  summary: Record<string, unknown>;
  cases: Array<Record<string, unknown>>;
  warnings?: string[];
};

export type RewriteResponse = {
  text: string;
  model?: string | null;
};

export type NoteCitation = {
  id: string;
  note_id: string;
  paper_id: string;
  title?: string | null;
  kind?: string | null;
  reference_text?: string | null;
  pdf_excerpt?: string | null;
  evidence_index?: number | null;
  created_timestamp?: string;
};

export type NoteAsset = {
  id: string;
  note_id: string;
  filename: string;
  content_type?: string | null;
  asset_path?: string;
  url: string;
  created_timestamp?: string;
};

export type Note = {
  id: string;
  project_id: string;
  title: string;
  markdown: string;
  excerpt?: string;
  citation_count?: number;
  asset_count?: number;
  citations?: NoteCitation[];
  assets?: NoteAsset[];
  created_timestamp?: string;
  updated_timestamp?: string;
};

export type NoteAiThread = {
  id: string;
  note_id: string;
  selected_text: string;
  instruction: string;
  response_text: string;
  replacement_text?: string | null;
  answer_payload?: Answer | Record<string, unknown>;
  anchor_start?: number | null;
  anchor_end?: number | null;
  anchor_quote?: string | null;
  ui_state?: Record<string, unknown>;
  messages?: NoteAiMessage[];
  created_timestamp?: string;
  updated_timestamp?: string;
};

export type NoteAiMessage = {
  id: string;
  thread_id: string;
  note_id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_timestamp?: string;
};

export type NoteAiEditResponse = {
  thread: NoteAiThread;
  replacement_text: string;
  answer: Partial<Answer>;
  model?: string | null;
  user_message?: NoteAiMessage;
  assistant_message?: NoteAiMessage;
};
