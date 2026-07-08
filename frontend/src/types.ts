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
  /** "model" when the LLM itself bound this citation to an evidence item ([pid#N]). */
  binding?: string;
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

export type Point = { x: number; y: number };

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
  abstract_available?: boolean;
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
  /** Resolved from llm.providers[vlm_provider].base_url — empty if unresolvable. */
  vlm_base_url: string;
  /** Assistent-only model override; falls back to vlm_model if unset. */
  helper_vlm_model: string;
  /** Native shell only: whether Tauri spawns/kills the bridge sidecar itself. */
  manage_sidecar: boolean;
  helper_enabled: boolean;
  observe_interval_seconds: number;
  observe_context_size: number;
};

/** Which AI-Cursor overlay mode is active: autonomous vs. live-assist. */
export type AgentMode = "self_managing" | "helper";

/** One SSE event streamed back from POST /agent/dispatch while the desktop agent runs. */
export type AgentDispatchEvent = {
  status: "started" | "step" | "done" | "error" | "aborted";
  runId?: string | null;
  from?: string | null;
  value?: unknown;
  error?: string;
  model?: string;
};

/** One SSE event streamed back from POST /agent/observe/start (Assistent mode). */
export type ObserveEvent = {
  status: "started" | "observation" | "error";
  sessionId?: string | null;
  value?: string | null;
  t?: number | null;
  error?: string;
};

/** One turn in the Assistent chat log (question asked or answer received).
 * `sources` is companion-only: grounding sources shown under the answer bubble. */
export type ObserveChatEntry = {
  role: "user" | "assistant";
  text: string;
  sources?: CompanionSource[];
};

/** Payload pushed into the overlay window via the `overlay://task` event, prefilling
 * it with a compiled variant brief — nothing runs until the user clicks "Starten". */
export type OverlayTaskPayload = {
  task: string;
  goal: string;
  mode: AgentMode;
  variantId?: string | null;
};

/** Result of POST /agent/observe/point — a grounded screen point for the Assistent's
 * pointer overlay ("zeig mir wo ich klicken kann"). Never implies a click happened. */
export type ObservePointResult = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  thought?: string;
  error?: string;
};

/** Payload pushed into the pointer overlay window via the `pointer://show` event.
 * `space` says which coordinate space `x`/`y` live in: `"physical"` for the Desktop
 * Companion (monitor-relative physical pixels), anything else/absent for the legacy
 * bridge path (logical px). For multi-monitor captures `monitor_width` lets the page
 * derive its scale from the viewport (devicePixelRatio can lag after the window moved
 * to a monitor with different DPI) and `origin_x`/`origin_y` locate the monitor in
 * the virtual desktop (needed to correct the global cursor position when dodging). */
export type PointerShowPayload = {
  x: number;
  y: number;
  label?: string | null;
  space?: "css" | "physical" | null;
  origin_x?: number | null;
  origin_y?: number | null;
  monitor_width?: number | null;
};

/** One physical display from the native `list_monitors` command (physical pixels;
 * ids are only stable for the current session — reload the list per overlay open). */
export type MonitorInfo = {
  id: number;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  scale_factor: number;
  is_primary: boolean;
};

/** One ordered click-guidance step from POST /companion/guide — coordinates in
 * original screenshot pixels (= physical monitor pixels for full captures). */
export type CompanionStep = { x: number; y: number; label: string };

/** One grounding source used for a companion answer (Quellen-Modus):
 * a local paper (id = e.g. "arxiv:2401.12345") or a web search hit (url). */
export type CompanionSource = {
  type: "paper" | "web";
  title: string;
  id?: string;
  url?: string;
};

/** Result of POST /companion/guide: German answer + optional pointing steps. */
export type CompanionGuideResult = {
  answer: string;
  found: boolean;
  steps: CompanionStep[];
  sources?: CompanionSource[];
  error?: string;
};

/** Result of POST /companion/ask (free-form screen Q&A, no pointing). */
export type CompanionAskResult = { answer: string; sources?: CompanionSource[]; error?: string };

/** One planned Selbst-Steuerung action (R7). Coordinates are original-screenshot
 * pixels; the overlay converts them to physical desktop pixels via the capture's
 * monitor origin before calling the control.rs command. */
