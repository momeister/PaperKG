import { api } from "../api";
import { isGreySourcePaperId } from "../citationColors";
import { loadAssistantSession, turnBlocks } from "./AssistantPage";
import type { AssistantTurn } from "./AssistantPage";
import type {
  Answer, CitationLink, ClaimCheckResult, DeepResearchFinding, GreySource,
  NoteAiMessage, NoteAiThread, NoteCitation, Paper, ParallelSession, ResearchNode,
  VerificationEvidence, VerificationSource,
} from "../types";
import type { DroppedSourceKind, WorkspaceCommandDef, WorkspacePaperRecord, WorkspacePdfTarget } from "./WorkspacePage";
import type { NotesSurfaceSnapshot } from "./NotesPage";

// Pure helpers/constants (workspace commands, paper/thread/note utils, UI-state
// storage) extracted from WorkspacePage.tsx. No JSX/component state; re-exported
// from WorkspacePage for backward compatibility.

export const ALL_PAPERS_SCOPES = new Set(["", "__all_papers__"]);
// Obergrenze für den Vorab-Nachcheck unsicherer Zuordnungen (bounded latency/cost).


export const MAX_PREVERIFY_CITATIONS = 6;


export const WORKSPACE_COMMANDS: WorkspaceCommandDef[] = [
  { name: "web", description: "Web-Recherche parallel zur Frage (Treffer manuell übernehmen)", group: "frage" },
  { name: "webfrage", aliases: ["webanswer"], args: "<frage>", description: "Web-Recherche + Antwort direkt aus den Treffern (speichert Grauquellen)", group: "frage" },
  { name: "auto", aliases: ["autorecherche"], args: "[frage]", description: "Auto-Recherche: findet lokal nichts, harvestet automatisch Paper + Web (inkl. verwandter Themen)", group: "frage" },
  { name: "new", aliases: ["neu"], args: "[frage]", description: "Neues Gespräch — mit Frage: sofort stellen; Weiterfragen bleibt an", group: "frage" },
  { name: "selected", aliases: ["auswahl"], args: "[frage]", description: "Auf ausgewählte Quellen eingrenzen", group: "frage" },
  { name: "alle", aliases: ["all"], args: "[frage]", description: "Auf alle Quellen des Projekts erweitern", group: "frage" },
  { name: "summary", args: "[fokus]", description: "Zusammenfassung des aktuellen Papers", group: "frage" },
  { name: "extract", args: "[fokus]", description: "Methoden, Ergebnisse & Schlussfolgerungen", group: "frage" },
  { name: "compare", args: "[fokus]", description: "Papers vergleichen", group: "frage" },
  { name: "kritisch", aliases: ["critical"], args: "[frage]", description: "Kritischer Modus: Limitationen, Risiken & Gegenbelege explizit prüfen (ohne Frage: an/aus)", group: "frage" },
  { name: "projekt", args: "<name>", description: "Neues Projekt anlegen und aktivieren", group: "aktion" },
  { name: "notiz", args: "[titel]", description: "Neue Notiz anlegen", group: "aktion" },
  { name: "suche", aliases: ["papers", "import"], args: "<thema>", description: "Neue Papers suchen & herunterladen (arXiv)", group: "aktion" },
  { name: "extraktion", description: "Extraktion für aktuelles/ausgewählte Paper starten", group: "aktion" },
  { name: "hauptquelle", description: "Aktuell geöffnetes Paper als Hauptquelle setzen/entfernen", group: "aktion" },
  { name: "entfernen", aliases: ["loeschen"], description: "Aktuell geöffnetes Paper bzw. Grauquelle aus dem Projekt entfernen", group: "aktion" },
  { name: "help", aliases: ["hilfe"], description: "Befehlsübersicht anzeigen", group: "frage" }
];


export function matchWorkspaceCommands(query: string): WorkspaceCommandDef[] {
  const needle = query.trim().toLowerCase().split(/\s+/)[0] ?? "";
  if (!needle) {
    return WORKSPACE_COMMANDS;
  }
  return WORKSPACE_COMMANDS.filter(
    (command) => command.name.startsWith(needle) || (command.aliases ?? []).some((alias) => alias.startsWith(needle))
  );
}

