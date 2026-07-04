import { api } from "../api";
import type { AssistantTurn } from "./AssistantPage";

// Assistant session + note persistence (localStorage fast-boot cache + debounced
// server sync). Extracted from AssistantPage.tsx so the concern lives on its own;
// the public API is re-exported from AssistantPage for backward compatibility.

function assistantStorageKey(projectId: string) {
  return `sciencekg.assistant.session.${projectId}`;
}

function notesStorageKey(projectId: string) {
  return `sciencekg.assistant.notes.${projectId}`;
}

export type AssistantSession = { history: AssistantTurn[]; activeTurnId: string; savedAt?: number };

export function loadAssistantSession(projectId: string): AssistantSession {
  try {
    const raw = window.localStorage.getItem(assistantStorageKey(projectId));
    if (!raw) {
      return { history: [], activeTurnId: "", savedAt: 0 };
    }
    const payload = JSON.parse(raw) as Partial<AssistantSession>;
    const history = Array.isArray(payload.history) ? payload.history : [];
    return {
      history,
      activeTurnId: payload.activeTurnId || history[history.length - 1]?.id || "",
      savedAt: Number(payload.savedAt) || 0
    };
  } catch {
    return { history: [], activeTurnId: "", savedAt: 0 };
  }
}

const serverSessionSaveTimers = new Map<string, number>();

/**
 * Research-tree turns carry the full per-node evidence, PDF excerpts and verification
 * payloads — easily many MB for a deep run. Persisting that verbatim blows the
 * localStorage quota (silent total loss) and bloats the server session, which is why
 * reopened deep analyses showed "0 Knoten" and lost all progress on reload. Drop the
 * heavy, re-derivable fields before persisting; the live in-memory copy keeps everything.
 */
export function slimTurnForPersist(turn: AssistantTurn): AssistantTurn {
  if (turn.type !== "research_tree" || !turn.researchNodes?.length) {
    return turn;
  }
  const nodes = turn.researchNodes.map((node) => {
    const answer = node.answer
      ? { ...node.answer, context_diagnostics: undefined, source_verification: undefined }
      : node.answer;
    const verification = node.verification?.map((src) => ({
      ...src,
      evidence: (src.evidence ?? []).map((ev) => ({
        ...ev,
        pdf_excerpt: "",
        matched_terms: [],
        reference_text: (ev.reference_text ?? "").slice(0, 600),
      })),
    }));
    return { ...node, answer, verification };
  });
  return { ...turn, researchNodes: nodes };
}

export function saveAssistantSession(projectId: string, session: { history: AssistantTurn[]; activeTurnId: string }) {
  const payload = {
    history: session.history.slice(-25).map(slimTurnForPersist),
    activeTurnId: session.activeTurnId,
    savedAt: Date.now(),
  };
  try {
    // localStorage is only a fast boot cache; the payloads (verification excerpts)
    // regularly exceed its quota, which used to lose whole conversations on reload.
    window.localStorage.setItem(assistantStorageKey(projectId), JSON.stringify(payload));
  } catch {
    // Quota exceeded or storage disabled — the server copy below still persists.
  }
  const existing = serverSessionSaveTimers.get(projectId);
  if (existing !== undefined) {
    window.clearTimeout(existing);
  }
  serverSessionSaveTimers.set(
    projectId,
    window.setTimeout(() => {
      serverSessionSaveTimers.delete(projectId);
      api.saveWorkspaceSession(projectId, payload).catch(() => {
        // Offline backend: the localStorage cache above still covers reloads.
      });
    }, 1200)
  );
}

/** Authoritative session from the backend; resolves null when none exists or offline. */
export async function fetchAssistantSession(projectId: string): Promise<AssistantSession | null> {
  try {
    const result = await api.getWorkspaceSession(projectId);
    const payload = (result.payload ?? {}) as Partial<AssistantSession>;
    const history = Array.isArray(payload.history) ? payload.history : [];
    if (!history.length) {
      return null;
    }
    return {
      history,
      activeTurnId: payload.activeTurnId || history[history.length - 1]?.id || "",
      savedAt: Number(payload.savedAt) || 0
    };
  } catch {
    return null;
  }
}

export function loadNotes(projectId: string) {
  try {
    return window.localStorage.getItem(notesStorageKey(projectId)) ?? window.localStorage.getItem("sciencekg.assistant.notes") ?? "";
  } catch {
    return "";
  }
}

export function saveNotes(projectId: string, value: string) {
  try {
    window.localStorage.setItem(notesStorageKey(projectId), value);
  } catch {
    // Local storage can be disabled in private/browser test contexts.
  }
}

export function noteTitleForSave(title: string, markdown: string) {
  const trimmed = title.trim();
  if (trimmed && !isUntitledNoteTitle(trimmed)) {
    return trimmed;
  }
  const suggestion = suggestNoteTitle(markdown);
  return suggestion || trimmed || "Neue Notiz";
}

export function isUntitledNoteTitle(title: string) {
  return ["", "Neue Notiz", "Assistant Notiz"].includes(title.trim());
}

export function suggestNoteTitle(markdown: string) {
  const heading = markdown.match(/^#{1,3}\s+(.+)$/m)?.[1]?.trim();
  const source = heading || markdown;
  const text = source
    .replace(/!\[[^\]]*\]\([^)]+\)/g, " ")
    .replace(/\[[^\]]+\]\([^)]+\)/g, " ")
    .replace(/[#>*_`|[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (text.length < 90 && !heading) {
    return "";
  }
  return text.split(/\s+/).slice(0, 8).join(" ").slice(0, 72);
}

export function formatTurnTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
