import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ShutdownGuard, registerWorkspaceShutdown } from "./ShutdownGuard";
import { WorkspaceWindowsContext } from "./PortablePane";
import { WorkspaceWindows } from "./windows";

const mocks = vi.hoisted(() => ({ flush: vi.fn(), sessions: vi.fn(), invoke: vi.fn(), request: undefined as undefined | (() => void) }));
vi.mock("../native", () => ({
  isTauri: () => true, nativeInvoke: mocks.invoke, openLinkedWorkspaceWindow: vi.fn(), openExternal: vi.fn(),
  nativeListen: async (_event: string, callback: () => void) => { mocks.request = callback; return () => {}; }
}));
vi.mock("./noteDrafts", () => ({ noteDrafts: { flushAll: mocks.flush, recover: vi.fn() } }));
vi.mock("../pages/assistantSession", () => ({ flushAssistantSessions: mocks.sessions }));
const releases: (() => void)[] = [];
beforeEach(() => { vi.clearAllMocks(); mocks.flush.mockResolvedValue(undefined); mocks.sessions.mockResolvedValue(undefined); mocks.invoke.mockResolvedValue(undefined); });
afterEach(() => { cleanup(); vi.useRealTimers(); for (const release of releases.splice(0)) release(); });

it("saves before stopping, flushes the final revision and locks inputs until exit", async () => {
  const order: string[] = [];
  mocks.flush.mockImplementation(async () => { order.push("notes"); });
  mocks.sessions.mockImplementation(async () => { order.push("sessions"); });
  mocks.invoke.mockImplementation(async () => { order.push("exit"); });
  releases.push(registerWorkspaceShutdown(() => { order.push("drafts"); }));
  releases.push(registerWorkspaceShutdown(() => { order.push("stop"); }, "stop"));
  const manager = new WorkspaceWindows({ enabled: false, open: vi.fn(), command: vi.fn() });
  const lock = vi.spyOn(manager, "setInputLocked");
  render(<WorkspaceWindowsContext.Provider value={manager}><ShutdownGuard /></WorkspaceWindowsContext.Provider>);
  await act(async () => { mocks.request!(); mocks.request!(); });
  await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith("workspace_finish_exit"));
  expect(order).toEqual(["drafts", "notes", "sessions", "stop", "notes", "sessions", "exit"]);
  expect(lock.mock.calls).toEqual([[true], [false]]);
});

it("leaves work running and exposes the save error when shutdown cannot save", async () => {
  mocks.flush.mockRejectedValue(new Error("disk full"));
  const stop = vi.fn(); releases.push(registerWorkspaceShutdown(stop, "stop"));
  render(<ShutdownGuard />);
  await act(async () => { mocks.request!(); });
  expect(await screen.findByRole("alert")).toHaveTextContent("disk full");
  expect(stop).not.toHaveBeenCalled(); expect(mocks.invoke).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Weiterarbeiten" })).toBeVisible();
});

it("does not stop work after a timed-out save eventually completes", async () => {
  vi.useFakeTimers();
  let finish!: () => void;
  mocks.flush.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  const stop = vi.fn(); releases.push(registerWorkspaceShutdown(stop, "stop"));
  render(<><input aria-label="Notiz auf anderer Seite" /><ShutdownGuard /></>);
  await act(async () => { mocks.request!(); });
  expect((screen.getByLabelText("Notiz auf anderer Seite") as HTMLElement).inert).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(15001); });
  expect(screen.getByRole("alert")).toHaveTextContent("Speichern dauert zu lange");
  expect((screen.getByLabelText("Notiz auf anderer Seite") as HTMLElement).inert).toBeFalsy();
  await act(async () => { finish(); });
  expect(stop).not.toHaveBeenCalled(); expect(mocks.invoke).not.toHaveBeenCalled();
});
