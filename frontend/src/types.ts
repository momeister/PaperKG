export type Project = {
  id: string;
  name: string;
  paper_ids: string[];
  paper_count: number;
  year_min?: number | null;
  year_max?: number | null;
  primary_paper_id?: string | null;
};

export type DiscoveryCandidate = Paper & {
  discovery_reason?: string;
  matched_query?: string;
};

export type DiscoveryAnalysis = {
  topic_summary: string;
  methods: string[];
  queries: { query: string; reason: string }[];
  error?: string;
};

export type DiscoveryResponse = {
  analysis: DiscoveryAnalysis;
  candidates: DiscoveryCandidate[];
};

export type ReferenceCandidate = Paper & {
  reference_string?: string;
};

export type ReferenceExtractResponse = {
  paper_id: string;
  references_detected: number;
  references_matched: number;
  references: ReferenceCandidate[];
};

export type DeepResearchFinding = {
  url: string;
  title: string;
  snippet: string;
  summary: string;
  evidence?: string[];
  injection_flags: string[];
  quarantined: boolean;
  raw_excerpt: string;
  full_text?: string;
  char_count?: number;
};

export type DeepResearchResponse = {
  question: string;
  provider: string;
  queries: string[];
  topic_summary: string;
  related_topics?: string[];
  findings: DeepResearchFinding[];
  warnings: string[];
};

export type HarvestDownloadResult = {
  paper_id: string;
  title: string;
  status: "downloaded" | "inserted" | "no_pdf" | "failed";
  error?: string;
  detail?: string | null;
  landing_url?: string | null;
};

export type HarvestDownloadResponse = {
  inserted: number;
  downloaded: number;
  failed_downloads: string[];
  results: HarvestDownloadResult[];
  project_id?: string | null;
  attached?: boolean;
};

export type GreySource = {
  id: string;
  project_id: string;
  query?: string | null;
  url: string;
  title?: string | null;
  summary?: string | null;
  raw_excerpt?: string | null;
  full_text?: string | null;
  evidence?: string[];
  injection_flags: string[];
  status?: string;
  created_timestamp?: string;
};

export type Paper = {
  id: string;
  title: string;
  paper_id?: string;
  paperId?: string;
  display_title?: string;
  abstract?: string;
  source?: string;
  source_id?: string;
  filename?: string;
  file_name?: string;
  pdf_filename?: string;
  pdf_path?: string;
  path?: string;
  year?: number | null;
  doi?: string | null;
  pdf_url?: string | null;
  has_full_text?: boolean;
  latest_extraction_status?: string | null;
  project_ids?: string[];
};

export type PaperMeta = {
  paper_id: string;
  title: string;
  abstract: string;
  doi?: string | null;
  pdf_url?: string | null;
  landing_page_url?: string | null;
  has_local_pdf: boolean;
  external_url?: string | null;
};

export type PaperIngestResponse = {
  paper_id: string;
  title?: string | null;
  has_local_pdf: boolean;
  attached: boolean;
  external_url?: string | null;
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
  evidence_id?: string;
  paper_id: string;
  kind: string;
  text: string;
  score: number;
  field?: string | null;
  metadata?: Record<string, unknown>;
};

export type CitationLink = {
  citation: string;
  citation_start: number;
  citation_end: number;
  paper_id: string;
  evidence_id?: string;
  evidence_index?: number | null;
  score?: number;
  context?: string;
  approximate?: boolean;
};

export type Answer = {
  question: string;
  answer: string;
  no_answer?: boolean;
  generation_error?: string | null;
  sources: Source[];
  evidence: Evidence[];
  citation_links?: CitationLink[];
  context_diagnostics?: Record<string, unknown>;
  source_verification?: Record<string, unknown> | null;
};

export type AutoHarvestSummary = {
  harvested: boolean;
  papers: Array<{ id: string; title: string }>;
  grey: Array<{ id: string; title: string; url: string }>;
  related_topics: string[];
};