/**
 * `/web` (und nur /web) wirkt inline: egal wo es im Prompt steht, es wird entfernt und
 * aktiviert die Web-Recherche für genau diese Frage.
 */


export function extractInlineWebToken(value: string): { text: string; web: boolean } {
  let web = false;
  const text = value
    .replace(/(^|\s)\/web\b/gi, (_match, prefix: string) => {
      web = true;
      return prefix;
    })
    .replace(/\s{2,}/g, " ")
    .trim();
  return { text, web };
}

/** The active turn restored from a persisted session (used to reopen the right view). */


export function restoredActiveTurnFor(projectId: string): AssistantTurn | null {
  const session = loadAssistantSession(projectId);
  return (
    session.history.find((turn) => turn.id === session.activeTurnId) ??
    session.history[session.history.length - 1] ??
    null
  );
}

/** Erkennvermerk, dass eine Antwort faktisch leer ausging und das Web helfen könnte. */


export function answerSuggestsWebSearch(answer: Answer | null | undefined): boolean {
  if (!answer) {
    return false;
  }
  if (answer.no_answer) {
    return true;
  }
  if (answer.context_diagnostics?.fallback_reason === "no_traceable_citations") {
    return true;
  }
  return /not contain enough evidence|does not contain enough|nicht genug (?:Evidenz|Belege)|keine ausreichenden? (?:Evidenz|Belege)|konnte keine .{0,40}finden/i.test(
    answer.answer || ""
  );
}


export const EMPTY_NOTES_SNAPSHOT: NotesSurfaceSnapshot = {
  activeNoteId: "",
  title: "",
  notes: [],
  notesLoading: false,
  currentNote: null,
  citations: [],
  citationRows: [],
  selectedCitation: null,
  threads: [],
  activeThreadId: "",
  historyOpen: false,
  threadMeta: new Map(),
  followUpDrafts: {},
  isFollowUpPending: false,
  deletingThreadId: ""
};


export function sameNotesSnapshot(left: NotesSurfaceSnapshot, right: NotesSurfaceSnapshot) {
  return (
    left.activeNoteId === right.activeNoteId &&
    left.title === right.title &&
    left.notes === right.notes &&
    left.notesLoading === right.notesLoading &&
    left.currentNote === right.currentNote &&
    left.citations === right.citations &&
    left.citationRows === right.citationRows &&
    left.selectedCitation === right.selectedCitation &&
    left.threads === right.threads &&
    left.activeThreadId === right.activeThreadId &&
    left.historyOpen === right.historyOpen &&
    left.threadMeta === right.threadMeta &&
    left.followUpDrafts === right.followUpDrafts &&
    left.isFollowUpPending === right.isFollowUpPending &&
    left.deletingThreadId === right.deletingThreadId
  );
}


export function findingToGreyRecord(finding: DeepResearchFinding): Record<string, unknown> {
  return {
    url: finding.url,
    title: finding.title,
    summary: finding.summary,
    raw_excerpt: finding.raw_excerpt,
    full_text: finding.full_text ?? finding.raw_excerpt,
    evidence: finding.evidence ?? [],
    injection_flags: finding.injection_flags ?? []
  };
}

/** Adapt a (not-yet-saved) web finding to the GreySource shape for the viewer. */


export function findingToGreySource(finding: DeepResearchFinding): GreySource {
  return {
    id: `pending_${finding.url}`,
    project_id: "",
    url: finding.url,
    title: finding.title,
    summary: finding.summary,
    raw_excerpt: finding.raw_excerpt,
    full_text: finding.full_text ?? finding.raw_excerpt,
    evidence: finding.evidence ?? [],
    injection_flags: finding.injection_flags ?? []
  };
}


export function classifyDroppedFile(file: File): DroppedSourceKind {
  const type = (file.type || "").toLowerCase();
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("image/")) return "image";
  const name = (file.name || "").toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (/\.(png|jpe?g|gif|webp|svg|bmp)$/.test(name)) return "image";
  return "unsupported";
}


