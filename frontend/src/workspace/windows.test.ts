import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceWindows, RESTORE_LAYOUT_KEY, type WindowAdapter } from "./windows";
import { PANE_IDS, PANE_PROTOCOL, type PaneId, type PaneMessage } from "./protocol";

function harness(options: { failOpen?: boolean; noCommit?: boolean } = {}) {
  const frames: HTMLIFrameElement[] = [];
  const slots = new Map<PaneId, { dock: HTMLElement; view: HTMLElement; input: HTMLTextAreaElement }>();
  const command = vi.fn(async () => {});
  let manager: WorkspaceWindows;
  const adapter: WindowAdapter = {
    enabled: true, command,
    open: vi.fn((url: URL) => {
      if (options.failOpen) return null;
      const frame = document.createElement("iframe"); document.body.append(frame); frames.push(frame);
      const child = frame.contentWindow!;
      child.document.body.innerHTML = '<div id="workspace-pane-root"></div>';
      const pane = url.searchParams.get("pane") as PaneId, actionId = url.searchParams.get("actionId")!;
      const receive = (type: PaneMessage["type"], revision: number) => manager.receive(new MessageEvent("message", {
        origin: window.location.origin, source: child, data: { protocol: PANE_PROTOCOL, pane, actionId, revision, type }
      }));
      child.postMessage = vi.fn((message: PaneMessage) => { if (!options.noCommit) queueMicrotask(() => receive("committed", message.revision)); });
      queueMicrotask(() => receive("ready", 0));
      return child;
    })
  };
  manager = new WorkspaceWindows(adapter, 30); manager.setOwner("p");
  for (const pane of PANE_IDS) {
    const dock = document.createElement("div"), view = document.createElement("div"), input = document.createElement("textarea");
    view.className = "workspace-portable-view"; view.append(input); document.body.append(dock);
    input.value = `${pane} draft`; input.setSelectionRange(2, 5);
    view.scrollTop = 51;
    manager.register(pane, "p", view, dock); slots.set(pane, { dock, view, input });
  }
  return { manager, adapter, command, frames, slots };
}
beforeEach(() => localStorage.clear());
afterEach(() => { document.body.innerHTML = ""; vi.restoreAllMocks(); });
describe("workspace window handoff", () => {
  it("moves all four actual views and preserves their node identity, selection and handlers", async () => {
    const { manager, slots, command } = harness();
    const listener = vi.fn(); slots.get("notes")!.input.addEventListener("input", listener);
    await Promise.all(PANE_IDS.map(p => manager.detach(p)));
    for (const pane of PANE_IDS) {
      const { view, input } = slots.get(pane)!;
      expect(manager.isDetached(pane)).toBe(true);
      expect(input.ownerDocument).not.toBe(document);
      expect(input.selectionStart).toBe(2); expect(input.selectionEnd).toBe(5);
      expect(input.value).toBe(`${pane} draft`); expect(view.scrollTop).toBe(51);
    }
    slots.get("notes")!.input.dispatchEvent(new Event("input")); expect(listener).toHaveBeenCalledOnce();
    for (let i = 0; i < 3; i++) {
      await Promise.all(PANE_IDS.map(p => manager.dock(p)));
      for (const pane of PANE_IDS) expect(slots.get(pane)!.input.ownerDocument).toBe(document);
      if (i < 2) await Promise.all(PANE_IDS.map(p => manager.detach(p)));
    }
    expect(command.mock.calls.filter(call => (call as unknown[])[1] === "destroy")).toHaveLength(12);
  });
  it("deduplicates double clicks", async () => {
    const { manager, adapter } = harness();
    const first = manager.detach("notes"), second = manager.detach("notes");
    expect(second).toBe(first); await first; expect(adapter.open).toHaveBeenCalledOnce();
  });
  it("switches project views inside the same window and keeps each draft", async () => {
    const { manager, slots, adapter } = harness();
    const dock = document.createElement("div"), view = document.createElement("div"), input = document.createElement("textarea");
    input.value = "second project"; view.append(input); document.body.append(dock);
    manager.register("notes", "other", view, dock);
    await manager.detach("notes");
    const first = slots.get("notes")!;
    const remote = first.view.ownerDocument;
    manager.setOwner("other");
    expect(first.view.parentElement).toBe(first.dock);
    expect(view.ownerDocument).toBe(remote);
    expect(input.value).toBe("second project");
    manager.setOwner("p");
    expect(first.view.ownerDocument).toBe(remote);
    expect(first.input.value).toBe("notes draft");
    expect(view.parentElement).toBe(dock);
    expect(adapter.open).toHaveBeenCalledOnce();
    await manager.dock("notes");
  });
  it("queues close during handoff and deduplicates repeated docking", async () => {
    const { manager, slots, command } = harness();
    const opening = manager.detach("notes");
    const closing = manager.dock("notes"), closingAgain = manager.dock("notes");
    await Promise.all([opening, closing, closingAgain]);
    expect(slots.get("notes")!.input.ownerDocument).toBe(document);
    expect(manager.isBusy("notes")).toBe(false);
    expect(manager.isDetached("notes")).toBe(false);
    expect(command.mock.calls.filter(call => (call as unknown[])[1] === "destroy")).toHaveLength(1);
  });
  it.each([{ failOpen: true }, { noCommit: true }])("rolls back failed or unconfirmed handoffs: %o", async options => {
    const { manager, slots } = harness(options);
    await manager.detach("notes");
    expect(manager.isDetached("notes")).toBe(false);
    const { view, dock, input } = slots.get("notes")!;
    expect(view.parentElement).toBe(dock); expect(input.value).toBe("notes draft");
    expect(view.inert).toBeFalsy(); expect(manager.error("notes")).toBeTruthy();
  });
  it("ignores forged acknowledgements and stale revisions", async () => {
    const { manager, slots } = harness({ noCommit: true });
    const operation = manager.detach("assistant");
    manager.receive(new MessageEvent("message", { origin: "https://other.test", data: { protocol: PANE_PROTOCOL, pane: "assistant", type: "committed", actionId: "old", revision: 999 } }));
    await operation;
    expect(slots.get("assistant")!.view.ownerDocument).toBe(document);
    expect(manager.isDetached("assistant")).toBe(false);
  });
  it("starts docked by default and restores only opted-in known panes", async () => {
    const { manager, adapter } = harness();
    localStorage.setItem("sciencekg.workspace.detachedWindows", '["notes","main","center"]');
    await manager.restore(); expect(adapter.open).not.toHaveBeenCalled();
    localStorage.setItem(RESTORE_LAYOUT_KEY, "true"); await manager.restore();
    expect(adapter.open).toHaveBeenCalledTimes(2);
    expect(manager.isDetached("notes")).toBe(true); expect(manager.isDetached("center")).toBe(true);
  });
  it("keeps browser operation docked", async () => {
    const open = vi.fn(); const manager = new WorkspaceWindows({ enabled: false, open, command: vi.fn() });
    await manager.detach("notes"); expect(open).not.toHaveBeenCalled();
  });
});
