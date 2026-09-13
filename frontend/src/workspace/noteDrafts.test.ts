import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NoteDraftStore } from "./noteDrafts";
import { api } from "../api";

beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });
const result = (markdown: string) => ({ note: { id: "n", title: "N", markdown } }) as Awaited<ReturnType<typeof api.updateNote>>;
describe("shared note drafts", () => {
  it("serializes saves and never clears a newer revision", async () => {
    let finish!: (value: ReturnType<typeof result>) => void;
    const save = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockImplementation(async (_id, payload) => result(payload.markdown));
    const store = new NoteDraftStore(save);
    store.seed("n", "N", "old"); store.set("n", "markdown", "first"); store.set("n", "dirty", true);
    const first = store.flushOne("n");
    store.set("n", "markdown", "second");
    expect(save).toHaveBeenCalledTimes(1);
    finish(result("first")); await first;
    expect(store.get("n").dirty).toBe(true); expect(store.get("n").markdown).toBe("second");
    await store.flushAll(); expect(save).toHaveBeenCalledTimes(2); expect(store.get("n").dirty).toBe(false);
  });
  it("retains dirty documents after switching to another note", async () => {
    const save = vi.fn(async (_id, payload) => result(payload.markdown));
    const store = new NoteDraftStore(save);
    store.set("n", "markdown", "draft"); store.set("n", "dirty", true);
    store.seed("other", "Other", "other note");
    await vi.advanceTimersByTimeAsync(1401);
    expect(save).toHaveBeenCalledWith("n", { title: "", markdown: "draft" });
    expect(store.get("other").markdown).toBe("other note");
  });
  it("does not autosave while a citation is being committed atomically", async () => {
    const save = vi.fn(async () => result("quote")); const store = new NoteDraftStore(save);
    const release = store.beginAtomic("n");
    store.set("n", "markdown", "quote"); store.set("n", "dirty", true);
    await vi.advanceTimersByTimeAsync(1500); expect(save).not.toHaveBeenCalled();
    release(); await vi.advanceTimersByTimeAsync(1); expect(save).toHaveBeenCalledOnce();
  });
  it("blocks shutdown on save failure and keeps a recoverable draft", async () => {
    const save = vi.fn(async () => { throw new Error("disk full"); }); const store = new NoteDraftStore(save);
    store.set("n", "markdown", "unsaved"); store.set("n", "dirty", true);
    await expect(store.flushAll()).rejects.toThrow("disk full");
    expect(store.get("n").dirty).toBe(true);
    expect(new NoteDraftStore(save).get("n").markdown).toBe("unsaved");
  });
  it("keeps an unreadable recovery backup and blocks silent replacement", async () => {
    localStorage.setItem("sciencekg.notes.unsavedDrafts.v1", "{broken");
    const store = new NoteDraftStore(vi.fn(async () => result("new")));
    store.set("n", "markdown", "new"); store.set("n", "dirty", true);
    await expect(store.flushAll()).rejects.toThrow("nicht gelesen");
    expect(localStorage.getItem("sciencekg.notes.unsavedDrafts.v1")).toBe("{broken");
  });
  it("rejects old query snapshots after a confirmed save", () => {
    const store = new NoteDraftStore(vi.fn());
    store.acknowledge({ ...result("new").note, updated_timestamp: "2026-09-09T12:00:00Z" });
    expect(store.isStaleQuery({ ...result("old").note, updated_timestamp: "2026-09-09T11:00:00Z" })).toBe(true);
    expect(store.isStaleQuery({ ...result("newer").note, updated_timestamp: "2026-09-09T13:00:00Z" })).toBe(false);
  });
  it("serializes explicit document operations and preserves edits made during restore", async () => {
    const store = new NoteDraftStore(vi.fn(async () => result("saved")));
    store.seed("n", "N", "before");
    let finish!: () => void;
    const operation = store.exclusive("n", async () => {
      const revision = store.get("n").revision;
      await new Promise<void>(resolve => { finish = resolve; });
      store.replace(result("restored").note, revision);
    });
    const second = vi.fn(async () => {});
    const queued = store.exclusive("n", second);
    store.set("n", "markdown", "typed during restore"); store.set("n", "dirty", true);
    expect(second).not.toHaveBeenCalled();
    finish(); await Promise.all([operation, queued]);
    expect(second).toHaveBeenCalledOnce();
    expect(store.get("n").markdown).toBe("typed during restore");
    expect(store.get("n").dirty).toBe(true);
  });
});
