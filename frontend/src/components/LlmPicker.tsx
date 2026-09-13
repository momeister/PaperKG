/**
 * Anbieter- und Modellwahl. Eine Komponente für alle Stellen, die eine haben.
 *
 * Vorher stand dieselbe Auswahl viermal im Programm — in der Topbar, in den
 * Einstellungen, bei den Benchmarks und im Overlay —, jedes Mal ein bisschen
 * anders: mal mit erkannten Modellen, mal nur mit denen aus `config.yaml`, in den
 * Einstellungen sogar als Freitextfeld. Wer das Modell an einer Stelle umstellte,
 * konnte an der nächsten nicht sehen, was gilt.
 *
 * `inheritFrom` ist der Grund, warum diese Komponente nicht einfach die globale
 * Wahl anzeigt: eine Nebenstelle (Code-Graph, Overlay, Benchmarks) soll *erben*
 * dürfen. Erbt sie, bleibt der Wert `undefined` und die Aufrufer schicken `null`
 * — dann entscheidet der Router anhand von `config.yaml`, und die Nebenstelle
 * friert keine Wahl ein, die der Nutzer längst geändert hat.
 *
 * Keine schweren Abhängigkeiten hier: `OverlayPage` liegt statisch im
 * Entry-Chunk, den fünf Webviews parsen.
 */
import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import { useLlmProviders } from "../hooks/useLlmProviders";
import type { LlmParams } from "../state";

/**
 * Läuft dieses Modell woanders?
 *
 * Ollamas Pro-Abo hängt `:cloud` an den Namen und schickt die Anfrage über
 * denselben lokalen Port an fremde Server. Von aussen sieht das aus wie ein
 * lokales Modell — und genau deshalb steht der Unterschied hier im Bild. Wer
 * einen Paper-Volltext oder seinen Quelltext extrahieren lässt, soll es sehen,
 * bevor er es tut, nicht danach.
 *
 * Nur eine Anzeige, keine Sperre: alle Anbieter bleiben nutzbar.
 */
export function isRemoteModel(model: string | undefined): boolean {
  return typeof model === "string" && model.endsWith(":cloud");
}

const REMOTE_HINT = "Läuft auf Ollamas Servern, nicht auf diesem Rechner.";

export type LlmPickerProps = {
  provider?: string;
  model?: string;
  onProviderChange: (provider?: string) => void;
  onModelChange: (model?: string) => void;
  /** topbar = Kopfzeile der App · inline = im Fliesstext einer Seite · compact = Werkzeugleiste */
  variant?: "topbar" | "inline" | "compact";
  /** Erlaubt „erbt global" als erste Option; die globale Wahl steht daneben. */
  inheritFrom?: { provider?: string; model?: string };
  /** Zahnrad mit Temperatur/Top-p/Tokens/Kontext. Ohne Angabe kein Zahnrad. */
  llmParams?: LlmParams;
  onLlmParamsChange?: (params: LlmParams) => void;
  disabled?: boolean;
  enabled?: boolean;
};

