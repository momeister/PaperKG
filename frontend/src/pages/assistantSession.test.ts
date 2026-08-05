import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { AssistantTurn } from "./AssistantPage";
import { loadAssistantSession, saveAssistantSession } from "./assistantSession";

// saveAssistantSession debounces the server PUT on a 1200ms timer and goes
// through `api.saveWorkspaceSession`. We mock that so tests can observe the
// force flag without waiting for the real debounce.

vi.mock("../api", () => ({
  api: {
    saveWorkspaceSession: vi.fn((_projectId: string, _payload: unknown, _force: boolean) =>
      Promise.resolve({ ok: true })
    ),
    getWorkspaceSession: vi.fn(() => Promise.resolve({ payload: {} })),
  },
}));

const saveWorkspaceSessionMock = api.saveWorkspaceSession as unknown as ReturnType<typeof vi.fn>;

const PROJ = "test-proj";
const STORAGE_KEY = `sciencekg.assistant.session.${PROJ}`;

function turn(id: string): AssistantTurn {
  return {
    id,
    type: "answer",
    question: "Q?",
    answer: { answer: "A", citation_links: [], context_diagnostics: null, source_verification: null },
  } as unknown as AssistantTurn;
}

beforeEach(() => {
  window.localStorage.clear();
  vi.useFakeTimers();
  saveWorkspaceSessionMock.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("saveAssistantSession — empty-save guards", () => {
  it("leaves localStorage intact when history is empty and allowEmpty is not set", () => {
    // Seed a non-empty session in localStorage first (simulating a populated cache).
    saveAssistantSession(PROJ, { history: [turn("t1")], activeTurnId: "t1" });
    vi.advanceTimersByTime(1500);
    const seeded = window.localStorage.getItem(STORAGE_KEY);
    expect(seeded).toBeTruthy();
    saveWorkspaceSessionMock.mockClear();

    // An empty save without allowEmpty must NOT wipe the local cache.
    saveAssistantSession(PROJ, { history: [], activeTurnId: "" });
    vi.advanceTimersByTime(1500);

    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(seeded);
    // No server PUT should fire for the empty save.
    expect(saveWorkspaceSessionMock).not.toHaveBeenCalled();
  });

  it("clears localStorage and sends force=true to the server when allowEmpty is set", () => {
    saveAssistantSession(PROJ, { history: [turn("t1")], activeTurnId: "t1" });
    vi.advanceTimersByTime(1500);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeTruthy();

    saveAssistantSession(PROJ, { history: [], activeTurnId: "" }, { allowEmpty: true });
    vi.advanceTimersByTime(1500);

    // localStorage was overwritten with the empty payload (savedAt bumped).
    const raw = window.localStorage.getItem(STORAGE_KEY);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw || "{}");
    expect(parsed.history).toEqual([]);
    expect(parsed.activeTurnId).toBe("");

    expect(saveWorkspaceSessionMock).toHaveBeenCalledWith(
      PROJ,
      expect.objectContaining({ history: [], activeTurnId: "" }),
      true
    );
  });

  it("loadAssistantSession returns empty defaults when nothing is cached", () => {
    window.localStorage.clear();
    const session = loadAssistantSession(PROJ);
    expect(session.history).toEqual([]);
    expect(session.activeTurnId).toBe("");
    expect(session.savedAt).toBe(0);
  });
});