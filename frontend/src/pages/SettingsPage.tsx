import { RESTORE_LAYOUT_KEY } from "../workspace/windows";
import { isTauri } from "../native";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Key, LogOut, RefreshCcw } from "lucide-react";

import { api, API_BASE_URL } from "../api";
import { LlmPicker } from "../components/LlmPicker";
import { Status } from "../components/Status";
import { ThemePicker } from "../components/ThemePicker";
import { KaggleLoginDialog } from "./KaggleLoginDialog";
import { FONT_SCALE_MAX, FONT_SCALE_MIN, FONT_SCALE_STEP, useAppState } from "../state";

export function SettingsPage() {
  const [restoreWindows, setRestoreWindows] = useState(() => localStorage.getItem(RESTORE_LAYOUT_KEY) === "true");
  const { provider, setProvider, model, setModel, fontScale, setFontScale, theme, setTheme } = useAppState();
  const queryClient = useQueryClient();
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.getProviders });
  const discover = useMutation({
    mutationFn: api.discoverModels,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] })
  });
  const check = useMutation({ mutationFn: ({ providerName, modelName }: { providerName: string; modelName?: string }) => api.checkProvider(providerName, modelName) });

  // Kaggle-Login: Status aus dem Backend (KAGGLE_USERNAME/KAGGLE_KEY in .env).
  // Der Login-Dialog schreibt die Credentials via POST /settings/kaggle direkt
  // in die .env — sie werden nie im localStorage gespeichert.
  const kaggleQuery = useQuery({ queryKey: ["kaggle-status"], queryFn: api.getKaggleStatus });
  const kaggleLogout = useMutation({
    mutationFn: api.logoutKaggle,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["kaggle-status"] })
  });
  const [kaggleDialogOpen, setKaggleDialogOpen] = useState(false);

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Runtime</span>
          <h1>Settings</h1>
        </div>
      </div>

      <section className="panel">
        <div className="settings-grid">
          <label>
            API Base URL
            <input value={API_BASE_URL} readOnly />
          </label>
          {/* Dieselbe Auswahl wie in der Kopfzeile — vorher stand hier ein
              Freitextfeld für das Modell, sodass ein Tippfehler erst beim
              nächsten LLM-Aufruf auffiel. */}
          <LlmPicker
            variant="inline"
            provider={provider}
            model={model}
            onProviderChange={setProvider}
            onModelChange={setModel}
          />
          <label>
            Schriftgröße ({Math.round(fontScale * 100)}%)
            <div className="font-scale-setting">
              <input
                type="range"
                min={FONT_SCALE_MIN}
                max={FONT_SCALE_MAX}
                step={FONT_SCALE_STEP}
                value={fontScale}
                onChange={(event) => setFontScale(Number(event.target.value))}
                aria-label="Schriftgröße des gesamten Programms"
              />
              <button type="button" className="button" onClick={() => setFontScale(1)} disabled={fontScale === 1}>
                Zurücksetzen
              </button>
            </div>
            <span className="muted">Skaliert das gesamte Programm (Text, Symbole, Layout).</span>
          </label>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Darstellung</span>
            <strong>Farbschema</strong>
          </div>
        </div>
        <ThemePicker variant="inline" theme={theme} onSelect={setTheme} />
      </section>

      {isTauri() ? <section className="panel">
        <label><input type="checkbox" checked={restoreWindows} onChange={event => {
          localStorage.setItem(RESTORE_LAYOUT_KEY, String(event.target.checked)); setRestoreWindows(event.target.checked);
        }} /> Fensteranordnung beim Start wiederherstellen</label>
        <p className="muted">Ohne diese Einstellung startet der Arbeitsplatz vollständig angedockt.</p>
      </section> : null}

      <div className="provider-grid">
        {(providersQuery.data?.providers ?? []).map((item) => (
          <section className="provider-card" key={item.name}>
            <div className="panel-heading">
              <div>
                <span>{item.provider_type}</span>
                <strong>{item.name}</strong>
              </div>
              <Status value={item.auth_configured ? "true" : "local"} />
            </div>
            <dl>
              <dt>Base URL</dt>
              <dd>{item.base_url}</dd>
              <dt>Default Model</dt>
              <dd>{item.default_model}</dd>
              <dt>Context</dt>
              <dd>{item.settings.context_size ?? "n/a"}</dd>
            </dl>
            <div className="button-row">
              <button className="button" onClick={() => discover.mutate(item.name)} disabled={discover.isPending}>
                <RefreshCcw size={16} />
                <span>Discover</span>
              </button>
              <button className="button button-primary" onClick={() => check.mutate({ providerName: item.name, modelName: model })} disabled={check.isPending}>
                <CheckCircle2 size={16} />
                <span>Check</span>
              </button>
            </div>
            <div className="model-list">
              {item.models.map((modelName) => (
                <button key={modelName} className={model === modelName ? "active" : ""} onClick={() => setModel(modelName)}>
                  {modelName}
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Datensatz-Quellen</span>
            <strong>Kaggle</strong>
          </div>
          <Status value={kaggleQuery.data?.authenticated ? "success" : kaggleQuery.isLoading ? "loading" : "local"} />
        </div>
        <div className="settings-grid">
          {kaggleQuery.data?.authenticated ? (
            <>
              <label>
                Angemeldet als
                <input value={kaggleQuery.data.username ?? "?"} readOnly />
              </label>
              <button
                type="button"
                className="button"
                onClick={() => kaggleLogout.mutate()}
                disabled={kaggleLogout.isPending}
              >
                <LogOut size={16} />
                <span>Abmelden</span>
              </button>
            </>
          ) : (
            <>
              <p className="muted">
                Kaggle-Wettbewerbe und -Datensätze brauchen einen Login (kostenloser
                Kaggle-Account). Die Credentials landen in der lokalen
                <code>.env</code> (KAGGLE_USERNAME / KAGGLE_KEY) — nie im localStorage
                oder in der Cloud.
              </p>
              <button
                type="button"
                className="button button-primary"
                onClick={() => setKaggleDialogOpen(true)}
              >
                <Key size={16} />
                <span>Kaggle verbinden</span>
              </button>
            </>
          )}
        </div>
      </section>

      {check.data ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Provider Check</span>
              <strong>
                {check.data.provider} · {check.data.model}
              </strong>
            </div>
            <Status value={check.data.ok ? "success" : "failed"} />
          </div>
          {check.data.error ? <div className="warning-row">{check.data.error}</div> : null}
        </section>
      ) : null}

      <KaggleLoginDialog
        open={kaggleDialogOpen}
        onClose={() => setKaggleDialogOpen(false)}
        onLoggedIn={() => {
          setKaggleDialogOpen(false);
          void queryClient.invalidateQueries({ queryKey: ["kaggle-status"] });
        }}
      />
    </section>
  );
}
