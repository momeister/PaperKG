export type Project = {
  id: string;
  name: string;
  paper_ids: string[];
  paper_count: number;
  year_min?: number | null;
  year_max?: number | null;
  primary_paper_id?: string | null;
  pinned?: boolean;
  /** Task-Focused Mode: Kreativitätsstufe 1–5 (Default 3, aus project_meta.json). */
  creativity_level?: CreativityLevel;
};

/**
 * Workspace-Modus pro Projekt.
 * - "research": klassische Forschungsansicht (Bibliothek, Notes, Assistant)
 * - "task": Task-Focused Mode (Hackathon/Kaggle/Anweisung) — Spec + Richtungen +
 *   parallele Varianten mit "Wie umsetzen"-Steps.
 * Persistiert per-projekt in localStorage `sciencekg.workspace.mode.{projectId}`.
 */
export type WorkspaceMode = "research" | "task";

/**
 * Kreativitätsstufe 1–5 für LLM-Vorschläge (Richtungen, Varianten, Steps).
 * - 1 = konservativ/mainstream
 * - 3 = ausgewogen (Default)
 * - 5 = aggressiv/querverweisend
 * Persistiert per-projekt in `project_meta.json` (nicht in localStorage, da
 * projektgebundene Konfiguration).
 */
export type CreativityLevel = 1 | 2 | 3 | 4 | 5;

export type HarvestSource = {
  id: string;
  label: string;
  group: string;
  tier: "journal" | "preprint" | "index";
  needs_key: boolean;
  note?: string | null;
};

export type HarvestSourceGroup = {
  id: string;
  label: string;
};

export type HarvestSourceCatalog = {
  sources: HarvestSource[];
  groups: HarvestSourceGroup[];
  default: string[];
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
  /** "web" (Fund aus dem Netz), "note" (veroeffentlichte Notiz) oder "analysis" (Tiefenanalyse). */
  source_kind?: string;
  /** Notiz-/Session-ID, aus der die Quelle erzeugt wurde. */
  origin_id?: string | null;
  /** Paper, auf denen diese Quelle selbst beruht (Analyse/Notiz-Zitate). */
  source_paper_ids?: string[];
  created_timestamp?: string;
};