export function LlmPicker({
  provider,
  model,
  onProviderChange,
  onModelChange,
  variant = "inline",
  inheritFrom,
  llmParams,
  onLlmParamsChange,
  disabled = false,
  enabled = true,
}: LlmPickerProps) {
  const [paramsOpen, setParamsOpen] = useState(false);
  // Beim Erben zeigt die Liste die Modelle des *geerbten* Anbieters — sonst
  // stünde dort „erbt global (lm_studio)" und darunter die Modelle von gar nichts.
  const effectiveProvider = provider ?? inheritFrom?.provider;
  const { providers, selectedProvider, modelOptions, isDiscovering, isError } = useLlmProviders(
    effectiveProvider,
    model ?? inheritFrom?.model,
    { enabled },
  );

  function updateParam(key: keyof LlmParams, rawValue: string) {
    onLlmParamsChange?.({
      ...(llmParams ?? {}),
      [key]: rawValue === "" ? undefined : Number(rawValue),
    });
  }

  const inheritLabel = inheritFrom
    ? `erbt global${inheritFrom.provider ? ` (${inheritFrom.provider})` : ""}`
    : null;

  return (
    <div className={`llm-picker llm-picker--${variant}`}>
      <label>
        Provider
        <select
          value={provider ?? ""}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value || undefined;
            onProviderChange(next);
            onModelChange(next ? providers.find(item => item.name === next)?.default_model || undefined : undefined);
          }}
        >
          {inheritLabel && <option value="">{inheritLabel}</option>}
          {isError && !inheritLabel && <option value="">⚠ Provider nicht ladbar</option>}
          {providers.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label className="topbar-model">
        Modell
        <select
          value={model ?? (inheritFrom ? "" : selectedProvider?.default_model ?? "")}
          disabled={disabled}
          onChange={(event) => onModelChange(event.target.value || undefined)}
        >
          {inheritLabel && (
            <option value="">
              {inheritFrom?.model ? `erbt global (${inheritFrom.model})` : "erbt global"}
            </option>
          )}
          {modelOptions.map((item) => (
            <option key={item} value={item} title={isRemoteModel(item) ? REMOTE_HINT : undefined}>
              {isRemoteModel(item) ? `☁ ${item}` : item}
            </option>
          ))}
        </select>
      </label>
      {isRemoteModel(model ?? (inheritFrom ? inheritFrom.model : selectedProvider?.default_model)) ? (
        <span className="topbar-hint llm-picker-remote" title={REMOTE_HINT}>
          ☁ nicht lokal
        </span>
      ) : null}
      {isDiscovering ? <span className="topbar-hint">erkenne Modelle…</span> : null}
      {llmParams && onLlmParamsChange ? (
        <span className="llm-params-wrap">
          <button
            className={`icon-button ${
              paramsOpen || Object.values(llmParams).some((value) => value !== undefined)
                ? "icon-button--active"
                : ""
            }`}
            type="button"
            aria-label="LLM-Parameter anpassen"
            title="LLM-Parameter anpassen"
            onClick={() => setParamsOpen((current) => !current)}
          >
            <SlidersHorizontal size={17} />
          </button>
          {paramsOpen ? (
            <div className="llm-params-popover">
              <strong>LLM-Parameter</strong>
              <p className="muted">Gelten für Assistant-Antworten; leer = Provider-Default.</p>
              <label>
                Temperatur
                <input
                  type="number" min="0" max="2" step="0.05"
                  value={llmParams.temperature ?? ""}
                  placeholder={String(selectedProvider?.settings?.temperature ?? 0.2)}
                  onChange={(event) => updateParam("temperature", event.target.value)}
                />
              </label>
              <label>
                Top-p
                <input
                  type="number" min="0.05" max="1" step="0.05"
                  value={llmParams.top_p ?? ""}
                  placeholder={String(selectedProvider?.settings?.top_p ?? 0.95)}
                  onChange={(event) => updateParam("top_p", event.target.value)}
                />
              </label>
              <label>
                Max. Tokens
                <input
                  type="number" min="128" max="131072" step="128"
                  value={llmParams.max_tokens ?? ""}
                  placeholder={String(selectedProvider?.settings?.max_tokens ?? 2048)}
                  onChange={(event) => updateParam("max_tokens", event.target.value)}
                />
              </label>
              <label>
                Kontext
                <input
                  type="number" min="1024" max="262144" step="1024"
                  value={llmParams.context_size ?? ""}
                  placeholder={String(selectedProvider?.settings?.context_size ?? 32768)}
                  onChange={(event) => updateParam("context_size", event.target.value)}
                />
              </label>
              <div className="button-row">
                <button
                  className="button button-compact"
                  type="button"
                  onClick={() => onLlmParamsChange({})}
                >
                  Zurücksetzen
                </button>
                <button
                  className="button button-compact button-primary"
                  type="button"
                  onClick={() => setParamsOpen(false)}
                >
                  Fertig
                </button>
              </div>
            </div>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

export default LlmPicker;
