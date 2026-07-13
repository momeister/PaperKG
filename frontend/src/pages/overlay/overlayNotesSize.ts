// Shared sizing state for the overlay's Notizen tab: persisted across tab-switches and
// app restarts (localStorage), read fresh by OverlayPage.tsx's resize effect and written
// by OverlayNotesPanel.tsx's corner drag handle. Split out to avoid a circular import
// between the two (OverlayPage.tsx renders OverlayNotesPanel, not vice versa).

export type OverlaySize = { width: number; height: number };

export const NOTES_SIZE_KEY = "sciencekg.overlay.notesSize";

/** Unchanged from the previous hardcoded default — first-run behavior stays the same. */
export const DEFAULT_NOTES_SIZE: OverlaySize = { width: 800, height: 640 };

/** Never smaller than the compact Agent/Companion window. */
export const MIN_NOTES_SIZE: OverlaySize = { width: 420, height: 360 };

/** Compact Agent/Companion size — unchanged. */
export const AGENT_SIZE: OverlaySize = { width: 420, height: 540 };

export function maxNotesSize(): OverlaySize {
  const margin = 40;
  const screen = typeof window !== "undefined" ? window.screen : undefined;
  return {
    width: Math.max(MIN_NOTES_SIZE.width, (screen?.availWidth ?? 1600) - margin),
    height: Math.max(MIN_NOTES_SIZE.height, (screen?.availHeight ?? 1200) - margin)
  };
}

export function clampNotesSize(size: OverlaySize): OverlaySize {
  const max = maxNotesSize();
  return {
    width: Math.min(max.width, Math.max(MIN_NOTES_SIZE.width, Math.round(size.width))),
    height: Math.min(max.height, Math.max(MIN_NOTES_SIZE.height, Math.round(size.height)))
  };
}

export function loadOverlayNotesSize(): OverlaySize {
  try {
    const raw = window.localStorage.getItem(NOTES_SIZE_KEY);
    if (!raw) return DEFAULT_NOTES_SIZE;
    const parsed = JSON.parse(raw) as Partial<OverlaySize>;
    if (!Number.isFinite(parsed.width) || !Number.isFinite(parsed.height)) return DEFAULT_NOTES_SIZE;
    return clampNotesSize({ width: parsed.width as number, height: parsed.height as number });
  } catch {
    return DEFAULT_NOTES_SIZE;
  }
}

export function saveOverlayNotesSize(size: OverlaySize): void {
  try {
    window.localStorage.setItem(NOTES_SIZE_KEY, JSON.stringify(clampNotesSize(size)));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}
