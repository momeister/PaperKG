import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { AppStateContext, type AppState } from "../state";
import { GlossaryPanel, GlossaryProvider } from "./GlossaryProvider";
import { GlossaryText } from "./GlossaryText";
import type { GlossaryEntry } from "../types";

const entry: GlossaryEntry = { id: "1", term: "Network", term_key: "network", explanation: "Connected nodes", created_at: "2026-01-01", updated_at: "2026-01-01" };
function setup() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><AppStateContext.Provider value={{ provider: "local", model: "chosen-model" } as AppState}><GlossaryProvider><GlossaryPanel /><GlossaryText text="A Network." /></GlossaryProvider></AppStateContext.Provider></QueryClientProvider>);
}
async function newDraft() {
  if (!screen.queryByText("Begriff anlegen")) fireEvent.click(screen.getByRole("button", { name: /Wörterbuch/ }));
  fireEvent.click(screen.getByText("Begriff anlegen"));
  fireEvent.change(screen.getByLabelText("Begriff"), { target: { value: "Term" } });
}
beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.spyOn(api, "listGlossary").mockResolvedValue({ items: [entry] });
  vi.spyOn(api, "suggestGlossary").mockResolvedValue({ suggestion: "Suggested meaning" });
  vi.spyOn(api, "createGlossary").mockResolvedValue(entry);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("glossary management", () => {
  it("persists independent hint/management switches and exposes focus/Escape tooltips", async () => {
    const view = setup();
    await waitFor(() => expect(document.querySelector(".glossary-term")).not.toBeNull());
    expect(screen.queryByText("Begriff anlegen")).toBeNull();
    fireEvent.focus(document.querySelector(".glossary-term")!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Connected nodes");
    fireEvent.keyDown(document.querySelector(".glossary-term")!, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Wörterbuch/ }));
    fireEvent.click(screen.getByLabelText("Begriffshinweise anzeigen"));
    expect(document.querySelector(".glossary-term")).toBeNull();
    view.unmount(); setup();
    expect(screen.getByText("Begriff anlegen")).toBeVisible();
    expect(screen.getByLabelText("Begriffshinweise anzeigen")).not.toBeChecked();
  });
  it("requests only on click and adopts/saves a suggestion only explicitly", async () => {
    setup(); await newDraft();
    fireEvent.change(screen.getByLabelText("Erklärung"), { target: { value: "My input" } });
    expect(api.suggestGlossary).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("KI-Vorschlag anfordern"));
    await screen.findByText("Vorschlag übernehmen");
    expect(api.suggestGlossary).toHaveBeenCalledWith({ term: "Term", selected_text: "", provider: "local", model: "chosen-model" });
    expect(screen.getByLabelText("Erklärung")).toHaveValue("My input");
    expect(api.createGlossary).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Vorschlag übernehmen"));
    expect(screen.getByLabelText("Erklärung")).toHaveValue("Suggested meaning");
    fireEvent.click(screen.getByText("Speichern"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.createGlossary).toHaveBeenCalledWith({ term: "Term", explanation: "Suggested meaning" });
  });
  it("preserves own text on errors and rejects responses belonging to cancelled drafts", async () => {
    let resolve!: (value: { suggestion: string }) => void;
    vi.mocked(api.suggestGlossary).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    setup(); await newDraft();
    fireEvent.click(screen.getByText("KI-Vorschlag anfordern"));
    fireEvent.click(screen.getByText("Abbrechen"));
    await newDraft();
    fireEvent.change(screen.getByLabelText("Erklärung"), { target: { value: "Keep this" } });
    await act(async () => { resolve({ suggestion: "Late obsolete answer" }); });
    expect(screen.queryByText("Late obsolete answer")).toBeNull();
    vi.mocked(api.suggestGlossary).mockRejectedValueOnce(Error("Offline"));
    fireEvent.click(screen.getByText("KI-Vorschlag anfordern"));
    await screen.findByText("Offline");
    expect(screen.getByLabelText("Erklärung")).toHaveValue("Keep this");
    expect(api.createGlossary).not.toHaveBeenCalled();
  });
  it("invalidates a suggestion when its term changes and offers duplicate editing", async () => {
    let resolve!: (value: { suggestion: string }) => void;
    vi.mocked(api.suggestGlossary).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    vi.mocked(api.createGlossary).mockRejectedValueOnce(new ApiError(409, { message: "Duplicate", entry }));
    setup(); await newDraft();
    fireEvent.click(screen.getByText("KI-Vorschlag anfordern"));
    fireEvent.change(screen.getByLabelText("Begriff"), { target: { value: "Changed" } });
    await act(async () => { resolve({ suggestion: "Stale term meaning" }); });
    expect(screen.queryByText("Stale term meaning")).toBeNull();
    fireEvent.change(screen.getByLabelText("Erklärung"), { target: { value: "My input" } });
    fireEvent.click(screen.getByText("Speichern"));
    fireEvent.click(await screen.findByText("Vorhandenen Eintrag bearbeiten"));
    expect(screen.getByLabelText("Begriff")).toHaveValue("Network");
    expect(screen.getByLabelText("Erklärung")).toHaveValue("Connected nodes");
  });
});