export const PASTED_URL_PATTERN = /^https?:\/\/\S+$/i;

/** A pasted/dropped plain-text snippet counts as a URL only if it's a single bare link. */


export function classifyPastedText(text: string): "url" | null {
  const trimmed = text.trim();
  return trimmed && !trimmed.includes("\n") && PASTED_URL_PATTERN.test(trimmed) ? "url" : null;
}


export function fileExtension(filename: string) {
  const match = /\.([a-z0-9]+)$/i.exec(filename || "");
  return match ? match[1].toLowerCase() : "";
}


export function activeScopePaperId(target: WorkspacePdfTarget | null, activeAssistantSource: VerificationSource | null) {
  if (target) {
    if (target.kind === "assistant") {
      return target.source.paper_id;
    }
    if (target.kind === "noteCitation") {
      return target.citation.paper_id;
    }
    if (target.kind === "grey") {
      return "";
    }
    if (target.kind === "missing") {
      return target.paperId;
    }
    return workspacePaperId(target.paper);
  }
  return activeAssistantSource?.paper_id ?? "";
}


export function normalizeWorkspacePaper(paper: Paper): Paper {
  const id = workspacePaperId(paper);
  const title = workspacePaperTitle(paper);
  return { ...paper, id, title };
}


export function workspacePaperId(paper: Paper | null | undefined) {
  if (!paper) {
    return "";
  }
  const record = paper as WorkspacePaperRecord;
  return cleanWorkspacePaperText(paper.id) || cleanWorkspacePaperText(record.paper_id) || cleanWorkspacePaperText(record.paperId) || cleanWorkspacePaperText(paper.source_id) || "";
}


export function workspacePaperTitle(paper: Paper | null | undefined) {
  if (!paper) {
    return "Unbenanntes Paper";
  }
  const record = paper as WorkspacePaperRecord;
  return (
    cleanWorkspacePaperText(paper.title) ||
    cleanWorkspacePaperText(record.display_title) ||
    cleanWorkspacePaperText(record.name) ||
    workspacePaperFileTitle(record) ||
    workspacePaperId(paper) ||
    "Unbenanntes Paper"
  );
}


export function cleanWorkspacePaperText(value: unknown) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text || ["undefined", "null", "none", "nan"].includes(text.toLowerCase())) {
    return "";
  }
  return text;
}