export type Paper = {
  id: string;
  title: string;
  paper_id?: string;
  paperId?: string;
  display_title?: string;
  abstract?: string;
  authors?: string[];
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
  landing_page_url?: string | null;
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

export type StudyDesign =
  | "meta_analysis"
  | "systematic_review"
  | "RCT"
  | "randomized_controlled"
  | "cohort"
  | "case_control"
  | "cross_sectional"
  | "observational"
  | "survey"
  | "case_series"
  | "case_report"
  | "benchmark"
  | "simulation"
  | "qualitative"
  | "theoretical"
  | "unknown";

export type EvidenceLevel = "high" | "moderate" | "low" | "very_low" | "unknown";

export type StudyQualitySummary = {
  evidence_level?: EvidenceLevel;
  quality_score?: number;
  flags?: string[];
  funding_sources?: Array<Record<string, unknown>>;
  coi_status?: "declared" | "undisclosed" | "none" | "unknown";
  study_design?: StudyDesign;
  sample_size_value?: number | null;
};

export type AnswerClaim = {
  claim_id: string; text: string; evidence_ids: string[]; kind: string;
  verification_status: "supported" | "partially_supported" | "not_supported" | "unknown" | "gap";
  text_found: boolean; explanation: string; start: number; end: number;
};

export type CitationLink = {
  claim_id?: string;
  passage_id?: string;
  verification_status?: string;
  citation: string;
  citation_start: number;
  citation_end: number;
  paper_id: string;
  evidence_id?: string;
  evidence_index?: number | null;
  score?: number;
  context?: string;
  approximate?: boolean;
  /** Three-level confidence label. `high` = exact/strong match, `medium` = usable
   * lexical match, `low` = weak/no overlap (UI shows as approximate). Backward
   * compat: `approximate` is also set when confidence == "low". */
  confidence?: "high" | "medium" | "low";
  /** "model" when the LLM itself bound this citation to an evidence item ([pid#N]). */
  binding?: string;
};

export type Answer = {
  model?: string | null;
  claims_version?: number | null;
  claims?: AnswerClaim[];
  verification_status?: "complete" | "incomplete" | "legacy";
  question: string;
  answer: string;
  no_answer?: boolean;
  generation_error?: string | null;
  sources: Source[];
  evidence: Evidence[];
  citation_links?: CitationLink[];
  context_diagnostics?: Record<string, unknown>;
  source_verification?: Record<string, unknown> | null;
  /** Structured professor critique (Parallel mode); absent on legacy free-text answers. */
  professor_review?: ProfessorReview;
  /** Per-paper study-quality summaries surfaced from the backend for
   *  evidence-level / flag badges in the UI. Keyed by paper_id. */
  study_quality_summaries?: Record<string, StudyQualitySummary>;
};

/** Per-variant verdict inside a professor stage review. */
export type ProfessorVariantVerdict = {
  variant_id: string;
  name: string;
  urteil: "weiterverfolgen" | "anpassen" | "verwerfen";
  begruendung: string;
};

/** Structured professor critique carried on Answer.professor_review (schema v1). */
export type ProfessorReview = {
  schema_version: number;
  kind: "entry" | "stage" | "final";
  verstaendnis?: string;
  staerken?: string[];
  probleme?: string[];
  ideen?: string[];
  naechste_schritte?: string[];
  /** kind="stage" only */
  varianten_bewertung?: ProfessorVariantVerdict[];
  /** kind="final" only */
  gesamtverstaendnis?: string;
  etappen_zusammenfassung?: Array<{ stage_id: string; name: string; fazit: string }>;
  offene_punkte?: string[];
  finale_antwort?: string;
};

/** Stufe der Auto-Recherche: wissenschaftlich → vertrauenswürdig → ungeprüft. */
export type AutoHarvestStage = "scientific" | "trusted" | "unverified";

export type AutoHarvestStageSummary = {
  stage: AutoHarvestStage;
  label: string;
  papers: number;
  grey: number;
  /** true, wenn die Antwort nach dieser Stufe trug (die Leiter endet dann). */
  sufficient: boolean;
};

export type AutoGreySource = { id: string; title: string; url: string; trust_tier?: string };

export type AutoHarvestSummary = {
  harvested: boolean;
  papers: Array<{ id: string; title: string }>;
  grey: AutoGreySource[];
  related_topics: string[];
  stages?: AutoHarvestStageSummary[];
};

/** One SSE event from POST /query/auto-answer (auto-research answering). */
export type AutoAnswerEvent = {
  status: "answer" | "planning" | "harvesting" | "reanswering" | "harvest_error" | "done" | "error";
  answer?: Answer;
  related_topics?: string[];
  scope?: "main" | "related";
  stage?: AutoHarvestStage;
  stage_label?: string;
  topic?: string;
  papers?: Array<{ id: string; title: string }>;
  grey?: AutoGreySource[];
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

export type ExtractionQualityRow = {
  id?: number;
  paper_id: string;
  concept_count?: number | null;
  method_count?: number | null;
  claim_count?: number | null;
  has_formulas?: boolean | null;
  parse_quality?: string | null;
  call_1_tokens_used?: number | null;
  call_2_tokens_used?: number | null;
  duration_seconds?: number | null;
  model?: string | null;
  provider?: string | null;
  context_policy?: string | null;
  whole_context_used?: boolean | null;
  chunk_count?: number | null;
  estimated_prompt_tokens?: number | null;
  context_margin_tokens?: number | null;
  context_fallback_reason?: string | null;
  timestamp?: string | null;
};

export type ExtractionResultDetail = {
  id: number;
  paper_id: string;
  paper_type?: string | null;
  llm_provider?: string | null;
  llm_model?: string | null;
  extraction_status?: string | null;
  extraction_timestamp?: string | null;
  extraction_duration_seconds?: number | null;
  concepts?: Array<Record<string, unknown>>;
  methods?: Array<Record<string, unknown>>;
  concept_candidates?: Array<Record<string, unknown>>;
  method_candidates?: Array<Record<string, unknown>>;
  relations?: Array<Record<string, unknown>>;
  claims?: Array<Record<string, unknown>>;
  cross_domain_hints?: Array<Record<string, unknown>>;
  terminology_conflicts?: Array<Record<string, unknown>>;
  temporal_coverage?: Record<string, unknown>;
  mathematical_content?: Record<string, unknown>;
  language_detected?: string | null;
  quality_warnings?: string[];
  metadata_status?: string | null;
  blocking_errors?: string[];
  candidate_count?: number | null;
  extraction_diagnostics?: Record<string, unknown>;
  context_diagnostics?: Record<string, unknown>;
  raw_response?: unknown;
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

/**
 * Ein "Wie umsetzen"-Step einer Variante. Status-Übergänge:
 * - "vorgeschlagen" → AI hat den Step vorgeschlagen, wartet auf Nutzerentscheid
 * - "in_progress"  → Nutzer: "Das probiere ich"
 * - "done"          → Nutzer: "Ergebnis zeigen" (result + professor-review)
 * - "rejected"     → Nutzer: "Weg nichts für mich"
 */
export type ParallelStepStatus =
  | "vorgeschlagen"
  | "in_progress"
  | "done"
  | "rejected";

export type ParallelStep = {
  id: string;
  variant_id: string;
  text: string;
  rationale?: string;
  citation?: string;
  status: ParallelStepStatus;
  origin: "user" | "ai";
  result?: string | null;
  /** Verweis auf ein ProfessorReview / parallel_entry nach "Ergebnis zeigen". */
  result_entry_id?: string | null;
  created_timestamp?: string | null;
  updated_timestamp?: string | null;
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
  stage_id?: string | null;
  entries: ParallelEntry[];
  /** "Wie umsetzen"-Steps — interaktive Umsetzung pro Variante. */
  user_steps?: ParallelStep[];
  /** Optional: Begründung einer "Weg nichts"-Ablehnung. */
  rejection_reason?: string | null;
  created_timestamp?: string | null;
  updated_timestamp?: string | null;
};

/** One Etappe of a Forschungsvorhaben (parallel session roadmap). */
export type ParallelStage = {
  id: string;
  session_id: string;
  name: string;
  goal: string;
  status: "offen" | "aktiv" | "abgeschlossen";
  position: number;
  review_markdown?: string | null;
  review_payload?: Answer | null;
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
  stages?: ParallelStage[];
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
  stage_count?: number;
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

// ---------------------------------------------------------------------------
// Task-Focused Mode — Task-Spec + Forschungsrichtungen
// ---------------------------------------------------------------------------

/** Dataset-Referenz in einem Task-Spec (Name + optionale Install-URL/Metadaten). */
export type TaskDataset = {
  name: string;
  install_url?: string | null;
  size?: string | null;
  license?: string | null;
};

/** Zeitrahmen eines Task-Specs. */
export type TaskTimeline = { start: string; end: string };

/**
 * Task-Spec: strukturierte Aufgabenstellung (Kaggle/Hackathon/Anweisung).
 * Backend-Quelle: ``query/task_extractor.normalize_task_spec``.
 */
export type TaskSpec = {
  title: string;
  objective: string;
  /** Hintergrund/Kontext der Aufgabe — warum sie existiert, Domäne. */
  background?: string;
  evaluation: string;
  datasets: TaskDataset[];
  timeline: TaskTimeline;
  /** Endgültige Einreichungsfrist (ISO-Datum oder wie in der Quelle angegeben). */
  deadline?: string;
  /** Vorgeschlagene/erforderliche Methoden/Ansätze (z. B. »supervised classification«). */
  methodology?: string[];
  rules: string[];
  constraints: string[];
  /** Akzeptanzkriterien — was erfüllt sein muss (z. B. Metrik-Schwellen). */
  inclusion_criteria: string[];
  /** Ausschlusskriterien — was disqualifiziert (verbotene Methoden, Leakage). */
  exclusion_criteria: string[];
  suggested_directions: TaskResearchDirection[];
  /** Dynamische Catch-All-Sections (Prizes, Score, Submission File,
   *  Code-Requirements, Efficiency Prize, …). LLM erzeugt sie via
   *  extra_sections im Schema; Frontend rendert sie als zusätzliche
   *  einklappbare Sections (label = Titel, body = Text). */
  extra_sections?: { label: string; body: string }[];
  /** Verifikations-Rohtext der Quelle (gekürzt auf 8 KB vom Backend). Wird beim
   *  Extrahieren gesetzt, beim PATCH-edit mitgeführt. Frontend zeigt ihn als
   *  einklappbare Quelle. */
  source_raw_text?: string;
  /** Fehler bei der Extraktion (z. B. LLM nicht erreichbar). Backend liefert HTTP
   *  200 mit leerem Spec + error-Feld statt eines Fehler-Status, damit das
   *  Frontend den Spec trotzdem rendern kann. Prominent als Banner anzeigen. */
  error?: string;
  /** Per-Richtung Tiefensuche-Ergebnisse (key = direction.label). Wird vom
   *  Backend nach Stream-Ende in task_json.deep_searches persistiert, damit
   *  die Ergebnisse beim Neuladen erhalten bleiben. */
  deep_searches?: Record<string, TaskDeepSearchResult>;
};

/**
 * Vom LLM vorgeschlagene Forschungsrichtung für einen Task-Spec.
 * Generiert via ``POST /tasks/{task_id}/suggest-directions``.
 */
export type TaskResearchDirection = {
  label: string;
  rationale: string;
  keywords: string[];
};

/** Gespeicherter Task (DB-Zeile, projektgebunden). */
export type Task = {
  id: string;
  project_id?: string | null;
  title: string;
  task_json: TaskSpec;
  source_kind: string; // "url" | "pdf" | "text"
  source_url?: string | null;
  source_pdf_path?: string | null;
  created_timestamp?: string | null;
  updated_timestamp?: string | null;
};

/** LLM-Antwort von ``POST /tasks/{task_id}/suggest-directions``. */
export type TaskSuggestDirectionsResponse = {
  directions: TaskResearchDirection[];
  creativity_level: number;
  provider?: string;
  model?: string;
};

/** LLM-Antwort von ``POST /tasks/{task_id}/plan``. */
export type TaskImplementationPlan = {
  plan_markdown: string;
  steps: { text: string; rationale: string; citation: string }[];
  creativity_level: number;
};

/** LLM-Antwort von ``POST /tasks/{task_id}/as-grey-source``. */
export type TaskGreySourceResponse = {
  task_id: string;
  grey_source: GreySource;
  citation: string; // "grey::task_{id}"
};

/** Request-Body für ``POST /tasks/{task_id}/deep-search``. */
export type TaskDeepSearchRequest = {
  direction: TaskResearchDirection;
  /** Rekursionstiefe (1-6), analog Tiefenanalyse. 1 = nur Wurzel, sonst Baum. */
  depth?: number;
  /** Verzweigungsgrad (2-8) — Sub-Fragen pro Knoten bei depth>1. */
  branches?: number;
  max_papers?: number;
  max_web_sources?: number;
  provider?: string | null;
  model?: string | null;
  creativity_level?: number | null;
  /** Wenn gesetzt, werden geharvestete Papiere an dieses Projekt angehängt
   *  (Bugfix: sonst No-Op im globalen __all_papers__-Modus). */
  target_project_id?: string | null;
};

/** Ein SSE-Event vom deep-search-Stream (status-Feld diskriminiert). */
export type TaskDeepSearchEvent =
  | { status: "planning"; direction: TaskResearchDirection; query: string }
  | { status: "harvesting_papers"; query: string }
  | { status: "search_complete"; phase: "papers"; found: number }
  | { status: "ingesting"; paper: { id: string; title: string } }
  | { status: "ingested"; paper: { id: string; title: string } }
  | { status: "ingest_failed"; paper: { id: string; title: string } }
  | { status: "papers_harvested"; count: number; papers: { id: string; title: string }[] }
  | { status: "harvesting_grey"; query: string }
  | { status: "grey_search_complete"; found: number }
  | { status: "fetched"; source: { id: string; title: string; url: string } }
  | {
      status: "grey_harvested";
      count: number;
      sources: { id: string; title: string; url: string }[];
    }
  | { status: "synthesizing"; papers: number; grey_sources: number }
  | {
      status: "node_running";
      node_id: string;
      parent_id: string | null;
      depth: number;
      question: string;
      path: string[];
    }
  | {
      status: "node_done";
      node_id: string;
      parent_id: string | null;
      depth: number;
      question: string;
      path: string[];
      papers_count: number;
      grey_count: number;
    }
  | {
      status: "sub_questions";
      node_id: string;
      depth: number;
      questions: string[];
      path: string[];
    }
  | {
      status: "done";
      summary: Answer;
      papers_count: number;
      grey_count: number;
      paper_ids: string[];
      grey_ids: string[];
      node_count: number;
      direction: TaskResearchDirection;
    }
  | { status: "harvest_error"; phase: "papers" | "grey"; error: string }
  | { status: "error"; error: string; phase?: string };

/** Persistiertes Ergebnis einer Richtung-Tiefensuche (in task_json.deep_searches[label]). */
export type TaskDeepSearchResult = {
  summary: Answer;
  papers_count: number;
  grey_count: number;
  paper_ids: string[];
  grey_ids: string[];
  direction: TaskResearchDirection;
  node_count?: number;
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
 * monitor origin before calling the control.rs command. `lookup` is resolved
 * server-side (research injected + re-planned); `ask` pauses the loop for a user
 * reply (POST /selfdrive/answer). */
export type SelfDriveAction = {
  type:
    | "click"
    | "double_click"
    | "type"
    | "key"
    | "scroll"
    | "move"
    | "wait"
    | "lookup"
    | "ask"
    | "done"
    | "fail";
  x?: number;
  y?: number;
  label?: string;
  text?: string;
  keys?: string;
  dx?: number;
  dy?: number;
  query?: string;
  question?: string;
  /** Sensitive-target safeguard: true forces per-action confirmation even in autopilot. */
  sensitive?: boolean;
  sensitive_reason?: string;
};

/** Verify verdict for the previous action (advisory feedback, never a hard gate). */
export type SelfDriveVerification = { ok: boolean; note?: string };

/** POST /selfdrive/start result. */
export type SelfDriveStartResult = {
  session_id?: string;
  goal?: string;
  max_steps?: number;
  autopilot?: boolean;
  error?: string;
};

/** POST /selfdrive/step result: the next action to confirm + execute. */
export type SelfDriveStepResult = {
  thought?: string;
  action?: SelfDriveAction;
  expectation?: string;
  verification?: SelfDriveVerification | null;
  refined?: boolean;
  done?: boolean;
  step?: number;
  max_steps?: number;
  error?: string;
};

/** GET /companion/config: defaults + selectable vision providers for the picker,
 * plus everything the overlay loops need (self-drive autopilot, guide settle). */
export type CompanionConfigInfo = {
  provider: string;
  model: string;
  language: string;
  default_provider: string;
  providers: { name: string; models: string[] }[];
  self_drive?: {
    enabled: boolean;
    autopilot: boolean;
    max_steps: number;
    settle_ms: number;
    mouse_abort_px?: number;
    action_timeout_ms?: number;
    step_timeout_ms?: number;
  };
  guide?: { max_steps: number; click_settle_ms: number };
};

/** One durable companion/selfdrive session (DuckDB `companion_sessions`). */
export type CompanionSessionSummary = {
  id: string;
  kind: "companion" | "selfdrive";
  title?: string | null;
  goal?: string | null;
  status?: string | null;
  message_count?: number;
  created_timestamp?: string;
  updated_timestamp?: string;
};

/** One persisted transcript row. `payload` carries structured extras (steps,
 * actions, sources, verification) depending on `role`. */
export type CompanionSessionMessage = {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "action" | "verification" | "system";
  content?: string | null;
  payload?: Record<string, unknown> | null;
  created_timestamp?: string;
};

export type CompanionSessionDetail = CompanionSessionSummary & {
  messages: CompanionSessionMessage[];
  error?: string;
};

/** POST /companion/guide/start result. */
export type GuideStartResult = {
  guide_id?: string;
  max_steps?: number;
  click_settle_ms?: number;
  settle_ms?: number;
  error?: string;
};

/** POST /companion/guide/step result: the next user-executed guidance step. */
export type GuideStepResult = {
  instruction?: string;
  step?: CompanionStep | null;
  expectation?: string;
  verification?: SelfDriveVerification | null;
  done?: boolean;
  step_index?: number;
  max_steps?: number;
  error?: string;
};

/** Payload of the native `companion://click` event (guided mode): the user's real
 * click in physical virtual-desktop pixels; `on_overlay` marks clicks on the chat
 * card itself (UI interaction, not a guided step). */
export type CompanionClickPayload = { x: number; y: number; on_overlay: boolean };

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

export type PdfAnchor = { page_number: number; rects: PdfAnnotationRect[] };
export type PdfSelection = { paperId: string; originalText: string; anchors: PdfAnchor[] };

export type NoteCitation = {
  pdf_anchors?: PdfAnchor[] | null;
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

// --- Git-Checkpoints (Stufe 2) — ein Weg zurueck, ohne die Stage anzufassen ---
export type CheckpointReason =
  | "manual"
  | "auto_symbol_write"
  | "auto_file_write"
  | "auto_refactor"
  | "auto_sandbox_apply"
  | "pre_restore";

export type Checkpoint = {
  id: string;
  code_project_id: string;
  ref_name: string;
  commit_sha: string;
  tree_sha: string | null;
  parent_sha: string | null;
  label: string | null;
  reason: CheckpointReason | string;
  file_count: number;
  created_timestamp: string | null;
};

export type CheckpointResult = {
  checkpoint: Checkpoint | null;
  reason: string;
  error?: string | null;
};

export type CheckpointDiffEntry = { status: string; path: string };

export type CheckpointRestorePlan = {
  available: boolean;
  entries: CheckpointDiffEntry[];
  plan_hash: string;
  commit_sha: string;
};

export type CheckpointRestoreResult = {
  applied: boolean;
  reason?: string;
  commit_sha?: string;
  backup_ref?: string | null;
  backup_sha?: string | null;
  entries?: { path: string; status: string; action: string; reason?: string }[];
};

// --- Was-wäre-wenn-Sandbox (Stufe 2, git worktree) ---
export type Sandbox = {
  id: string;
  code_project_id: string;
  checkpoint_id: string | null;
  base_sha: string | null;
  path: string | null;
  status: string;
  test_command: string | null;
  last_exit_code: number | null;
  last_run_timestamp: string | null;
  created_timestamp: string | null;
};

export type SandboxRunResult = {
  returncode: number;
  stdout: string;
  stderr: string;
  timed_out: boolean;
  duration_s: number;
  command: string[];
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
export type DatasetSource = {
  id: string;
  label: string;
  domain: string;
  /** Quelle benötigt Authentifizierung (z.B. Kaggle). */
  needs_key?: boolean;
  /** Hinweistext bei fehlendem Login (z.B. "Kaggle-Token in Einstellungen hinterlegen"). */
  hint?: string | null;
};

/** Status einer Datenquelle (Login-Badge) via ``GET /datasets/sources/status``. */
export type DatasetSourceStatus = {
  id: string;
  authenticated: boolean;
  username?: string | null;
  hint?: string | null;
};

/** Anfrage für ``POST /datasets/download`` (Kaggle auth-pflichtig). */
export type DatasetDownloadRequest = {
  source: string;
  external_id: string;
  file_name?: string;
  dest_dir?: string;
};

/** Resultat eines Downloads (Pfad im lokalen Dateisystem). */
export type DatasetDownloadResponse = {
  source: string;
  external_id: string;
  file_name: string;
  local_path: string;
  bytes: number;
};

/** Login-Status eines Drittsystems (Kaggle/HuggingFace) via ``GET /settings/{system}``. */
export type KaggleStatus = {
  authenticated: boolean;
  username?: string | null;
  hint?: string | null;
};

/** Payload für ``POST /settings/kaggle`` (username+key ODER kaggle_json Inhalt). */
export type KaggleLoginRequest = {
  username?: string;
  key?: string;
  kaggle_json?: string;
};

/** Antwort von ``POST /settings/kaggle``. */
export type KaggleLoginResponse = {
  ok: boolean;
  username: string;
  hint: string;
};

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

// --- PDF-Notizen (an einer Textstelle/Punkt im PDF verankert) ---
// Rects sind 0..1 normalisiert relativ zur Seiten-Oberfläche (zoom-unabhängig).
export type PdfAnnotationRect = { x: number; y: number; width: number; height: number };

export type PdfAnnotation = {
  id: string;
  paper_id: string;
  page_number: number;
  kind: "highlight" | "point" | string;
  rects: PdfAnnotationRect[];
  quote?: string | null;
  body: string;
  color?: string | null;
  created_timestamp?: string;
  updated_timestamp?: string;
};

// --- Code-Graph (CodeSearch) -------------------------------------------------
// Spiegel der Rust-Strukturen aus codesearch/crates/cs-graph. Zwei Dinge sind
// hier nicht verhandelbar:
//
//   * `CodeNodeId` ist ein **String** (16 Hex-Zeichen). Es ist ein 64-Bit-Hash;
//     als `number` verlöre er in JavaScript stillschweigend seine unteren Bits
//     und zeigte auf ein anderes Symbol.
//   * Jede Beziehung trägt `confidence` und ihre Belegstelle. Wer eine Kante
//     anzeigt, ohne beides danebenzustellen, macht aus einer Vermutung eine
//     Tatsache — genau das, was dieses Werkzeug verhindern soll.

export type CodeNodeId = string;

/** Wie sicher ist diese Beziehung? Geordnet: measured schlägt alles. */
export type CodeConfidence = "guessed" | "resolved" | "verified" | "measured";

export type CodeNodeKind =
  | "file" | "module" | "class" | "interface" | "function" | "method" | "field"
  | "global" | "route" | "db_table" | "db_column" | "test" | "config_key"
  | "external_package" | "dynamic_gap";

export type CodeEdgeKind =
  | "contains" | "calls" | "imports" | "inherits" | "implements" | "reads"
  | "writes" | "param_type" | "returns_type" | "throws" | "tested_by"
  | "touches_table" | "handles_route" | "gated_by";

/**
 * Dieselben Arten als Werte, in Anzeigereihenfolge. Die `satisfies`-Klausel
 * sorgt dafür, dass ein neuer Name im Typ oben hier einen Fehler auslöst statt
 * still zu fehlen — beide Listen spiegeln `cs-core/src/lib.rs:140-236`, ebenso
 * wie `ALL_EDGE_KINDS`/`ALL_NODE_KINDS` in `api/routers/codegraph.py`.
 */
export const CODE_EDGE_KINDS = [
  "calls", "reads", "writes", "contains", "imports", "inherits", "implements",
  "param_type", "returns_type", "throws", "tested_by", "touches_table",
  "handles_route", "gated_by",
] as const satisfies readonly CodeEdgeKind[];

export const CODE_NODE_KINDS = [
  "function", "method", "class", "interface", "module", "file", "field",
  "global", "route", "test", "db_table", "db_column", "config_key",
  "external_package", "dynamic_gap",
] as const satisfies readonly CodeNodeKind[];

export type CodeDirection = "in" | "out" | "both";

export type CodeSpan = {
  start_byte: number;
  end_byte: number;
  start_line: number;
  end_line: number;
};

export type CodeSymbolHit = {
  id: CodeNodeId;
  name: string;
  qualified: string;
  kind: CodeNodeKind;
  lang: string;
  path: string;
  line: number;
  relevance: number;
};

export type CodeTextHit = {
  path: string;
  line: number;
  text: string;
  in_symbol: CodeNodeId | null;
};

export type CodeParam = {
  name: string;
  type_name?: string | null;
  default?: string | null;
};

export type CodeFacts = {
  signature: string;
  params: CodeParam[];
  returns?: string | null;
  throws: string[];
  side_effects: string[];
  complexity: number;
  loc: number;
  max_nesting: number;
  callers: number;
  callees: number;
  pure?: boolean | null;
  weakest_edge?: CodeConfidence | null;
};

export type CodeMetrics = {
  pagerank: number;
  fan_in: number;
  fan_out: number;
  reach_depth?: number | null;
  churn: number;
  risk: number;
  authors: number;
  last_touched?: number | null;
  coverage?: number | null;
  hits?: number | null;
  relevance: number;
};

export type CodeNodeDetail = {
  id: CodeNodeId;
  name: string;
  qualified: string;
  kind: CodeNodeKind;
  lang: string;
  path: string;
  span: CodeSpan;
  parent: CodeNodeId | null;
  doc?: string | null;
  facts?: CodeFacts | null;
  metrics: CodeMetrics;
};

/** Ein Nachbar im Graphen — samt Sicherheitsstufe und Fundstelle des Belegs. */
export type CodeNeighbour = {
  node: CodeSymbolHit;
  kind: CodeEdgeKind;
  confidence: CodeConfidence;
  evidence_path: string;
  evidence_line: number;
  /** >1 heißt: der Name passt auf mehrere Ziele, es ist eine Vermutung. */
  candidates: number;
  occurrences: number;
};

/**
 * Eine Kante im Ausschnitt. `from`/`to` sind Hex-Strings — nie `Number()`.
 *
 * `evidence_path`/`evidence_line` zeigen auf die *Aufrufstelle*, nicht auf das
 * Ziel: „hier steht der Beleg für diese Beziehung".
 */
export type CodeSliceEdge = {
  from: CodeNodeId;
  to: CodeNodeId;
  kind: CodeEdgeKind;
  confidence: CodeConfidence;
  occurrences: number;
  evidence_line: number;
  evidence_path: string;
};

export type CodeGraphSlice = {
  nodes: CodeSymbolHit[];
  edges: CodeSliceEdge[];
  /** Das Budget war erschöpft — der Ausschnitt ist gekürzt, nicht vollständig. */
  truncated: boolean;
};

export type CodePathStep = {
  id: CodeNodeId;
  name: string;
  qualified: string;
  path: string;
  line: number;
};

/** `null` heißt „kein **Aufruf**pfad" — verfolgt werden nur calls/reads/writes. */
export type CodePathResult = { path: CodePathStep[] | null };

// --- Auswirkungsanalyse (Stufe 2) — „was bricht, wenn ich das ändere?" ---
export type CodeImpactNode = {
  node: CodeSymbolHit;
  /** Hop-Distanz vom geänderten Symbol; 1 = direkter Aufrufer. */
  hops: number;
  /** Schwächste Sicherheitsstufe entlang des besten Pfades. */
  confidence: CodeConfidence;
};

export type CodeImpactFile = {
  path: string;
  symbols: number;
  churn: number;
  risk: number;
};

export type CodeImpact = {
  root: CodeNodeId;
  max_depth: number;
  reached: CodeImpactNode[];
  direct_callers: CodeNeighbour[];
  tests: CodeSymbolHit[];
  dynamic_gaps: CodeSymbolHit[];
  files: CodeImpactFile[];
  truncated: boolean;
  edge_kinds: string[];
};

// --- Spaghetti-Löser (Stufe 2) — Diagnose ohne LLM, Vorschlag mit LLM ---
export type HotspotRule = {
  /** Welche Regel gerissen wurde: ``loc``/``complexity``/``max_nesting``/``fan_in``/``fan_out``/``churn``. */
  rule: string;
  /** Der gemessene Wert — das Belegprinzip auf Zahlen. */
  value: number;
  threshold: number;
};

export type Hotspot = {
  node: CodeSymbolHit;
  loc: number;
  complexity: number;
  max_nesting: number;
  fan_in: number;
  fan_out: number;
  churn: number;
  risk: number;
  rules: HotspotRule[];
};

export type CycleEdge = {
  from: CodeNodeId;
  to: CodeNodeId;
  kind: string;
  confidence: CodeConfidence;
  evidence_path: string;
  evidence_line: number;
};

export type CycleNode = {
  id: CodeNodeId;
  path: string;
  name: string;
  kind: string;
  line: number;
};

export type Cycle = {
  level: "file" | "symbol";
  size: number;
  nodes: CycleNode[];
  edges: CycleEdge[];
  weakest: CodeConfidence;
};

export type RefactorProposal = {
  node_id: CodeNodeId;
  begruendung: string;
  dateien: { pfad: string; inhalt: string }[];
  geloescht: string[];
  valid: boolean;
  errors: string[];
  provider: string;
  model: string;
};

// --- Diagramme ---------------------------------------------------------------
// Auch hier gilt die Regel: jede Kante trägt Sicherheitsstufe und Belegstelle.
// Ein Diagramm ist das Autoritativste, was ein Werkzeug ausgeben kann — ohne die
// Marker würde es eine geratene Kante in eine gezeichnete Tatsache verwandeln.

export type CodeDiagramMember = {
  name: string;
  kind: CodeNodeKind;
  signature?: string | null;
};

export type CodeDiagramClass = {
  id: CodeNodeId;
  name: string;
  path: string;
  line: number;
  kind: CodeNodeKind;
  members: CodeDiagramMember[];
};

export type CodeDiagramEdge = {
  from: string;
  to: string;
  kind: CodeEdgeKind;
  confidence: CodeConfidence;
  evidence_path: string;
  evidence_line: number;
};

export type CodeSequenceStep = {
  from: string;
  to: string;
  to_id: CodeNodeId;
  confidence: CodeConfidence;
  candidates: number;
  evidence_path: string;
  evidence_line: number;
};

export type CodeDiagram = {
  classes: CodeDiagramClass[];
  inherits: CodeDiagramEdge[];
  sequence: CodeSequenceStep[];
};

export type CodeBlueprint = {
  focus: CodeNodeDetail;
  callers: CodeNeighbour[];
  callees: CodeNeighbour[];
  children: CodeNeighbour[];
};

export type CodeStats = {
  files: number;
  parsed_files: number;
  nodes: number;
  edges: number;
  guessed_edges: number;
  dynamic_gaps: number;
};

export type CodeHotFile = {
  path: string;
  churn: number;
  risk: number;
};

export type CodeOverview = {
  stats: CodeStats;
  important: CodeSymbolHit[];
  hot_files: CodeHotFile[];
  dependencies: CodeSymbolHit[];
  gaps: CodeSymbolHit[];
};

/**
 * Ein Bereich des Projekts — ein Pfad-Präfix und alles darunter.
 *
 * Die Identität ist der `path`, nicht eine ID: Bereiche sind Ordner, und ein
 * Ordner ist über seinen Pfad eindeutig und im Deep-Link lesbar.
 */
export type CodeCluster = {
  path: string;
  /** Ordnername oder, wenn hinterlegt, der vom Modell vergebene Name. */
  label: string;
  depth: number;
  has_children: boolean;
  symbols: number;
  files: number;
  kinds: [CodeNodeKind, number][];
  relevance: number;
  /** Anteil geratener ausgehender Beziehungen, 0..1. */
  guessed_share: number;
  top_symbols: CodeSymbolHit[];
  fingerprint: string;
  /** `directory` = Ordnername, `llm` = vom Modell benannt. */
  label_source: "directory" | "llm";
  /** Ein Satz zum Zweck — nur bei `label_source === "llm"`. */
  purpose: string | null;
  /** Der hinterlegte Name passt nicht mehr zur jetzigen Gestalt des Bereichs. */
  label_stale: boolean;
};

/**
 * Eine aufsummierte Beziehung zwischen zwei Bereichen.
 *
 * `weakest` ist die **schwächste** enthaltene Sicherheitsstufe, nicht die
 * häufigste: neunzig belegte und zehn geratene Kanten ergeben `guessed`. Sonst
 * würde auf genau der Zoomstufe, auf der niemand nachsehen kann, aus einer
 * Vermutung eine Tatsache.
 */
export type CodeClusterEdge = {
  from: string;
  to: string;
  count: number;
  occurrences: number;
  weakest: CodeConfidence;
  kinds: [CodeEdgeKind, number][];
};

export type CodeClusterLevel = {
  prefix: string;
  parent: string | null;
  nodes: CodeCluster[];
  edges: CodeClusterEdge[];
};

export type CodeClusterLabel = {
  id: string;
  cluster_path: string;
  label: string;
  purpose: string | null;
  source: string;
};

export type CodeClusterNameEvent =
  | { event: "activity"; text: string }
  | { event: "failed"; error: string; kind?: string }
  | { event: "done"; labels: CodeClusterLabel[]; skipped?: string };

/**
 * Ein Symbol, das zu einer Frage gehört — mit dem Grund, warum es dasteht.
 *
 * `why` ist nicht Zierde. „zitiert" heisst, die Stelle steht belegt in der
 * Antwort; „vorab-suche" heisst nur, dass sie im Kontext lag und das Modell sie
 * vielleicht nie gelesen hat. Ohne diesen Unterschied wäre die Trefferliste
 * eine Behauptung mit dem Aussehen eines Ergebnisses.
 */
export type CodeFocusNode = {
  id: CodeNodeId;
  name: string;
  qualified: string;
  kind: CodeNodeKind;
  path: string;
  line: number;
  relevance: number;
  why: "zitiert" | "spur" | "nachgeschlagen" | "vorab-suche";
};

export type CodeChat = {
  id: string;
  code_project_id: string;
  project_id: string | null;
  title: string | null;
  /** Die Rust-Sitzung: ein Gespräch = eine Lizenz zum Zitieren. */
  session_key: string;
  created_timestamp?: string;
  updated_timestamp?: string;
};

export type CodeChatTurn = {
  id: string;
  chat_id: string;
  ordinal: number;
  question: string;
  answer: string;
  citations: CodeCitation[];
  trail: CodeTrailStep[];
  focus_nodes: CodeFocusNode[];
  papers: CodePaperEvidence[];
  verdict: CodeVerdict | null;
  tool_calls: number;
  truncated: boolean;
  provider: string | null;
  model: string | null;
  /** Dieses Modell lief nicht auf diesem Rechner (`:cloud`). */
  remote_model: boolean;
  created_timestamp?: string;
};

export type CodeIndexStatus = {
  code_project_id: string;
  name?: string | null;
  path?: string | null;
  binary_available: boolean;
  binary_hint?: string | null;
  index_exists: boolean;
  db_path: string;
  status: "none" | "pending" | "indexing" | "ready" | "failed";
  stats: CodeStats;
  skipped: Record<string, number>;
  duration_ms?: number | null;
  commits_walked?: number | null;
  error_message?: string | null;
  last_indexed_timestamp?: string | null;
};

/** Phasennamen kommen unverändert aus cs-workspace. */
export type CodeIndexPhase =
  | "scanning" | "parsing" | "resolving" | "history" | "ranking" | "done";

export type CodeIndexEvent =
  | { event: "started"; code_project_id: string }
  | { event: "progress"; phase: CodeIndexPhase; done: number; total: number; detail: string }
  | { event: "failed"; error: string }
  | {
      event: "done";
      report: {
        files_seen: number;
        files_parsed: number;
        files_reused: number;
        files_removed: number;
        skipped: Record<string, number>;
        duration_ms: number;
        commits_walked: number;
        history_truncated: boolean;
      };
      stats: CodeStats;
    };

export type CodeSourceText = {
  path: string;
  text: string;
  /** Datei hat sich seit dem Indizieren geändert — Zeilennummern passen nicht mehr. */
  stale: boolean;
};

/** Der Quelltext *einer Funktion*, nicht der Datei. */
export type CodeSymbolSource = {
  node_id: CodeNodeId;
  qualified: string | null;
  lang: string | null;
  path: string;
  start_line: number;
  end_line: number;
  text: string;
  /**
   * Der Index kennt einen älteren Stand — die Zeilennummern zeigen woandershin.
   * Dann wird angezeigt, aber nicht geschrieben.
   */
  stale: boolean;
  /** Zustand der Datei beim Lesen; muss beim Schreiben noch passen. */
  content_hash: string;
};

export type CodeSymbolWrite = {
  node_id: CodeNodeId;
  path: string;
  start_line: number;
  end_line: number;
  written: boolean;
  content_hash: string;
  /** Nach dem Schreiben zeigen die Zeilennummern im Graphen woandershin. */
  index_stale: boolean;
};

export type CodeTerminalPosition = {
  path: string;
  line: number;
  node_id: CodeNodeId | null;
  qualified: string | null;
};

// --- Der Begleiter: geprüfte Antworten ---------------------------------------
// Belege werden hier **geprüft, nicht erbeten**. Der Status jedes Zitats kommt
// aus `cs_llm::citation` (vier Prüfungen: Datei im Index, Zeilen in dieser
// Sitzung nachgeschlagen, Datei seither unverändert, Zitat byte-gleich) und
// muss in der Anzeige sichtbar bleiben — ein ungeprüftes Zitat, das aussieht wie
// ein geprüftes, ist schlimmer als gar keins.

export type CodeCitationStatus =
  /** Abgerufen, aktuell, wörtlich — der einzige Status, der als Beleg zählt. */
  | "verified"
  /** Die Datei gibt es, diese Zeilen hat das Modell aber nie gesehen. */
  | "not_retrieved"
  /** Datei seit dem Indizieren geändert — die Zeilennummern bedeuten nichts mehr. */
  | "stale"
  /** Keine solche Datei im Index. */
  | "unknown_file";

export type CodeVerdict = "sound" | "uncited" | "broken";

export type CodeCitation = {
  path: string;
  from_line: number;
  to_line: number;
  status: CodeCitationStatus;
  /** Zeichenversätze im Antworttext (im Backend von Bytes auf JS umgerechnet). */
  start: number;
  end: number;
};

export type CodeTrailStep = {
  path: string;
  line: number;
  reason: string;
  node_id: CodeNodeId | null;
  /** Falsch heißt: der Schritt zeigt auf Code, den das Modell nie gesehen hat. */
  verified: boolean;
};

export type CodePaperEvidence = {
  paper_id: string;
  title: string;
  year: number | null;
  snippets: string[];
};

export type CodeAnswer = {
  id?: string | null;
  question: string;
  answer: string;
  trail_text: string;
  citations: CodeCitation[];
  trail: CodeTrailStep[];
  verdict: CodeVerdict;
  verdict_label: string;
  is_clean: boolean;
  quote_mismatches: string[];
  uncited_sentences: number;
  tool_calls: number;
  truncated: boolean;
  /** Der Anbieter kann kein Tool-Calling — die Antwort entstand ohne Werkzeuge. */
  tool_calling_fallback: boolean;
  provider: string;
  model: string;
  papers: CodePaperEvidence[];
  paper_citations: string[];
  /** Anzahl nackter `[1]`-Verweise. Laut CLAUDE.md ein Qualitätsfehler. */
  bare_citations: number;
  /** Alle Symbole, die zu dieser Frage gehören — je mit `why`. */
  focus_nodes?: CodeFocusNode[];
  /** Nur im Chat gesetzt: Platz im Gespräch. */
  ordinal?: number;
  /** Nur im Chat gesetzt: das Modell lief nicht auf diesem Rechner. */
  remote_model?: boolean;
  persist_error?: string | null;
};

/** Historieneintrag aus `code_answers` — dieselben Felder, aus der DB gelesen. */
export type CodeAnswerRecord = {
  id: string;
  code_project_id: string;
  project_id?: string | null;
  question: string;
  answer: string;
  citations: CodeCitation[];
  trail: CodeTrailStep[];
  verdict: CodeVerdict | "";
  tool_calls: number;
  provider?: string | null;
  model?: string | null;
  created_timestamp?: string;
};

/** Erklärung eines einzelnen Symbols — dieselbe Beleg-Prüfung wie eine Antwort. */
export type CodeExplanation = {
  node_id: CodeNodeId;
  text: string;
  citations: CodeCitation[];
  verdict: CodeVerdict | null;
  verdict_label: string | null;
  is_clean: boolean;
  quote_mismatches: string[];
  provider: string;
  model: string;
};

export type CodeExplainEvent =
  | { event: "activity"; text: string }
  | { event: "failed"; error: string; kind?: string }
  | { event: "done"; answer: CodeExplanation };

// --- Spaghetti-Löser: Vorschlag (SSE) und Probelauf (SSE) ---
export type CodeRefactorEvent =
  | { event: "activity"; text: string }
  | { event: "failed"; error: string; kind?: string; raw?: string }
  | { event: "done"; proposal: RefactorProposal };

export type CodeRefactorAppliedEntry = { path: string; action: string; reason?: string };
export type CodeRefactorApplied = {
  written: CodeRefactorAppliedEntry[];
  deleted: CodeRefactorAppliedEntry[];
};

export type CodeRefactorTryEvent =
  | { event: "activity"; text: string }
  | { event: "applied"; applied: CodeRefactorApplied }
  | { event: "failed"; error: string; errors?: string[]; applied?: CodeRefactorApplied }
  | { event: "done"; sandbox_id: string; run: SandboxRunResult; applied: CodeRefactorApplied; node_id: CodeNodeId };

/**
 * „Warum wurde das so gebaut" — die zwei Hälften, absichtlich zwei Typen.
 *
 * Der Prompt einer erzeugenden KI ist nirgends aufgezeichnet. Was es gibt, ist
 * *Aufgezeichnetes* (git über genau diese Zeilen, selbst hinterlegte
 * Begründungen) und *Hergeleitetes* (zwei Sätze eines Modells). Ein gemeinsamer
 * Typ lüde dazu ein, beides in einen Absatz zu rendern — und dann liesse sich
 * eine erfundene Absicht nicht mehr von einem Commit-Betreff unterscheiden.
 */
export type CodeCommit = {
  hash: string;
  author: string;
  date: string;
  subject: string;
};

export type CodeRationale = {
  id: string;
  code_project_id: string;
  rel_path: string;
  start_line: number | null;
  end_line: number | null;
  symbol_id: string | null;
  text: string;
  content_hash: string | null;
  author: string | null;
  created_timestamp?: string | null;
  /** Datei hat sich seit dem Festhalten geändert — die Begründung ist überholt. */
  stale: boolean;
};

export type CodeRecordedRationale = {
  node_id: CodeNodeId;
  path: string;
  start_line: number;
  end_line: number;
  history: {
    available: boolean;
    /** Warum es nichts gibt: `no_git` | `no_repo` | `no_commits` | `untracked` | `error`. */
    reason: string | null;
    error?: string | null;
    commits: CodeCommit[];
  };
  notes: CodeRationale[];
};

/** Die hergeleitete Hälfte. `kind: "derived"` steht dran, damit es drangeschrieben wird. */
export type CodeDerivedRationale = {
  node_id: CodeNodeId;
  path: string;
  start_line: number;
  end_line: number;
  kind: "derived";
  text: string;
  citations: CodeCitation[];
  verdict: CodeVerdict | null;
  verdict_label: string | null;
  is_clean: boolean;
  quote_mismatches: string[];
  /** Worauf die zwei Sätze beruhen — Anzahl je Quelle, nicht „vertrau mir". */
  based_on: { tests: number; callers: number; commits: number; notes: number };
  provider: string;
  model: string;
};

export type CodeWhyEvent =
  | { event: "activity"; text: string }
  | { event: "failed"; error: string; kind?: string }
  | { event: "done"; answer: CodeDerivedRationale };

export type CodeAskEvent =
  | { event: "activity"; text: string }
  | { event: "failed"; error: string; kind?: string }
  | { event: "done"; answer: CodeAnswer };

/** Ein Code-Zitat in einer Notiz. Liegt in `note_citations` neben den Papern. */
export type CodeNoteCitation = {
  id: string;
  note_id: string;
  /** Synthetisch: `code:<projekt>:<pfad>:<zeile>` — so lesen alte Abfragen weiter. */
  paper_id: string;
  title?: string | null;
  source_kind: "code";
  code_project_id: string;
  rel_path: string;
  start_line: number;
  end_line: number;
  reference_text?: string | null;
  content_hash?: string | null;
  /** Datei seit dem Zitieren geändert — die Zeilennummer zeigt woandershin. */
  stale?: boolean;
  /** Datei ganz weg. */
  missing?: boolean;
};

export type CodePaperLink = {
  id: string;
  code_project_id: string;
  project_id?: string | null;
  paper_id?: string | null;
  symbol_id?: string | null;
  rel_path?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  kind: string;
  note?: string | null;
  created_timestamp?: string;
};

export type GlossaryEntry = {
  id: string; term: string; term_key: string; explanation: string;
  created_at: string; updated_at: string;
};
