import { useId, useSyncExternalStore } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "../api";

const BACKUP_KEY = "sciencekg.notes.unsavedDrafts.v1";
export type NoteDraft = { title: string; markdown: string; dirty: boolean; revision: number; saving: boolean; error: string };
type Setter<T> = T | ((value: T) => T);
type SavedNote = Awaited<ReturnType<typeof api.updateNote>>;

/** A single writer per document across every NotesSurface in the main app.
 * Timers and in-flight saves belong here, not to a route or editor instance. */
export class NoteDraftStore {
  private drafts = new Map<string, NoteDraft>();
  private listeners = new Set<() => void>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private pending = new Map<string, Promise<SavedNote>>();
  private atomic = new Map<string, Promise<void>>();
  private acknowledged = new Map<string, SavedNote["note"]>();
  private storageError = "";
  private recoveryError = false;
  queryClient?: QueryClient;
  constructor(private save: typeof api.updateNote = (...args) => api.updateNote(...args), private storage: Storage = localStorage) {
    try {
      const data: unknown = JSON.parse(storage.getItem(BACKUP_KEY) ?? "{}");
      if (data && typeof data === "object") for (const [id, value] of Object.entries(data)) {
        if (value && typeof value.title === "string" && typeof value.markdown === "string") {
          this.drafts.set(id, { title: value.title, markdown: value.markdown, dirty: true, revision: 1, saving: false, error: "" });
        }
      }
    } catch { this.recoveryError = true; this.storageError = "Die lokale Entwurfssicherung konnte nicht gelesen werden. Die Sicherung bleibt erhalten."; }
  }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  get = (id: string): NoteDraft => {
    if (!this.drafts.has(id)) this.drafts.set(id, { title: "", markdown: "", dirty: false, revision: 0, saving: false, error: this.storageError });
    return this.drafts.get(id)!;
  };
  seed(id: string, title: string, markdown: string) {
    if (this.get(id).dirty) return;
    this.publish(id, { title, markdown, dirty: false });
  }
  acknowledge(note: SavedNote["note"]) { this.acknowledged.set(note.id, note); }
  isStaleQuery(note: SavedNote["note"]) {
    const saved = this.acknowledged.get(note.id);
    if (!saved || (saved.markdown === note.markdown && saved.title === note.title)) return false;
    return !note.updated_timestamp || note.updated_timestamp <= (saved.updated_timestamp ?? "");
  }
  forget(id: string) {
    this.acknowledged.delete(id);
    clearTimeout(this.timers.get(id)); this.timers.delete(id);
    this.publish(id, { title: "", markdown: "", dirty: false, error: "" }); this.backup();
  }
  beginAtomic(id: string) {
    if (!id) return () => {};
    if (this.atomic.has(id) || this.pending.has(id)) throw new Error("Notiz wird noch gespeichert. Bitte erneut versuchen.");
    clearTimeout(this.timers.get(id)); this.timers.delete(id);
    let release!: () => void;
    this.atomic.set(id, new Promise<void>(resolve => { release = resolve; }));
    this.publish(id, { saving: true });
    return () => { this.atomic.delete(id); this.publish(id, { saving: false }); release(); if (this.get(id).dirty) this.schedule(id); };
  }
  async exclusive<T>(id: string, operation: () => Promise<T>): Promise<T> {
    while (this.atomic.has(id) || this.pending.has(id)) await (this.atomic.get(id) ?? this.pending.get(id));
    const release = this.beginAtomic(id);
    try { return await operation(); } finally { release(); }
  }
  replace(note: SavedNote["note"], expectedRevision: number) {
    this.acknowledge(note);
    if (this.get(note.id).revision !== expectedRevision) return;
    this.publish(note.id, { title: note.title, markdown: note.markdown, dirty: false, error: "", revision: expectedRevision + 1 });
    this.backup();
  }
  private publish(id: string, patch: Partial<NoteDraft>) {
    this.drafts.set(id, { ...this.get(id), ...patch }); this.listeners.forEach(fn => fn());
  }
  set<K extends "title" | "markdown" | "dirty">(id: string, field: K, update: Setter<NoteDraft[K]>) {
    const draft = this.get(id);
    const value = typeof update === "function" ? (update as (v: NoteDraft[K]) => NoteDraft[K])(draft[field]) : update;
    if (draft[field] === value) return;
    this.publish(id, { [field]: value, revision: draft.revision + 1 });
    if (this.get(id).dirty && !id.startsWith("unsaved:")) {
      this.backup(); this.schedule(id);
    }
    if (field === "dirty" && value === false) this.backup();
  }
  private schedule(id: string) {
    clearTimeout(this.timers.get(id));
    this.timers.set(id, setTimeout(() => { this.timers.delete(id); void this.flushOne(id).catch(() => {}); }, 1400));
  }
  private backup() {
    if (this.recoveryError) return;
    try {
      this.storage.setItem(BACKUP_KEY, JSON.stringify(Object.fromEntries([...this.drafts].filter(([id, d]) => d.dirty && !id.startsWith("unsaved:"))
        .map(([id, d]) => [id, { title: d.title, markdown: d.markdown }]))));
      this.storageError = "";
    } catch {
      this.storageError = "Entwurf konnte lokal nicht gesichert werden. Bitte vor dem Beenden erneut speichern.";
      for (const [id, draft] of this.drafts) if (draft.dirty) this.publish(id, { error: this.storageError });
    }
  }
  async flushOne(id: string): Promise<SavedNote | undefined> {
    clearTimeout(this.timers.get(id)); this.timers.delete(id);
    if (this.atomic.has(id)) await this.atomic.get(id);
    if (this.pending.has(id)) {
      await this.pending.get(id);
      return this.get(id).dirty ? this.flushOne(id) : undefined;
    }
    const draft = this.get(id);
    if (!draft.dirty || id.startsWith("unsaved:")) return;
    const { title, markdown, revision } = draft;
    this.publish(id, { saving: true, error: "" });
    const operation = this.save(id, { title, markdown });
    this.pending.set(id, operation);
    try {
      const result = await operation;
      this.acknowledge(result.note);
      if (this.get(id).revision === revision) this.publish(id, { dirty: false });
      this.queryClient?.setQueryData(["note", id], result);
      void this.queryClient?.invalidateQueries({ queryKey: ["notes"] });
      this.backup();
      return result;
    } catch (error) {
      this.publish(id, { error: error instanceof Error ? error.message : String(error) });
      this.backup(); throw error;
    } finally {
      this.pending.delete(id); this.publish(id, { saving: false });
      // Edits made while saving belong to the next revision.
      if (this.get(id).dirty && !this.get(id).error) this.schedule(id);
    }
  }
  async flushAll() {
    this.backup();
    const results = await Promise.allSettled([...this.drafts].filter(([, d]) => d.dirty || d.saving).map(async ([id]) => {
      do { await this.flushOne(id); } while (this.get(id).dirty && !id.startsWith("unsaved:"));
    }));
    const failed = results.find(r => r.status === "rejected");
    if (failed?.status === "rejected") throw failed.reason;
    if (this.storageError) throw new Error(this.storageError);
  }
  recover() { for (const [id, draft] of this.drafts) if (draft.dirty) this.schedule(id); }
}

export const noteDrafts = new NoteDraftStore();
export function useNoteDraft(noteId: string) {
  const localId = useId();
  const id = noteId || `unsaved:${localId}`;
  noteDrafts.queryClient = useQueryClient();
  const draft = useSyncExternalStore(noteDrafts.subscribe, () => noteDrafts.get(id));
  return {
    ...draft,
    setTitle: (value: Setter<string>) => noteDrafts.set(id, "title", value),
    setMarkdown: (value: Setter<string>) => noteDrafts.set(id, "markdown", value),
    setDirty: (value: Setter<boolean>) => noteDrafts.set(id, "dirty", value)
  };
}