export type SelfDriveAction = {
  type: "click" | "double_click" | "type" | "key" | "scroll" | "move" | "wait" | "done" | "fail";
  x?: number;
  y?: number;
  text?: string;
  keys?: string;
  dx?: number;
  dy?: number;
};

/** POST /selfdrive/start result. */
export type SelfDriveStartResult = {
  session_id?: string;
  goal?: string;
  max_steps?: number;
  error?: string;
};

/** POST /selfdrive/step result: the next action to confirm + execute. */
export type SelfDriveStepResult = {
  thought?: string;
  action?: SelfDriveAction;
  done?: boolean;
  step?: number;
  max_steps?: number;
  error?: string;
};

/** GET /companion/config: defaults + selectable vision providers for the picker. */
export type CompanionConfigInfo = {
  provider: string;
  model: string;
  language: string;
  default_provider: string;
  providers: { name: string; models: string[] }[];
};

/** Result of the native `capture_screen` command — physical monitor pixels.
 * `origin_x`/`origin_y` locate the captured monitor in the virtual desktop so the
 * pointer ring can be moved onto the right screen. */
export type CaptureResult = {
  image_base64: string;
  width: number;
  height: number;
  scale_factor: number;
  monitor_id: number;
  monitor_name: string;
  origin_x: number;
  origin_y: number;
};

/** Payload of `snip://begin` into the snip window (the frozen full-screen frame). */
export type SnipBeginPayload = CaptureResult;

/** Payload of `snip://result` into the chat overlay (the cropped region). */
export type SnipResultPayload = {
  image_base64: string;
  width: number;
  height: number;
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

// --- Zitat-Nachcheck (Claim gegen Quelle prüfen) ---
export type ClaimCheckVerdict = "supported" | "partially_supported" | "not_supported" | "insufficient_evidence";

export type ClaimCheckResult = {
  paper_id: string;
  statement: string;
  verdict: ClaimCheckVerdict;
  explanation: string;
  supporting_quotes: string[];
  excerpts: string[];
  source_origin: "pdf" | "abstract" | "grey" | "shown_evidence" | "none" | string;
  /** Wie weit wurde geprüft: nur die Belegstelle/Abstract, oder das ganze Paper (bei
   *  unsicherem Auszug-Urteil wird das ganze PDF fensterweise nachgeprüft). */
  checked_scope?: "excerpt" | "whole_paper" | string;
};

// --- Analyse-Werkstatt (reproduzierbare Skript-Läufe) ---
export type AnalysisArtifactKind = "figure" | "table" | "data" | "log" | string;

export type AnalysisArtifact = {
  id: string;
  run_id: string;
  kind: AnalysisArtifactKind;
  filename: string;
  rel_path: string;
  caption?: string | null;
  size?: number | null;
  sha256?: string | null;
  /** Download URL, added by the backend response shaper. */
  url?: string;
};

// --- Datensätze (freie Forschungs-Registries) ---
export type DatasetSource = { id: string; label: string; domain: string };

export type DatasetHit = {
  source: string;
  external_id: string;
  title: string;
  description?: string;
  url?: string;
  doi?: string | null;
  license?: string | null;
  size?: string | null;
  year?: number | null;
  metadata?: Record<string, unknown>;
};

export type Dataset = DatasetHit & {
  id: string;
  project_id?: string | null;
  linked_paper_id?: string | null;
  created_timestamp?: string;
};

export type DatasetFileInfo = {
  name: string;
  size?: string | null;
  download_url?: string | null;
};

export type DatasetDetails = {
  source: string;
  external_id: string;
  description?: string | null;
  license?: string | null;
  files: DatasetFileInfo[];
  download_url?: string | null;
  warning?: string | null;
};

export type AnalysisRun = {
  id: string;
  project_id?: string | null;
  code_project_id?: string | null;
  run_dir: string;
  rel_dir?: string | null;
  title?: string | null;
  description?: string | null;
  request?: string | null;
  script_rel?: string | null;
  status: "ok" | "error" | "timeout" | string;
  provider?: string | null;
  model?: string | null;
  seed?: number | null;
  output_hash?: string | null;
  verified_hash?: string | null;
  stdout?: string | null;
  stderr?: string | null;
  duration_s?: number | null;
  created_timestamp?: string;
  updated_timestamp?: string;
  artifacts?: AnalysisArtifact[];
};
