import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LlmPicker, isRemoteModel } from "./LlmPicker";
import type { Provider } from "../types";

/**
 * Der Picker ist die eine Stelle, an der man sieht, welches Modell gleich
 * antwortet. Zwei Dinge müssen stimmen: die Liste muss die *erkannten* Modelle
 * enthalten (sonst ist ein frisch geladenes LM-Studio-Modell nicht wählbar),
 * und „erbt global" muss `undefined` melden — nur dann schickt der Aufrufer
 * `null` und der Router bleibt bei config.yaml, statt eine alte Wahl
 * einzufrieren.
 */
vi.mock("../api", () => ({
  api: {
    getProviders: vi.fn(),
    discoverModels: vi.fn(),
  },
}));

const { api } = await import("../api");

function provider(extra: Partial<Provider> = {}): Provider {
  return {
    name: "lm_studio",
    provider_type: "lm_studio",
    base_url: "http://127.0.0.1:1234",
    default_model: "aus-config",
    models: ["aus-config", "auch-aus-config"],
    settings: {},
    auth_configured: true,
    ...extra,
  };
}

function renderPicker(props: Partial<React.ComponentProps<typeof LlmPicker>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // Bewusst ohne AppStateContext: der Picker nutzt useOptionalAppState und muss
  // auch in einer Testumgebung ohne App-Schale rendern.
  return render(
    <QueryClientProvider client={client}>
      <LlmPicker
        provider="lm_studio"
        onProviderChange={() => {}}
        onModelChange={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.getProviders).mockResolvedValue({
    default_provider: "lm_studio",
    providers: [provider()],
  });
  vi.mocked(api.discoverModels).mockResolvedValue({
    provider: "lm_studio",
    models: ["frisch-geladen"],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LlmPicker", () => {
  it("resets GLM to the DeepSeek provider default in the same interaction", async () => {
    vi.mocked(api.getProviders).mockResolvedValue({ default_provider: "glm", providers: [provider({ name: "glm", default_model: "glm-model" }), provider({ name: "deepseek", default_model: "deepseek-model" })] });
    const onProviderChange = vi.fn(), onModelChange = vi.fn();
    renderPicker({ provider: "glm", model: "glm-model", onProviderChange, onModelChange });
    await screen.findByRole("option", { name: "deepseek" });
    fireEvent.change(screen.getByLabelText("Provider", { selector: "select" }), { target: { value: "deepseek" } });
    expect(onProviderChange).toHaveBeenCalledWith("deepseek");
    expect(onModelChange).toHaveBeenCalledWith("deepseek-model");
  });

  it("stellt erkannte Modelle vor die aus config.yaml und dedupliziert", async () => {
    renderPicker({ model: "handverlesen" });

    await waitFor(() => expect(screen.getByRole("option", { name: "frisch-geladen" })).toBeTruthy());
    const options = screen
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value)
      .filter((value) => value !== "lm_studio");

    expect(options).toEqual(["frisch-geladen", "aus-config", "auch-aus-config", "handverlesen"]);
  });

  it("meldet undefined statt eines Namens, wenn geerbt wird", async () => {
    const onModelChange = vi.fn();
    renderPicker({
      provider: undefined,
      model: undefined,
      inheritFrom: { provider: "lm_studio", model: "global-gewählt" },
      onModelChange,
    });

    const modelSelect = screen.getByLabelText("Modell", { selector: "select" }) as HTMLSelectElement;
    expect(modelSelect.value).toBe("");
    // Der leere Wert ist der Erb-Eintrag — der Aufrufer macht daraus `null`.
    expect(modelSelect.selectedOptions[0].textContent).toContain("erbt global");
  });

  it("nennt beim Erben den Namen der globalen Wahl", async () => {
    renderPicker({
      provider: undefined,
      inheritFrom: { provider: "lm_studio", model: "global-gewählt" },
    });
    expect(screen.getByText("erbt global (lm_studio)")).toBeTruthy();
    expect(screen.getByText("erbt global (global-gewählt)")).toBeTruthy();
  });

  it("erkennt Ollamas :cloud-Modelle als nicht-lokal", () => {
    expect(isRemoteModel("deepseek-v4-flash:cloud")).toBe(true);
    expect(isRemoteModel("glm-5.2:cloud")).toBe(true);
    // Nicht am Namen hängen bleiben: ein lokales Modell darf "cloud" heissen.
    expect(isRemoteModel("qwen3.5:9b")).toBe(false);
    expect(isRemoteModel("cloud-llama:7b")).toBe(false);
    expect(isRemoteModel(undefined)).toBe(false);
  });

  it("markiert ein :cloud-Modell in der Liste und daneben", async () => {
    vi.mocked(api.discoverModels).mockResolvedValue({
      provider: "lm_studio",
      models: ["deepseek-v4-flash:cloud"],
    });
    renderPicker({ model: "deepseek-v4-flash:cloud" });

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "☁ deepseek-v4-flash:cloud" })).toBeTruthy(),
    );
    expect(screen.getByText("☁ nicht lokal")).toBeTruthy();
  });

  it("fragt keine Modelle ab für Anbieter, die das nicht können", async () => {
    vi.mocked(api.getProviders).mockResolvedValue({
      default_provider: "gemini",
      providers: [provider({ name: "gemini", provider_type: "gemini" })],
    });
    renderPicker({ provider: "gemini" });

    await waitFor(() => expect(api.getProviders).toHaveBeenCalled());
    expect(api.discoverModels).not.toHaveBeenCalled();
  });
});