/** One SSE event from POST /query/auto-answer (auto-research answering). */
export type AutoAnswerEvent = {
  status: "answer" | "planning" | "harvesting" | "reanswering" | "harvest_error" | "done" | "error";
  answer?: Answer;
  related_topics?: string[];
  scope?: "main" | "related";
  topic?: string;
  papers?: Array<{ id: string; title: string }>;
  grey?: Array<{ id: string; title: string; url: string }>;
  harvest_summary?: AutoHarvestSummary;
  error?: string;
};

export type VerificationEvidence = {
  evidence_id?: string;
  paper_id: string;
  kind: string;
  field?: string | null;
  reference_text: string;
  pdf_excerpt: string;
  matched_terms: string[];
  found_in_pdf_text: boolean;
  source_evidence_index?: number | null;
  fragment_index?: number | null;
  evidence_index?: number | null;
  metadata?: Record<string, unknown>;
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

export type BatchJobItem = {
  job_id: string;
  paper_id: string;
  pdf_path: string | null;
  status: string;
  attempts: number;
  error_message: string | null;
  started_timestamp: string | null;
  completed_timestamp: string | null;
};

export type ExtractionLibraryItem = {
  paper_id: string;
  title: string;
  filename: string;
  pdf_path: string;
  pdf_available?: boolean;
  source_type?: "pdf" | "grey";
  text?: string;
  size_bytes?: number | null;
  modified_timestamp?: string | null;
  latest_extraction_status?: string | null;
  known_paper?: boolean;
};

export type ExtractionResultPayload = {
  paper_id: string;
  paper_type: string;
  concepts: Array<Record<string, unknown>>;
  methods: Array<Record<string, unknown>>;
  concept_candidates: Array<Record<string, unknown>>;
  method_candidates: Array<Record<string, unknown>>;
  relations: Array<Record<string, unknown>>;
  claims: Array<Record<string, unknown>>;
  cross_domain_hints: Array<Record<string, unknown>>;
  terminology_conflicts: Array<Record<string, unknown>>;
  temporal_coverage: Record<string, unknown>;
  mathematical_content: Record<string, unknown>;
  language_detected: string;
  quality_warnings: string[];
  metadata_status: string;
  blocking_errors: string[];
  candidate_count: number;
  extraction_diagnostics: Record<string, unknown>;
  context_diagnostics?: Record<string, unknown>;
  raw_response?: string | null;
};

export type ExtractionParseResponse = {
  paper_id: string;
  pdf_path: string;
  text: string;
  page_count: number;
  parser: string;
  metadata: Record<string, unknown>;
  excerpt: string;
};

export type ExtractionRunResponse = {
  result_id?: number | null;
  paper_id: string;
  status: string;
  error_message?: string | null;
  duration_seconds?: number | null;
  parse?: Omit<ExtractionParseResponse, "paper_id" | "text"> | null;
  result: ExtractionResultPayload;
};

export type ExtractionHistoryItem = {
  id: number;
  paper_id: string;
  llm_provider?: string | null;
  llm_model?: string | null;
  extraction_status?: string | null;
  extraction_timestamp?: string | null;
  extraction_duration_seconds?: number | null;
  paper_type?: string | null;
  concepts?: Array<Record<string, unknown>>;
  methods?: Array<Record<string, unknown>>;
  claims?: Array<Record<string, unknown>>;
  concept_candidates?: Array<Record<string, unknown>>;
  method_candidates?: Array<Record<string, unknown>>;
  relations?: Array<Record<string, unknown>>;
  quality_warnings?: string[];
  error_message?: string | null;
};

export type VocabularyEntry = {
  canonical_label: string;
  aliases: string[];
  openalx_id?: string | null;
  domain?: string | null;
  confidence?: number;
  custom_metadata?: Record<string, unknown>;
};

export type BenchmarkReport = {
  run_id?: string;
  summary: Record<string, unknown>;
  cases?: Array<Record<string, unknown>>;
  extraction?: Record<string, unknown>;
  answering?: Record<string, unknown>;
  warnings?: string[];
};

export type BenchmarkRun = {
  id: string;
  kind: "extraction" | "qa";
  provider?: string | null;
  model?: string | null;
  summary: Record<string, unknown>;
  report: Record<string, unknown>;
  duration_ms?: number | null;
  created_timestamp?: string;
};

export type RewriteResponse = {
  text: string;
  model?: string | null;
};

export type ResearchNode = {
  id: string;
  parent_id: string | null;
  question: string;
  depth: number;
  status: "running" | "done" | "error" | "harvesting" | "synthesis" | "llm_error";
  answer: Answer | null;
  child_count?: number;
  error?: string;
  error_kind?: string;
  message?: string;
  verification?: VerificationSource[];
  document?: string;
  harvested_papers?: Array<{ id: string; title: string }>;
  harvested_grey?: Array<{ id: string; title: string; url: string }>;
};

export type ResearchSessionSummary = {
  id: string;
  project_id?: string | null;
  question: string;
  status: string;
  node_count: number;
  done_count: number;
  has_synthesis: boolean;
  updated_timestamp?: string | null;
};

export type ResearchSession = {
  id: string;
  project_id?: string | null;
  question: string;
  status: string;
  nodes: ResearchNode[];
};

export type ParallelEntry = {
  id: string;
  variant_id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  answer_payload?: Answer | null;
  created_timestamp?: string | null;
};

export type ParallelVariant = {
  id: string;
  session_id: string;
  name: string;
  approach: string;
  rationale: string;
  suggested_prompt: string;
  origin: "ai" | "manual";
  status: string;
  position: number;
  entries: ParallelEntry[];
  created_timestamp?: string | null;
  updated_timestamp?: string | null;
};

export type ParallelFollowup = {
  id: string;
  session_id: string;
  question: string;
  answer_payload?: Answer | null;
  created_timestamp?: string | null;
};

export type ParallelSession = {
  id: string;
  project_id?: string | null;
  question: string;
  status: string;
  overview_markdown?: string | null;
  overview_payload?: Answer | null;
  synthesis_markdown?: string | null;
  synthesis_payload?: Answer | null;
  variants: ParallelVariant[];
  followups?: ParallelFollowup[];
  created_timestamp?: string | null;
  updated_timestamp?: string | null;
};

export type ParallelSessionSummary = {
  id: string;
  project_id?: string | null;
  question: string;
  status: string;
  variant_count: number;
  updated_timestamp?: string | null;
};

/** A variant compiled into an actionable instruction for an external desktop agent. */
export type TaskBrief = {
  goal: string;
  context: string;
  steps: string[];
  constraints: string[];
  success_criteria: string[];
  artifacts: string[];
  raw_prompt: string;
};

export type AgentHandoffResponse = {
  brief: TaskBrief;
  /** Copy-/POST-ready plain-text rendering of the brief. */
  text: string;
  bridge: { enabled: boolean; type: string };
};

export type AgentConfig = {
  enabled: boolean;
  type: string;
  has_url: boolean;
  vlm_model: string;
  vlm_provider: string;
};

/** One SSE event streamed back from POST /agent/dispatch while the desktop agent runs. */
export type AgentDispatchEvent = {
  status: "started" | "step" | "done" | "error";
  from?: string | null;
  value?: unknown;
  error?: string;
  model?: string;
};

export type NoteCitation = {
  id: string;
  note_id: string;
  paper_id: string;
  title?: string | null;
  kind?: string | null;
  reference_text?: string | null;
  pdf_excerpt?: string | null;
  evidence_id?: string | null;
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

// --- Code-Werkstatt (coding projects, file tree, editor, git) ---

export type CodeProject = {
  id: string;
  name: string;
  path: string;
  kind: "managed" | "external" | string;
  created_timestamp?: string;
  updated_timestamp?: string;
  exists?: boolean;
};

export type WorkspaceList = {
  projects: CodeProject[];
  base_dir: string;
  git_available: boolean;
};

export type FileTreeNode = {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number | null;
  children?: FileTreeNode[];
  truncated?: boolean;
};

export type FileContent = {
  path: string;
  content: string | null;
  size: number;
  too_large: boolean;
  binary: boolean;
};

export type GitStatusFile = {
  x: string;
  y: string;
  path: string;
  staged: boolean;
  untracked: boolean;
  code: string;
};

export type GitStatus = {
  available: boolean;
  is_repo: boolean;
  files: GitStatusFile[];
  error?: string;
};

export type GitDiff = {
  available: boolean;
  is_repo: boolean;
  diff: string;
  error?: string | null;
};