export function workspacePaperFileTitle(paper: WorkspacePaperRecord) {
  const raw =
    cleanWorkspacePaperText(paper.pdf_filename) ||
    cleanWorkspacePaperText(paper.file_name) ||
    cleanWorkspacePaperText(paper.filename) ||
    cleanWorkspacePaperText(paper.pdf_path) ||
    cleanWorkspacePaperText(paper.path) ||
    cleanWorkspacePaperText(paper.pdf_url);
  if (!raw) {
    return "";
  }
  const withoutQuery = raw.split(/[?#]/)[0] || raw;
  const fileName = withoutQuery.split(/[\\/]/).pop() || withoutQuery;
  const decoded = decodeWorkspacePaperText(fileName);
  return cleanWorkspacePaperText(decoded.replace(/\.pdf$/i, "").replace(/[_-]+/g, " "));
}


export function decodeWorkspacePaperText(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}


export function noteCitationEvidence(citation: NoteCitation): VerificationEvidence[] {
  return [
    {
      evidence_id: citation.evidence_id || undefined,
      paper_id: citation.paper_id,
      kind: citation.kind || "note",
      reference_text: citation.reference_text || "",
      pdf_excerpt: citation.pdf_excerpt || "",
      matched_terms: textTerms(`${citation.reference_text ?? ""} ${citation.pdf_excerpt ?? ""}`),
      found_in_pdf_text: Boolean(citation.pdf_excerpt),
      evidence_index: citation.evidence_index ?? 0
    }
  ];
}


export function latestThreadAnswer(thread: NoteAiThread) {
  const messages = threadMessages(thread);
  const answer = [...messages].reverse().find((message) => message.role === "assistant")?.content;
  return (answer || thread.replacement_text || thread.response_text || "").trim();
}


export function threadMessages(thread: NoteAiThread): NoteAiMessage[] {
  if (thread.messages?.length) {
    const hidden = new Set((Array.isArray(thread.ui_state?.hidden_message_ids) ? thread.ui_state?.hidden_message_ids : []).map((item) => String(item)));
    return thread.messages.filter((message) => !hidden.has(message.id));
  }
  return [
    {
      id: `${thread.id}:user`,
      thread_id: thread.id,
      note_id: thread.note_id,
      role: "user",
      content: thread.instruction,
      created_timestamp: thread.created_timestamp
    },
    {
      id: `${thread.id}:assistant`,
      thread_id: thread.id,
      note_id: thread.note_id,
      role: "assistant",
      content: thread.response_text,
      created_timestamp: thread.created_timestamp
    }
  ];
}


export function threadCollapsed(thread: NoteAiThread) {
  return thread.ui_state?.collapsed !== false;
}


export function threadPinned(thread: NoteAiThread) {
  return thread.ui_state?.pinned === true;
}


export function sortPinnedThreads(threads: NoteAiThread[]) {
  return threads
    .map((thread, index) => ({ thread, index }))
    .sort((left, right) => {
      const pinnedDelta = Number(threadPinned(right.thread)) - Number(threadPinned(left.thread));
      return pinnedDelta || left.index - right.index;
    })
    .map((item) => item.thread);
}


export function shortSelectionPreview(value: string) {
  const text = stripThreadContext(value).replace(/\s+/g, " ").trim();
  return text.length <= 260 ? text : `${text.slice(0, 257)}...`;
}


export function stripThreadContext(value: string) {
  return String(value || "").replace(/^==([\s\S]*)==$/, "$1").trim();
}


export function textTerms(text: string) {
  return Array.from(new Set(text.toLowerCase().replace(/[^a-z0-9-]+/g, " ").split(" "))).filter((term) => term.length >= 5).slice(0, 12);
}


export function normalizeFilter(value: string) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}


export function workspaceUiKey(projectId: string, key: string) {
  return `sciencekg.workspace.ui.${projectId}.${key}`;
}


export function loadWorkspaceBoolean(projectId: string, key: string, fallback: boolean) {
  try {
    const value = window.localStorage.getItem(workspaceUiKey(projectId, key));
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}


export function saveWorkspaceBoolean(projectId: string, key: string, value: boolean) {
  try {
    window.localStorage.setItem(workspaceUiKey(projectId, key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}


export function loadWorkspaceNumber(projectId: string, key: string, fallback: number) {
  try {
    const value = Number(window.localStorage.getItem(workspaceUiKey(projectId, key)));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}


export function saveWorkspaceNumber(projectId: string, key: string, value: number) {
  try {
    window.localStorage.setItem(workspaceUiKey(projectId, key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}

// ── Research Tree View ─────────────────────────────────────────────────────

/**
 * Build the citation-resolution pool for a reloaded research tree.
 *
 * Citations only render as coloured/clickable chips when their token resolves to a
 * source in the pool; otherwise they fall back to the muted "!"-chip. The heavy
 * verification payload is trimmed when a session is persisted (localStorage/server
 * quota), so on reload `node.verification` can be empty — which used to grey out
 * *every* citation. `answer.sources` is small and always survives persistence, so we
 * fold it in as a minimal fallback: verification wins (richer evidence), and any
 * source not already covered is added so its citation still resolves and keeps its
 * colour (real papers vivid, `grey::` sources grey by intent).
 */


export function citationPoolFor(
  verification: VerificationSource[] | undefined,
  answer: ResearchNode["answer"] | null | undefined,
): VerificationSource[] {
  const pool: VerificationSource[] = [...(verification ?? [])];
  const seen = new Set(pool.map((s) => s.paper_id));
  for (const src of answer?.sources ?? []) {
    if (!src.paper_id || seen.has(src.paper_id)) continue;
    seen.add(src.paper_id);
    pool.push({ paper_id: src.paper_id, title: src.title || src.paper_id, pdf_available: false, evidence: [] });
  }
  return pool;
}
